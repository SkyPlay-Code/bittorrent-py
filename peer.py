import asyncio
import struct
import logging
import message

# Configure logging to show us what's happening
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class PeerConnection:
    """
    Manages the TCP connection to a single peer.
    """
    def __init__(self, queue, info_hash, peer_id, ip, port):
        self.queue = queue 
        self.info_hash = info_hash
        self.my_peer_id = peer_id
        self.ip = ip
        self.port = port
        self.remote_peer_id = None
        
        self.reader = None
        self.writer = None
        
        # State
        self.peer_choking = True 
        self.peer_interested = False
        self.am_choking = True
        self.am_interested = False
        
        self.bitfield = None

    async def start(self):
        logging.info(f"Connecting to peer {self.ip}:{self.port}")
        try:
            self.reader, self.writer = await asyncio.open_connection(self.ip, self.port)
            
            # 1. Handshake
            await self._send_handshake()
            await self._receive_handshake()
            
            # 2. Start Message Loop
            async for msg in self._message_iterator():
                await self._handle_message(msg)
                
        except asyncio.CancelledError:
            logging.info("Connection cancelled.")
            raise
        except Exception as e:
            logging.error(f"Error with peer {self.ip}: {e}")
        finally:
            self.stop()

    def stop(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        logging.info("Peer connection closed")

    async def _send_handshake(self):
        hs = message.Handshake(self.info_hash, self.my_peer_id)
        self.writer.write(hs.encode())
        await self.writer.drain()

    async def _receive_handshake(self):
        # Read exactly 68 bytes
        data = await self.reader.readexactly(68)
        
        # Validate protocol string
        pstrlen = data[0]
        pstr = data[1:1+pstrlen]
        if pstr != b'BitTorrent protocol':
             raise ValueError("Unknown protocol")
             
        # Validate info_hash
        received_info_hash = data[28:48]
        if received_info_hash != self.info_hash:
            raise ValueError("Info hash mismatch. Dropping connection.")
            
        self.remote_peer_id = data[48:]
        logging.info(f"Handshake successful with {self.ip}")

    async def _message_iterator(self):
        """
        Async iterator that yields PeerMessages.
        """
        while True:
            try:
                # Read 4 bytes length
                length_data = await self.reader.readexactly(4)
                length = struct.unpack(">I", length_data)[0]
                
                if length == 0:
                    yield message.PeerMessage(message.KEEP_ALIVE)
                    continue
                
                # Read ID (1 byte)
                id_data = await self.reader.readexactly(1)
                msg_id = id_data[0]
                
                # Read Payload
                payload_length = length - 1
                payload = b''
                if payload_length > 0:
                    payload = await self.reader.readexactly(payload_length)
                
                yield message.PeerMessage(msg_id, payload)
                
            except (asyncio.IncompleteReadError, ConnectionError):
                logging.info("Peer disconnected (EOF)")
                break

    async def _handle_message(self, msg):
        if msg.msg_id == message.CHOKE:
            logging.info(f"{self.ip} Choked us")
            self.peer_choking = True
        elif msg.msg_id == message.UNCHOKE:
            logging.info(f"{self.ip} Unchoked us")
            self.peer_choking = False
        elif msg.msg_id == message.INTERESTED:
            self.peer_interested = True
        elif msg.msg_id == message.NOT_INTERESTED:
            self.peer_interested = False
        elif msg.msg_id == message.HAVE:
            index = struct.unpack(">I", msg.payload)[0]
            logging.info(f"{self.ip} Has piece {index}")
        elif msg.msg_id == message.BITFIELD:
            self.bitfield = msg.payload
            logging.info(f"{self.ip} sent BitField")
        elif msg.msg_id == message.REQUEST:
            pass 
        elif msg.msg_id == message.PIECE:
            pass 
        elif msg.msg_id == message.CANCEL:
            pass