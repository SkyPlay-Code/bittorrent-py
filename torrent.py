import hashlib
import os
from bencoding import Decoder, Encoder

class TorrentFile:
    """
    Data class representing a specific file within the torrent.
    """
    def __init__(self, path: str, length: int):
        self.path = path
        self.length = length
        self.start_offset = 0 # Global offset where this file begins
        self.end_offset = 0   # Global offset where this file ends

class Torrent:
    """
    Wrapper around the bencoded meta-info files.
    """
    def __init__(self, filename):
        self.filename = filename
        self._meta_info = {}
        self._info_hash = None
        self.files = [] # List of TorrentFile objects
        self.total_size = 0
        self.root_name = "" # Root filename or directory name
        self._parse()

    def _parse(self):
        if not os.path.isfile(self.filename):
            raise ValueError(f"File {self.filename} not found.")
        
        with open(self.filename, 'rb') as f:
            data = f.read()
            self._meta_info = Decoder(data).decode()
            
        info = self._meta_info.get(b'info')
        if not info:
            raise ValueError("Invalid torrent file: missing 'info' dictionary")
            
        # Calculate SHA1 hash of the bencoded 'info' dict
        encoded_info = Encoder(info).encode()
        self._info_hash = hashlib.sha1(encoded_info).digest()
        
        # Parse Files
        self._parse_files(info)

    def _parse_files(self, info):
        """
        Normalizes single-file and multi-file torrents into a standard list.
        """
        self.root_name = info[b'name'].decode('utf-8')
        
        if b'files' in info:
            # Multi-file mode
            offset = 0
            for file_data in info[b'files']:
                length = file_data[b'length']
                path_parts = [p.decode('utf-8') for p in file_data[b'path']]
                path = os.path.join(self.root_name, *path_parts)
                
                tf = TorrentFile(path, length)
                tf.start_offset = offset
                tf.end_offset = offset + length
                
                self.files.append(tf)
                offset += length
            self.total_size = offset
        else:
            # Single-file mode
            length = info[b'length']
            tf = TorrentFile(self.root_name, length)
            tf.start_offset = 0
            tf.end_offset = length
            
            self.files.append(tf)
            self.total_size = length

    @property
    def output_file(self) -> str:
        """
        Returns the name of the main file or directory.
        Used for logging in the Client.
        """
        return self.root_name

    @property
    def announce(self) -> str:
        return self._meta_info.get(b'announce').decode('utf-8')

    @property
    def piece_length(self) -> int:
        return self._meta_info[b'info'][b'piece length']

    @property
    def pieces(self) -> list:
        pieces_data = self._meta_info[b'info'][b'pieces']
        return [pieces_data[i:i+20] for i in range(0, len(pieces_data), 20)]

    @property
    def info_hash(self) -> bytes:
        return self._info_hash
    
    def __str__(self):
        return (f"Name: {self.root_name}\n"
                f"Total Size: {self.total_size} bytes\n"
                f"Piece Length: {self.piece_length}\n"
                f"Pieces: {len(self.pieces)}\n"
                f"Info Hash: {self.info_hash.hex()}\n"
                f"Files: {len(self.files)}")