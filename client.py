import asyncio
import logging
import time
import sys
from torrent import Torrent
from tracker import Tracker
from piece_manager import PieceManager
from metadata import MetadataManager
from peer import PeerConnection
from nat import NatTraverser
from utp import UtpManager
from kademlia import DHT
from connection_manager import ConnectionManager # NEW

MAX_PEER_CONNECTIONS = 50  
MAX_HALF_OPEN = 10         

class TorrentClient:
    def __init__(self, torrent_file):
        self.torrent = Torrent(torrent_file)
        self.tracker = Tracker(self.torrent)
        self.piece_manager = None 
        self.peers_queue = asyncio.Queue()
        self.workers = []
        self.abort = False
        self.nat = NatTraverser()
        self.dial_semaphore = asyncio.Semaphore(MAX_HALF_OPEN)
        self.utp = UtpManager(port=6881)
        self.dht = DHT(self.peers_queue, port=6882)
        
        # NEW: Connection Manager (Choker)
        self.conn_manager = None 

    async def start(self):
        logging.info(f"Starting client...")
        
        # ... UDP / DHT / UPnP Init ...
        loop = asyncio.get_running_loop()
        try:
            utp_transport, _ = await loop.create_datagram_endpoint(
                lambda: self.utp, local_addr=('0.0.0.0', 6881)
            )
            self.utp.transport = utp_transport
            dht_transport, _ = await loop.create_datagram_endpoint(
                lambda: self.dht, local_addr=('0.0.0.0', 6882)
            )
            self.dht.transport = dht_transport
            asyncio.create_task(self.dht.bootstrap())
        except OSError as e:
            logging.warning(f"UDP Error: {e}")

        print("Attempting UPnP Port Mapping...")
        await self.nat.map_port(6881)
        await self.nat.map_port(6882, "UDP")

        # Phase 1: Metadata
        if not self.torrent.loaded:
            print("Magnet Link detected. Fetching Metadata...")
            success = await self._fetch_metadata()
            if not success:
                print("Failed. Exiting.")
                return
            print("Metadata received and verified.")

        # Phase 2: File Download
        print(f"Initializing Download: {self.torrent.output_file}")
        
        self.piece_manager = PieceManager(self.torrent)
        
        # Initialize Connection Manager
        self.conn_manager = ConnectionManager(self.piece_manager)
        self.conn_manager.start()
        
        if self.piece_manager.complete:
            print("Download complete! Seeding...")
        elif self.piece_manager.downloaded_bytes > 0:
            percent = (self.piece_manager.downloaded_bytes / self.torrent.total_size) * 100
            print(f"Resuming from {percent:.2f}%")
        
        self.workers = [] 
        for _ in range(MAX_PEER_CONNECTIONS): 
            worker = PeerConnection(self.peers_queue, self.piece_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id,
                                    dial_semaphore=self.dial_semaphore,
                                    utp_manager=self.utp,
                                    conn_manager=self.conn_manager) # Pass Manager
            task = asyncio.create_task(worker.run())
            self.workers.append(task)

        await self._download_loop()

    async def _fetch_metadata(self):
        meta_manager = MetadataManager(self.torrent.info_hash)
        # Metadata Phase doesn't use complex choking, pass None for conn_manager
        for _ in range(MAX_PEER_CONNECTIONS):
            worker = PeerConnection(self.peers_queue, meta_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id,
                                    dial_semaphore=self.dial_semaphore,
                                    is_metadata_mode=True,
                                    utp_manager=self.utp)
            task = asyncio.create_task(worker.run())
            self.workers.append(task)
            
        print("Connecting for metadata...")
        asyncio.create_task(self._announce_wrapper())
        asyncio.create_task(self._dht_search_loop())
        
        start_time = time.time()
        while not meta_manager.complete and not self.abort:
            if time.time() - start_time > 60:
                 asyncio.create_task(self._announce_wrapper())
                 start_time = time.time()
            sys.stdout.write(f"\rPeers: {self.peers_queue.qsize()} | Metadata: {sum(1 for p in meta_manager.pieces if p)}/{meta_manager.num_pieces}   ")
            sys.stdout.flush()
            await asyncio.sleep(1)
        print() 
        if self.abort: return False
        
        self.torrent.load_metadata(meta_manager.raw_data)
        for task in self.workers: task.cancel()
        self.workers = []
        while not self.peers_queue.empty():
            try: self.peers_queue.get_nowait()
            except: break
        return True

    async def _download_loop(self):
        previous_announce = 0
        interval = 30 * 60 
        last_time = time.time()
        last_downloaded = self.piece_manager.downloaded_bytes
        
        print("Connecting to swarm...")
        asyncio.create_task(self._dht_search_loop())

        try:
            while not self.abort:
                now = time.time()
                if (now - previous_announce) >= interval:
                    asyncio.create_task(self._announce_wrapper())
                    previous_announce = now
                
                current_downloaded = self.piece_manager.downloaded_bytes
                total_size = self.torrent.total_size
                
                time_delta = now - last_time
                if time_delta >= 1.0: 
                    bytes_delta = current_downloaded - last_downloaded
                    speed = bytes_delta / time_delta
                    last_time = now
                    last_downloaded = current_downloaded
                    
                    remaining = total_size - current_downloaded
                    eta_seconds = remaining / speed if speed > 0 else 0
                    
                    self._render_dashboard(current_downloaded, total_size, speed, eta_seconds, self.peers_queue.qsize())

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            print("\nStopped.")
        finally:
            self.stop()

    async def _announce_wrapper(self):
        try:
            downloaded = 0
            if self.piece_manager:
                downloaded = self.piece_manager.downloaded_bytes
            peers = await self.tracker.connect(downloaded=downloaded)
            if peers:
                for peer in peers: await self.peers_queue.put(peer)
        except Exception as e:
            logging.error(f"Tracker: {e}")

    async def _dht_search_loop(self):
        while not self.abort:
            try: await self.dht.get_peers(self.torrent.info_hash)
            except Exception: pass
            await asyncio.sleep(30)

    def _render_dashboard(self, downloaded, total, speed, eta, peers):
        if total == 0: return 
        percent = (downloaded / total) * 100
        bar_len = 30
        filled_len = int(bar_len * percent // 100)
        bar = '=' * filled_len + '-' * (bar_len - filled_len)
        if speed < 1024: speed_str = f"{speed:.0f} B/s"
        elif speed < 1024**2: speed_str = f"{speed/1024:.2f} KB/s"
        else: speed_str = f"{speed/1024**2:.2f} MB/s"
        if eta == 0 and percent < 100: eta_str = "∞"
        elif eta < 60: eta_str = f"{int(eta)}s"
        elif eta < 3600: eta_str = f"{int(eta//60)}m {int(eta%60)}s"
        else: eta_str = f"{int(eta//3600)}h {int((eta%3600)//60)}m"
        status = "Seeding" if downloaded == total else "Downloading"
        sys.stdout.write(f"\r[{bar}] {percent:.2f}% | {speed_str} | ETA: {eta_str} | Peers: {peers} | {status}   ")
        sys.stdout.flush()

    def stop(self):
        self.abort = True
        if self.conn_manager: self.conn_manager.stop()
        for task in self.workers:
            task.cancel()
        if self.piece_manager:
            self.piece_manager.close()