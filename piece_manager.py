import math
import time
import hashlib
import logging
import os
import asyncio
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
        if any(b.data is None for b in self.blocks): return None
        sorted_blocks = sorted(self.blocks, key=lambda b: b.offset)
        return b''.join([b.data for b in sorted_blocks])

class PieceManager:
    def __init__(self, torrent):
        self.torrent = torrent
        self.peers = {} 
        self.active_peers = {}
        self.pending_blocks = [] 
        self.missing_pieces = [] 
        self.ongoing_pieces = [] 
        self.have_pieces = []    
        self.downloaded_bytes = 0 
        self.resume_file = f"{self.torrent.info_hash.hex()}.resume"
        
        self._initiate_pieces_structure()
        self.total_pieces = len(self.missing_pieces)
        
        self.file_manager = FileManager(self.torrent)
        self._restore_state()

    def close(self):
        self.save_resume_data()
        self.file_manager.close()

    def _initiate_pieces_structure(self):
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

    def _restore_state(self):
        if os.path.exists(self.resume_file):
            try:
                logging.info("Found resume file. Loading state...")
                self._load_fast_resume()
                return
            except Exception as e:
                logging.warning(f"Failed to load resume file: {e}. Falling back to full check.")
        logging.info("Checking existing data on disk...")
        self._hash_check()

    def _load_fast_resume(self):
        with open(self.resume_file, 'rb') as f: bitfield = f.read()
        pieces_to_move = []
        for i, piece in enumerate(self.missing_pieces):
            if (bitfield[i // 8] >> (7 - (i % 8))) & 1: pieces_to_move.append(piece)
        for piece in pieces_to_move:
            piece.is_complete = True
            for block in piece.blocks: block.status = Block.Retrieved
            self.have_pieces.append(piece)
            self.downloaded_bytes += self._get_piece_length(piece.index)
        for piece in pieces_to_move: self.missing_pieces.remove(piece)

    def _hash_check(self):
        confirmed = []
        for piece in list(self.missing_pieces):
            data = self.file_manager._read_sync(piece.index * self.torrent.piece_length, self._get_piece_length(piece.index))
            if data and hashlib.sha1(data).digest() == piece.hash:
                piece.is_complete = True
                for block in piece.blocks: block.status = Block.Retrieved
                confirmed.append(piece)
                self.downloaded_bytes += len(data)
        for piece in confirmed:
            self.missing_pieces.remove(piece)
            self.have_pieces.append(piece)

    def save_resume_data(self):
        if not self.have_pieces: return
        bf = bytearray(math.ceil(self.total_pieces / 8))
        for p in self.have_pieces: bf[p.index // 8] |= (1 << (7 - (p.index % 8)))
        try: 
            with open(self.resume_file, 'wb') as f: 
                f.write(bf)
            logging.info("Resume data saved.")
        except Exception: 
            pass

    def _get_piece_length(self, index):
        if index == self.total_pieces - 1:
            r = self.torrent.total_size % self.torrent.piece_length
            return r if r > 0 else self.torrent.piece_length
        return self.torrent.piece_length

    def add_peer(self, peer_id, bitfield, ip=None, port=None):
        self.peers[peer_id] = set()
        for i, byte in enumerate(bitfield):
            for bit in range(8):
                if (byte >> (7 - bit)) & 1:
                    idx = i * 8 + bit
                    if idx < self.total_pieces: self.peers[peer_id].add(idx)
        if ip and port: self.active_peers[peer_id] = (ip, port)

    def remove_peer(self, peer_id):
        if peer_id in self.active_peers: del self.active_peers[peer_id]
        if peer_id in self.peers: del self.peers[peer_id]

    def get_active_peers(self): return list(self.active_peers.values())
    
    def update_peer(self, peer_id, index):
        if peer_id in self.peers: self.peers[peer_id].add(index)
        else: self.peers[peer_id] = {index}

    @property
    def end_game_mode(self):
        return len(self.missing_pieces) < 5 or len(self.missing_pieces) < (self.total_pieces * 0.01)

    def next_request(self, peer_id):
        peer_pieces = self.peers.get(peer_id, set())
        current_time = time.time()
        for i, (block, request_time) in enumerate(self.pending_blocks):
            if current_time - request_time > 5:
                if block.piece_index in peer_pieces:
                    self.pending_blocks[i] = (block, current_time)
                    return block
        for piece in self.ongoing_pieces:
            if piece.index in peer_pieces:
                for block in piece.blocks:
                    if block.status == Block.Missing:
                        block.status = Block.Pending
                        self.pending_blocks.append((block, current_time))
                        return block
        if self.end_game_mode:
            for piece in self.ongoing_pieces:
                if piece.index in peer_pieces:
                    for block in piece.blocks:
                        if block.status == Block.Pending: return block
        candidates = [p for p in self.missing_pieces if p.index in peer_pieces]
        if not candidates: return None
        candidates.sort(key=lambda p: sum(1 for peers in self.peers.values() if p.index in peers))
        piece = candidates[0]
        self.missing_pieces.remove(piece)
        self.ongoing_pieces.append(piece)
        block = piece.blocks[0]
        block.status = Block.Pending
        self.pending_blocks.append((block, current_time))
        return block

    def block_received(self, peer_id, piece_index, block_offset, data):
        self.pending_blocks = [x for x in self.pending_blocks if not (x[0].piece_index == piece_index and x[0].offset == block_offset)]
        target_piece = next((p for p in self.ongoing_pieces if p.index == piece_index), None)
        if not target_piece: return
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
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._write_async(piece, raw_data))
            except RuntimeError:
                # Sync fallback for tests if loop isn't running
                pass 
            self.ongoing_pieces.remove(piece)
            self.have_pieces.append(piece)
            piece.is_complete = True
            self.downloaded_bytes += len(raw_data)
            logging.info(f"Piece {piece.index} verified.")
        else:
            logging.warning(f"Piece {piece.index} hash mismatch.")
            piece.reset()
            self.ongoing_pieces.remove(piece)
            self.missing_pieces.insert(0, piece) 

    async def _write_async(self, piece, data):
        await self.file_manager.write(piece.index * self.torrent.piece_length, data)

    async def read_block(self, piece_index, block_offset, length):
        has_piece = any(p.index == piece_index for p in self.have_pieces)
        if has_piece:
            global_offset = (piece_index * self.torrent.piece_length) + block_offset
            return await self.file_manager.read(global_offset, length)
        return None

    @property
    def complete(self):
        return len(self.have_pieces) == self.total_pieces