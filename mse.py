import asyncio
import os
import struct
import hashlib
from crypto_utils import ARC4, DiffieHellman, get_encryption_keys

# Verification Constant
VC = b'\x00' * 8 
CRYPTO_PROVIDE = b'\x00\x00\x00\x02' # 0x02 indicates RC4 support

class EncryptedConnection:
    """
    Wraps reader/writer to provide transparent encryption.
    """
    def __init__(self, reader, writer, encryptor, decryptor):
        self._reader = reader
        self._writer = writer
        self.encryptor = encryptor
        self.decryptor = decryptor

    async def readexactly(self, n):
        data = await self._reader.readexactly(n)
        return self.decryptor.process(data)

    async def read(self, n):
        data = await self._reader.read(n)
        return self.decryptor.process(data)

    def write(self, data):
        encrypted = self.encryptor.process(data)
        self._writer.write(encrypted)

    async def drain(self):
        await self._writer.drain()

    def close(self):
        self._writer.close()

async def perform_mse_handshake(reader, writer, info_hash):
    """
    Performs the BEP 8 handshake as Initiator.
    Returns an EncryptedConnection object if successful.
    """
    try:
        # 1. Generate DH Keypair
        dh = DiffieHellman()
        
        # 2. Send Public Key + Padding
        # Padding is random 0-512 bytes
        pad_len = os.urandom(1)[0] % 200 # Keep it reasonable
        padding = os.urandom(pad_len)
        writer.write(dh.public_key_bytes() + padding)
        await writer.drain()

        # 3. Receive Remote Public Key
        # This is tricky: remote might send padding.
        # However, BEP 8 says initiator shouldn't wait for padding, 
        # but pure streams make detecting boundaries hard.
        # Ideally we read 96 bytes (Key) then try to sync.
        
        # Wait for at least 96 bytes
        remote_pub_bytes = await reader.readexactly(96)
        
        # Calculate Shared Secret
        s_bytes = dh.compute_secret(remote_pub_bytes)
        
        # Derive RC4 Keys
        # For Initiator: encrypt using keyA, decrypt using keyB
        key_a, key_b = get_encryption_keys(s_bytes, info_hash)
        encryptor = ARC4(key_a)
        decryptor = ARC4(key_b)

        # 4. Synchronization (Step 4 in BEP 8)
        # We need to read until we find the synchronization hash.
        # But wait, as initiator, we SEND the sync hash first?
        # BEP 8: "The initiator sends: Hash('req1', S) ..."
        
        req1_hash = hashlib.sha1(b'req1' + s_bytes).digest()
        req2_hash = hashlib.sha1(b'req2' + s_bytes).digest()
        req3_hash = hashlib.sha1(b'req3' + s_bytes).digest()
        
        # XOR req2 and req3
        xor_hash = bytes(a ^ b for a, b in zip(req2_hash, req3_hash))
        
        # 5. Send ENCRYPTION_SETUP
        # Structure: H('req1', S) + (H('req2', S) ^ H('req3', S)) + ENCRYPT(VC, provide, len(pad), pad, len(IA), IA)
        # We will put the BitTorrent Handshake in IA (Initial Payload) later in peer.py
        # For now, IA is empty 
        
        vc_payload = VC + CRYPTO_PROVIDE + b'\x00\x00' # Zero pad len + zero pad
        # IA Length (0) for now
        vc_payload += b'\x00\x00' 
        
        encrypted_vc = encryptor.process(vc_payload)
        
        writer.write(req1_hash + xor_hash + encrypted_vc)
        await writer.drain()

        # 6. Receive ENCRYPTION_SETUP from Receiver
        # Receiver sends: VC + crypto_select + len(pad) + pad
        # This stream is ENCRYPTED with keyB.
        # But first, we might have to read through their padding from Step 3.
        # This is the "Synchronization" phase.
        
        # We need to find VC (8 bytes of 0x00) in the decrypted stream.
        # Since we don't know how much padding they sent after their PubKey,
        # we try decrypting incoming bytes until we hit VC.
        # Max padding is 512.
        
        # Simplified sync strategy:
        # Read small chunks, decrypt, look for VC.
        
        buffer = b''
        synced = False
        attempts = 0
        
        while not synced and attempts < 600:
            byte = await reader.read(1)
            if not byte: raise ConnectionError("Connection closed during MSE")
            
            dec_byte = decryptor.process(byte)
            buffer += dec_byte
            
            if buffer.endswith(VC):
                # Found the verification constant!
                # The rest of buffer might contain crypto_select etc.
                synced = True
                
                # Consume crypto_select (4 bytes) + len(pad) (2 bytes)
                # We need to read more if we don't have it
                remaining_header = 4 + 2
                
                header_data = b''
                while len(header_data) < remaining_header:
                    b = await reader.read(1)
                    header_data += decryptor.process(b)
                
                # Check len(pad)
                pad_len = struct.unpack(">H", header_data[4:6])[0]
                
                # Consume pad
                if pad_len > 0:
                    pad_read = 0
                    while pad_read < pad_len:
                        b = await reader.read(1)
                        decryptor.process(b) # discard
                        pad_read += 1
                        
                # We are now synced and ready for IA (Initial Payload) or stream.
                return EncryptedConnection(reader, writer, encryptor, decryptor)
            
            attempts += 1
            
        raise ConnectionError("Failed to synchronize MSE stream")
        
    except Exception as e:
        # logging.debug(f"MSE Handshake failed: {e}")
        return None