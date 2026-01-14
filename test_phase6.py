import unittest
import asyncio
import message
import struct
from client import TorrentClient
from unittest.mock import MagicMock, AsyncMock, patch

class TestClientIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server_received_handshake = False
        self.server_received_request = False
        
        self.server = await asyncio.start_server(
            self.handle_fake_peer, '127.0.0.1', 9999
        )
        self.server_task = asyncio.create_task(self.server.serve_forever())

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.server_task.cancel()
        try:
            await self.server_task
        except asyncio.CancelledError:
            pass

    async def handle_fake_peer(self, reader, writer):
        try:
            data = await reader.read(68)
            if len(data) == 68:
                self.server_received_handshake = True
                
                # 1. Send Handshake
                info_hash = data[28:48]
                hs = message.Handshake(info_hash, b'-PC0001-SERVER000000')
                writer.write(hs.encode())
                
                # 2. Send Bitfield (CRITICAL FIX)
                # We claim to have piece 0 (0x80 = 10000000)
                bitfield_msg = struct.pack(">IB", 2, message.BITFIELD) + b'\x80'
                writer.write(bitfield_msg)

                # 3. Send Unchoke
                writer.write(message.PeerMessage(message.UNCHOKE).encode())
                await writer.drain()
                
                # 4. Wait for Request
                while True:
                    try:
                        msg_len_data = await asyncio.wait_for(reader.read(4), timeout=2.0)
                    except asyncio.TimeoutError:
                        break
                        
                    if not msg_len_data: break
                    msg_len = struct.unpack(">I", msg_len_data)[0]
                    if msg_len == 0: continue
                    
                    msg_id = (await reader.read(1))[0]
                    payload = await reader.read(msg_len - 1)
                    
                    if msg_id == message.REQUEST:
                        self.server_received_request = True
                        break
        except Exception:
            pass
        finally:
            writer.close()

    @patch('client.Torrent')
    @patch('client.Tracker')
    async def test_client_flow(self, MockTracker, MockTorrent):
        # Mock Torrent
        t_instance = MockTorrent.return_value
        t_instance.output_file = "test_integration.bin"
        t_instance.total_size = 32768
        t_instance.piece_length = 32768
        t_instance.pieces = [b'\x00'*20]
        t_instance.info_hash = b'\x11'*20
        
        # Mock Tracker
        tr_instance = MockTracker.return_value
        tr_instance.peer_id = b'-PC0001-CLIENT000000'
        tr_instance.connect = AsyncMock(return_value=[('127.0.0.1', 9999)])
        
        client = TorrentClient("dummy.torrent")
        task = asyncio.create_task(client.start())
        
        await asyncio.sleep(1.0) # Give enough time for handshake + bitfield + request
        
        client.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
        self.assertTrue(self.server_received_handshake, "Client failed to handshake")
        self.assertTrue(self.server_received_request, "Client failed to request piece")

if __name__ == '__main__':
    unittest.main()