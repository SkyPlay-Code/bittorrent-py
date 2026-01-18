import hashlib
import os
import math
from bencoding import Decoder, Encoder

class TorrentFile:
    def __init__(self, path: str, length: int):
        self.path = path
        self.length = length
        self.start_offset = 0 
        self.end_offset = 0   

class Torrent:
    def __init__(self, source=None):
        self.filename = None
        self._meta_info = {}
        self._info_hash = None
        self.files = [] 
        self.total_size = 0
        self.root_name = "" 
        self.trackers_list = []
        
        # Metadata State
        self.loaded = False
        
        if source:
            if source.startswith('magnet:'):
                self._parse_magnet(source)
            else:
                self.filename = source
                self._parse_file(source)

    def _parse_file(self, filename):
        if not os.path.isfile(filename):
            raise ValueError(f"File {filename} not found.")
        
        with open(filename, 'rb') as f:
            data = f.read()
            self._load_from_bytes(data)

    def _load_from_bytes(self, data):
        """
        Parses raw bencoded torrent data.
        """
        try:
            self._meta_info = Decoder(data).decode()
        except Exception:
            raise ValueError("Invalid Bencoded Data")

        info = self._meta_info.get(b'info')
        if not info:
            raise ValueError("Invalid torrent: missing 'info'")
            
        encoded_info = Encoder(info).encode()
        self._info_hash = hashlib.sha1(encoded_info).digest()
        
        self._parse_files(info)
        self._parse_trackers()
        self.loaded = True

    def _parse_magnet(self, uri):
        """
        Parses magnet URI to get info_hash and trackers.
        magnet:?xt=urn:btih:<hash>&dn=<name>&tr=<tracker>
        """
        import urllib.parse
        
        parsed = urllib.parse.urlparse(uri)
        params = urllib.parse.parse_qs(parsed.query)
        
        if 'xt' not in params:
            raise ValueError("Invalid Magnet Link: Missing xt parameter")
            
        # xt=urn:btih:HASH
        xt = params['xt'][0]
        if not xt.startswith('urn:btih:'):
            raise ValueError("Invalid Magnet Link: Not BT info hash")
            
        hex_hash = xt.split(':')[-1]
        try:
            self._info_hash = bytes.fromhex(hex_hash)
        except ValueError:
            # Handle base32 encoding if necessary, usually hex standard
            import base64
            # Minimal base32 handling fallback if standard library unavailable/complex
            raise ValueError("Info hash must be hex encoded")
            
        if 'tr' in params:
            self.trackers_list = params['tr']
            
        if 'dn' in params:
            self.root_name = params['dn'][0]
            
        self.loaded = False # Explicitly not loaded yet

    def load_metadata(self, metadata_bytes):
        """
        Called when we successfully download the info dict via BEP 10.
        Wraps it in a standard torrent structure.
        """
        # Validate Hash
        new_hash = hashlib.sha1(metadata_bytes).digest()
        if new_hash != self._info_hash:
            raise ValueError("Metadata hash mismatch!")
            
        # Decode info dict
        info = Decoder(metadata_bytes).decode()
        
        # Reconstruct full meta_info dict
        self._meta_info = {b'info': info}
        if self.trackers_list:
            # Convert string trackers back to list of lists for consistency if needed
            # But simpler to just store them
            pass
            
        self._parse_files(info)
        self.loaded = True

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

    def _parse_trackers(self):
        # Existing logic moved here
        urls = []
        if b'announce-list' in self._meta_info:
            for tier in self._meta_info[b'announce-list']:
                for url_bytes in tier:
                    urls.append(url_bytes.decode('utf-8'))
        if b'announce' in self._meta_info:
            urls.append(self._meta_info[b'announce'].decode('utf-8'))
            
        seen = set()
        for u in urls:
            if u not in seen:
                self.trackers_list.append(u)
                seen.add(u)

    @property
    def trackers(self) -> list:
        return self.trackers_list

    @property
    def output_file(self) -> str:
        return self.root_name or "Magnet_Download"

    @property
    def announce(self) -> str:
        return self.trackers_list[0] if self.trackers_list else ""

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