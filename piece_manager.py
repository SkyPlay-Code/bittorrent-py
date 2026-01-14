import math
import time
import hashlib
import logging

# Fixed block size 16KB
BLOCK_SIZE = 2 ** 14

class Block:
    """
    Represents a specific block within a piece.
    """
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
    """
    Represents a Piece of the torrent.
    Holds the state of its blocks and manages the final data assembly.
    """
    def __init__(self, index: int, blocks: list, hash_value: bytes):
        self.index = index
        self.blocks = blocks
        self.hash = hash_value
        self.is_complete = False

    def reset(self):
        """Reset piece state on hash failure"""
        self.is_complete = False
        for block in self.blocks:
            block.status = Block.Missing
            block.data = None

    @property
    def data(self):
        """Assemble blocks into bytes if all blocks have data"""
        if any(b.data is None for b in self.blocks):
            return None
        
        sorted_blocks = sorted(self.blocks, key=lambda b: b.offset)
        return b''.join([b.data for b in sorted_blocks])

class PieceManager:
    """
    Manages the logic of which pieces/blocks to request and
    validates received data against SHA1 hashes.
    """
    def __init__(self, torrent):
        self.torrent = torrent
        self.peers = {} # peer_id -> bitfield
        self.pending_blocks = [] # List of (block, timestamp)
        self.missing_pieces = [] # List of Piece objects
        self.ongoing_pieces = [] # List of Piece objects
        self.have_pieces = []    # List of Piece objects
        
        self._initiate_pieces()
        self.total_pieces = len(self.missing_pieces)
        
        # Open file for writing
        self.fd = open(self.torrent.output_file, "wb")
        # Pre-allocate file size
        self.fd.seek(self.torrent.total_size - 1)
        self.fd.write(b'\0')
        self.fd.flush()

    def close(self):
        if self.fd:
            self.fd.close()

    def _initiate_pieces(self):
        """
        Pre-construct all pieces and blocks based on torrent meta-info.
        """
        total_length = self.torrent.total_size
        piece_length = self.torrent.piece_length
        
        num_pieces = math.ceil(total_length / piece_length)
        
        for index in range(num_pieces):
            # Calculate start and end byte of this piece
            start = index * piece_length
            end = min(start + piece_length, total_length)
            this_piece_length = end - start
            
            # Generate blocks for this piece
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
        Register a peer and their bitfield.
        """
        self.peers[peer_id] = bitfield

    def update_peer(self, peer_id, index):
        """
        Update a peer's bitfield when they send a HAVE message.
        """
        if peer_id in self.peers:
            pass

    def next_request(self, peer_id):
        """
        Determines the next block to request from a specific peer.
        """
        # 1. Check for expired pending blocks (timeout: 5 seconds)
        current_time = time.time()
        for i, (block, request_time) in enumerate(self.pending_blocks):
            if current_time - request_time > 5:
                self.pending_blocks.pop(i)
                logging.info(f"Block timeout: Piece {block.piece_index} Offset {block.offset}")
                block.status = Block.Pending
                self.pending_blocks.append((block, current_time))
                return block

        # 2. Check ongoing pieces
        for piece in self.ongoing_pieces:
            for block in piece.blocks:
                if block.status == Block.Missing:
                    block.status = Block.Pending
                    self.pending_blocks.append((block, current_time))
                    return block

        # 3. Start a new piece
        if self.missing_pieces:
            piece = self.missing_pieces.pop(0)
            self.ongoing_pieces.append(piece)
            
            block = piece.blocks[0]
            block.status = Block.Pending
            self.pending_blocks.append((block, current_time))
            return block
            
        return None

    def block_received(self, peer_id, piece_index, block_offset, data):
        """
        Handle receiving a block of data.
        """
        # Find the block in pending
        matches = [x for x in self.pending_blocks 
                   if x[0].piece_index == piece_index and x[0].offset == block_offset]
        
        if matches:
            self.pending_blocks.remove(matches[0])
            
        # Find the piece
        target_piece = None
        for p in self.ongoing_pieces:
            if p.index == piece_index:
                target_piece = p
                break
        
        if not target_piece:
            # It might have been re-added to missing if failed previously?
            # Or it's already done.
            return

        # Find the block inside the piece
        target_block = None
        for b in target_piece.blocks:
            if b.offset == block_offset:
                target_block = b
                break
                
        if target_block:
            target_block.status = Block.Retrieved
            target_block.data = data
            
        # Check if piece is complete (all blocks retrieved)
        if all(b.status == Block.Retrieved for b in target_piece.blocks):
            self._validate_piece(target_piece)

    def _validate_piece(self, piece):
        """
        Verify SHA1 hash and write to disk.
        """
        raw_data = piece.data
        if not raw_data:
            logging.error("Validation failed: piece data incomplete")
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
        """
        Writes the piece data to the correct offset in the file.
        """
        offset = piece.index * self.torrent.piece_length
        self.fd.seek(offset)
        self.fd.write(data)
        self.fd.flush()

    @property
    def complete(self):
        return len(self.have_pieces) == self.total_pieces