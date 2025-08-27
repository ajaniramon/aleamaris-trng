
from __future__ import annotations
import struct, hmac, hashlib
from typing import Optional

# Optional libsodium (PyNaCl) acceleration
try:
    from nacl.bindings import (
        crypto_stream_chacha20_ietf_xor_ic,
        crypto_stream_chacha20_ietf_NONCEBYTES as SODIUM_NONCEBYTES,
        crypto_stream_chacha20_ietf_KEYBYTES as SODIUM_KEYBYTES,
    )
    _HAS_SODIUM = True
except Exception:
    _HAS_SODIUM = False

def _rotl32(x, n): return ((x << n) & 0xffffffff) | (x >> (32 - n))

def _qr(a, b, c, d):
    a = (a + b) & 0xffffffff; d ^= a; d = _rotl32(d, 16)
    c = (c + d) & 0xffffffff; b ^= c; b = _rotl32(b, 12)
    a = (a + b) & 0xffffffff; d ^= a; d = _rotl32(d, 8)
    c = (c + d) & 0xffffffff; b ^= c; b = _rotl32(b, 7)
    return a, b, c, d

def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    # key: 32 bytes, counter: 64-bit, nonce: 12 bytes
    const = b"expand 32-byte k"
    state = list(struct.unpack("<4I", const) +
                 struct.unpack("<8I", key) +
                 (counter & 0xffffffff, (counter >> 32) & 0xffffffff) +
                 struct.unpack("<3I", nonce))
    working = state[:]
    for _ in range(10):  # 20 rounds (10 double)
        # column rounds
        working[0], working[4], working[8],  working[12] = _qr(working[0], working[4], working[8],  working[12])
        working[1], working[5], working[9],  working[13] = _qr(working[1], working[5], working[9],  working[13])
        working[2], working[6], working[10], working[14] = _qr(working[2], working[6], working[10], working[14])
        working[3], working[7], working[11], working[15] = _qr(working[3], working[7], working[11], working[15])
        # diagonal rounds
        working[0], working[5], working[10], working[15] = _qr(working[0], working[5], working[10], working[15])
        working[1], working[6], working[11], working[12] = _qr(working[1], working[6], working[11], working[12])
        working[2], working[7], working[8],  working[13] = _qr(working[2], working[7], working[8],  working[13])
        working[3], working[4], working[9],  working[14] = _qr(working[3], working[4], working[9],  working[14])
    out = [(working[i] + state[i]) & 0xffffffff for i in range(16)]
    return struct.pack("<16I", *out)

def _hkdf_mix(key: bytes, data: bytes, out_len: int) -> bytes:
    # HKDF-like: extract+expand con HMAC-SHA256
    prk = hmac.new(key, data, hashlib.sha256).digest()
    t = b""; out = b""; counter = 1
    while len(out) < out_len:
        t = hmac.new(prk, t + b"" + bytes([counter]), hashlib.sha256).digest()
        out += t; counter += 1
    return out[:out_len]

class _PureChaCha20DRBG:
    """
    Versión pura en Python (fallback). Mantiene compat API.
    Estado: key(32), nonce(12), counter(64)
    """
    def __init__(self, seed: bytes):
        if len(seed) < 32:
            raise ValueError("seed must be >= 32 bytes")
        material = _hkdf_mix(b"\x00"*32, seed, 44)  # 32+12
        self.key   = material[:32]
        self.nonce = material[32:44]
        self.counter = 0  # 64-bit block counter

    def generate(self, n: int) -> bytes:
        out = bytearray()
        # batch por bloques de 64 bytes
        blocks = (n + 63) // 64
        for _ in range(blocks):
            out.extend(_chacha20_block(self.key, self.counter, self.nonce))
            self.counter = (self.counter + 1) & ((1<<64)-1)
        return bytes(out[:n])

    def reseed(self, entropy: bytes):
        if not entropy:
            return
        material = _hkdf_mix(self.key, entropy + struct.pack("<Q", self.counter), 44)
        self.key   = material[:32]
        self.nonce = material[32:44]
        self.counter = 0

class _SodiumChaCha20DRBG:
    """
    Implementación acelerada con libsodium (PyNaCl).
    Usa chacha20-ietf (nonce 12, counter 32/64-bit ic).
    """
    def __init__(self, seed: bytes):
        if len(seed) < 32:
            raise ValueError("seed must be >= 32 bytes")
        material = _hkdf_mix(b"\x00"*32, seed, 44)  # 32+12
        self.key   = material[:32]
        self.nonce = material[32:44]
        self.counter = 0  # ic: bloque inicial (en bloques de 64 bytes)

    def generate(self, n: int) -> bytes:
        out = bytearray()
        remaining = n
        # chunks grandes para minimizar overhead (256 KiB)
        CHUNK = 1024 * 1024
        while remaining > 0:
            take = CHUNK if remaining > CHUNK else remaining
            zeros = b"\x00" * take
            ct = crypto_stream_chacha20_ietf_xor_ic(zeros, self.nonce, self.counter, self.key)
            out.extend(ct)
            # avanzar contador en bloques de 64 bytes
            self.counter += (take + 63) // 64
            remaining -= take
        return bytes(out[:n])

    def reseed(self, entropy: bytes):
        if not entropy:
            return
        material = _hkdf_mix(self.key, entropy + struct.pack("<Q", self.counter), 44)
        self.key   = material[:32]
        self.nonce = material[32:44]
        self.counter = 0

class ChaCha20DRBG:
    """
    DRBG minimal con backend auto-seleccionado:
      - Si hay libsodium → _SodiumChaCha20DRBG (rápido)
      - Si no → _PureChaCha20DRBG (fallback)
    API: generate(n) -> bytes, reseed(entropy)
    """
    def __init__(self, seed: bytes):
        self._impl = _SodiumChaCha20DRBG(seed) if _HAS_SODIUM else _PureChaCha20DRBG(seed)

    def generate(self, n: int) -> bytes:
        return self._impl.generate(n)

    def reseed(self, entropy: bytes):
        self._impl.reseed(entropy)
