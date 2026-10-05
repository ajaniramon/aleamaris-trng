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
    # replaying the exact same recording gives the same blocks -> rejected
    c.state = "idle"
    c._block_counter = 0
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
