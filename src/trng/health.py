"""Online health tests and a min-entropy estimator for raw noise samples.

Follows NIST SP 800-90B:
- 4.4.1 Repetition Count Test (RCT)
- 4.4.2 Adaptive Proportion Test (APT), non-binary window W = 512
- 6.3.1 Most Common Value (MCV) min-entropy estimate

Both tests use a false-positive rate of alpha = 2**-20 for a source that
really delivers `h_claim` bits of min-entropy per sample. Samples are uint8.

Frames are tested in tiles of APT_WINDOW consecutive samples: a region with
no noise (clipped highlights, black bars, codec-static areas) fails and is
discarded on its own instead of sinking the whole frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

ALPHA_LOG2 = 20  # alpha = 2**-20
APT_WINDOW = 512


def rct_cutoff(h_claim: float) -> int:
    return 1 + math.ceil(ALPHA_LOG2 / h_claim)


def apt_cutoff(h_claim: float, window: int = APT_WINDOW) -> int:
    """1 + smallest k with P(Binomial(window, 2**-h) <= k) >= 1 - alpha."""
    p = 2.0 ** -h_claim
    if p >= 1.0:
        return window + 1
    alpha = 2.0 ** -ALPHA_LOG2
    log_p, log_q = math.log(p), math.log1p(-p)
    cdf = 0.0
    for k in range(window + 1):
        log_pmf = (math.lgamma(window + 1) - math.lgamma(k + 1) - math.lgamma(window - k + 1)
                   + k * log_p + (window - k) * log_q)
        cdf += math.exp(log_pmf)
        if cdf >= 1.0 - alpha:
            return 1 + k
    return window + 1


def max_run_length(samples: np.ndarray) -> int:
    if samples.size == 0:
        return 0
    change = np.flatnonzero(samples[1:] != samples[:-1]) + 1
    bounds = np.concatenate(([0], change, [samples.size]))
    return int(np.max(np.diff(bounds)))


def apt_max_count(samples: np.ndarray, window: int = APT_WINDOW) -> int:
    """Max, over complete windows, of how often the window's first sample appears in it."""
    nwin = samples.size // window
    if nwin == 0:
        return 0
    w = samples[: nwin * window].reshape(nwin, window)
    return int(np.max(np.sum(w == w[:, :1], axis=1)))


def tile_max_runs(tiles: np.ndarray) -> np.ndarray:
    """Longest run of identical consecutive values in each row of a 2-D array."""
    same = tiles[:, 1:] == tiles[:, :-1]
    idx = np.arange(same.shape[1])
    last_break = np.maximum.accumulate(np.where(~same, idx, -1), axis=1)
    return (idx - last_break).max(axis=1) + 1


def mcv_min_entropy(samples: np.ndarray) -> float:
    """MCV estimate (bits/sample) using the 99% upper bound on the top probability."""
    n = samples.size
    if n < 2:
        return 0.0
    p_hat = np.bincount(samples, minlength=256).max() / n
    p_u = min(1.0, p_hat + 2.576 * math.sqrt(p_hat * (1.0 - p_hat) / (n - 1)))
    return -math.log2(p_u)


@dataclass
class HealthResult:
    good: np.ndarray        # samples from the tiles that passed, concatenated
    tiles: int
    tiles_passed: int
    max_run: int            # worst tile
    apt_count: int          # worst tile
    h_estimate: float       # MCV estimate over the passing tiles

    @property
    def ok(self) -> bool:
        return self.tiles_passed > 0


class HealthTester:
    def __init__(self, h_claim: float):
        if not 0 < h_claim <= 8:
            raise ValueError("h_claim must be in (0, 8] bits per 8-bit sample")
        self.h_claim = h_claim
        self.rct_c = rct_cutoff(h_claim)
        self.apt_c = apt_cutoff(h_claim)

    def check(self, samples: np.ndarray, window: int = APT_WINDOW) -> HealthResult:
        ntiles = samples.size // window
        if ntiles == 0:
            return HealthResult(samples[:0], 0, 0, 0, 0, 0.0)
        tiles = samples[: ntiles * window].reshape(ntiles, window)
        runs = tile_max_runs(tiles)
        apt = np.sum(tiles == tiles[:, :1], axis=1)
        passed = (runs < self.rct_c) & (apt < self.apt_c)
        good = tiles[passed].ravel()
        return HealthResult(
            good=good,
            tiles=ntiles,
            tiles_passed=int(passed.sum()),
            max_run=int(runs.max()),
            apt_count=int(apt.max()),
            h_estimate=mcv_min_entropy(good) if good.size else 0.0,
        )
