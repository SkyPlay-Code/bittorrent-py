import hashlib
import time
import os
import sys
from collections import OrderedDict

# Minimal Bencoder for the generator to be standalone
def bencode(data):
    if isinstance(data, int):
        return f"i{data}e".encode()
    elif isinstance(data, bytes):
        return f"{len(data)}:".encode() + data
    elif isinstance(data, str):
        return f"{len(data)}:".encode() + data.encode()
    elif isinstance(data, list):
        return b"l" + b"".join([bencode(item) for item in data]) + b"e"
    elif isinstance(data, dict) or isinstance(data, OrderedDict):
        out = b"d"
        # Keys must be sorted
        for k in sorted(data.keys()):
            out += bencode(k) + bencode(data[k])
        out += b"e"
        return out
    else:
        raise TypeError(f"Unknown type: {type(data)}")

def create_torrent(filename="my_test_file.bin", size_mb=5, tracker_url="http://127.0.0.1:8000/announce"):
    # 1. Create Dummy Data
    print(f"Generating {size_mb}MB of random data...")
    total_size = size_mb * 1024 * 1024
    piece_length = 262144 # 256KB pieces
    
    with open(filename, "wb") as f:
        # We use os.urandom for random data, or repeat a pattern for speed
        # Using a pattern makes it compressible/fast, urandom ensures strict hashing
        data = os.urandom(total_size)
        f.write(data)
    
    # 2. Calculate Piece Hashes
    print("Hashing pieces...")
    pieces = b""
    with open(filename, "rb") as f:
        while True:
            chunk = f.read(piece_length)
            if not chunk: break
            pieces += hashlib.sha1(chunk).digest()
            
    # 3. Build Dictionary
    info = {
        'name': filename,
        'piece length': piece_length,
        'pieces': pieces,
        'length': total_size
    }
    
    torrent_dict = {
        'announce': tracker_url,
        'info': info,
        'creation date': int(time.time()),
        'created by': 'MyCustomGen'
    }
    
    # 4. Save .torrent
    output_name = "test.torrent"
    with open(output_name, "wb") as f:
        f.write(bencode(torrent_dict))
        
    print(f"Success! Created {filename} and {output_name}")
    print(f"Info Hash: {hashlib.sha1(bencode(info)).hexdigest()}")

if __name__ == "__main__":
    create_torrent()