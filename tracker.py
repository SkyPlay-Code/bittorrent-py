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
    """
    def __init__(self, torrent):
        self.torrent = torrent
        self.peer_id = self._generate_peer_id()

    def _generate_peer_id(self):
        prefix = '-PC0001-'
        random_digits = ''.join(random.choice(string.digits) for _ in range(12))
        return (prefix + random_digits).encode('utf-8')

    async def connect(self, uploaded=0, downloaded=0):
        """
        Attempts to connect to any working HTTP tracker in the list.
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
        
        for tracker_url in self.torrent.trackers:
            # Skip UDP trackers as we don't support BEP 15
            if not tracker_url.startswith('http'):
                logging.debug(f"Skipping non-HTTP tracker: {tracker_url}")
                continue

            url = tracker_url + '?' + urlencode(params)
            logging.info(f"Connecting to tracker: {tracker_url}")

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=5) as response:
                        if response.status != 200:
                            logging.warning(f"Tracker {tracker_url} returned status: {response.status}")
                            continue # Try next tracker
                        
                        data = await response.read()
                        try:
                            peers = self._decode_tracker_response(data)
                            return peers # Success!
                        except Exception as e:
                            logging.warning(f"Failed to decode response from {tracker_url}: {e}")
                            continue

            except Exception as e:
                logging.error(f"Failed to connect to tracker {tracker_url}: {e}")
                continue # Try next tracker

        logging.error("No working HTTP trackers found.")
        return []

    def _decode_tracker_response(self, data):
        response = Decoder(data).decode()
        
        if b'failure reason' in response:
            raise ConnectionError(f"Tracker failed: {response[b'failure reason'].decode('utf-8')}")

        peers_binary = response[b'peers']
        peers = []
        
        peer_size = 6
        if len(peers_binary) % peer_size != 0:
            raise ValueError("Invalid peers binary length")

        for i in range(0, len(peers_binary), peer_size):
            chunk = peers_binary[i : i + peer_size]
            ip_bytes = chunk[:4]
            port_bytes = chunk[4:]
            
            ip = socket.inet_ntoa(ip_bytes)
            port = struct.unpack(">H", port_bytes)[0]
            
            peers.append((ip, port))
            
        return peers