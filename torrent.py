import hashlib
import os
from bencoding import Decoder, Encoder

class Torrent:
    """
    Wrapper around the bencoded meta-info files.
    Parses the file and exposes key properties.
    Reference: Page 2 of PDF.
    """
    def __init__(self, filename):
        self.filename = filename
        self._meta_info = {}
        self._info_hash = None
        self._parse()

    def _parse(self):
        if not os.path.isfile(self.filename):
            raise ValueError(f"File {self.filename} not found.")
        
        with open(self.filename, 'rb') as f:
            data = f.read()
            self._meta_info = Decoder(data).decode()
            
        # The 'info' dictionary is the most critical part. 
        # We need to re-encode it to calculate the SHA1 hash.
        info = self._meta_info.get(b'info')
        if not info:
            raise ValueError("Invalid torrent file: missing 'info' dictionary")
            
        # Calculate SHA1 hash of the bencoded 'info' dict
        encoded_info = Encoder(info).encode()
        self._info_hash = hashlib.sha1(encoded_info).digest()

    @property
    def announce(self) -> str:
        # The URL of the tracker
        return self._meta_info.get(b'announce').decode('utf-8')

    @property
    def piece_length(self) -> int:
        # Length in bytes for each piece
        return self._meta_info[b'info'][b'piece length']

    @property
    def pieces(self) -> list:
        # The 'pieces' string contains concatenated 20-byte SHA1 hashes
        pieces_data = self._meta_info[b'info'][b'pieces']
        # Split into 20-byte chunks
        return [pieces_data[i:i+20] for i in range(0, len(pieces_data), 20)]

    @property
    def output_file(self) -> str:
        # For single file torrents, name is the filename
        return self._meta_info[b'info'][b'name'].decode('utf-8')

    @property
    def total_size(self) -> int:
        # Length of the file in bytes
        # Note: Multi-file torrents use a 'files' list, but we assume single file per PDF scope.
        if b'length' in self._meta_info[b'info']:
            return self._meta_info[b'info'][b'length']
        else:
            raise NotImplementedError("Multi-file torrents not supported yet")

    @property
    def info_hash(self) -> bytes:
        return self._info_hash
    
    def __str__(self):
        return (f"Filename: {self.output_file}\n"
                f"Size: {self.total_size} bytes\n"
                f"Piece Length: {self.piece_length}\n"
                f"Pieces: {len(self.pieces)}\n"
                f"Announce URL: {self.announce}\n"
                f"Info Hash: {self.info_hash.hex()}")