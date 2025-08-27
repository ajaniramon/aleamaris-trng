
# src/trng/alea.py
import struct
from typing import Callable
from .chacha_drbg import ChaCha20DRBG
import numpy as np

class AleaMaris:
    """RNG de alto nivel con rechazo (sin sesgo) y buffering para rendimiento."""
    def __init__(self, seed_provider: Callable[[int], bytes]):
        seed = seed_provider(48)  # pedimos >32B para arrancar con holgura
        if len(seed) < 32:
            raise RuntimeError("Not enough seed material")
        self.drbg = ChaCha20DRBG(seed)
        self.generated = 0
        self.reseed_interval_bytes = 1_000_000  # configurable
        self.seed_provider = seed_provider
        # buffer interno para reducir llamadas al DRBG
        self._buf = bytearray()
        self._buf_pos = 0
        self._buf_chunk = 4096 * 1024

    def _fill_buffer(self, need: int):
        # recarga al menos 'need', pero preferimos _buf_chunk
        take = max(need, self._buf_chunk)
        chunk = self.drbg.generate(take)
        # reemplazamos el buffer para no crecer sin control
        self._buf = bytearray(chunk)
        self._buf_pos = 0

    def maybe_reseed(self):
        if self.generated >= self.reseed_interval_bytes:
            extra = self.seed_provider(32)
            if extra:
                self.drbg.reseed(extra)
            self.generated = 0

    def random_bytes(self, n: int) -> bytes:
        if n <= 0:
            return b""
        out = bytearray()
        while len(out) < n:
            available = len(self._buf) - self._buf_pos
            need = n - len(out)
            if available <= 0:
                self._fill_buffer(need)
                available = len(self._buf) - self._buf_pos
            take = need if need < available else available
            out.extend(self._buf[self._buf_pos:self._buf_pos+take])
            self._buf_pos += take
        self.generated += n
        self.maybe_reseed()
        return bytes(out)

    def rand_u32(self) -> int:
        # Evita 100k llamadas al DRBG: tira del buffer
        b = self.random_bytes(4)
        return struct.unpack(">I", b)[0]

    def randrange(self, n: int) -> int:
        if n <= 0: raise ValueError("n must be > 0")
        limit = (1<<32) - ((1<<32) % n)
        while True:
            x = self.rand_u32()
            if x < limit:
                return x % n

    def randint(self, a: int, b: int) -> int:
        if a > b: raise ValueError("a must be <= b")
        return a + self.randrange(b - a + 1)

    def reseed(self, entropy: bytes):
        self.drbg.reseed(entropy)

    # Método opcional: batch de u32 para máxima velocidad
    def rand_u32_array(self, count: int):
        """Devuelve un np.ndarray dtype='>u4' (big-endian) sin bucles de Python."""
        if count <= 0:
            return np.empty(0, dtype='>u4')
        raw = self.random_bytes(count * 4)
        arr = np.frombuffer(raw, dtype='>u4', count=count)
        return arr

    def rand_u32_batch(self, count: int) -> list[int]:
        if count <= 0:
            return []
        raw = self.random_bytes(count * 4)
        # desempaqueta en bloque
        ints = list(struct.unpack(">" + "I"*count, raw))
        return ints
