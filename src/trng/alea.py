
# src/trng/alea.py
import struct
import threading
from typing import Callable, Optional
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
        self._buf_chunk = 4096 * 1024  # por defecto 4 MiB; configurable
        # lock para seguridad entre hilos cuando calentamos en background
        self._lock = threading.Lock()

    def _fill_buffer(self, need: int, *, chunk_size: Optional[int] = None) -> None:
        # recarga al menos 'need', pero preferimos _buf_chunk
        chunk_pref = chunk_size if chunk_size is not None else self._buf_chunk
        take = max(need, chunk_pref)
        chunk = self.drbg.generate(take)
        # reemplazamos el buffer para no crecer sin control
        self._buf = bytearray(chunk)
        self._buf_pos = 0

    def maybe_reseed(self):
        with self._lock:
            if self.generated >= self.reseed_interval_bytes:
                extra = self.seed_provider(32)
                if extra:
                    self.drbg.reseed(extra)
                self.generated = 0

    def random_bytes(self, n: int) -> bytes:
        if n <= 0:
            return b""
        with self._lock:
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
        # fuera del lock para reducir tiempo bloqueado
        self.maybe_reseed()
        return bytes(out)

    def rand_u32(self) -> int:
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
        with self._lock:
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

    # -------- utilidades de buffer/calor --------
    def set_buffer_chunk_size(self, size: int) -> None:
        """Configura el tamaño objetivo de buffer para recargas. Útil para tuning."""
        if size <= 0:
            return
        with self._lock:
            self._buf_chunk = int(size)

    def buffer_available(self) -> int:
        """Bytes disponibles actualmente en el buffer interno sin generar más."""
        with self._lock:
            return max(0, len(self._buf) - self._buf_pos)

    def warm_buffer(self, ensure_bytes: int, *, chunk_size: Optional[int] = None) -> int:
        """Precalienta el buffer interno para tener al menos ensure_bytes disponibles.
        chunk_size permite sobreescribir el tamaño de generación para evitar pedir picos grandes.
        Devuelve los bytes disponibles tras calentar.
        """
        if ensure_bytes <= 0:
            return self.buffer_available()
        with self._lock:
            avail = len(self._buf) - self._buf_pos
            if avail >= ensure_bytes:
                return avail
            # generamos con chunk_size explícito para evitar el salto a _buf_chunk si queremos más fino
            self._fill_buffer(ensure_bytes, chunk_size=chunk_size)
            return len(self._buf) - self._buf_pos
