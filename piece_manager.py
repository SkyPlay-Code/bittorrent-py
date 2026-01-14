import math
import time
import hashlib
import logging

BLOCK_SIZE = 2 ** 14

class Block:
    Missing = 0
    Pending = 1
    Retrieved = 2

    def __init__(self, piece: int, offset: int, length: int):
        self.piece_index = piece
        self.offset = offset
        self.length = length
        self.status = Block.Missing
        self.data = None

class Piece:
    def __init__(self, index: int, blocks: list, hash_value: bytes):
        self.index = index
        self.blocks = blocks
        self.hash = hash_value
        self.is_complete = False

    def reset(self):
        self.is_complete = False
        for block in self.blocks:
            block.status = Block.Missing
            block.data = None

    @property
    def data(self):
        if any(b.data is None for b in self.blocks):
            return None
        sorted_blocks = sorted(self.blocks, key=lambda b: b.offset)
        return b''.join([b.data for b in sorted_blocks])

class PieceManager:
    def __init__(self, torrent):
        self.torrent = torrent
        self.peers = {} # peer_id -> set(piece_indices)
        self.pending_blocks = [] 
        self.missing_pieces = [] 
        self.ongoing_pieces = [] 
        self.have_pieces = []    
        
        self._initiate_pieces()
        self.total_pieces = len(self.missing_pieces)
        
        self.fd = open(self.torrent.output_file, "wb")
        self.fd.seek(self.torrent.total_size - 1)
        self.fd.write(b'\0')
        self.fd.flush()

    def close(self):
        if self.fd:
            self.fd.close()

    def _initiate_pieces(self):
        total_length = self.torrent.total_size
        piece_length = self.torrent.piece_length
        num_pieces = math.ceil(total_length / piece_length)
        
        for index in range(num_pieces):
            start = index * piece_length
            end = min(start + piece_length, total_length)
            this_piece_length = end - start
            
            num_blocks = math.ceil(this_piece_length / BLOCK_SIZE)
            blocks = []
            
            for b_idx in range(num_blocks):
                b_start = b_idx * BLOCK_SIZE
                b_end = min(b_start + BLOCK_SIZE, this_piece_length)
                b_length = b_end - b_start
                blocks.append(Block(index, b_start, b_length))
            
            self.missing_pieces.append(Piece(index, blocks, self.torrent.pieces[index]))

    def add_peer(self, peer_id, bitfield):
        """
        Parses a binary bitfield and initializes the peer's available pieces set.
        """
        self.peers[peer_id] = set()
        for i, byte in enumerate(bitfield):
            for bit in range(8):
                if (byte >> (7 - bit)) & 1:
                    index = i * 8 + bit
                    if index < self.total_pieces:
                        self.peers[peer_id].add(index)

    def update_peer(self, peer_id, index):
        """
        Updates the peer's set when a HAVE message is received.
        """
        if peer_id in self.peers:
            self.peers[peer_id].add(index)
        else:
            # If peer not registered yet (rare race condition), init set
            self.peers[peer_id] = {index}

    def next_request(self, peer_id):
        """
        Get the next block for this peer, ensuring they actually have the piece.
        """
        peer_pieces = self.peers.get(peer_id, set())

        # 1. Retry timed-out blocks
        current_time = time.time()
        for i, (block, request_time) in enumerate(self.pending_blocks):
            if current_time - request_time > 5:
                # Only retry if this specific peer has the piece
                if block.piece_index in peer_pieces:
                    self.pending_blocks.pop(i)
                    logging.info(f"Block timeout: Piece {block.piece_index} Offset {block.offset}")
                    block.status = Block.Pending
                    self.pending_blocks.append((block, current_time))
                    return block

        # 2. Check ongoing pieces
        for piece in self.ongoing_pieces:
            if piece.index in peer_pieces:
                for block in piece.blocks:
                    if block.status == Block.Missing:
                        block.status = Block.Pending
                        self.pending_blocks.append((block, current_time))
                        return block

        # 3. Start a new piece (Rarest First could go here, for now strictly Ordered)
        for i, piece in enumerate(self.missing_pieces):
            if piece.index in peer_pieces:
                # Move from missing to ongoing
                self.missing_pieces.pop(i)
                self.ongoing_pieces.append(piece)
                
                block = piece.blocks[0]
                block.status = Block.Pending
                self.pending_blocks.append((block, current_time))
                return block
            
        return None

    def block_received(self, peer_id, piece_index, block_offset, data):
        matches = [x for x in self.pending_blocks 
                   if x[0].piece_index == piece_index and x[0].offset == block_offset]
        
        if matches:
            self.pending_blocks.remove(matches[0])
            
        target_piece = next((p for p in self.ongoing_pieces if p.index == piece_index), None)
        if not target_piece:
            return

        target_block = next((b for b in target_piece.blocks if b.offset == block_offset), None)
        if target_block:
            target_block.status = Block.Retrieved
            target_block.data = data
            
        if all(b.status == Block.Retrieved for b in target_piece.blocks):
            self._validate_piece(target_piece)

    def _validate_piece(self, piece):
        raw_data = piece.data
        if not raw_data: 
            return

        hashed = hashlib.sha1(raw_data).digest()
        
        if hashed == piece.hash:
            self._write(piece, raw_data)
            self.ongoing_pieces.remove(piece)
            self.have_pieces.append(piece)
            piece.is_complete = True
            
            percent = (len(self.have_pieces) / self.total_pieces) * 100
            logging.info(f"Piece {piece.index} verified. Progress: {percent:.2f}%")
        else:
            logging.error(f"Piece {piece.index} hash mismatch! Re-queueing.")
            piece.reset()
            self.ongoing_pieces.remove(piece)
            self.missing_pieces.insert(0, piece) 

    def _write(self, piece, data):
        offset = piece.index * self.torrent.piece_length
        self.fd.seek(offset)
        self.fd.write(data)
        self.fd.flush()

    @property
    def complete(self):
        return len(self.have_pieces) == self.total_pieces