import unittest
import asyncio
import message
import struct
from client import TorrentClient
from unittest.mock import MagicMock, AsyncMock, patch

class TestClientIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 1. Start a Fake Peer Server
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
            # Read handshake
            data = await reader.read(68)
            if len(data) == 68:
                self.server_received_handshake = True
                
                # Send Handshake back
                # Use arbitrary hash/id, client only checks hash match which we can mock or mirror
                info_hash = data[28:48]
                hs = message.Handshake(info_hash, b'-PC0001-SERVER000000')
                writer.write(hs.encode())
                
                # Send Unchoke
                writer.write(message.PeerMessage(message.UNCHOKE).encode())
                await writer.drain()
                
                # Wait for Request
                while True:
                    msg_len_data = await reader.read(4)
                    if not msg_len_data: break
                    msg_len = struct.unpack(">I", msg_len_data)[0]
                    if msg_len == 0: continue
                    
                    msg_id = (await reader.read(1))[0]
                    payload = await reader.read(msg_len - 1)
                    
                    if msg_id == message.REQUEST:
                        self.server_received_request = True
                        # Don't need to actually send PIECE for this test to pass
                        # We just want to prove the client connected and asked.
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
        t_instance.pieces = [b'\x00'*20] # Dummy hash
        t_instance.info_hash = b'\x11'*20
        
        # Mock Tracker to return our Fake Peer
        tr_instance = MockTracker.return_value
        tr_instance.peer_id = b'-PC0001-CLIENT000000'
        tr_instance.connect = AsyncMock(return_value=[('127.0.0.1', 9999)])
        
        # Instantiate Client
        client = TorrentClient("dummy.torrent")
        
        # We run the client for a brief moment
        task = asyncio.create_task(client.start())
        
        # Wait for interaction
        await asyncio.sleep(1)
        
        # Stop client
        client.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
        # Verify
        self.assertTrue(self.server_received_handshake, "Client failed to handshake")
        self.assertTrue(self.server_received_request, "Client failed to request piece")

if __name__ == '__main__':
    unittest.main()