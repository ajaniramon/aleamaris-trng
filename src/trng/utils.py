import math


def shannon_entropy_per_byte(data: bytes) -> float:
    """Shannon entropy of the byte histogram. Only meaningful on *raw* samples;
    on hashed/DRBG output it is always ~8 and tells you nothing."""
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


class RawSampleWriter:
    """Appends raw noise samples to a file, one byte per sample, so they can be
    fed to NIST's SP 800-90B tools (ea_non_iid / ea_iid) for offline assessment."""

    def __init__(self, path: str, limit_bytes: int | None = None):
        self._f = open(path, "wb")
        self.limit = limit_bytes
        self.written = 0

    def __call__(self, data: bytes) -> None:
        if self.limit is not None:
            data = data[: max(0, self.limit - self.written)]
        if data:
            self._f.write(data)
            self.written += len(data)

    def close(self) -> None:
        self._f.close()
