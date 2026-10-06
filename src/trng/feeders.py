"""Where the DRBG gets its reseed material from, with per-origin accounting."""
from __future__ import annotations

import os
import threading

from .queue import TrngQueue


class ReseedFeeder:
    """Callable entropy source for AleaMaris.

    Combines, in this order:
      - conditioned TRNG output from the pool (non-blocking),
      - bytes ingested through the API (only ever mixed into the DRBG, never
        served to clients as random output),
      - os.urandom, only if allow_urandom is set.
    """

    def __init__(self, pool: TrngQueue, *, allow_urandom: bool):
        self.pool = pool
        self.allow_urandom = allow_urandom
        self._ingested = bytearray()
        self._lock = threading.Lock()
        self.used = {"trng": 0, "ingested": 0, "urandom": 0}

    def add_ingested(self, data: bytes, cap: int = 1 << 20) -> int:
        with self._lock:
            room = max(0, cap - len(self._ingested))
            self._ingested += data[:room]
            return min(len(data), room)

    def __call__(self, n: int) -> bytes:
        parts = []
        trng = self.pool.poll(n)
        if trng:
            parts.append(trng)
        with self._lock:
            ingested = bytes(self._ingested)
            self._ingested.clear()
            self.used["trng"] += len(trng)
            self.used["ingested"] += len(ingested)
            if ingested:
                parts.append(ingested)
            if self.allow_urandom:
                parts.append(os.urandom(n))
                self.used["urandom"] += n
        if not trng and not ingested and not self.allow_urandom:
            return b""
        # length-prefixed so the parts cannot be confused with each other
        return b"".join(len(p).to_bytes(4, "big") + p for p in parts)
