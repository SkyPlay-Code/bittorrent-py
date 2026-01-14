import unittest
import os
import hashlib
from collections import OrderedDict
from bencoding import Encoder
from torrent import Torrent

class TestTorrentClass(unittest.TestCase):
    def setUp(self):
        # Create a dummy .torrent file structure
        self.filename = "test_dummy.torrent"
        
        # 1. Create the Info Dictionary
        info = OrderedDict()
        info[b'piece length'] = 262144 # 256KB
        info[b'name'] = b'dummy_content.txt'
        info[b'length'] = 12345
        
        # Create fake pieces (SHA1 hash is 20 bytes)
        # We'll pretend we have 1 piece
        dummy_piece_hash = b'\x00' * 20
        info[b'pieces'] = dummy_piece_hash
        
        # 2. Create the Root Dictionary
        meta_info = OrderedDict()
        meta_info[b'announce'] = b'http://tracker.example.com/announce'
        meta_info[b'info'] = info
        
        # 3. Calculate expected Info Hash manually for verification
        encoded_info = Encoder(info).encode()
        self.expected_info_hash = hashlib.sha1(encoded_info).digest()
        
        # 4. Save to disk
        with open(self.filename, 'wb') as f:
            f.write(Encoder(meta_info).encode())

    def tearDown(self):
        if os.path.exists(self.filename):
            os.remove(self.filename)

    def test_parse_torrent(self):
        t = Torrent(self.filename)
        
        self.assertEqual(t.announce, 'http://tracker.example.com/announce')
        self.assertEqual(t.output_file, 'dummy_content.txt')
        self.assertEqual(t.total_size, 12345)
        self.assertEqual(t.piece_length, 262144)
        self.assertEqual(len(t.pieces), 1)
        self.assertEqual(t.pieces[0], b'\x00' * 20)
        
        # The critical test: Does the calculated hash match the source?
        self.assertEqual(t.info_hash, self.expected_info_hash)
        print("\nTorrent Metadata Verified Successfully:")
        print(t)

if __name__ == '__main__':
    unittest.main()