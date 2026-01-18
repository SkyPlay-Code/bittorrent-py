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
from utp import UtpManager # NEW

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
        
        # NEW: uTP Manager (One UDP socket for all connections)
        self.utp = UtpManager(port=6881)

    async def start(self):
        logging.info(f"Starting client...")
        
        # Start UDP Listener
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: self.utp, local_addr=('0.0.0.0', 6881)
        )
        self.utp.transport = transport # Link transport manually
        
        # UPnP
        print("Attempting UPnP Port Mapping...")
        await self.nat.map_port(6881)
        await self.nat.map_port(6881, protocol="UDP") # Map UDP for uTP

        # Phase 1: Metadata
        if not self.torrent.loaded:
            print("Magnet Link detected. Fetching Metadata...")
            success = await self._fetch_metadata()
            if not success:
                print("Failed to retrieve metadata. Exiting.")
                return
            print("Metadata received and verified.")

        # Phase 2: File Download
        print(f"Initializing Download: {self.torrent.output_file}")
        
        self.piece_manager = PieceManager(self.torrent)
        if self.piece_manager.complete:
            print("Download already complete! Seeding...")
        elif self.piece_manager.downloaded_bytes > 0:
            percent = (self.piece_manager.downloaded_bytes / self.torrent.total_size) * 100
            print(f"Resuming from {percent:.2f}%")
        
        self.workers = [] 
        for _ in range(MAX_PEER_CONNECTIONS): 
            # Pass uTP manager to workers
            worker = PeerConnection(self.peers_queue, self.piece_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id,
                                    dial_semaphore=self.dial_semaphore,
                                    utp_manager=self.utp) # NEW param
            task = asyncio.create_task(worker.run())
            self.workers.append(task)

        await self._download_loop()

    # ... (Rest of _fetch_metadata, _download_loop, etc. remains same) ...
    # Be sure to update _fetch_metadata to pass utp_manager=self.utp to PeerConnection too!

    async def _fetch_metadata(self):
        meta_manager = MetadataManager(self.torrent.info_hash)
        
        for _ in range(MAX_PEER_CONNECTIONS):
            worker = PeerConnection(self.peers_queue, meta_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id,
                                    dial_semaphore=self.dial_semaphore,
                                    is_metadata_mode=True,
                                    utp_manager=self.utp) # NEW
            task = asyncio.create_task(worker.run())
            self.workers.append(task)
            
        print("Connecting to swarm for metadata...")
        asyncio.create_task(self._announce_wrapper())
        
        start_time = time.time()
        while not meta_manager.complete and not self.abort:
            if time.time() - start_time > 60:
                 asyncio.create_task(self._announce_wrapper())
                 start_time = time.time()
            
            sys.stdout.write(f"\rPeers: {self.peers_queue.qsize()} | Metadata: {'Searching...' if not meta_manager.active else f'{sum(1 for p in meta_manager.pieces if p)}/{meta_manager.num_pieces}'}   ")
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

    # ... (Include _download_loop, _announce_wrapper, _render_dashboard, stop) ...
    async def _download_loop(self):
        previous_announce = 0
        interval = 30 * 60 
        last_time = time.time()
        last_downloaded = self.piece_manager.downloaded_bytes
        
        print("Connecting to swarm for files...")

        try:
            while not self.abort:
                now = time.time()
                if (now - previous_announce) >= interval:
                    logging.info("Announcing to tracker...")
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
                logging.info(f"Tracker returned {len(peers)} peers")
                for peer in peers:
                    await self.peers_queue.put(peer)
        except Exception as e:
            logging.error(f"Tracker announce failed: {e}")

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
        for task in self.workers:
            task.cancel()
        if self.piece_manager:
            self.piece_manager.close()