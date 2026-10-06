import os

import pytest

from trng.chacha_drbg import ChaCha20DRBG


def test_deterministic_for_same_seed():
    a, b = ChaCha20DRBG(b"s" * 32), ChaCha20DRBG(b"s" * 32)
    assert a.generate(100) == b.generate(100)
    assert a.generate(5000) == b.generate(5000)


def test_successive_outputs_differ_and_key_is_erased():
    d = ChaCha20DRBG(os.urandom(32))
    k0 = d._key
    x = d.generate(64)
    assert d._key != k0
    assert d.generate(64) != x


def test_reseed_changes_stream():
    a, b = ChaCha20DRBG(b"s" * 32), ChaCha20DRBG(b"s" * 32)
    b.reseed(b"fresh entropy")
    assert a.generate(32) != b.generate(32)
    a.reseed(b"")  # no-op
    assert a._key != b._key


def test_short_seed_rejected():
    with pytest.raises(ValueError):
        ChaCha20DRBG(b"short")


def test_output_not_obviously_biased():
    data = ChaCha20DRBG(os.urandom(32)).generate(1 << 20)
    ones = int.from_bytes(data, "big").bit_count()
    n = len(data) * 8
    assert abs(ones - n / 2) < 6 * (n ** 0.5) / 2
