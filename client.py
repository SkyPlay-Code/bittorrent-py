import asyncio
import logging
import time
import sys
from torrent import Torrent
from tracker import Tracker
from piece_manager import PieceManager
from peer import PeerConnection

class TorrentClient:
    """
    The main entry point. Orchestrates the download.
    """
    def __init__(self, torrent_file):
        self.torrent = Torrent(torrent_file)
        self.tracker = Tracker(self.torrent)
        self.piece_manager = PieceManager(self.torrent)
        self.peers_queue = asyncio.Queue()
        self.workers = []
        self.abort = False

    async def start(self):
        logging.info(f"Starting download: {self.torrent.output_file}")
        
        # 1. Start Workers
        for _ in range(5):
            worker = PeerConnection(self.peers_queue, self.piece_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id)
            task = asyncio.create_task(worker.run())
            self.workers.append(task)

        # 2. Main UI Loop
        previous_announce = 0
        interval = 30 * 60 
        
        # Speed calculation variables
        last_time = time.time()
        last_downloaded = 0
        
        print(f"Downloading: {self.torrent.output_file}")
        print("Connecting to swarm...")

        try:
            while not self.piece_manager.complete and not self.abort:
                now = time.time()
                
                # Announce Logic
                if (now - previous_announce) >= interval:
                    logging.info("Announcing to tracker...")
                    try:
                        peers = await self.tracker.connect()
                        if peers:
                            logging.info(f"Tracker returned {len(peers)} peers")
                            for peer in peers:
                                await self.peers_queue.put(peer)
                        previous_announce = now
                    except Exception as e:
                        logging.error(f"Tracker announce failed: {e}")
                
                # --- UI / Stats Calculation ---
                current_downloaded = self.piece_manager.downloaded_bytes
                total_size = self.torrent.total_size
                
                # Calculate Speed
                time_delta = now - last_time
                if time_delta >= 1.0: # Update speed every second
                    bytes_delta = current_downloaded - last_downloaded
                    speed = bytes_delta / time_delta
                    
                    last_time = now
                    last_downloaded = current_downloaded
                    
                    # Calculate ETA
                    remaining = total_size - current_downloaded
                    eta_seconds = remaining / speed if speed > 0 else 0
                    
                    self._render_dashboard(current_downloaded, total_size, speed, eta_seconds, self.peers_queue.qsize())

                await asyncio.sleep(0.5)
            
            # Final render
            self._render_dashboard(self.torrent.total_size, self.torrent.total_size, 0, 0, 0)
            print("\n\nDownload Complete!")
            
        except asyncio.CancelledError:
            print("\nStopped.")
        finally:
            self.stop()

    def _render_dashboard(self, downloaded, total, speed, eta, peers):
        """
        Renders a progress bar and stats to stdout.
        Format: [=====>....] 45.0% | 1.2 MB/s | ETA: 5m 30s | Peers: 4
        """
        # Percentage
        percent = (downloaded / total) * 100
        
        # Bar
        bar_len = 30
        filled_len = int(bar_len * percent // 100)
        bar = '=' * filled_len + '-' * (bar_len - filled_len)
        
        # Speed formatting
        if speed < 1024:
            speed_str = f"{speed:.0f} B/s"
        elif speed < 1024**2:
            speed_str = f"{speed/1024:.2f} KB/s"
        else:
            speed_str = f"{speed/1024**2:.2f} MB/s"
            
        # ETA formatting
        if eta == 0 and percent < 100:
            eta_str = "∞"
        elif eta < 60:
            eta_str = f"{int(eta)}s"
        elif eta < 3600:
            eta_str = f"{int(eta//60)}m {int(eta%60)}s"
        else:
            eta_str = f"{int(eta//3600)}h {int((eta%3600)//60)}m"

        # Output line
        # \r moves cursor to start of line, allowing overwrite
        sys.stdout.write(f"\r[{bar}] {percent:.2f}% | {speed_str} | ETA: {eta_str} | Peers in Q: {peers}   ")
        sys.stdout.flush()

    def stop(self):
        self.abort = True
        for task in self.workers:
            task.cancel()
        self.piece_manager.close()