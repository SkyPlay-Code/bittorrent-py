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
            # 1. Expect Handshake
            data = await reader.read(68)
            if len(data) < 68: return
            
            # 2. Send Handshake
            hs = message.Handshake(self.server_info_hash, self.server_peer_id)
            writer.write(hs.encode())
            
            # 3. Send Unchoke
            writer.write(message.PeerMessage(message.UNCHOKE).encode())
            
            # 4. Send Bitfield (Required by new Peer Logic to register presence)
            # Payload: 1 byte (b'\x80') indicating we have piece 0
            bitfield_payload = b'\x80' 
            # Length: 1 (id) + 1 (payload) = 2
            writer.write(struct.pack(">IB", 2, message.BITFIELD) + bitfield_payload)
            
            await writer.drain()
            
            # Keep open briefly then close
            await asyncio.sleep(0.1)
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def test_handshake_and_loop(self):
        queue = asyncio.Queue()
        queue.put_nowait(('127.0.0.1', 8888))
        
        client_id = b'-PC0001-123456789012' 
        
        # Mock PieceManager
        pm_mock = MagicMock()
        
        pc = PeerConnection(queue, pm_mock, self.server_info_hash, client_id)
        
        # Run the worker. It should connect, handshake, process messages, 
        # and then loop again (waiting on queue).
        # We wrap it in a task and cancel it after a short delay.
        task = asyncio.create_task(pc.run())
        
        await asyncio.sleep(0.5)
        
        # Verify Handshake Success: remote_peer_id should be set
        self.assertEqual(pc.remote_peer_id, self.server_peer_id)
        
        # Verify Bitfield was processed
        # The client should have called pm.add_peer
        pm_mock.add_peer.assert_called()
        
        # Cleanup
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

if __name__ == '__main__':
    unittest.main()