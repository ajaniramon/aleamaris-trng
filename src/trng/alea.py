# src/trng/alea.py
import struct
from typing import Callable
from .chacha_drbg import ChaCha20DRBG

class AleaMaris:
    """RNG de alto nivel con rechazo para evitar sesgo."""
    def __init__(self, seed_provider: Callable[[int], bytes]):
        seed = seed_provider(48)  # pedimos más de 32B para arrancar con holgura
        if len(seed) < 32:
            raise RuntimeError("Not enough seed material")
        self.drbg = ChaCha20DRBG(seed)
        self.generated = 0
        self.reseed_interval_bytes = 1_000_000  # configurable
        self.seed_provider = seed_provider

    def maybe_reseed(self):
        if self.generated >= self.reseed_interval_bytes:
            extra = self.seed_provider(32)
            if extra:
                self.drbg.reseed(extra)
            self.generated = 0

    def random_bytes(self, n: int) -> bytes:
        b = self.drbg.generate(n)
        self.generated += n
        self.maybe_reseed()
        return b

    def rand_u32(self) -> int:
        return struct.unpack(">I", self.random_bytes(4))[0]

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
