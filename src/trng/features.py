import cv2
import numpy as np

def to_gray_small(bgr: np.ndarray, size: int) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray_small = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    return gray_small

def laplacian_edges(gray_small: np.ndarray) -> np.ndarray:
    lap = cv2.Laplacian(gray_small, ddepth=cv2.CV_16S, ksize=3)
    edges = np.clip(np.abs(lap) >> 1, 0, 255).astype(np.uint8)
    return edges

def make_features(gray_small: np.ndarray,
                  prev_gray_small: np.ndarray | None,
                  use_diff: bool) -> bytes:
    edges = laplacian_edges(gray_small)
    parts = [gray_small.tobytes(), edges.tobytes()]
    if use_diff and prev_gray_small is not None:
        diff = cv2.absdiff(gray_small, prev_gray_small)
        parts.append(diff.tobytes())
    return b"".join(parts)
