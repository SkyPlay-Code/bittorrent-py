import unittest
import asyncio
import struct
import message
from peer import PeerConnection
from unittest.mock import MagicMock

class TestUploading(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server_info_hash = b'\xAA' * 20
        self.server_peer_id = b'-PC0001-000000000000'
        self.server = await asyncio.start_server(self.handle_client, '127.0.0.1', 8888)
        self.server_task = asyncio.create_task(self.server.serve_forever())

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.server_task.cancel()
        try:
            await self.server_task
        except asyncio.CancelledError:
            pass

    async def handle_client(self, reader, writer):
        try:
            # 1. Handshake
            await reader.read(68)
            hs = message.Handshake(self.server_info_hash, self.server_peer_id)
            writer.write(hs.encode())
            
            # 2. Say we are INTERESTED in the client's data
            msg_int = message.PeerMessage(message.INTERESTED)
            writer.write(msg_int.encode())
            await writer.drain()
            
            # 3. Wait for UNCHOKE
            while True:
                len_data = await reader.read(4)
                length = struct.unpack(">I", len_data)[0]
                if length == 0: continue
                msg_id = (await reader.read(1))[0]
                if length > 1: await reader.read(length-1)
                
                if msg_id == message.UNCHOKE:
                    break
            
            # 4. Request Piece 0
            req = message.Request(0, 0, 16384)
            writer.write(req.encode())
            await writer.drain()
            
            # 5. Expect PIECE message
            resp_header = await reader.read(13) 
            resp_len = struct.unpack(">I", resp_header[0:4])[0]
            resp_id = resp_header[4]
            
            if resp_id == message.PIECE:
                self.piece_received = True
            
            await reader.read(resp_len - 9)
            
            await asyncio.sleep(0.1)
        except Exception:
            pass
        finally:
            writer.close()

    async def test_uploading_flow(self):
        self.piece_received = False
        queue = asyncio.Queue()
        queue.put_nowait(('127.0.0.1', 8888))
        
        pm = MagicMock()
        pm.read_block.return_value = b'A' * 16384
        
        # DISABLE MSE
        pc = PeerConnection(queue, pm, self.server_info_hash, b'-PC0001-TEST00000000', enable_mse=False)
        
        task = asyncio.create_task(pc.run())
        
        await asyncio.sleep(1.0)
        
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
        self.assertTrue(self.piece_received, "Client did not send PIECE response to REQUEST")

if __name__ == '__main__':
    unittest.main()