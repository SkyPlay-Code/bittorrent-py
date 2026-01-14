import unittest
import asyncio
import struct
import message
from peer import PeerConnection

class TestPeerProtocol(unittest.TestCase):
    def test_handshake_encoding(self):
        info_hash = b'\x11' * 20
        peer_id = b'\x22' * 20
        hs = message.Handshake(info_hash, peer_id)
        encoded = hs.encode()
        
        self.assertEqual(len(encoded), 68)
        self.assertEqual(encoded[0], 19)
        self.assertEqual(encoded[1:20], b'BitTorrent protocol')
        self.assertEqual(encoded[28:48], info_hash)
        self.assertEqual(encoded[48:68], peer_id)

    def test_message_encoding(self):
        msg = message.PeerMessage(message.UNCHOKE)
        encoded = msg.encode()
        self.assertEqual(encoded, b'\x00\x00\x00\x01\x01')

        msg = message.Have(123)
        encoded = msg.encode()
        self.assertEqual(encoded, b'\x00\x00\x00\x05\x04' + struct.pack(">I", 123))

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
            if len(data) < 68:
                return 
                
            # 2. Send Handshake
            hs = message.Handshake(self.server_info_hash, self.server_peer_id)
            writer.write(hs.encode())
            
            # 3. Send Unchoke
            unchoke = message.PeerMessage(message.UNCHOKE)
            writer.write(unchoke.encode())
            await writer.drain()
            
            # 4. CRITICAL: Close connection to prevent client from waiting forever
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            print(f"Server error: {e}")

    async def test_handshake_flow(self):
        queue = asyncio.Queue()
        # Correct 20-byte client ID
        client_id = b'-PC0001-123456789012' 
        
        pc = PeerConnection(queue, self.server_info_hash, client_id, '127.0.0.1', 8888)
        
        # We start the client. 
        # Since the server closes connection after sending unchoke, 
        # this task should finish naturally without needing cancellation.
        await pc.start()
        
        # Assert handshake success and state update
        self.assertEqual(pc.remote_peer_id, self.server_peer_id)
        self.assertFalse(pc.peer_choking) # Should be False after receiving Unchoke
        
if __name__ == '__main__':
    unittest.main()