import asyncio
import random
import logging

class ConnectionManager:
    """
    Implements the BitTorrent Choking Algorithm (Tit-for-Tat).
    Manages global upload slots to maximize reciprocity.
    """
    def __init__(self, piece_manager):
        self.piece_manager = piece_manager
        self.connections = set()
        self.running = False
        self.task = None
        
        # Algorithm State
        self.optimistic_unchoke_peer = None
        self.round_counter = 0

    def start(self):
        self.running = True
        self.task = asyncio.create_task(self._choke_loop())

    def stop(self):
        self.running = False
        if self.task: self.task.cancel()

    def add_connection(self, peer_conn):
        self.connections.add(peer_conn)

    def remove_connection(self, peer_conn):
        if peer_conn in self.connections:
            self.connections.remove(peer_conn)

    async def _choke_loop(self):
        """
        Runs every 10 seconds to recalculate upload slots.
        """
        while self.running:
            try:
                await asyncio.sleep(10)
                self.round_counter += 1
                self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Choker Error: {e}")

    def _tick(self):
        # 1. Update Stats for all peers
        for peer in self.connections:
            peer.tick_stats()

        # 2. Filter Candidates (Peers who want our data)
        # Exclude "Snubbed" peers (haven't sent us data in >60s) unless we are seeding
        i_am_seeding = self.piece_manager.complete
        
        candidates = []
        for p in self.connections:
            if p.peer_interested:
                if i_am_seeding or not p.is_snubbed:
                    candidates.append(p)

        # 3. Sort Candidates
        if i_am_seeding:
            # If seeding, maximize upload throughput (Upload Rate)
            candidates.sort(key=lambda p: p.upload_rate, reverse=True)
        else:
            # If leeching, maximize reciprocity (Download Rate)
            candidates.sort(key=lambda p: p.download_rate, reverse=True)

        # 4. Pick Top 4 (Regular Unchoke)
        upload_slots = 4
        top_peers = candidates[:upload_slots]

        # 5. Optimistic Unchoke (Every 3rd cycle = 30s)
        # We rotate the optimistic slot to find new fast peers
        if self.round_counter % 3 == 0:
            # Pick a candidate that is NOT in top_peers
            remaining = [p for p in candidates if p not in top_peers]
            if remaining:
                self.optimistic_unchoke_peer = random.choice(remaining)
            else:
                self.optimistic_unchoke_peer = None
        else:
            # Keep existing optimistic peer if they are still connected/interested
            if self.optimistic_unchoke_peer and \
               (self.optimistic_unchoke_peer not in self.connections or \
                not self.optimistic_unchoke_peer.peer_interested):
                self.optimistic_unchoke_peer = None

        # If optimistic peer became fast enough to be in Top 4, we need a new optimistic one next time
        # For now, just add it to allowed list
        
        allowed_peers = set(top_peers)
        if self.optimistic_unchoke_peer:
            allowed_peers.add(self.optimistic_unchoke_peer)

        # 6. Apply Decisions
        for p in self.connections:
            if p in allowed_peers:
                p.unchoke()
            else:
                p.choke()