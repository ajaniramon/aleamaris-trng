"""ChaCha20-based DRBG with fast key erasure.

Design (same idea as the Linux kernel CRNG / "fast-key-erasure RNGs"):

- State is a single 256-bit key.
- generate(n): run ChaCha20 under the current key, use the first 32 bytes of
  keystream as the *next* key and return the rest. The old key is gone, so a
  later state compromise cannot reproduce output that was already returned
  (backtracking resistance).
- reseed(entropy): key = HMAC-SHA256(key, entropy). Mixing can never reduce
  the entropy already in the state, so it is safe to feed untrusted input.

The ChaCha20 core comes from `cryptography` (OpenSSL), not a hand-rolled one.
"""
from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

KEY_BYTES = 32
MIN_SEED_BYTES = 32
# ChaCha20 in `cryptography` uses a 32-bit block counter (256 GiB per key);
# we rekey on every call and cap a single call well below that.
MAX_REQUEST_BYTES = 1 << 30
_ZERO_NONCE = b"\x00" * 16  # counter(4) || nonce(12); safe because the key never repeats


class ChaCha20DRBG:
    def __init__(self, seed: bytes):
        if len(seed) < MIN_SEED_BYTES:
            raise ValueError(f"seed must be >= {MIN_SEED_BYTES} bytes")
        self._key = hmac.new(b"AleaMaris/ChaCha20DRBG/seed", seed, hashlib.sha256).digest()

    def generate(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("n must be >= 0")
        if n > MAX_REQUEST_BYTES:
            raise ValueError(f"n must be <= {MAX_REQUEST_BYTES}; request in chunks")
        enc = Cipher(algorithms.ChaCha20(self._key, _ZERO_NONCE), mode=None).encryptor()
        stream = enc.update(bytes(KEY_BYTES + n))
        self._key = stream[:KEY_BYTES]
        return stream[KEY_BYTES:]

    def reseed(self, entropy: bytes) -> None:
        if not entropy:
            return
        self._key = hmac.new(self._key, b"reseed" + entropy, hashlib.sha256).digest()
