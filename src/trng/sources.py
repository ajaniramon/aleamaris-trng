import sys
from typing import Optional

import cv2
import numpy as np


class VideoSource:
    """A frame source.

    physical: frames come from a live sensor (real noise) rather than a recording.
    finite:   read() returning None means the source is exhausted for good.
    """
    name = "video"
    physical = False
    finite = True

    def read(self) -> Optional[np.ndarray]:
        raise NotImplementedError

    def release(self) -> None:
        pass


class FileVideoSource(VideoSource):
    """A recorded video. Deterministic: anyone with the file can replay it, so
    it carries no real entropy. Useful only for demos and pipeline testing."""
    physical = False
    finite = True

    def __init__(self, path: str):
        self.name = f"file:{path}"
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video: {path}")

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def release(self):
        self.cap.release()


class CameraVideoSource(VideoSource):
    """Live camera: sensor shot/thermal noise is a genuine physical source."""
    physical = True
    finite = False

    def __init__(self, index: int = 0):
        self.name = f"camera:{index}"
        # DirectShow only exists on Windows; elsewhere let OpenCV pick (V4L2, AVFoundation...)
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera index {index}")

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def release(self):
        self.cap.release()
