import math
import hashlib
import logging

METADATA_BLOCK_SIZE = 16 * 1024 # 16KB

class MetadataManager:
    """
    Manages the downloading of the info dictionary (metadata) via BEP 10.
    """
    def __init__(self, info_hash):
        self.info_hash = info_hash
        self.size = 0 # Total size in bytes (from Extension Handshake)
        self.num_pieces = 0
        self.pieces = [] # List of bytearray or None
        self.active = False
        self.complete = False

    def set_size(self, size):
        if self.size > 0: return # Already set
        self.size = size
        self.num_pieces = math.ceil(size / METADATA_BLOCK_SIZE)
        self.pieces = [None] * self.num_pieces
        self.active = True
        logging.info(f"Metadata download started. Size: {size} bytes, Pieces: {self.num_pieces}")

    def get_next_request(self):
        """Returns index of next missing piece"""
        if not self.active or self.complete: return None
        
        for i in range(self.num_pieces):
            if self.pieces[i] is None:
                return i
        return None

    def receive_data(self, piece_index, data):
        if not self.active or piece_index >= self.num_pieces: return
        
        self.pieces[piece_index] = data
        
        # Check completion
        if all(p is not None for p in self.pieces):
            self._verify()

    def _verify(self):
        raw_metadata = b''.join(self.pieces)
        # Check SHA1
        if hashlib.sha1(raw_metadata).digest() == self.info_hash:
            logging.info("Metadata verified successfully.")
            self.complete = True
            self.raw_data = raw_metadata
        else:
            logging.error("Metadata hash mismatch! Restarting.")
            self.pieces = [None] * self.num_pieces