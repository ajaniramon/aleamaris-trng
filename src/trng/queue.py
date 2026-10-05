import threading
from collections import deque


class TrngQueue:
    """Thread-safe bounded FIFO of conditioned TRNG bytes.

    Every byte is handed out at most once, even with concurrent readers
    (API worker threads, DRBG reseeds) and the collector thread writing.
    """

    def __init__(self, cap_bytes: int = 1 << 20):
        self._chunks: deque[bytes] = deque()
        self._lock = threading.Lock()
        self.cap = cap_bytes
        self._size = 0
        self.total_in = 0
        self.total_out = 0

    def offer(self, data: bytes) -> int:
        """Append as much of `data` as fits. Returns bytes accepted."""
        if not data:
            return 0
        with self._lock:
            room = self.cap - self._size
            if room <= 0:
                return 0
            chunk = bytes(data[:room])
            self._chunks.append(chunk)
            self._size += len(chunk)
            self.total_in += len(chunk)
            return len(chunk)

    def poll(self, count: int, *, exact: bool = False) -> bytes:
        """Remove and return up to `count` bytes (all `count` or nothing if exact)."""
        if count <= 0:
            return b""
        with self._lock:
            if self._size == 0 or (exact and self._size < count):
                return b""
            n = min(count, self._size)
            out = bytearray()
            while len(out) < n:
                head = self._chunks[0]
                need = n - len(out)
                if len(head) <= need:
                    out += head
                    self._chunks.popleft()
                else:
                    out += head[:need]
                    self._chunks[0] = head[need:]
            self._size -= n
            self.total_out += n
            return bytes(out)

    def available(self) -> int:
        with self._lock:
            return self._size

    def free(self) -> int:
        with self._lock:
            return self.cap - self._size
