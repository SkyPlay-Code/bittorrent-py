import unittest
import os
import shutil
from unittest.mock import MagicMock
from torrent import Torrent, TorrentFile
from file_manager import FileManager

class TestMultiFileSupport(unittest.TestCase):
    def setUp(self):
        # Setup a dummy torrent object manually
        self.torrent = MagicMock(spec=Torrent)
        self.test_dir = "test_multifile_output"
        
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
            
        # Scenario:
        # File A: 10 bytes
        # File B: 5 bytes
        # File C: 10 bytes
        # Total: 25 bytes
        
        # Files are laid out sequentially in the directory
        self.f1_path = os.path.join(self.test_dir, "fileA.txt")
        self.f2_path = os.path.join(self.test_dir, "sub", "fileB.txt")
        self.f3_path = os.path.join(self.test_dir, "fileC.txt")
        
        tf1 = TorrentFile(self.f1_path, 10)
        tf1.start_offset = 0
        tf1.end_offset = 10
        
        tf2 = TorrentFile(self.f2_path, 5)
        tf2.start_offset = 10
        tf2.end_offset = 15
        
        tf3 = TorrentFile(self.f3_path, 10)
        tf3.start_offset = 15
        tf3.end_offset = 25
        
        self.torrent.files = [tf1, tf2, tf3]
        self.torrent.total_size = 25
        
        self.fm = FileManager(self.torrent)

    def tearDown(self):
        self.fm.close()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_file_creation(self):
        # Check if files were created with correct size (0 padded)
        self.assertTrue(os.path.exists(self.f1_path))
        self.assertEqual(os.path.getsize(self.f1_path), 10)
        
        self.assertTrue(os.path.exists(self.f2_path))
        self.assertEqual(os.path.getsize(self.f2_path), 5)

    def test_simple_write(self):
        # Write 5 bytes to File A (Offset 0)
        data = b'AAAAA'
        self.fm.write(0, data)
        
        with open(self.f1_path, 'rb') as f:
            self.assertEqual(f.read(5), b'AAAAA')

    def test_boundary_crossing_write(self):
        # Write data that starts at offset 8 (File A) 
        # spans across File B (5 bytes) 
        # and ends in File C
        
        # File A ends at 10. Offset 8 means 2 bytes go to A.
        # File B is 10-15. 5 bytes go to B.
        # File C starts at 15.
        
        # Data: XX YYYYY ZZ (9 bytes total)
        # Expectation: 
        # File A: ...XX
        # File B: YYYYY
        # File C: ZZ...
        
        data = b'XXYYYYYZZ'
        self.fm.write(8, data)
        
        # Verify File A
        with open(self.f1_path, 'rb') as f:
            f.seek(8)
            self.assertEqual(f.read(2), b'XX')
            
        # Verify File B
        with open(self.f2_path, 'rb') as f:
            self.assertEqual(f.read(5), b'YYYYY')
            
        # Verify File C
        with open(self.f3_path, 'rb') as f:
            self.assertEqual(f.read(2), b'ZZ')

if __name__ == '__main__':
    unittest.main()