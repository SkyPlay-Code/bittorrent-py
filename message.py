import struct

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

class PeerMessage:
    """
    Represents a message to be sent or received from a peer.
    """
    def __init__(self, msg_id, payload=b''):
        self.msg_id = msg_id
        self.payload = payload

    def encode(self) -> bytes:
        """
        Encodes the message to bytes: <length><id><payload>
        """
        if self.msg_id is None:
             # Keep Alive is just 0000 length prefix
             return struct.pack(">I", 0)
        
        length = 1 + len(self.payload) # 1 byte for ID
        return struct.pack(">IB", length, self.msg_id) + self.payload

class Handshake:
    """
    Handshake: <pstrlen><pstr><reserved><info_hash><peer_id>
    """
    def __init__(self, info_hash, peer_id):
        if len(info_hash) != 20 or len(peer_id) != 20:
             raise ValueError("Info Hash and Peer ID must be 20 bytes long")
        self.info_hash = info_hash
        self.peer_id = peer_id

    def encode(self) -> bytes:
        pstr = b'BitTorrent protocol'
        pstrlen = len(pstr)
        reserved = b'\x00' * 8
        return struct.pack("B", pstrlen) + pstr + reserved + self.info_hash + self.peer_id

class Have(PeerMessage):
    def __init__(self, index):
        payload = struct.pack(">I", index)
        super().__init__(HAVE, payload)

class Request(PeerMessage):
    def __init__(self, index, begin, length=16384):
        payload = struct.pack(">III", index, begin, length)
        super().__init__(REQUEST, payload)

class Piece(PeerMessage):
    def __init__(self, index, begin, block):
        # We construct the payload manually here
        payload = struct.pack(">II", index, begin) + block
        super().__init__(PIECE, payload)

class Cancel(PeerMessage):
    def __init__(self, index, begin, length=16384):
        payload = struct.pack(">III", index, begin, length)
        super().__init__(CANCEL, payload)