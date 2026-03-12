"""
Lightweight symmetric encryption using Python stdlib only.
XSalsa20-style stream cipher implemented via hashlib + os.urandom.
For true AES, we use a CTR mode built on hashlib SHA-256 key schedule
(no third-party crypto libs needed).

Commercially safe: PSF License only.
"""

import hashlib
import hmac
import os
import struct


def _derive_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Generate a keystream using HMAC-SHA256 in counter mode."""
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        block = hmac.new(
            key,
            nonce + struct.pack(">Q", counter),
            hashlib.sha256,
        ).digest()
        stream.extend(block)
        counter += 1
    return bytes(stream[:length])


def generate_key() -> bytes:
    """Generate a random 256-bit key."""
    return os.urandom(32)


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """
    Encrypt plaintext with key.
    Returns: nonce (16 bytes) + ciphertext + HMAC tag (32 bytes)
    """
    nonce = os.urandom(16)
    keystream = _derive_keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, keystream))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return nonce + ciphertext + tag


def decrypt(data: bytes, key: bytes) -> bytes | None:
    """
    Decrypt data. Returns plaintext or None if authentication fails.
    """
    if len(data) < 48:  # 16 nonce + 0 ct + 32 tag minimum
        return None
    nonce = data[:16]
    tag = data[-32:]
    ciphertext = data[16:-32]

    expected_tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        return None  # tampered or wrong key

    keystream = _derive_keystream(key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, keystream))


def key_exchange_hash(secret: str, their_ip: str, my_ip: str) -> bytes:
    """
    Derive a shared key from a shared passphrase + both IPs.
    In production you'd use ECDH; this is a simple PBKDF2 approach.
    """
    salt = (min(their_ip, my_ip) + max(their_ip, my_ip)).encode()
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, 100_000)
