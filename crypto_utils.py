import os
import hashlib

# BEP 8 Constants
# P is a 768-bit prime
P_HEX = "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A63A3620FFFFFFFFFFFFFFFF"
P = int(P_HEX, 16)
G = 2

class ARC4:
    """
    Pure Python implementation of RC4 (Alleged RC4).
    """
    def __init__(self, key):
        self.x = 0
        self.y = 0
        self.S = list(range(256))
        
        # KSA (Key-Scheduling Algorithm)
        j = 0
        for i in range(256):
            j = (j + self.S[i] + key[i % len(key)]) % 256
            self.S[i], self.S[j] = self.S[j], self.S[i]
            
        # BEP 8 Requirement: Discard first 1024 bytes
        self.process(b'\x00' * 1024)

    def process(self, data):
        """
        Encrypts/Decrypts data (XOR stream).
        """
        out = bytearray(len(data))
        for k, byte in enumerate(data):
            self.x = (self.x + 1) % 256
            self.y = (self.y + self.S[self.x]) % 256
            self.S[self.x], self.S[self.y] = self.S[self.y], self.S[self.x]
            K = self.S[(self.S[self.x] + self.S[self.y]) % 256]
            out[k] = byte ^ K
        return bytes(out)

class DiffieHellman:
    def __init__(self):
        self.private_key = int.from_bytes(os.urandom(20), 'big')
        self.public_key = pow(G, self.private_key, P)
        self.shared_secret = None

    def compute_secret(self, remote_public_bytes):
        remote_pub = int.from_bytes(remote_public_bytes, 'big')
        self.shared_secret = pow(remote_pub, self.private_key, P)
        return self.shared_secret_bytes()

    def public_key_bytes(self):
        return self.public_key.to_bytes(96, 'big') # 768 bits = 96 bytes

    def shared_secret_bytes(self):
        # Format as big-endian bytes
        # Size depends on value, but usually padded to 96
        s_bytes = self.shared_secret.to_bytes((self.shared_secret.bit_length() + 7) // 8, 'big')
        return s_bytes

def get_encryption_keys(s_bytes, info_hash):
    """
    Derives the RC4 keys from the Shared Secret (S) and Info Hash.
    Ref: BEP 8 "Cryptographic Primitives"
    """
    # KeyA = Hash('keyA', S, S_hash)
    # KeyB = Hash('keyB', S, S_hash)
    
    req1 = b'keyA'
    req2 = b'keyB'
    req3 = b'req1'
    req4 = b'req2'
    req5 = b'req3'
    
    # Calculate RC4 keys
    sha_s = hashlib.sha1(s_bytes).digest()
    
    # Outgoing Key (if initiator)
    key_a_hash = hashlib.sha1(req1 + s_bytes + sha_s).digest()
    
    # Incoming Key (if initiator)
    key_b_hash = hashlib.sha1(req2 + s_bytes + sha_s).digest()
    
    return key_a_hash, key_b_hash