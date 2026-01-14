import aiohttp
import random
import string
import struct
import socket
import logging
from urllib.parse import urlencode
from bencoding import Decoder

class Tracker:
    """
    Manages communication with the HTTP Tracker.
    Reference: Page 3 & 4 of PDF.
    """
    def __init__(self, torrent):
        self.torrent = torrent
        self.peer_id = self._generate_peer_id()

    def _generate_peer_id(self):
        """
        Generates a 20-byte peer id using Azureus-style convention.
        Format: -<Client><Version>-<Random>
        Example: -PC0001-478269329936
        """
        prefix = '-PC0001-'
        random_digits = ''.join(random.choice(string.digits) for _ in range(12))
        return (prefix + random_digits).encode('utf-8')

    async def connect(self, first=None, uploaded=0, downloaded=0):
        """
        Makes the announce call to the tracker to get a list of peers.
        """
        params = {
            'info_hash': self.torrent.info_hash,
            'peer_id': self.peer_id,
            'port': 6881,
            'uploaded': uploaded,
            'downloaded': downloaded,
            'left': self.torrent.total_size - downloaded,
            'compact': 1
        }
        
        url = self.torrent.announce + '?' + urlencode(params)
        logging.info(f"Connecting to tracker: {self.torrent.announce}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise ConnectionError(f"Tracker returned status: {response.status}")
                    data = await response.read()
                    return self._decode_tracker_response(data)
        except Exception as e:
            logging.error(f"Failed to connect to tracker: {e}")
            raise

    def _decode_tracker_response(self, data):
        """
        Decodes the bencoded response and parses the compact peer list.
        """
        response = Decoder(data).decode()
        
        if b'failure reason' in response:
            raise ConnectionError(f"Tracker failed: {response[b'failure reason'].decode('utf-8')}")

        peers_binary = response[b'peers']
        peers = []
        
        # 6 bytes per peer: 4 for IP, 2 for Port
        peer_size = 6
        if len(peers_binary) % peer_size != 0:
            raise ValueError("Invalid peers binary length")

        for i in range(0, len(peers_binary), peer_size):
            chunk = peers_binary[i : i + peer_size]
            ip_bytes = chunk[:4]
            port_bytes = chunk[4:]
            
            ip = socket.inet_ntoa(ip_bytes)
            port = struct.unpack(">H", port_bytes)[0] # Big-endian unsigned short
            
            peers.append((ip, port))
            
        return peers