import asyncio
import struct
import socket
import logging
import message
from bencoding import Decoder

class PeerConnection:
    def __init__(self, queue, piece_manager, info_hash, peer_id):
        self.queue = queue 
        self.piece_manager = piece_manager
        self.info_hash = info_hash
        self.my_peer_id = peer_id
        self.remote_peer_id = None
        
        self.reader = None
        self.writer = None
        self.ip = None
        self.port = None
        
        self.remote_extensions = {} 
        self.supports_extensions = False
        
        # State
        self.peer_choking = True      # Peer is choking us (We can't download)
        self.peer_interested = False  # Peer wants our data
        self.am_choking = True        # We are choking peer (Peer can't download)
        self.am_interested = False    # We want peer's data

    async def run(self):
        while True:
            try:
                self.ip, self.port = await self.queue.get()
            except asyncio.CancelledError:
                break

            try:
                await self._connect_and_loop()
            except asyncio.CancelledError:
                self.stop()
                self.queue.task_done()
                raise 
            except Exception as e:
                pass
            
            self.stop()
            self.queue.task_done()

    async def _connect_and_loop(self):
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port), timeout=10
            )
            
            await asyncio.wait_for(self._perform_handshake(), timeout=10)
            
            if self.supports_extensions:
                await self._send_extended_handshake()
            
            # Send Bitfield (Tell them what we have so they can get interested)
            # Note: For strict compliance, Bitfield should be the FIRST message after handshake.
            # We skip constructing the full bitfield message for brevity here, 
            # relying on HAVE messages sent later or assuming we start empty.
            
            await self._send_interested()
            
            async for msg in self._message_iterator():
                await self._handle_message(msg)
                
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error(f"Error {self.ip}: {e}")

    async def _perform_handshake(self):
        hs = message.Handshake(self.info_hash, self.my_peer_id)
        self.writer.write(hs.encode())
        await self.writer.drain()
        
        data = await self.reader.readexactly(68)
        
        if data[1:20] != b'BitTorrent protocol':
             raise ValueError("Unknown protocol")
             
        reserved_byte_5 = data[25]
        if reserved_byte_5 & 0x10:
            self.supports_extensions = True
            
        if data[28:48] != self.info_hash:
            raise ValueError("Info hash mismatch")
            
        self.remote_peer_id = data[48:]

    async def _send_extended_handshake(self):
        msg = message.ExtendedHandshake()
        self.writer.write(msg.encode())
        await self.writer.drain()

    async def _send_interested(self):
        msg = message.PeerMessage(message.INTERESTED)
        self.writer.write(msg.encode())
        await self.writer.drain()
        self.am_interested = True

    async def _send_unchoke(self):
        msg = message.PeerMessage(message.UNCHOKE)
        self.writer.write(msg.encode())
        await self.writer.drain()
        self.am_choking = False

    async def _message_iterator(self):
        while True:
            try:
                length_data = await asyncio.wait_for(self.reader.readexactly(4), timeout=120)
                length = struct.unpack(">I", length_data)[0]
                
                if length == 0: continue
                
                id_data = await asyncio.wait_for(self.reader.readexactly(1), timeout=120)
                msg_id = id_data[0]
                
                payload_length = length - 1
                payload = b''
                if payload_length > 0:
                    payload = await asyncio.wait_for(self.reader.readexactly(payload_length), timeout=120)
                
                yield message.PeerMessage(msg_id, payload)
            except Exception:
                break

    async def _handle_message(self, msg):
        if msg.msg_id == message.CHOKE:
            self.peer_choking = True
        elif msg.msg_id == message.UNCHOKE:
            self.peer_choking = False
            await self._request_piece()
        
        elif msg.msg_id == message.INTERESTED:
            self.peer_interested = True
            # Simple Seeding Strategy: Always Unchoke interested peers
            # (In a real client, use Tit-for-Tat logic here)
            await self._send_unchoke()
            
        elif msg.msg_id == message.NOT_INTERESTED:
            self.peer_interested = False
            
        elif msg.msg_id == message.HAVE:
            index = struct.unpack(">I", msg.payload)[0]
            self.piece_manager.update_peer(self.remote_peer_id, index)
            
        elif msg.msg_id == message.BITFIELD:
            self.piece_manager.add_peer(self.remote_peer_id, msg.payload)
            await self._request_piece()
            
        elif msg.msg_id == message.REQUEST:
            # Payload: index(4), begin(4), length(4)
            if len(msg.payload) != 12: return
            index, begin, length = struct.unpack(">III", msg.payload)
            await self._handle_request(index, begin, length)
            
        elif msg.msg_id == message.PIECE:
            index = struct.unpack(">I", msg.payload[0:4])[0]
            begin = struct.unpack(">I", msg.payload[4:8])[0]
            block_data = msg.payload[8:]
            self.piece_manager.block_received(self.remote_peer_id, index, begin, block_data)
            # Notify peer we have this piece (Strictly we should send HAVE to *all* peers)
            # For this simplified worker, we rely on PieceManager to manage global state eventually
            await self._request_piece()
            
        elif msg.msg_id == message.EXTENDED:
            await self._handle_extended_message(msg.payload)

    async def _handle_request(self, index, begin, length):
        """
        Peer wants data. Check if we can send it.
        """
        if self.am_choking:
            # We are choking them, ignore request (Protocol rule)
            return

        if length > 16384 * 2: # Anti-DoS: Don't allow huge requests
            return

        block_data = self.piece_manager.read_block(index, begin, length)
        
        if block_data:
            # Send PIECE message
            # Payload: index(4) + begin(4) + data
            payload = struct.pack(">II", index, begin) + block_data
            msg = message.PeerMessage(message.PIECE, payload)
            self.writer.write(msg.encode())
            await self.writer.drain()
            # logging.info(f"Uploaded block {index}:{begin} to {self.ip}")

    async def _handle_extended_message(self, payload):
        ext_id = payload[0]
        data = payload[1:]
        
        if ext_id == 0:
            self._handle_ext_handshake(data)
        else:
            ext_name = None
            for name, remote_id in self.remote_extensions.items():
                if remote_id == ext_id:
                    ext_name = name
                    break
            
            if ext_name == b'ut_pex':
                self._handle_pex(data)

    def _handle_ext_handshake(self, data):
        try:
            handshake_dict = Decoder(data).decode()
            if b'm' in handshake_dict:
                self.remote_extensions = handshake_dict[b'm']
        except Exception:
            pass

    def _handle_pex(self, data):
        try:
            pex_dict = Decoder(data).decode()
            if b'added' in pex_dict:
                peers_binary = pex_dict[b'added']
                self._parse_and_add_peers(peers_binary)
        except Exception:
            pass

    def _parse_and_add_peers(self, binary_data):
        peer_size = 6
        if len(binary_data) % peer_size != 0: return

        count = 0
        for i in range(0, len(binary_data), peer_size):
            chunk = binary_data[i : i + peer_size]
            ip = socket.inet_ntoa(chunk[:4])
            port = struct.unpack(">H", chunk[4:])[0]
            try:
                self.queue.put_nowait((ip, port))
                count += 1
            except Exception: pass
        if count > 0:
            logging.info(f"PEX: Discovered {count} new peers from {self.ip}")

    async def _request_piece(self):
        if self.peer_choking: return
        block = self.piece_manager.next_request(self.remote_peer_id)
        if block:
            req = message.Request(block.piece_index, block.offset, block.length)
            self.writer.write(req.encode())
            await self.writer.drain()

    def stop(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception: pass
        self.writer = None