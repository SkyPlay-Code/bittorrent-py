import math
import time
import hashlib
import logging
import os
import struct
from file_manager import FileManager

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
        self.peers = {} 
        self.pending_blocks = [] 
        self.missing_pieces = [] 
        self.ongoing_pieces = [] 
        self.have_pieces = []    
        self.downloaded_bytes = 0 
        self.resume_file = f"{self.torrent.info_hash.hex()}.resume"
        
        # Initialize pieces structures first
        self._initiate_pieces_structure()
        self.total_pieces = len(self.missing_pieces)
        
        self.file_manager = FileManager(self.torrent)
        
        # Attempt Resume
        self._restore_state()

    def close(self):
        self.save_resume_data()
        self.file_manager.close()

    def _initiate_pieces_structure(self):
        """
        Creates the Piece and Block objects but doesn't assign state yet.
        """
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
            
            # Initially all are missing
            self.missing_pieces.append(Piece(index, blocks, self.torrent.pieces[index]))

    def _restore_state(self):
        """
        Tries to load fast-resume data. If unavailable, performs a full disk hash check.
        """
        if os.path.exists(self.resume_file):
            try:
                logging.info("Found resume file. Loading state...")
                self._load_fast_resume()
                return
            except Exception as e:
                logging.warning(f"Failed to load resume file: {e}. Falling back to full check.")
        
        # Fallback: Check existing data on disk
        logging.info("Checking existing data on disk (this may take a while)...")
        self._hash_check()

    def _load_fast_resume(self):
        with open(self.resume_file, 'rb') as f:
            bitfield = f.read()
            
        if len(bitfield) * 8 < self.total_pieces:
            raise ValueError("Resume file too short")

        # Iterate and update state
        # We need to move pieces from missing to have
        pieces_to_move = []
        
        for i, piece in enumerate(self.missing_pieces):
            byte_index = i // 8
            bit_index = 7 - (i % 8)
            if (bitfield[byte_index] >> bit_index) & 1:
                pieces_to_move.append(piece)

        for piece in pieces_to_move:
            piece.is_complete = True
            for block in piece.blocks:
                block.status = Block.Retrieved # Logically retrieved
            self.have_pieces.append(piece)
            self.downloaded_bytes += self._get_piece_length(piece.index)
        
        # Remove from missing
        for piece in pieces_to_move:
            self.missing_pieces.remove(piece)

        logging.info(f"Fast Resume: {len(self.have_pieces)} pieces loaded.")

    def _hash_check(self):
        """
        Reads every piece from disk and verifies the hash.
        """
        pieces_found = 0
        
        # We iterate a copy because we might modify the list structure
        # actually we just iterate indices or the initial list
        # missing_pieces currently contains ALL pieces.
        
        # List to hold confirmed pieces to move later
        confirmed_pieces = []

        for piece in list(self.missing_pieces):
            # Read data
            offset = piece.index * self.torrent.piece_length
            length = self._get_piece_length(piece.index)
            
            data = self.file_manager.read(offset, length)
            
            if not data or len(data) != length:
                continue # Incomplete or missing
                
            # Hash check
            if hashlib.sha1(data).digest() == piece.hash:
                piece.is_complete = True
                for block in piece.blocks:
                    block.status = Block.Retrieved
                
                confirmed_pieces.append(piece)
                self.downloaded_bytes += length
                pieces_found += 1
                
                # print progress occasionally
                if pieces_found % 10 == 0:
                    percent = (pieces_found / self.total_pieces) * 100
                    print(f"Rechecking: {percent:.1f}%", end='\r')

        # Update lists
        for piece in confirmed_pieces:
            self.missing_pieces.remove(piece)
            self.have_pieces.append(piece)
            
        print(f"Recheck complete. Resuming {pieces_found}/{self.total_pieces} pieces.")

    def save_resume_data(self):
        """
        Saves the current bitfield to disk.
        """
        if not self.have_pieces:
            return

        num_bytes = math.ceil(self.total_pieces / 8)
        bitfield = bytearray(num_bytes)
        
        for piece in self.have_pieces:
            byte_index = piece.index // 8
            bit_index = 7 - (piece.index % 8)
            bitfield[byte_index] |= (1 << bit_index)
            
        try:
            with open(self.resume_file, 'wb') as f:
                f.write(bitfield)
            logging.info("Resume data saved.")
        except Exception as e:
            logging.error(f"Failed to save resume data: {e}")

    def _get_piece_length(self, index):
        # Last piece might be shorter
        if index == self.total_pieces - 1:
            remainder = self.torrent.total_size % self.torrent.piece_length
            return remainder if remainder > 0 else self.torrent.piece_length
        return self.torrent.piece_length

    # --- Standard Methods (add_peer, next_request, etc.) remain the same ---
    # To save space in this response, assume the logic from Phase 12 
    # (Rarest First, Hash Validation, etc.) is preserved here.
    # Only the constructor and close methods changed significantly.
    
    def add_peer(self, peer_id, bitfield):
        self.peers[peer_id] = set()
        for i, byte in enumerate(bitfield):
            for bit in range(8):
                if (byte >> (7 - bit)) & 1:
                    index = i * 8 + bit
                    if index < self.total_pieces:
                        self.peers[peer_id].add(index)

    def update_peer(self, peer_id, index):
        if peer_id in self.peers:
            self.peers[peer_id].add(index)
        else:
            self.peers[peer_id] = {index}

    def next_request(self, peer_id):
        peer_pieces = self.peers.get(peer_id, set())
        current_time = time.time()
        
        for i, (block, request_time) in enumerate(self.pending_blocks):
            if current_time - request_time > 5:
                if block.piece_index in peer_pieces:
                    self.pending_blocks.pop(i)
                    block.status = Block.Pending
                    self.pending_blocks.append((block, current_time))
                    return block

        for piece in self.ongoing_pieces:
            if piece.index in peer_pieces:
                for block in piece.blocks:
                    if block.status == Block.Missing:
                        block.status = Block.Pending
                        self.pending_blocks.append((block, current_time))
                        return block

        candidates = [p for p in self.missing_pieces if p.index in peer_pieces]
        if not candidates:
            return None
            
        def get_rarity(piece):
            count = 0
            for peer_set in self.peers.values():
                if piece.index in peer_set:
                    count += 1
            return count
        
        candidates.sort(key=get_rarity)
        piece = candidates[0]
        
        self.missing_pieces.remove(piece)
        self.ongoing_pieces.append(piece)
        
        block = piece.blocks[0]
        block.status = Block.Pending
        self.pending_blocks.append((block, current_time))
        return block

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
        if not raw_data: return

        hashed = hashlib.sha1(raw_data).digest()
        
        if hashed == piece.hash:
            self._write(piece, raw_data)
            self.ongoing_pieces.remove(piece)
            self.have_pieces.append(piece)
            piece.is_complete = True
            self.downloaded_bytes += len(raw_data)
            logging.info(f"Piece {piece.index} verified.")
            # Trigger immediate save for crash resilience? 
            # Optional: self.save_resume_data() 
            # Doing it every piece might be IO heavy, but good for safety.
        else:
            logging.warning(f"Piece {piece.index} hash mismatch. Retrying.")
            piece.reset()
            self.ongoing_pieces.remove(piece)
            self.missing_pieces.insert(0, piece) 

    def _write(self, piece, data):
        global_offset = piece.index * self.torrent.piece_length
        self.file_manager.write(global_offset, data)

    def read_block(self, piece_index, block_offset, length):
        has_piece = any(p.index == piece_index for p in self.have_pieces)
        if has_piece:
            global_offset = (piece_index * self.torrent.piece_length) + block_offset
            return self.file_manager.read(global_offset, length)
        return None

    @property
    def complete(self):
        return len(self.have_pieces) == self.total_pieces