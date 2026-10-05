import numpy as np

from trng.health import (HealthTester, apt_cutoff, apt_max_count, max_run_length,
                         mcv_min_entropy, rct_cutoff, tile_max_runs)


def test_cutoffs_match_sp800_90b():
    assert rct_cutoff(1.0) == 21
    assert rct_cutoff(8.0) == 4
    # 1 - 2**-20 quantile of Binomial(512, 1/2) is ~310
    assert 300 <= apt_cutoff(1.0) <= 320
    assert apt_cutoff(4.0) < apt_cutoff(1.0)


def test_tile_runs_match_reference():
    rng = np.random.default_rng(0)
    tiles = rng.integers(0, 3, size=(50, 512)).astype(np.uint8)
    tiles[7, 100:160] = 9
    got = tile_max_runs(tiles)
    want = [max_run_length(t) for t in tiles]
    assert list(got) == want


def test_good_noise_passes():
    rng = np.random.default_rng(1)
    s = rng.integers(0, 256, 16384).astype(np.uint8)
    res = HealthTester(1.0).check(s)
    assert res.tiles == res.tiles_passed == 32
    assert res.h_estimate > 7


def test_stuck_region_is_dropped_but_frame_survives():
    rng = np.random.default_rng(2)
    s = rng.integers(0, 256, 16384).astype(np.uint8)
    s[:4096] = 0
    res = HealthTester(1.0).check(s)
    assert res.tiles_passed == 24
    assert res.good.size == 24 * 512 and res.ok


def test_constant_fails_everything():
    res = HealthTester(1.0).check(np.zeros(16384, dtype=np.uint8))
    assert not res.ok and res.good.size == 0


def test_biased_source_caught_by_apt():
    rng = np.random.default_rng(3)
    # value 0 two thirds of the time, interleaved so RCT alone would not see long runs
    s = np.where(rng.random(16384) < 0.66, 0, rng.integers(1, 256, 16384)).astype(np.uint8)
    t = HealthTester(1.0)
    assert apt_max_count(s) >= t.apt_c
    assert t.check(s).tiles_passed < 32


def test_mcv():
    assert mcv_min_entropy(np.zeros(1000, dtype=np.uint8)) == 0.0
    s = np.arange(256, dtype=np.uint8).repeat(100)
    assert 7.0 < mcv_min_entropy(s) <= 8.0
