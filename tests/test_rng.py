import os
import threading

import numpy as np
import pytest

from trng.alea import AleaMaris, randbelow, randints


def make(**kw):
    return AleaMaris(os.urandom(48), **kw)


@pytest.mark.parametrize("n", [1, 2, 3, 37, 2**32 - 1, 2**32, 2**32 + 1, 10**10, 2**64, 10**40])
def test_randbelow_any_size_terminates_in_range(n):
    r = make()
    for _ in range(200):
        assert 0 <= r.randbelow(n) < n


def test_randint_huge_range_regression():
    # used to loop forever: limit became 0 for ranges > 2**32
    r = make()
    v = r.randint(0, 10**10)
    assert 0 <= v <= 10**10


@pytest.mark.parametrize("lo,hi", [(0, 36), (-5, 5), (0, 2**32 - 1), (0, 2**32),
                                   (-(2**63), 2**63 - 1), (2**63 - 10, 2**63 - 1), (7, 7)])
def test_randints_bounds(lo, hi):
    r = make()
    a = r.randints(lo, hi, 50_000)
    assert a.dtype == np.int64 and a.size == 50_000
    assert int(a.min()) >= lo and int(a.max()) <= hi


def test_randints_uniform_chi2():
    r = make()
    a = r.randints(1, 6, 600_000)
    counts = np.bincount(a, minlength=7)[1:]
    exp = 100_000
    chi2 = float(((counts - exp) ** 2 / exp).sum())
    assert chi2 < 30  # df=5, p ~ 1e-5


def test_randints_non_power_of_two_unbiased_mod():
    # span 3*2**30: naive modulo would give 0..2**30 twice the weight
    r = make()
    span = 3 * 2**30
    a = r.randints(0, span - 1, 300_000)
    frac_low = float((a < 2**30).mean())
    assert abs(frac_low - 1 / 3) < 0.01


def test_randints_rejects_bad_bounds():
    r = make()
    with pytest.raises(ValueError):
        r.randints(5, 1, 10)
    with pytest.raises(ValueError):
        r.randints(0, 2**63, 10)


def test_reseed_interval_pulls_from_source_without_blocking():
    calls = []

    def src(n):
        calls.append(n)
        return b"x" * n

    r = make(reseed_interval_bytes=1000, entropy_source=src)
    r.random_bytes(999)
    assert calls == []
    r.random_bytes(10)
    assert calls == [64] and r.generated_since_reseed == 0 and r.reseed_count == 1


def test_empty_source_does_not_fake_a_reseed():
    r = make(reseed_interval_bytes=10, entropy_source=lambda n: b"")
    r.random_bytes(100)
    assert r.reseed_count == 0 and r.generated_since_reseed == 100


def test_fork_children_are_independent():
    r = make()
    a, b = r.fork(), r.fork()
    assert a.generate(64) != b.generate(64)


def test_threads_never_get_same_bytes():
    r = make()
    out, lock = [], threading.Lock()

    def worker():
        local = [r.random_bytes(16) for _ in range(2000)]
        with lock:
            out.extend(local)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(out)) == len(out)


def test_large_random_bytes_chunked():
    r = make()
    assert len(r.random_bytes(5 * (1 << 20) + 3)) == 5 * (1 << 20) + 3


def test_module_level_helpers():
    rb = os.urandom
    assert 0 <= randbelow(rb, 10) < 10
    assert randints(rb, 0, 1, 10).size == 10


def test_child_stream_counts_toward_parent_reseed():
    calls = []

    def src(n):
        calls.append(n)
        return b"e" * n

    r = make(reseed_interval_bytes=1 << 20, entropy_source=src)
    child = r.fork()
    child.generate(5 << 20)  # 5 MiB through the child
    assert child.rekeys >= 4
    assert r.reseed_count >= 4 and len(calls) >= 4


def test_child_picks_up_parent_reseed():
    seed = os.urandom(48)
    a, b = AleaMaris(seed, reseed_interval_bytes=1 << 20), AleaMaris(seed, reseed_interval_bytes=1 << 20)
    ca, cb = a.fork(), b.fork()
    assert ca.generate(1 << 20) == cb.generate(1 << 20)
    b.reseed(b"fresh")
    # after the next re-key the streams diverge
    assert ca.generate(1 << 20) != cb.generate(1 << 20)
