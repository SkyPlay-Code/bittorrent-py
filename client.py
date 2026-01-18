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

class TorrentClient:
    def __init__(self, torrent_file):
        self.torrent = Torrent(torrent_file)
        self.tracker = Tracker(self.torrent)
        # We DO NOT init PieceManager yet if it's a magnet link
        self.piece_manager = None 
        self.peers_queue = asyncio.Queue()
        self.workers = []
        self.abort = False
        self.nat = NatTraverser()

    async def start(self):
        logging.info(f"Starting client...")
        
        print("Attempting UPnP Port Mapping...")
        await self.nat.map_port(6881)

        # --- Phase 1: Metadata Download (Magnet Links) ---
        if not self.torrent.loaded:
            print("Magnet Link detected. Fetching Metadata...")
            success = await self._fetch_metadata()
            if not success:
                print("Failed to retrieve metadata. Exiting.")
                return
            print("Metadata received and verified.")

        # --- Phase 2: File Download ---
        print(f"Initializing Download: {self.torrent.output_file}")
        self.piece_manager = PieceManager(self.torrent)
        
        # Start Normal Workers
        self.workers = [] # Clear any metadata workers
        for _ in range(10): 
            worker = PeerConnection(self.peers_queue, self.piece_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id)
            task = asyncio.create_task(worker.run())
            self.workers.append(task)

        # Main UI Loop
        await self._download_loop()

    async def _fetch_metadata(self):
        """
        Runs a specialized loop to fetch the .torrent info dictionary.
        """
        meta_manager = MetadataManager(self.torrent.info_hash)
        
        # Start Metadata Workers
        # We reuse PeerConnection but in 'metadata mode'
        for _ in range(10):
            worker = PeerConnection(self.peers_queue, meta_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id,
                                    is_metadata_mode=True)
            task = asyncio.create_task(worker.run())
            self.workers.append(task)
            
        # Loop until complete
        print("Connecting to swarm for metadata...")
        
        # Initial Announce
        asyncio.create_task(self._announce_wrapper())
        
        start_time = time.time()
        while not meta_manager.complete and not self.abort:
            # Announce periodically
            if time.time() - start_time > 60:
                 asyncio.create_task(self._announce_wrapper())
                 start_time = time.time()
            
            # Simple progress spinner
            sys.stdout.write(f"\rPeers: {self.peers_queue.qsize()} | Metadata: {'Searching...' if not meta_manager.active else f'{sum(1 for p in meta_manager.pieces if p)}/{meta_manager.num_pieces}'}   ")
            sys.stdout.flush()
            await asyncio.sleep(1)
            
        print() # Newline
        
        if self.abort: return False
        
        # Load the data into Torrent object
        self.torrent.load_metadata(meta_manager.raw_data)
        
        # Stop all metadata workers
        for task in self.workers:
            task.cancel()
        self.workers = []
        
        # Empty the queue (optional, but good practice to clear old connection attempts)
        while not self.peers_queue.empty():
            try: self.peers_queue.get_nowait()
            except: break
            
        return True

    async def _download_loop(self):
        previous_announce = 0
        interval = 30 * 60 
        last_time = time.time()
        last_downloaded = 0
        
        print("Connecting to swarm for files...")

        try:
            while not self.piece_manager.complete and not self.abort:
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
            
            self._render_dashboard(self.torrent.total_size, self.torrent.total_size, 0, 0, 0)
            print("\n\nDownload Complete!")
            
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

        sys.stdout.write(f"\r[{bar}] {percent:.2f}% | {speed_str} | ETA: {eta_str} | Peers: {peers}   ")
        sys.stdout.flush()

    def stop(self):
        self.abort = True
        for task in self.workers:
            task.cancel()
        if self.piece_manager:
            self.piece_manager.close()