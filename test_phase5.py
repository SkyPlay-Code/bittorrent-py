import unittest
import os
import hashlib
from piece_manager import PieceManager, Block
from torrent import Torrent
from unittest.mock import MagicMock

class TestPieceManager(unittest.TestCase):
    def setUp(self):
        # Create a mock torrent
        self.torrent = MagicMock(spec=Torrent)
        self.torrent.output_file = "test_output.bin"
        self.torrent.total_size = 50000 # ~50KB
        self.torrent.piece_length = 32768 # 32KB pieces
        
        # Piece 0: 32KB
        # Piece 1: 17232 bytes (remainder)
        
        # Calculate Hashes
        self.data_p0 = b'a' * 32768
        self.data_p1 = b'b' * 17232
        
        hash0 = hashlib.sha1(self.data_p0).digest()
        hash1 = hashlib.sha1(self.data_p1).digest()
        
        self.torrent.pieces = [hash0, hash1]
        
        self.pm = PieceManager(self.torrent)
        
        # CRITICAL FIX: Register "peer1" as having all pieces (Bitfield: 11111111)
        # We have 2 pieces, so 1 byte (8 bits) is sufficient.
        self.pm.add_peer("peer1", b'\xff')

    def tearDown(self):
        self.pm.close()
        if os.path.exists("test_output.bin"):
            os.remove("test_output.bin")

    def test_initialization(self):
        self.assertEqual(len(self.pm.missing_pieces), 2)
        self.assertEqual(self.pm.total_pieces, 2)
        self.assertEqual(len(self.pm.missing_pieces[0].blocks), 2)
        self.assertEqual(len(self.pm.missing_pieces[1].blocks), 2)

    def test_block_request_flow(self):
        # 1. Request first block
        block = self.pm.next_request("peer1")
        self.assertIsNotNone(block, "Should return a block because peer1 has everything")
        self.assertEqual(block.piece_index, 0)
        self.assertEqual(block.offset, 0)
        self.assertEqual(block.status, Block.Pending)
        
        # 2. Request second block
        block2 = self.pm.next_request("peer1")
        self.assertEqual(block2.offset, 16384)
        
        # 3. Simulate receiving data for block 1
        self.pm.block_received("peer1", 0, 0, b'a' * 16384)
        
        # Block status should be Retrieved
        p0 = next(p for p in self.pm.ongoing_pieces if p.index == 0)
        self.assertEqual(p0.blocks[0].status, Block.Retrieved)

    def test_integrity_check_success(self):
        # Request and fulfill all blocks for Piece 0
        self.pm.next_request("peer1")
        self.pm.next_request("peer1")
        
        self.pm.block_received("peer1", 0, 0, self.data_p0[:16384])
        self.pm.block_received("peer1", 0, 16384, self.data_p0[16384:])
        
        # Piece 0 should be done and moved to have_pieces
        self.assertEqual(len(self.pm.have_pieces), 1)
        self.assertEqual(self.pm.have_pieces[0].index, 0)
        
        # Verify file content
        with open("test_output.bin", "rb") as f:
            content = f.read(32768)
            self.assertEqual(content, self.data_p0)

    def test_integrity_check_failure(self):
        # Request blocks for Piece 0
        self.pm.next_request("peer1")
        self.pm.next_request("peer1")
        
        # Send WRONG data
        bad_data = b'X' * 16384
        self.pm.block_received("peer1", 0, 0, bad_data)
        self.pm.block_received("peer1", 0, 16384, self.data_p0[16384:])
        
        # Should detect failure, reset piece, and put back in missing
        self.assertEqual(len(self.pm.have_pieces), 0)
        self.assertEqual(len(self.pm.missing_pieces), 2) 
        self.assertEqual(self.pm.missing_pieces[0].index, 0)
        
if __name__ == '__main__':
    unittest.main()