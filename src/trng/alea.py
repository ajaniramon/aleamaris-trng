"""High-level RNG on top of the DRBG: thread-safe bytes, unbiased integers,
and cheap per-request child generators for massive streams."""
from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

from .chacha_drbg import ChaCha20DRBG

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
# Above this we split generate() calls so one huge request never holds the lock long.
_LOCKED_CHUNK = 1 << 20


def randbelow(random_bytes: Callable[[int], bytes], n: int) -> int:
    """Uniform integer in [0, n) for any n > 0 (arbitrary precision).

    Bitmask rejection sampling: each draw is accepted with probability > 1/2,
    so this always terminates quickly, whatever the size of n.
    """
    if n <= 0:
        raise ValueError("n must be > 0")
    k = n.bit_length()
    nbytes = (k + 7) // 8
    shift = nbytes * 8 - k
    while True:
        x = int.from_bytes(random_bytes(nbytes), "big") >> shift
        if x < n:
            return x


def randints(random_bytes: Callable[[int], bytes], lo: int, hi: int, count: int) -> np.ndarray:
    """`count` uniform int64 values in [lo, hi] (both inclusive), vectorized.

    lo and hi must fit in int64. Uses 32-bit draws when the span allows it.
    """
    if lo > hi:
        raise ValueError("lo must be <= hi")
    if lo < INT64_MIN or hi > INT64_MAX:
        raise ValueError("bounds must fit in int64")
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    span = hi - lo + 1  # 1 .. 2**64
    if span == 1:
        return np.full(count, lo, dtype=np.int64)

    bits = 32 if span <= (1 << 32) else 64
    dtype = np.dtype("<u4") if bits == 32 else np.dtype("<u8")
    full = 1 << bits
    limit = full - (full % span)  # accept x < limit; == full when span divides 2**bits
    exact = limit == full

    out = np.empty(count, dtype=np.uint64)
    filled = 0
    while filled < count:
        need = count - filled
        # expected acceptance = limit/full >= 1/2; over-draw a little to usually finish in one pass
        draw = need if exact else int(need * full / limit * 1.02) + 16
        x = np.frombuffer(random_bytes(draw * dtype.itemsize), dtype=dtype)
        if not exact:
            x = x[x < limit]
        take = min(len(x), need)
        chunk = x[:take].astype(np.uint64)
        if span != full:
            chunk %= np.uint64(span)
        out[filled:filled + take] = chunk
        filled += take
    # lo + offset in two's complement; wraps correctly because the result fits int64
    out += np.uint64(lo & 0xFFFFFFFFFFFFFFFF)
    return out.view(np.int64)


class AleaMaris:
    """Thread-safe DRBG front-end.

    `entropy_source(n)` is polled (non-blocking) for fresh entropy whenever
    `reseed_interval_bytes` have been produced; it may return b"".
    """

    def __init__(self, seed: bytes, *,
                 reseed_interval_bytes: int = 1 << 26,
                 entropy_source: Optional[Callable[[int], bytes]] = None,
                 reseed_bytes: int = 64):
        self._drbg = ChaCha20DRBG(seed)
        self._lock = threading.Lock()
        self.reseed_interval_bytes = reseed_interval_bytes
        self.reseed_bytes = reseed_bytes
        self.entropy_source = entropy_source
        self.generated_since_reseed = 0
        self.reseed_count = 0
        self.children_forked = 0

    # ---- bytes ----
    def random_bytes(self, n: int) -> bytes:
        if n <= 0:
            return b""
        if n <= _LOCKED_CHUNK:
            with self._lock:
                out = self._drbg.generate(n)
                self._account(n)
            return out
        parts = []
        remaining = n
        while remaining > 0:
            take = min(remaining, _LOCKED_CHUNK)
            parts.append(self.random_bytes(take))
            remaining -= take
        return b"".join(parts)

    def _account(self, n: int) -> None:
        # caller holds the lock
        self.generated_since_reseed += n
        if self.generated_since_reseed >= self.reseed_interval_bytes:
            self._reseed_from_source_locked()

    # ---- reseeding ----
    def reseed(self, entropy: bytes) -> None:
        if not entropy:
            return
        with self._lock:
            self._drbg.reseed(entropy)
            self.generated_since_reseed = 0
            self.reseed_count += 1

    def reseed_from_source(self) -> int:
        """Pull fresh entropy from the source (if any). Returns bytes mixed in."""
        with self._lock:
            return self._reseed_from_source_locked()

    def _reseed_from_source_locked(self) -> int:
        if self.entropy_source is None:
            return 0
        fresh = self.entropy_source(self.reseed_bytes)
        if not fresh:
            # nothing new: keep counting so we retry on the next call
            return 0
        self._drbg.reseed(fresh)
        self.generated_since_reseed = 0
        self.reseed_count += 1
        return len(fresh)

    # ---- children for streaming ----
    def fork(self) -> ChaCha20DRBG:
        """Independent DRBG seeded from this one. Use it for one big stream so
        the shared lock is touched exactly once per request."""
        seed = self.random_bytes(32)
        with self._lock:
            self.children_forked += 1
        return ChaCha20DRBG(seed)

    # ---- integers ----
    def randbelow(self, n: int) -> int:
        return randbelow(self.random_bytes, n)

    def randint(self, a: int, b: int) -> int:
        if a > b:
            raise ValueError("a must be <= b")
        return a + self.randbelow(b - a + 1)

    def randints(self, lo: int, hi: int, count: int) -> np.ndarray:
        return randints(self.random_bytes, lo, hi, count)

    def stats(self) -> dict:
        with self._lock:
            return {
                "generated_bytes_since_last_reseed": self.generated_since_reseed,
                "reseed_interval_bytes": self.reseed_interval_bytes,
                "reseed_bytes": self.reseed_bytes,
                "reseed_count": self.reseed_count,
                "children_forked": self.children_forked,
            }
