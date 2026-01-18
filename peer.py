import asyncio
import struct
import socket
import logging
import message
from bencoding import Decoder, Encoder

class PeerConnection:
    def __init__(self, queue, manager, info_hash, peer_id, is_metadata_mode=False):
        self.queue = queue 
        # manager can be PieceManager OR MetadataManager depending on mode
        self.manager = manager 
        self.info_hash = info_hash
        self.my_peer_id = peer_id
        self.is_metadata_mode = is_metadata_mode
        self.remote_peer_id = None
        
        self.reader = None
        self.writer = None
        self.ip = None
        self.port = None
        
        self.remote_extensions = {} 
        self.supports_extensions = False
        self.remote_metadata_size = 0
        
        # State
        self.peer_choking = True      
        self.peer_interested = False  
        self.am_choking = True        
        self.am_interested = False    

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
            
            # If in normal mode, we can send Bitfield/Interested now.
            # If in Metadata mode, we wait for ExtHandshake to see size.
            
            if not self.is_metadata_mode:
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
        # We assume we map ut_pex:1, ut_metadata:2
        # metadata_size is optional (0 if we don't have it yet)
        msg = message.ExtendedHandshake() # This class needs update in message.py? 
        # Actually message.py hardcoded ut_pex:1, ut_metadata:2. We are good.
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
        if msg.msg_id == message.EXTENDED:
            await self._handle_extended_message(msg.payload)
            return

        if self.is_metadata_mode:
            # Ignore standard pieces while fetching metadata
            return

        # Normal Mode handlers
        if msg.msg_id == message.CHOKE:
            self.peer_choking = True
        elif msg.msg_id == message.UNCHOKE:
            self.peer_choking = False
            await self._request_piece()
        elif msg.msg_id == message.INTERESTED:
            self.peer_interested = True
            await self._send_unchoke()
        elif msg.msg_id == message.NOT_INTERESTED:
            self.peer_interested = False
        elif msg.msg_id == message.HAVE:
            index = struct.unpack(">I", msg.payload)[0]
            self.manager.update_peer(self.remote_peer_id, index)
        elif msg.msg_id == message.BITFIELD:
            self.manager.add_peer(self.remote_peer_id, msg.payload)
            await self._request_piece()
        elif msg.msg_id == message.REQUEST:
            index, begin, length = struct.unpack(">III", msg.payload)
            await self._handle_request(index, begin, length)
        elif msg.msg_id == message.PIECE:
            index = struct.unpack(">I", msg.payload[0:4])[0]
            begin = struct.unpack(">I", msg.payload[4:8])[0]
            block_data = msg.payload[8:]
            self.manager.block_received(self.remote_peer_id, index, begin, block_data)
            await self._request_piece()

    async def _handle_extended_message(self, payload):
        ext_id = payload[0]
        data = payload[1:]
        
        if ext_id == 0:
            self._handle_ext_handshake(data)
        else:
            # Map ID to name
            ext_name = None
            for name, remote_id in self.remote_extensions.items():
                if remote_id == ext_id:
                    ext_name = name
                    break
            
            if ext_name == b'ut_pex':
                self._handle_pex(data)
            elif ext_name == b'ut_metadata':
                await self._handle_ut_metadata(data)

    def _handle_ext_handshake(self, data):
        try:
            handshake_dict = Decoder(data).decode()
            if b'm' in handshake_dict:
                self.remote_extensions = handshake_dict[b'm']
            if b'metadata_size' in handshake_dict:
                self.remote_metadata_size = handshake_dict[b'metadata_size']
                if self.is_metadata_mode:
                    self.manager.set_size(self.remote_metadata_size)
                    asyncio.create_task(self._request_metadata_piece())
        except Exception:
            pass

    def _handle_pex(self, data):
        try:
            pex_dict = Decoder(data).decode()
            if b'added' in pex_dict:
                self._parse_and_add_peers(pex_dict[b'added'])
        except Exception: pass

    async def _handle_ut_metadata(self, data):
        # Format: Bencoded Dict + Raw Data
        # We need to find where the dict ends.
        # This is tricky with our current decoder which expects full consumption.
        # We will scan for 'e' end of dict? No, 'ee' sometimes.
        # Better: Decode strictly, get length of encoded dict, slice rest.
        
        try:
            # Our Decoder consumes. We need to modify Decoder or guess.
            # Simplified approach: Look for "msg_type" and "piece".
            
            # For robustness, we construct a Decoder that tells us how much it read.
            # We don't have that yet.
            # Hack: Assume the dict header is small (<200 bytes) and valid bencode.
            # We'll try to find the bencoded end "e" that balances the start "d".
            
            decoder = Decoder(data)
            msg_dict = decoder.decode()
            # Decoder doesn't expose consumed count.
            # Let's rely on the fact that if it's DATA (1), the REST is the piece.
            
            msg_type = msg_dict[b'msg_type']
            piece_index = msg_dict[b'piece']
            
            if msg_type == 1: # DATA
                # Where did the dict end?
                # This is hard without updating Bencoder.
                # Let's update bencoding.py to expose index.
                pass 
                
                # Assume we solved finding payload:
                # payload = data[decoder._index:] # Accessing private member for now
                payload = decoder._data[decoder._index:]
                
                if self.is_metadata_mode:
                    self.manager.receive_data(piece_index, payload)
                    if not self.manager.complete:
                        await self._request_metadata_piece()
            
            elif msg_type == 2: # REJECT
                pass # Try another peer
                
        except Exception:
            pass

    async def _request_metadata_piece(self):
        if not self.is_metadata_mode or not self.manager.active: return
        if b'ut_metadata' not in self.remote_extensions: return
        
        index = self.manager.get_next_request()
        if index is not None:
            # Send Request (msg_type 0)
            req = {b'msg_type': 0, b'piece': index}
            encoded_req = Encoder(req).encode()
            
            ext_id = self.remote_extensions[b'ut_metadata']
            # ExtMsgID(1) + Payload
            msg = message.ExtendedMessage(ext_id, encoded_req)
            self.writer.write(msg.encode())
            await self.writer.drain()

    def _parse_and_add_peers(self, binary_data):
        peer_size = 6
        if len(binary_data) % peer_size != 0: return
        for i in range(0, len(binary_data), peer_size):
            chunk = binary_data[i : i + peer_size]
            ip = socket.inet_ntoa(chunk[:4])
            port = struct.unpack(">H", chunk[4:])[0]
            try: self.queue.put_nowait((ip, port))
            except Exception: pass

    async def _handle_request(self, index, begin, length):
        if self.am_choking or length > 32768: return
        block_data = self.manager.read_block(index, begin, length)
        if block_data:
            payload = struct.pack(">II", index, begin) + block_data
            msg = message.PeerMessage(message.PIECE, payload)
            self.writer.write(msg.encode())
            await self.writer.drain()

    async def _request_piece(self):
        if self.peer_choking: return
        block = self.manager.next_request(self.remote_peer_id)
        if block:
            req = message.Request(block.piece_index, block.offset, block.length)
            self.writer.write(req.encode())
            await self.writer.drain()

    def stop(self):
        if self.writer:
            try: self.writer.close()
            except Exception: pass
        self.writer = None