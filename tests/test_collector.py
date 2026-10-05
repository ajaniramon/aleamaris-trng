from conftest import FrozenCamera, NoiseCamera

from trng.generator import EntropyCollector
from trng.queue import TrngQueue
from trng.sources import VideoSource


def run(collector, frames):
    for _ in range(frames):
        if collector.step() < 0:
            break


def test_noise_camera_produces_conditioned_blocks():
    pool = TrngQueue()
    c = EntropyCollector(lambda: NoiseCamera(), pool)
    run(c, 5)
    st = c.status()
    assert st["state"] == "running" and st["physical"] and st["entropy_credited"]
    assert st["blocks"] > 0 and pool.available() == 32 * st["blocks"]
    # every block backed by >= 512 credited bits
    assert st["bits_credited"] >= 512 * st["blocks"]


def test_frozen_camera_produces_nothing_and_fails():
    pool = TrngQueue()
    c = EntropyCollector(lambda: FrozenCamera(), pool, max_consecutive_failures=3)
    run(c, 10)
    assert pool.available() == 0
    assert c.status()["state"] == "failed"


def test_recovers_after_failure():
    cams = iter([FrozenCamera(), NoiseCamera()])
    pool = TrngQueue()
    c = EntropyCollector(lambda: next(cams), pool, max_consecutive_failures=2)
    run(c, 4)
    assert c.state == "failed"
    c.close()  # e.g. camera re-plugged
    run(c, 4)
    assert c.state == "running" and pool.available() > 0


class Replay(VideoSource):
    """A recording: not physical, finite."""
    name = "replay"
    physical = False
    finite = True

    def __init__(self, frames=5):
        cam = NoiseCamera(seed=7)
        self.frames = [cam.read() for _ in range(frames)]

    def read(self):
        return self.frames.pop(0) if self.frames else None


def test_recording_not_credited_by_default():
    pool = TrngQueue()
    c = EntropyCollector(lambda: Replay(), pool)
    run(c, 10)
    assert pool.available() == 0
    assert c.status()["state"] == "exhausted" and not c.credited


def test_recording_demo_mode_and_replay_detection():
    pool = TrngQueue(cap_bytes=1 << 24)
    c = EntropyCollector(lambda: Replay(), pool, credit_non_physical=True)
    run(c, 10)
    first = pool.available()
    assert first > 0
    # replaying the same recording (block counter keeps going) -> rejected
    c.state = "idle"
    run(c, 10)
    assert pool.available() == first
    assert c.duplicate_blocks > 0


def test_background_thread_fills_pool_and_stops():
    pool = TrngQueue(cap_bytes=4096)
    c = EntropyCollector(lambda: NoiseCamera(), pool)
    c.start()
    assert c.wait_for(4096, timeout=10)
    c.stop()
    assert c._thread is None


class DyingCamera(NoiseCamera):
    """Live camera that stops delivering frames after a while (unplugged)."""

    def __init__(self, good_frames=3):
        super().__init__()
        self.left = good_frames

    def read(self):
        self.left -= 1
        return super().read() if self.left >= 0 else None


def test_live_camera_that_stops_delivering_is_reported_and_reopened():
    opened = []

    def factory():
        cam = DyingCamera()
        opened.append(cam)
        return cam

    c = EntropyCollector(factory, TrngQueue(), max_read_failures=5)
    results = [c.step() for _ in range(3 + 5)]
    assert results[-1] == -1
    assert c.state == "unavailable" and c.source is None
    c.step()  # next attempt reopens the camera
    assert len(opened) == 2 and c.state == "running"


def test_pool_admits_whole_blocks_and_credits_only_those():
    pool = TrngQueue(cap_bytes=100)  # room for 3 blocks + 4 stray bytes
    c = EntropyCollector(lambda: NoiseCamera(), pool)
    run(c, 3)
    assert pool.available() == 96
    st = c.status()
    assert st["blocks"] == 3
    assert 3 * 512 <= st["bits_credited"] < 4 * 512
    assert st["blocks_dropped_pool_full"] > 0
