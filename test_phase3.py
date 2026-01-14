import unittest
import asyncio
import socket
import struct
from unittest.mock import MagicMock, patch, AsyncMock
from tracker import Tracker
from torrent import Torrent
from collections import OrderedDict
from bencoding import Encoder

class TestTracker(unittest.TestCase):
    def setUp(self):
        # Mock the Torrent object
        self.torrent = MagicMock(spec=Torrent)
        self.torrent.announce = "http://tracker.example.com/announce"
        self.torrent.info_hash = b'\x12' * 20
        self.torrent.total_size = 1000

    def test_peer_id_generation(self):
        t = Tracker(self.torrent)
        peer_id = t.peer_id
        self.assertEqual(len(peer_id), 20)
        self.assertTrue(peer_id.startswith(b'-PC0001-'))

    def test_parse_compact_peers(self):
        t = Tracker(self.torrent)
        
        # Construct a fake compact peer list
        # IP: 192.168.0.1 -> C0 A8 00 01
        # Port: 6881 -> 1A E1
        ip_bytes = socket.inet_aton("192.168.0.1")
        port_bytes = struct.pack(">H", 6881)
        peers_binary = ip_bytes + port_bytes
        
        # Another peer: 127.0.0.1:8080
        ip_bytes2 = socket.inet_aton("127.0.0.1")
        port_bytes2 = struct.pack(">H", 8080)
        peers_binary += ip_bytes2 + port_bytes2
        
        # Create bencoded response
        response_dict = OrderedDict()
        response_dict[b'interval'] = 1800
        response_dict[b'peers'] = peers_binary
        encoded_response = Encoder(response_dict).encode()
        
        # Test the private decoding method directly to verify logic
        peers = t._decode_tracker_response(encoded_response)
        
        self.assertEqual(len(peers), 2)
        self.assertEqual(peers[0], ("192.168.0.1", 6881))
        self.assertEqual(peers[1], ("127.0.0.1", 8080))

    @patch('aiohttp.ClientSession.get')
    def test_connect_success(self, mock_get):
        # Setup Async Mock for response
        mock_response = AsyncMock()
        mock_response.status = 200
        
        # Prepare valid bencoded response data
        response_dict = OrderedDict()
        response_dict[b'interval'] = 1800
        response_dict[b'peers'] = socket.inet_aton("10.0.0.5") + struct.pack(">H", 5000)
        mock_response.read.return_value = Encoder(response_dict).encode()
        
        # Context manager mock: ClientSession().get() -> response
        mock_get.return_value.__aenter__.return_value = mock_response

        t = Tracker(self.torrent)
        
        async def run_test():
            # We need to mock ClientSession context manager as well
            with patch('aiohttp.ClientSession') as MockSession:
                instance = MockSession.return_value
                instance.__aenter__.return_value = instance
                instance.get.return_value.__aenter__.return_value = mock_response
                
                peers = await t.connect()
                return peers

        peers = asyncio.run(run_test())
        self.assertEqual(peers[0], ("10.0.0.5", 5000))

if __name__ == '__main__':
    unittest.main()