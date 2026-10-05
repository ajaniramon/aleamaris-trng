"""Turn video frames into raw noise samples.

The sample is the temporal difference of each pixel between two consecutive
frames (mod 256), on a sparse grid at native resolution. No resizing/blurring:
averaging pixels averages away exactly the sensor noise we want to measure.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def grid_step(shape: tuple[int, int], max_samples: int) -> int:
    h, w = shape
    return max(1, math.ceil(math.sqrt(h * w / max_samples)))


def diff_samples(gray: np.ndarray, prev_gray: np.ndarray, max_samples: int) -> np.ndarray:
    """uint8 samples: (gray - prev_gray) mod 256 on a grid of at most max_samples pixels."""
    step = grid_step(gray.shape, max_samples)
    cur = gray[::step, ::step].astype(np.int16)
    prev = prev_gray[::step, ::step].astype(np.int16)
    return ((cur - prev) & 0xFF).astype(np.uint8).ravel()[:max_samples]
