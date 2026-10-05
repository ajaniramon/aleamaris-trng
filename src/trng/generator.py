"""Entropy collector: frames -> raw noise samples -> health tests -> SHA-256 -> pool.

Entropy accounting per frame:
  h = min(MCV estimate, h_claim) bits per sample
  each 32-byte output block consumes ceil(bits_per_block / h) samples
With the default bits_per_block = 512 every 256-bit block is backed by at
least twice its size in assessed min-entropy.

Samples are health-tested in 512-sample tiles (see health.py); tiles that
fail RCT/APT are discarded and only passing tiles are estimated and used.
A frame with no passing tile is a failure. After `max_consecutive_failures`
failing frames in a row the source is reported as "failed" (it keeps being
tested and recovers on its own if the noise comes back).
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

import numpy as np

from .conditioners import sha256_condition
from .features import diff_samples, to_gray
from .health import HealthTester
from .logging import get_logger
from .queue import TrngQueue
from .sources import VideoSource

log = get_logger("trng.collector")

BLOCK_BYTES = 32


class EntropyCollector:
    def __init__(self,
                 source_factory: Optional[Callable[[], VideoSource]],
                 pool: TrngQueue,
                 *,
                 h_claim: float = 1.0,
                 max_samples: int = 16384,
                 bits_per_block: int = 512,
                 credit_non_physical: bool = False,
                 max_consecutive_failures: int = 30,
                 reopen_delay_sec: float = 5.0,
                 raw_sink: Optional[Callable[[bytes], None]] = None):
        self.source_factory = source_factory
        self.pool = pool
        self.tester = HealthTester(h_claim)
        self.max_samples = max_samples
        self.bits_per_block = bits_per_block
        self.credit_non_physical = credit_non_physical
        self.max_consecutive_failures = max_consecutive_failures
        self.reopen_delay_sec = reopen_delay_sec
        self.raw_sink = raw_sink

        self.source: Optional[VideoSource] = None
        self.source_name: Optional[str] = None
        self.source_physical: Optional[bool] = None
        self._prev: Optional[np.ndarray] = None
        self._block_counter = 0
        self._recent_blocks: OrderedDict[bytes, None] = OrderedDict()
        self._recent_cap = 65536
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # status
        self.state = "idle" if source_factory else "no-source"
        self.frames = 0
        self.frames_failed = 0
        self.tiles = 0
        self.tiles_passed = 0
        self.consecutive_failures = 0
        self.blocks = 0
        self.duplicate_blocks = 0
        self.bits_credited = 0.0
        self.last_h_estimate: Optional[float] = None
        self.last_error: Optional[str] = None

    # ---------- source management ----------
    def open(self) -> bool:
        if self.source is not None:
            return True
        if self.source_factory is None:
            self.state = "no-source"
            return False
        try:
            self.source = self.source_factory()
        except Exception as e:  # camera unplugged, missing file...
            self.state = "unavailable"
            self.last_error = str(e)
            log.warning("entropy source unavailable", extra={"error": str(e)})
            return False
        self._prev = None
        self.source_name = self.source.name
        self.source_physical = bool(self.source.physical)
        self.state = "running"
        log.info("entropy source opened", extra={"source": self.source.name, "physical": self.source.physical})
        return True

    def close(self) -> None:
        if self.source is not None:
            try:
                self.source.release()
            finally:
                self.source = None

    @property
    def credited(self) -> bool:
        return self.source_physical is not None and (self.source_physical or self.credit_non_physical)

    # ---------- core ----------
    def process_samples(self, samples: np.ndarray, *, credit: bool = True) -> list[bytes]:
        """Health-test one batch of samples and return conditioned 32-byte blocks."""
        if self.raw_sink is not None:
            self.raw_sink(samples.tobytes())
        res = self.tester.check(samples)
        self.frames += 1
        self.tiles += res.tiles
        self.tiles_passed += res.tiles_passed
        self.last_h_estimate = res.h_estimate
        if not res.ok:
            self.frames_failed += 1
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_consecutive_failures and self.state != "failed":
                self.state = "failed"
                log.error("entropy source failed health tests", extra={
                    "max_run": res.max_run, "rct_cutoff": self.tester.rct_c,
                    "apt_count": res.apt_count, "apt_cutoff": self.tester.apt_c})
            return []
        self.consecutive_failures = 0
        if self.state == "failed":
            self.state = "running"
            log.info("entropy source recovered")
        if not credit:
            return []
        h = min(res.h_estimate, self.tester.h_claim)
        if h <= 0:
            return []
        good = res.good
        per_block = math.ceil(self.bits_per_block / h)
        out = []
        for i in range(good.size // per_block):
            block = sha256_condition(good[i * per_block:(i + 1) * per_block].tobytes(), self._block_counter)
            self._block_counter += 1
            if self._seen(block):
                # cannot happen with real noise; a replayed source would trigger it
                self.duplicate_blocks += 1
                continue
            out.append(block)
            self.bits_credited += per_block * h
        self.blocks += len(out)
        return out

    def _seen(self, block: bytes) -> bool:
        if block in self._recent_blocks:
            return True
        self._recent_blocks[block] = None
        if len(self._recent_blocks) > self._recent_cap:
            self._recent_blocks.popitem(last=False)
        return False

    def step(self) -> int:
        """Read and process one frame. Returns conditioned bytes produced, or -1
        if the source is gone (exhausted or could not be opened)."""
        with self._lock:
            if not self.open():
                return -1
            frame = self.source.read()
            if frame is None:
                if self.source.finite:
                    self.state = "exhausted"
                    log.info("entropy source exhausted", extra={"source": self.source.name})
                    self.close()
                    return -1
                return 0
            gray = to_gray(frame)
            prev, self._prev = self._prev, gray
            if prev is None or prev.shape != gray.shape:
                return 0
            samples = diff_samples(gray, prev, self.max_samples)
            blocks = self.process_samples(samples, credit=self.credited)
        data = b"".join(blocks)
        if data:
            self.pool.offer(data)
        return len(data)

    # ---------- background thread ----------
    def start(self) -> None:
        if self._thread is not None or self.source_factory is None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="entropy-collector", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        with self._lock:
            self.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.pool.free() < BLOCK_BYTES:
                # pool full: drop the reference frame so the next diff is between
                # two fresh consecutive frames, then wait for consumers
                self._prev = None
                self._stop.wait(0.05)
                continue
            try:
                produced = self.step()
            except Exception as e:
                self.last_error = str(e)
                log.error("collector error", exc_info=True)
                with self._lock:
                    self.close()
                produced = -1
            if produced < 0:
                if self.state == "exhausted":
                    return
                self._stop.wait(self.reopen_delay_sec)

    def status(self) -> dict:
        return {
            "state": self.state,
            "source": self.source_name,
            "physical": self.source_physical,
            "entropy_credited": self.credited,
            "h_claim_bits_per_sample": self.tester.h_claim,
            "last_min_entropy_estimate": self.last_h_estimate,
            "rct_cutoff": self.tester.rct_c,
            "apt_cutoff": self.tester.apt_c,
            "frames": self.frames,
            "frames_failed": self.frames_failed,
            "tile_pass_rate": round(self.tiles_passed / self.tiles, 4) if self.tiles else None,
            "blocks": self.blocks,
            "duplicate_blocks": self.duplicate_blocks,
            "bits_credited": round(self.bits_credited),
            "last_error": self.last_error,
        }

    def wait_for(self, n: int, timeout: float) -> bool:
        """Block until the pool holds n bytes, the source gives up, or timeout."""
        deadline = time.monotonic() + timeout
        while self.pool.available() < n:
            if time.monotonic() >= deadline or self.state in ("exhausted", "no-source"):
                return False
            time.sleep(0.05)
        return True
