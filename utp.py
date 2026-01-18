import asyncio
import struct
import time
import random
import logging
import collections

# Constants
ST_DATA = 0
ST_FIN = 1
ST_STATE = 2 # ACK
ST_RESET = 3
ST_SYN = 4

# Fixed Header Size: 20 bytes
HEADER_FMT = ">BBHIIHHH"
HEADER_SIZE = 20

class UtpPacket:
    def __init__(self, type_id, conn_id, seq_nr, ack_nr, payload=b'', wnd_size=65535, ts=0, ts_diff=0):
        self.type_id = type_id
        self.version = 1
        self.extension = 0
        self.conn_id = conn_id
        self.ts = int(time.time() * 1000000) & 0xFFFFFFFF if ts == 0 else ts
        self.ts_diff = ts_diff
        self.wnd_size = wnd_size
        self.seq_nr = seq_nr
        self.ack_nr = ack_nr
        self.payload = payload

    def encode(self):
        type_ver = (self.type_id << 4) | self.version
        header = struct.pack(HEADER_FMT, 
                             type_ver, self.extension, self.conn_id, 
                             self.ts, self.ts_diff, self.wnd_size, 
                             self.seq_nr, self.ack_nr)
        return header + self.payload

    @classmethod
    def decode(cls, data):
        if len(data) < HEADER_SIZE:
            raise ValueError("Packet too short")
        
        header_data = data[:HEADER_SIZE]
        type_ver, ext, conn_id, ts, ts_diff, wnd, seq, ack = struct.unpack(HEADER_FMT, header_data)
        
        type_id = type_ver >> 4
        ver = type_ver & 0x0F
        
        if ver != 1:
            raise ValueError("Unsupported uTP version")
            
        payload = data[HEADER_SIZE:]
        return cls(type_id, conn_id, seq, ack, payload, wnd, ts, ts_diff)

class UtpSocket:
    """
    State machine for a single uTP connection.
    Mimics TCP reliability over UDP.
    """
    def __init__(self, manager, addr, conn_id_recv, conn_id_send):
        self.manager = manager
        self.addr = addr
        self.conn_id_recv = conn_id_recv
        self.conn_id_send = conn_id_send
        
        self.seq_nr = random.randint(0, 65535)
        self.ack_nr = 0
        
        self.read_buffer = collections.deque() # Ordered packets
        self.recv_future = None # For asyncio waiting
        self.connected_event = asyncio.Event()
        
        self.state = "NONE" # NONE, SYN_SENT, CONNECTED, FIN_SENT

    async def connect(self):
        self.state = "SYN_SENT"
        # Send SYN
        pkt = UtpPacket(ST_SYN, self.conn_id_recv, self.seq_nr, 0)
        self.manager.send_packet(pkt, self.addr)
        
        # Wait for ST_STATE (Ack)
        try:
            await asyncio.wait_for(self.connected_event.wait(), timeout=5.0)
            self.seq_nr += 1
        except asyncio.TimeoutError:
            raise ConnectionError("uTP Connection Timed Out")

    def handle_packet(self, pkt):
        # Basic reliability logic
        if pkt.type_id == ST_STATE:
            # ACK received
            if self.state == "SYN_SENT":
                self.state = "CONNECTED"
                self.ack_nr = pkt.seq_nr 
                self.connected_event.set()
                
        elif pkt.type_id == ST_DATA:
            if self.state != "CONNECTED": return
            
            # Simple Seq check (In a real implementation, we handle out-of-order)
            # We assume sequential for this basic implementation
            expected_seq = (self.ack_nr + 1) & 0xFFFF
            
            if pkt.seq_nr == expected_seq:
                self.ack_nr = pkt.seq_nr
                self.read_buffer.append(pkt.payload)
                self._notify_reader()
                self._send_ack()
            else:
                # Out of order or duplicate: Send ACK for what we HAVE
                self._send_ack()

        elif pkt.type_id == ST_FIN:
            self.ack_nr = pkt.seq_nr
            self._send_ack()
            # Signal EOF
            self.read_buffer.append(b'') 
            self._notify_reader()

    def _send_ack(self):
        pkt = UtpPacket(ST_STATE, self.conn_id_send, self.seq_nr, self.ack_nr)
        self.manager.send_packet(pkt, self.addr)

    def _notify_reader(self):
        if self.recv_future and not self.recv_future.done():
            self.recv_future.set_result(True)

    async def read(self, n):
        while not self.read_buffer:
            self.recv_future = asyncio.get_running_loop().create_future()
            await self.recv_future
            
        data = b''
        while self.read_buffer and len(data) < n:
            chunk = self.read_buffer[0]
            if chunk == b'': # EOF
                return b''
            
            if len(chunk) <= n - len(data):
                data += chunk
                self.read_buffer.popleft()
            else:
                to_take = n - len(data)
                data += chunk[:to_take]
                self.read_buffer[0] = chunk[to_take:]
                break
        return data

    def write(self, data):
        # Fragmentation into MSS (Max Segment Size) ~1400 bytes
        MSS = 1380
        for i in range(0, len(data), MSS):
            chunk = data[i:i+MSS]
            self.seq_nr = (self.seq_nr + 1) & 0xFFFF
            pkt = UtpPacket(ST_DATA, self.conn_id_send, self.seq_nr, self.ack_nr, payload=chunk)
            self.manager.send_packet(pkt, self.addr)
            # No flow control wait in this simplified version

    async def drain(self):
        # Mock drain
        await asyncio.sleep(0)

    def close(self):
        pkt = UtpPacket(ST_FIN, self.conn_id_send, (self.seq_nr + 1) & 0xFFFF, self.ack_nr)
        self.manager.send_packet(pkt, self.addr)

class UtpManager:
    """
    Manages the UDP socket and routes packets to UtpSocket instances.
    """
    def __init__(self, port=6881):
        self.port = port
        self.transport = None
        self.protocol = None
        self.sockets = {} # conn_id -> UtpSocket

    def start(self):
        loop = asyncio.get_running_loop()
        # We use a low-level UDP protocol factory
        self.transport, self.protocol = loop.create_datagram_endpoint(
            lambda: self, local_addr=('0.0.0.0', self.port)
        )

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            pkt = UtpPacket.decode(data)
            
            # Incoming Connection
            if pkt.type_id == ST_SYN:
                # Passive open not fully implemented in this phase
                pass
            
            # Look up socket
            # NOTE: conn_id in header is the Receiver's ID. 
            # When we receive, it matches our conn_id_recv.
            if pkt.conn_id in self.sockets:
                self.sockets[pkt.conn_id].handle_packet(pkt)
                
        except Exception as e:
            # logging.debug(f"uTP Decode Error: {e}")
            pass

    def send_packet(self, pkt, addr):
        if self.transport:
            self.transport.sendto(pkt.encode(), addr)

    def connect(self, ip, port):
        """
        Creates a new outgoing uTP connection.
        Returns a UtpSocket.
        """
        conn_id_recv = random.randint(0, 65535)
        conn_id_send = (conn_id_recv + 1) & 0xFFFF
        
        # We register it by the ID the REMOTE will send back to us (conn_id_recv)
        # Wait, BEP 29: Initiator picks Recv ID. Sender sends to Recv ID.
        # So remote will send packets with conn_id = conn_id_recv.
        
        sock = UtpSocket(self, (ip, port), conn_id_recv, conn_id_send)
        self.sockets[conn_id_recv] = sock # Register for routing
        
        return sock