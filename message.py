import struct
import logging
from bencoding import Encoder, Decoder

# Message IDs
KEEP_ALIVE = None
CHOKE = 0
UNCHOKE = 1
INTERESTED = 2
NOT_INTERESTED = 3
HAVE = 4
BITFIELD = 5
REQUEST = 6
PIECE = 7
CANCEL = 8
EXTENDED = 20 # BEP 10

# Extension Handshake ID
EXT_HANDSHAKE_ID = 0

class PeerMessage:
    def __init__(self, msg_id, payload=b''):
        self.msg_id = msg_id
        self.payload = payload

    def encode(self) -> bytes:
        if self.msg_id is None:
             return struct.pack(">I", 0)
        
        length = 1 + len(self.payload)
        return struct.pack(">IB", length, self.msg_id) + self.payload

class Handshake:
    def __init__(self, info_hash, peer_id):
        self.info_hash = info_hash
        self.peer_id = peer_id

    def encode(self) -> bytes:
        pstr = b'BitTorrent protocol'
        pstrlen = len(pstr)
        
        # Reserved bytes: 8 bytes.
        # BEP 10 says: set the 20th bit from the right (0x10 in byte 5) to 1.
        # Bytes: 0, 1, 2, 3, 4, 5, 6, 7
        reserved = bytearray(8)
        reserved[5] |= 0x10 # Signal Support for Extension Protocol
        
        return struct.pack("B", pstrlen) + pstr + reserved + self.info_hash + self.peer_id

class ExtendedMessage(PeerMessage):
    """
    BEP 10 Extended Message.
    Format: <Length><ID=20><ExtMsgID><Payload>
    """
    def __init__(self, ext_msg_id, payload):
        # payload here is the raw bytes after the extension ID
        self.ext_msg_id = ext_msg_id
        # We construct the full PeerMessage payload
        # PeerMessage Payload = <ExtMsgID (1 byte)> + <Actual Payload>
        full_payload = struct.pack("B", ext_msg_id) + payload
        super().__init__(EXTENDED, full_payload)

class ExtendedHandshake(ExtendedMessage):
    """
    The dictionary sent to map extension names to IDs.
    """
    def __init__(self):
        data = {
            b'm': {
                b'ut_pex': 1,      # We map PEX to ID 1
                b'ut_metadata': 2 # Preparation for Magnet Links
            }
        }
        encoded = Encoder(data).encode()
        super().__init__(EXT_HANDSHAKE_ID, encoded)

class Request(PeerMessage):
    def __init__(self, index, begin, length=16384):
        payload = struct.pack(">III", index, begin, length)
        super().__init__(REQUEST, payload)

# ... (Previous simple classes Keep Alive, Have, etc. remain implied or use PeerMessage generic)