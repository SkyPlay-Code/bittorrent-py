import math
import hashlib
import logging

METADATA_BLOCK_SIZE = 16 * 1024 

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
        self.active_peers = {} # peer_id -> (ip, port) [NEW]

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
        if all(p is not None for p in self.pieces): self._verify()

    def update_peer(self, peer_id, index):
        pass

    # Updated signature to match PieceManager
    def add_peer(self, peer_id, payload, ip=None, port=None):
        if ip and port:
            self.active_peers[peer_id] = (ip, port)

    def remove_peer(self, peer_id):
        if peer_id in self.active_peers:
            del self.active_peers[peer_id]

    def get_active_peers(self):
        return list(self.active_peers.values())

    def block_received(self, peer_id, index, begin, data):
        pass

    def read_block(self, index, begin, length):
        return None

    def _verify(self):
        raw_metadata = b''.join(self.pieces)
        if hashlib.sha1(raw_metadata).digest() == self.info_hash:
            logging.info("Metadata verified successfully.")
            self.complete = True
            self.raw_data = raw_metadata
        else:
            logging.error("Metadata hash mismatch! Restarting.")
            self.pieces = [None] * self.num_pieces