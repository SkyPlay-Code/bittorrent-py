import asyncio
import struct
import socket
import os
import logging
from bencoding import Decoder, Encoder

# Bootstrap nodes to enter the global DHT network
BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881)
]

def random_id():
    return os.urandom(20)

def split_nodes(nodes_bytes):
    """
    Parses compact node info (ID 20b + IP 4b + Port 2b = 26 bytes).
    """
    nodes = []
    if len(nodes_bytes) % 26 != 0: return nodes
    for i in range(0, len(nodes_bytes), 26):
        nid = nodes_bytes[i:i+20]
        ip = socket.inet_ntoa(nodes_bytes[i+20:i+24])
        port = struct.unpack(">H", nodes_bytes[i+24:i+26])[0]
        nodes.append((nid, ip, port))
    return nodes

def pack_nodes(nodes):
    b = b""
    for n in nodes:
        try:
            # n is (id, ip, port)
            b += n[0] + socket.inet_aton(n[1]) + struct.pack(">H", n[2])
        except Exception: pass
    return b

class RoutingTable:
    """
    Manages known DHT nodes. 
    Simplified implementation: Uses a single list instead of full K-Buckets.
    """
    def __init__(self, my_id):
        self.my_id = my_id
        self.nodes = [] # List of (id, ip, port)

    def add_node(self, node):
        if node[0] == self.my_id: return
        
        # If exists, move to end (Most Recently Used)
        for i, n in enumerate(self.nodes):
            if n[0] == node[0]:
                self.nodes.pop(i)
                self.nodes.append(node)
                return
        
        # Cap size at 500 for this simplified implementation
        if len(self.nodes) >= 500:
            self.nodes.pop(0) # Evict oldest
            
        self.nodes.append(node)

    def get_closest_nodes(self, target_id, k=8):
        """
        Returns the k nodes whose IDs are XOR-closest to the target_id.
        """
        t_int = int.from_bytes(target_id, 'big')
        # Sort by XOR distance
        self.nodes.sort(key=lambda n: int.from_bytes(n[0], 'big') ^ t_int)
        return self.nodes[:k]

class DHT(asyncio.DatagramProtocol):
    def __init__(self, peer_queue, port=6881):
        self.peer_queue = peer_queue
        self.port = port
        self.node_id = random_id()
        self.routing_table = RoutingTable(self.node_id)
        self.transport = None
        self.transactions = {} # tid -> future
        self.peers = {} # info_hash -> list of (ip, port)

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            msg = Decoder(data).decode()
            msg_type = msg.get(b'y')
            
            if msg_type == b'q': # Query
                self._handle_query(msg, addr)
            elif msg_type == b'r': # Response
                self._handle_response(msg, addr)
            elif msg_type == b'e': # Error
                pass
        except Exception:
            pass

    def _handle_query(self, msg, addr):
        t = msg[b't']
        q = msg[b'q']
        a = msg[b'a']
        nid = a[b'id']
        
        # Opportunistic: Add querying node to our routing table
        self.routing_table.add_node((nid, addr[0], addr[1]))
        
        if q == b'ping':
            self._send_response(t, {b'id': self.node_id}, addr)
            
        elif q == b'find_node':
            target = a[b'target']
            nodes = self.routing_table.get_closest_nodes(target)
            packed = pack_nodes(nodes)
            self._send_response(t, {b'id': self.node_id, b'nodes': packed}, addr)
            
        elif q == b'get_peers':
            info_hash = a[b'info_hash']
            token = os.urandom(2) # Stateless token for simplicity
            
            resp = {b'id': self.node_id, b'token': token}
            
            # If we know peers for this hash, return them
            if info_hash in self.peers:
                compact_peers = []
                for p in self.peers[info_hash]:
                    try:
                        compact_peers.append(socket.inet_aton(p[0]) + struct.pack(">H", p[1]))
                    except: pass
                # BEP 5: 'values' is a list of byte strings
                resp[b'values'] = compact_peers
            else:
                # Otherwise return closest nodes
                nodes = self.routing_table.get_closest_nodes(info_hash)
                resp[b'nodes'] = pack_nodes(nodes)
            
            self._send_response(t, resp, addr)

        elif q == b'announce_peer':
            # We skip token validation for this minimal implementation
            info_hash = a[b'info_hash']
            port = a[b'port']
            
            if info_hash not in self.peers:
                self.peers[info_hash] = []
            self.peers[info_hash].append((addr[0], port))

            self._send_response(t, {b'id': self.node_id}, addr)

    def _handle_response(self, msg, addr):
        t = msg[b't']
        if t in self.transactions:
            future = self.transactions.pop(t)
            if not future.done():
                future.set_result((msg, addr))
                
        r = msg.get(b'r', {})
        
        # Update Routing Table with 'nodes'
        if b'nodes' in r:
            nodes = split_nodes(r[b'nodes'])
            for n in nodes:
                self.routing_table.add_node(n)

        # Handle 'values' (Peers found!)
        if b'values' in r:
            values = r[b'values']
            count = 0
            for v in values:
                try:
                    ip = socket.inet_ntoa(v[:4])
                    port = struct.unpack(">H", v[4:])[0]
                    self.peer_queue.put_nowait((ip, port))
                    count += 1
                except: pass
            if count > 0:
                logging.info(f"DHT: Found {count} peers from {addr[0]}")

    def _send_response(self, t, args, addr):
        msg = {b't': t, b'y': b'r', b'r': args}
        self.transport.sendto(Encoder(msg).encode(), addr)

    def _send_query(self, q, args, addr):
        tid = os.urandom(2)
        msg = {b't': tid, b'y': b'q', b'q': q, b'a': args}
        
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.transactions[tid] = future
        
        try:
            self.transport.sendto(Encoder(msg).encode(), addr)
        except Exception: 
            pass # UDP send fail
            
        return future

    async def bootstrap(self):
        """
        Connects to public DHT nodes to populate the routing table.
        """
        logging.info("DHT: Bootstrapping...")
        for host, port in BOOTSTRAP_NODES:
            try:
                # Resolve DNS in executor to avoid blocking
                loop = asyncio.get_running_loop()
                ip = await loop.run_in_executor(None, socket.gethostbyname, host)
                
                # Send find_node to get neighbors
                await self.find_node((ip, port), self.node_id)
            except Exception as e:
                logging.debug(f"DHT Bootstrap failed for {host}: {e}")

    async def find_node(self, addr, target_id):
        try:
            future = self._send_query(b'find_node', {b'id': self.node_id, b'target': target_id}, addr)
            await asyncio.wait_for(future, timeout=2)
        except: pass

    async def get_peers(self, info_hash):
        """
        Query closest nodes in our routing table for the info_hash.
        """
        # Ask the top 16 closest nodes
        candidates = self.routing_table.get_closest_nodes(info_hash, k=16)
        if not candidates: return

        # logging.info(f"DHT: Querying {len(candidates)} nodes...")
        for node in candidates:
            # node is (id, ip, port)
            try:
                future = self._send_query(b'get_peers', {b'id': self.node_id, b'info_hash': info_hash}, (node[1], node[2]))
                # We don't await response here to allow parallel queries (Fire & Forget logic)
                # The _handle_response will trigger callback/queue addition
            except: pass