import asyncio
import logging
import time
from torrent import Torrent
from tracker import Tracker
from piece_manager import PieceManager
from peer import PeerConnection

class TorrentClient:
    """
    The main entry point.
    Reference: PDF Page 4.
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
        
        # 1. Start Worker Tasks
        # We spawn 5 concurrent peer workers (arbitrary number, PDF doesn't specify limit but implies N)
        for _ in range(5):
            worker = PeerConnection(self.peers_queue, self.piece_manager, 
                                    self.torrent.info_hash, self.tracker.peer_id)
            task = asyncio.create_task(worker.run())
            self.workers.append(task)

        # 2. Main Loop
        previous_announce = 0
        interval = 30 * 60 # Default 30 min, will update from tracker
        
        try:
            while not self.piece_manager.complete and not self.abort:
                now = time.time()
                
                # Check if we need to announce
                if (now - previous_announce) >= interval:
                    logging.info("Announcing to tracker...")
                    try:
                        peers = await self.tracker.connect()
                        if peers:
                            logging.info(f"Tracker returned {len(peers)} peers")
                            for peer in peers:
                                await self.peers_queue.put(peer)
                        previous_announce = now
                        # interval = ... (Ideally we update interval from tracker response)
                    except Exception as e:
                        logging.error(f"Tracker announce failed: {e}")
                
                # Print progress
                percent = (len(self.piece_manager.have_pieces) / self.piece_manager.total_pieces) * 100
                print(f"Progress: {percent:.2f}% - Peers in queue: {self.peers_queue.qsize()}", end='\r')
                
                await asyncio.sleep(5)
                
            logging.info("\nDownload Complete!")
            
        except asyncio.CancelledError:
            logging.info("Client stopping...")
        finally:
            self.stop()

    def stop(self):
        self.abort = True
        for task in self.workers:
            task.cancel()
        self.piece_manager.close()
        # Tracker close needs to be awaited, but we are in sync method.
        # Ideally we clean up better, but for this scope it's fine.