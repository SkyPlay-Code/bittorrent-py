import hashlib
import os
from bencoding import Decoder, Encoder

class TorrentFile:
    def __init__(self, path: str, length: int):
        self.path = path
        self.length = length
        self.start_offset = 0 
        self.end_offset = 0   

class Torrent:
    def __init__(self, filename):
        self.filename = filename
        self._meta_info = {}
        self._info_hash = None
        self.files = [] 
        self.total_size = 0
        self.root_name = "" 
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
            
        encoded_info = Encoder(info).encode()
        self._info_hash = hashlib.sha1(encoded_info).digest()
        
        self._parse_files(info)

    def _parse_files(self, info):
        self.root_name = info[b'name'].decode('utf-8')
        
        if b'files' in info:
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
            length = info[b'length']
            tf = TorrentFile(self.root_name, length)
            tf.start_offset = 0
            tf.end_offset = length
            
            self.files.append(tf)
            self.total_size = length

    @property
    def trackers(self) -> list:
        """
        Returns a list of all tracker URLs (strings).
        Checks 'announce-list' first, falls back to 'announce'.
        """
        urls = []
        
        # Check announce-list (list of lists of bytes)
        if b'announce-list' in self._meta_info:
            for tier in self._meta_info[b'announce-list']:
                for url_bytes in tier:
                    urls.append(url_bytes.decode('utf-8'))
        
        # Check announce (single bytes)
        if b'announce' in self._meta_info:
            urls.append(self._meta_info[b'announce'].decode('utf-8'))
            
        # Deduplicate while preserving order
        seen = set()
        unique_urls = []
        for u in urls:
            if u not in seen:
                unique_urls.append(u)
                seen.add(u)
                
        return unique_urls

    @property
    def output_file(self) -> str:
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