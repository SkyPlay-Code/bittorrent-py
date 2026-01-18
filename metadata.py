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
        self.size = 0 
        self.num_pieces = 0
        self.pieces = [] 
        self.active = False
        self.complete = False
        self.raw_data = None

    def set_size(self, size):
        if self.size > 0: return 
        self.size = size
        self.num_pieces = math.ceil(size / METADATA_BLOCK_SIZE)
        self.pieces = [None] * self.num_pieces
        self.active = True
        logging.info(f"Metadata download started. Size: {size} bytes, Pieces: {self.num_pieces}")

    def get_next_request(self):
        if not self.active or self.complete: return None
        for i in range(self.num_pieces):
            if self.pieces[i] is None:
                return i
        return None

    def receive_data(self, piece_index, data):
        if not self.active or piece_index >= self.num_pieces: return
        self.pieces[piece_index] = data
        
        if all(p is not None for p in self.pieces):
            self._verify()

    def update_peer(self, peer_id, index):
        # Metadata manager doesn't track peer have/bitfield for files
        # It relies on Extension Handshake which we handle in PeerConnection
        pass

    def add_peer(self, peer_id, payload):
        pass

    def block_received(self, peer_id, index, begin, data):
        # Peer sent a normal block during metadata phase? Ignore.
        pass

    def _verify(self):
        raw_metadata = b''.join(self.pieces)
        if hashlib.sha1(raw_metadata).digest() == self.info_hash:
            logging.info("Metadata verified successfully.")
            self.complete = True
            self.raw_data = raw_metadata
        else:
            logging.error("Metadata hash mismatch! Restarting.")
            self.pieces = [None] * self.num_pieces