import cv2
import numpy as np
from typing import Optional

class VideoSource:
    def read(self) -> Optional[np.ndarray]:
        raise NotImplementedError
    def rewind(self) -> None:
        raise NotImplementedError
    def release(self) -> None:
        raise NotImplementedError

class FileVideoSource(VideoSource):
    def __init__(self, path: str):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError(f"No se pudo abrir el vídeo: {path}")

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        return frame  # BGR uint8

    def rewind(self):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def release(self):
        self.cap.release()

class CameraVideoSource(VideoSource):
    def __init__(self, index: int = 0):
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)  # en Windows ayuda CAP_DSHOW
        if not self.cap.isOpened():
            raise RuntimeError(f"No se pudo abrir la cámara index {index}")

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        return frame

    def rewind(self):
        # no aplica a cámara; no hacemos nada
        pass

    def release(self):
        self.cap.release()
