import unittest
import asyncio
import struct
import socket
from peer import PeerConnection
from message import ExtendedHandshake, ExtendedMessage
from bencoding import Encoder
from unittest.mock import MagicMock

class TestExtensionProtocol(unittest.IsolatedAsyncioTestCase):
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
            # 1. Receive Handshake
            data = await reader.read(68)
            
            # 2. Send Handshake (With Extension Bit Set)
            pstr = b'BitTorrent protocol'
            reserved = bytearray(8)
            reserved[5] |= 0x10 # Set Extension Bit
            hs = struct.pack("B", 19) + pstr + reserved + self.server_info_hash + self.server_peer_id
            writer.write(hs)
            
            # 3. Receive Extension Handshake from Client
            try:
                await asyncio.wait_for(reader.read(1024), timeout=1.0)
            except asyncio.TimeoutError:
                pass

            # 4. Send OUR Extension Handshake
            # Map 'ut_pex' to ID 1
            handshake_payload = {b'm': {b'ut_pex': 1}}
            encoded_hs = Encoder(handshake_payload).encode()
            # ID 20 (Extended), ExtMsgID 0 (Handshake)
            ext_msg = ExtendedMessage(0, encoded_hs)
            writer.write(ext_msg.encode())
            
            # 5. Send a PEX Message (ID 1)
            # IP: 1.2.3.4 (01 02 03 04), Port: 5555 (15 B3)
            peer_ip = socket.inet_aton("1.2.3.4")
            peer_port = struct.pack(">H", 5555)
            # PEX payload: dictionary with key 'added'
            pex_data = {b'added': peer_ip + peer_port}
            encoded_pex = Encoder(pex_data).encode()
            
            # Msg ID 20, ExtMsgID 1 (ut_pex as defined above)
            pex_msg = ExtendedMessage(1, encoded_pex)
            writer.write(pex_msg.encode())
            
            await writer.drain()
            
            # CRITICAL FIX: Keep connection open longer to allow client to process
            await asyncio.sleep(1.0)
        except Exception:
            pass
        finally:
            writer.close()

    async def test_pex_discovery(self):
        queue = asyncio.Queue()
        # Add the fake server as the first peer
        queue.put_nowait(('127.0.0.1', 8888))
        
        pm_mock = MagicMock()
        client_id = b'-PC0001-123456789012' 
        
        pc = PeerConnection(queue, pm_mock, self.server_info_hash, client_id)
        
        # Run worker in background
        task = asyncio.create_task(pc.run())
        
        # Wait enough time for the full exchange + PEX processing
        await asyncio.sleep(1.5)
        
        # Stop worker
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
        # Check Queue
        # Initial item was popped. PEX should have added 1 item.
        # So qsize should be 1.
        self.assertEqual(queue.qsize(), 1)
        
        new_peer = await queue.get()
        self.assertEqual(new_peer, ("1.2.3.4", 5555))

if __name__ == '__main__':
    unittest.main()