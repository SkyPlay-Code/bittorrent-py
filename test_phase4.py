import unittest
import asyncio
import struct
import message
from peer import PeerConnection
from unittest.mock import MagicMock

class TestPeerProtocol(unittest.TestCase):
    def test_handshake_encoding(self):
        info_hash = b'\x11' * 20
        peer_id = b'\x22' * 20
        hs = message.Handshake(info_hash, peer_id)
        encoded = hs.encode()
        self.assertEqual(len(encoded), 68)
        self.assertEqual(encoded[0], 19)
        self.assertEqual(encoded[1:20], b'BitTorrent protocol')

    def test_message_encoding(self):
        msg = message.PeerMessage(message.UNCHOKE)
        encoded = msg.encode()
        self.assertEqual(encoded, b'\x00\x00\x00\x01\x01')

class TestPeerCommunication(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server_info_hash = b'\xAA' * 20
        self.server_peer_id = b'-PC0001-000000000000'
        self.server = await asyncio.start_server(
            self.handle_client, '127.0.0.1', 8888
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

    async def handle_client(self, reader, writer):
        try:
            data = await reader.read(68)
            if len(data) < 68: return
            
            hs = message.Handshake(self.server_info_hash, self.server_peer_id)
            writer.write(hs.encode())
            writer.write(message.PeerMessage(message.UNCHOKE).encode())
            
            bitfield_msg = struct.pack(">IB", 2, message.BITFIELD) + b'\x80'
            writer.write(bitfield_msg)
            
            await writer.drain()
            await asyncio.sleep(0.1)
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def test_handshake_and_loop(self):
        queue = asyncio.Queue()
        queue.put_nowait(('127.0.0.1', 8888))
        
        client_id = b'-PC0001-123456789012' 
        pm_mock = MagicMock()
        
        # CRITICAL FIX: Disable MSE for this test
        pc = PeerConnection(queue, pm_mock, self.server_info_hash, client_id, enable_mse=False)
        
        task = asyncio.create_task(pc.run())
        await asyncio.sleep(0.5)
        
        self.assertEqual(pc.remote_peer_id, self.server_peer_id)
        pm_mock.add_peer.assert_called()
        
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

if __name__ == '__main__':
    unittest.main()