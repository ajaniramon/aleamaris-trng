import numpy as np
import pytest

from trng.sources import VideoSource


class NoiseCamera(VideoSource):
    """Fake live camera: a fixed scene plus Gaussian sensor noise per frame."""
    name = "fake-camera"
    physical = True
    finite = False

    def __init__(self, sigma: float = 3.0, shape=(240, 320), seed: int = 1):
        self.rng = np.random.default_rng(seed)
        self.scene = self.rng.integers(40, 200, size=shape).astype(np.float64)
        self.sigma = sigma

    def read(self):
        frame = self.scene + self.rng.normal(0, self.sigma, self.scene.shape)
        return np.clip(frame, 0, 255).astype(np.uint8)


class FrozenCamera(VideoSource):
    """Lens cap on / stuck sensor: every frame identical."""
    name = "frozen-camera"
    physical = True
    finite = False

    def read(self):
        return np.full((240, 320), 17, dtype=np.uint8)


@pytest.fixture
def noise_camera():
    return NoiseCamera
