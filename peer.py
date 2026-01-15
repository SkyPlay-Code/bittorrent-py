import asyncio
import struct
import logging
import message

class PeerConnection:
    """
    A persistent worker that continuously pulls peers from the queue.
    If a connection fails, it resets and grabs the next peer.
    It only stops if the Client explicitly cancels the task.
    """
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
        
        # State
        self.peer_choking = True 
        self.peer_interested = False
        self.am_choking = True
        self.am_interested = False

    async def run(self):
        while True:
            # 1. Get a peer from the queue
            try:
                self.ip, self.port = await self.queue.get()
            except asyncio.CancelledError:
                # Main client is shutting down
                break

            # 2. Attempt Connection
            try:
                logging.info(f"Worker attempting to connect to {self.ip}:{self.port}")
                await self._connect_and_loop()
            except asyncio.CancelledError:
                # We were cancelled while connected/connecting
                self.stop()
                self.queue.task_done()
                break 
            except Exception as e:
                # Any other error (timeout, connection refused, etc.)
                # We Log it, but we DO NOT BREAK the loop.
                logging.error(f"Worker error with {self.ip}: {e}")
            finally:
                # Cleanup state for this peer, mark queue item done, 
                # and IMMEDIATELY loop back to get the next peer.
                self.stop()
                self.queue.task_done()

    async def _connect_and_loop(self):
        try:
            # 1. Open TCP Connection (10s timeout)
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port), timeout=10
            )
            logging.info(f"TCP Established with {self.ip}. Handshaking...")
            
            # 2. Perform Handshake (10s timeout)
            await asyncio.wait_for(self._perform_handshake(), timeout=10)
            logging.info(f"Handshake OK with {self.ip}. Starting Loop...")
            
            # 3. Send Interested
            await self._send_interested()
            
            # 4. Message Loop (With inactivity timeout)
            async for msg in self._message_iterator():
                await self._handle_message(msg)
                
        except asyncio.TimeoutError:
            logging.warning(f"Timeout connecting/handshaking with {self.ip}")
        except (ConnectionError, OSError) as e:
            logging.warning(f"Connection error with {self.ip}: {e}")
        except asyncio.CancelledError:
            raise # Re-raise to be caught by the run loop
        except Exception as e:
            logging.error(f"Unexpected error with {self.ip}: {e}")

    async def _perform_handshake(self):
        hs = message.Handshake(self.info_hash, self.my_peer_id)
        self.writer.write(hs.encode())
        await self.writer.drain()
        
        data = await self.reader.readexactly(68)
        pstrlen = data[0]
        if data[1:1+pstrlen] != b'BitTorrent protocol':
             raise ValueError("Unknown protocol")
        
        received_hash = data[28:48]
        if received_hash != self.info_hash:
            raise ValueError("Info hash mismatch")
            
        self.remote_peer_id = data[48:]

    async def _send_interested(self):
        msg = message.PeerMessage(message.INTERESTED)
        self.writer.write(msg.encode())
        await self.writer.drain()
        self.am_interested = True

    async def _message_iterator(self):
        while True:
            try:
                # Wait up to 120 seconds for a message (Keep-Alive logic)
                length_data = await asyncio.wait_for(self.reader.readexactly(4), timeout=120)
                length = struct.unpack(">I", length_data)[0]
                
                if length == 0:
                    continue
                
                id_data = await asyncio.wait_for(self.reader.readexactly(1), timeout=120)
                msg_id = id_data[0]
                
                payload_length = length - 1
                payload = b''
                if payload_length > 0:
                    payload = await asyncio.wait_for(self.reader.readexactly(payload_length), timeout=120)
                
                yield message.PeerMessage(msg_id, payload)
                
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
                break

    async def _handle_message(self, msg):
        if msg.msg_id == message.CHOKE:
            logging.info(f"{self.ip} Choked us")
            self.peer_choking = True
        elif msg.msg_id == message.UNCHOKE:
            logging.info(f"{self.ip} Unchoked us")
            self.peer_choking = False
            await self._request_piece()
            
        elif msg.msg_id == message.HAVE:
            index = struct.unpack(">I", msg.payload)[0]
            self.piece_manager.update_peer(self.remote_peer_id, index)
            
        elif msg.msg_id == message.BITFIELD:
            self.piece_manager.add_peer(self.remote_peer_id, msg.payload)
            await self._request_piece()
            
        elif msg.msg_id == message.PIECE:
            index = struct.unpack(">I", msg.payload[0:4])[0]
            begin = struct.unpack(">I", msg.payload[4:8])[0]
            block_data = msg.payload[8:]
            
            self.piece_manager.block_received(self.remote_peer_id, index, begin, block_data)
            await self._request_piece()

    async def _request_piece(self):
        if self.peer_choking:
            return
        
        block = self.piece_manager.next_request(self.remote_peer_id)
        if block:
            req = message.Request(block.piece_index, block.offset, block.length)
            self.writer.write(req.encode())
            await self.writer.drain()

    def stop(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        self.writer = None
        self.reader = None
        self.peer_choking = True