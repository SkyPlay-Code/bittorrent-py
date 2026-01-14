import aiohttp
import asyncio
import random
import string
import struct
import socket
import logging
from urllib.parse import urlparse, urlencode
from bencoding import Decoder

class Tracker:
    """
    Manages communication with both HTTP and UDP Trackers.
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
        Attempts to connect to trackers (HTTP or UDP) to get peers.
        """
        # We need to shuffle to avoid hammering the same broken tracker first every time
        trackers = self.torrent.trackers
        # random.shuffle(trackers) # Optional: enable if you want random order

        for tracker_url in trackers:
            if tracker_url.startswith('http'):
                peers = await self._connect_http(tracker_url, uploaded, downloaded)
                if peers: return peers
            elif tracker_url.startswith('udp'):
                peers = await self._connect_udp(tracker_url, uploaded, downloaded)
                if peers: return peers
            
        logging.error("No working trackers found.")
        return []

    async def _connect_http(self, url, uploaded, downloaded):
        logging.info(f"Connecting to HTTP tracker: {url}")
        params = {
            'info_hash': self.torrent.info_hash,
            'peer_id': self.peer_id,
            'port': 6881,
            'uploaded': uploaded,
            'downloaded': downloaded,
            'left': self.torrent.total_size - downloaded,
            'compact': 1
        }
        full_url = url + '?' + urlencode(params)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(full_url, timeout=5) as response:
                    if response.status != 200:
                        return None
                    data = await response.read()
                    return self._decode_peers(Decoder(data).decode()[b'peers'])
        except Exception as e:
            logging.debug(f"HTTP Tracker failed {url}: {e}")
            return None

    async def _connect_udp(self, url, uploaded, downloaded):
        logging.info(f"Connecting to UDP tracker: {url}")
        parsed = urlparse(url)
        ip = parsed.hostname
        port = parsed.port
        
        if not ip or not port:
            return None

        # Offload blocking UDP socket ops to a thread
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, 
                self._udp_announce_transaction, 
                ip, port, uploaded, downloaded
            )
        except Exception as e:
            logging.debug(f"UDP Tracker failed {url}: {e}")
            return None

    def _udp_announce_transaction(self, ip, port, uploaded, downloaded):
        """
        Synchronous blocking method to handle BEP 15 UDP negotiation.
        Designed to be run in a thread executor.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(4) # 4 seconds timeout
        
        try:
            # 1. CONNECT REQUEST
            connection_id = 0x41727101980
            action = 0 # Connect
            trans_id = random.randint(0, 65535)
            
            # Pack: >QII (Protocol ID, Action, Trans ID)
            req = struct.pack(">QII", connection_id, action, trans_id)
            sock.sendto(req, (ip, port))
            
            resp, _ = sock.recvfrom(2048)
            
            # Unpack Connect Response: >IIQ (Action, Trans ID, Conn ID)
            if len(resp) < 16:
                raise ValueError("Invalid connect response length")
                
            action_resp, trans_id_resp, conn_id = struct.unpack(">IIQ", resp[:16])
            
            if trans_id_resp != trans_id or action_resp != 0:
                raise ValueError("Invalid connect response data")

            # 2. ANNOUNCE REQUEST
            action = 1 # Announce
            trans_id = random.randint(0, 65535)
            
            # Pack: >QII (Conn ID, Action, Trans ID) + 20s (Info Hash) + 20s (Peer ID) + QQQ (Down, Left, Up) + III (Event, IP, Key) + i (Num Want) + H (Port)
            # Total Header size: 98 bytes
            
            # Event: 0 (None), 2 (Started) - Let's use 2 if starting, but 0 is safer generally
            event = 0 
            key = random.randint(0, 65535)
            num_want = -1 # Default
            
            req = struct.pack(">QII", conn_id, action, trans_id) + \
                  self.torrent.info_hash + \
                  self.peer_id + \
                  struct.pack(">QQQIIIiH", downloaded, self.torrent.total_size - downloaded, uploaded, event, 0, key, num_want, 6881)
                  
            sock.sendto(req, (ip, port))
            
            resp, _ = sock.recvfrom(2048)
            
            # Unpack Announce Response: >IIIII (Action, Trans ID, Interval, Leechers, Seeders)
            if len(resp) < 20:
                raise ValueError("Invalid announce response length")
                
            header = struct.unpack(">IIIII", resp[:20])
            action_resp = header[0]
            trans_id_resp = header[1]
            
            if trans_id_resp != trans_id or action_resp != 1:
                raise ValueError("Invalid announce response data")
            
            # Peers are the rest of the body
            peers_binary = resp[20:]
            return self._decode_peers(peers_binary)
            
        except socket.timeout:
            return None
        except Exception as e:
            logging.debug(f"UDP Error: {e}")
            return None
        finally:
            sock.close()

    def _decode_peers(self, peers_binary):
        peers = []
        peer_size = 6
        if len(peers_binary) % peer_size != 0:
            logging.warning("Received truncated peers binary")
            return []

        for i in range(0, len(peers_binary), peer_size):
            chunk = peers_binary[i : i + peer_size]
            ip_bytes = chunk[:4]
            port_bytes = chunk[4:]
            
            ip = socket.inet_ntoa(ip_bytes)
            port = struct.unpack(">H", port_bytes)[0]
            
            peers.append((ip, port))
        return peers