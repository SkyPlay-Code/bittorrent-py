import asyncio
import struct
import logging
import message

class PeerConnection:
    """
    A worker that continuously pulls peers from the queue and attempts to exchange data.
    Reference: PDF Page 4 & 6.
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
        """
        Main worker loop.
        """
        while True:
            # 1. Get a peer from the queue
            try:
                # wait for a peer
                self.ip, self.port = await self.queue.get()
                logging.info(f"Worker grabbed peer {self.ip}:{self.port}")
                
                # 2. Connect
                await self._connect_and_loop()
                
            except asyncio.CancelledError:
                # Graceful shutdown
                self.stop()
                break
            except Exception as e:
                logging.error(f"Worker error with {self.ip}: {e}")
            finally:
                self.stop()
                self.queue.task_done()

    async def _connect_and_loop(self):
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port), timeout=10
            )
            
            # Handshake
            await self._send_handshake()
            await self._receive_handshake()
            
            # Register with PieceManager
            # (In a real implementation we would wait for Bitfield first, 
            # but we'll register presence now)
            
            # Send Interested
            await self._send_interested()
            
            # Message Loop
            async for msg in self._message_iterator():
                await self._handle_message(msg)
                
        except (ConnectionError, asyncio.TimeoutError, OSError):
            # Normal connection failures
            pass
        except Exception as e:
            logging.debug(f"Connection lost {self.ip}: {e}")

    def stop(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        self.writer = None
        self.reader = None
        self.peer_choking = True

    async def _send_handshake(self):
        hs = message.Handshake(self.info_hash, self.my_peer_id)
        self.writer.write(hs.encode())
        await self.writer.drain()

    async def _receive_handshake(self):
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
                length_data = await self.reader.readexactly(4)
                length = struct.unpack(">I", length_data)[0]
                
                if length == 0:
                    yield message.PeerMessage(message.KEEP_ALIVE)
                    continue
                
                id_data = await self.reader.readexactly(1)
                msg_id = id_data[0]
                
                payload_length = length - 1
                payload = b''
                if payload_length > 0:
                    payload = await self.reader.readexactly(payload_length)
                
                yield message.PeerMessage(msg_id, payload)
            except Exception:
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
            
        elif msg.msg_id == message.PIECE:
            index = struct.unpack(">I", msg.payload[0:4])[0]
            begin = struct.unpack(">I", msg.payload[4:8])[0]
            block_data = msg.payload[8:]
            
            self.piece_manager.block_received(self.remote_peer_id, index, begin, block_data)
            await self._request_piece()

    async def _request_piece(self):
        if self.peer_choking:
            return
        
        # Ask manager what's next
        block = self.piece_manager.next_request(self.remote_peer_id)
        if block:
            req = message.Request(block.piece_index, block.offset, block.length)
            self.writer.write(req.encode())
            await self.writer.drain()