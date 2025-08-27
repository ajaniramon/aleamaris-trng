from collections import deque

class TrngQueue:
    """
    FIFO por bloques byte[] sin boxing; simple y suficiente para la API.
    CAP_BYTES controla el tamaño máximo acumulado.
    """
    def __init__(self, cap_bytes: int = 1_000_000):
        self.chunks: deque[bytes] = deque()
        self.cap = cap_bytes
        self.size = 0

    def offer(self, data: bytes) -> int:
        room = self.cap - self.size
        if room <= 0:
            return 0
        to_write = min(len(data), room)
        if to_write <= 0:
            return 0
        if to_write == len(data):
            self.chunks.append(data)
            self.size += to_write
            return to_write
        # si no cabe entero, truncamos
        self.chunks.append(data[:to_write])
        self.size += to_write
        return to_write

    def poll(self, count: int) -> bytes:
        if self.size == 0 or count <= 0:
            return b""
        n = min(count, self.size)
        out = bytearray()
        remaining = n
        while remaining > 0 and self.chunks:
            head = self.chunks[0]
            if len(head) <= remaining:
                out += head
                self.size -= len(head)
                remaining -= len(head)
                self.chunks.popleft()
            else:
                out += head[:remaining]
                self.chunks[0] = head[remaining:]
                self.size -= remaining
                remaining = 0
        return bytes(out)

    def available(self) -> int:
        return self.size
