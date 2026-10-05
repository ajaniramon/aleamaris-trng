import time

import numpy as np
import pytest
from conftest import NoiseCamera
from fastapi.testclient import TestClient

from api.app import create_app
from trng.config import Settings

KEY = "s3cret"


def client(**kw):
    defaults = dict(api_key=KEY, boot_timeout_sec=10, max_stream_bytes=1 << 30)
    defaults.update(kw)
    factory = kw.pop("factory", lambda: NoiseCamera())
    defaults.pop("factory", None)
    return TestClient(create_app(Settings(**defaults), source_factory=factory))


@pytest.fixture(scope="module")
def c():
    with client() as tc:
        yield tc


def test_boot_seeds_from_trng(c):
    st = c.get("/rng/stats").json()
    assert st["drbg_seeded_from"] == "trng"
    h = c.get("/trng/health").json()
    assert h["status"] == "ok" and h["collector"]["physical"]


def test_trng_bytes(c):
    deadline = time.time() + 10
    while c.get("/trng/health").json()["available"] < 512 and time.time() < deadline:
        time.sleep(0.05)
    r = c.get("/trng/bytes?count=512")
    assert r.status_code == 200 and len(r.content) == 512


def test_trng_bytes_503_when_empty():
    with client(factory=None, allow_urandom=True) as tc:
        r = tc.get("/trng/bytes?count=16")
        assert r.status_code == 503
        assert tc.get("/rng/stats").json()["drbg_seeded_from"] == "urandom"
        assert tc.get("/trng/health").json()["status"] == "degraded"


def test_boot_fails_without_entropy():
    with pytest.raises(RuntimeError):
        with client(factory=None, allow_urandom=False):
            pass


def test_ingest_and_reseed_require_key(c):
    assert c.post("/trng/ingest", content=b"abc").status_code == 401
    assert c.post("/rng/reseed", content=b"abc").status_code == 401
    assert c.post("/rng/reseed", content=b"abc", headers={"X-API-Key": "nope"}).status_code == 401
    r = c.post("/trng/ingest", content=b"\x00" * 1000, headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json()["received"] == 1000
    assert c.post("/rng/reseed", content=b"abc", headers={"X-API-Key": KEY}).json()["status"] == "ok"


def test_ingested_bytes_are_never_served(c):
    c.post("/trng/ingest", content=b"\x00" * 4096, headers={"X-API-Key": KEY})
    r = c.get("/trng/bytes?count=64")
    if r.status_code == 200:
        assert r.content != b"\x00" * 64


def test_ingest_disabled_without_key():
    with client(api_key=None) as tc:
        assert tc.post("/trng/ingest", content=b"abc").status_code == 403
        assert tc.post("/rng/reseed", content=b"abc").status_code == 403


def test_ingest_size_limit():
    with client(max_ingest_bytes=100) as tc:
        r = tc.post("/trng/ingest", content=b"x" * 101, headers={"X-API-Key": KEY})
        assert r.status_code == 413


def test_ints_huge_range_does_not_hang(c):
    t = time.time()
    r = c.get("/rng/ints?min=0&max=10000000000&count=1000")
    assert r.status_code == 200 and time.time() - t < 2
    v = r.json()["values"]
    assert len(v) == 1000 and all(0 <= x <= 10**10 for x in v)


def test_ints_beyond_int64(c):
    r = c.get(f"/rng/ints?min=0&max={10**30}&count=5")
    vals = [int(x) for x in r.json()["values"]]
    assert all(0 <= x <= 10**30 for x in vals)
    assert c.get(f"/rng/ints?min=0&max={10**30}&count=5&fmt=bin").status_code == 400


def test_ints_bin_dtypes(c):
    r = c.get("/rng/ints?min=0&max=36&count=1000&fmt=bin")
    assert r.headers["x-dtype"] == "u32le"
    a = np.frombuffer(r.content, dtype="<u4")
    assert a.size == 1000 and a.max() <= 36
    r = c.get("/rng/ints?min=-5&max=5&count=1000&fmt=bin")
    assert r.headers["x-dtype"] == "i64le"
    a = np.frombuffer(r.content, dtype="<i8")
    assert a.min() >= -5 and a.max() <= 5


def test_ints_validation(c):
    assert c.get("/rng/ints?min=5&max=1").status_code == 400
    assert c.get("/rng/ints?count=2000000&fmt=json").status_code == 400


def test_ints_ndjson_massive(c):
    n = 3_000_000
    t = time.time()
    r = c.get(f"/rng/ints?min=1&max=6&count={n}&fmt=ndjson")
    lines = r.content.split(b"\n")
    assert len(lines) == n + 1 and lines[-1] == b""
    assert time.time() - t < 20


def test_u32_bin_massive_and_fast(c):
    n = 25_000_000  # 100 MB, the old hard cap
    t = time.time()
    r = c.get(f"/rng/u32.bin?count={n}")
    dt = time.time() - t
    assert len(r.content) == 4 * n
    assert dt < 10, dt


def test_rng_bytes_streams_past_old_limit(c):
    r = c.get(f"/rng/bytes?count={5 * (1 << 20) + 1}")
    assert len(r.content) == 5 * (1 << 20) + 1


def test_u32_jsonl(c):
    r = c.get("/rng/u32.jsonl?count=1000")
    vals = [int(x) for x in r.content.split()]
    assert len(vals) == 1000 and max(vals) < 2**32


def test_size_limit():
    with client(max_stream_bytes=1000) as tc:
        assert tc.get("/rng/u32.bin?count=1000").status_code == 413


def test_stream_slots_released():
    with client(max_concurrent_streams=1) as tc:
        for _ in range(5):
            assert tc.get(f"/rng/bytes?count={3 << 20}").status_code == 200


def test_api_stays_responsive_during_big_stream(c):
    import threading
    done = threading.Event()

    def big():
        c.get("/rng/u32.bin?count=50000000")
        done.set()

    th = threading.Thread(target=big)
    th.start()
    time.sleep(0.2)
    t = time.time()
    assert c.get("/trng/health").status_code == 200
    assert time.time() - t < 1.0
    th.join()
    assert done.is_set()


def test_reseed_query_uses_trng(c):
    before = c.get("/rng/stats").json()
    c.get("/rng/bytes?count=16&reseed=true")
    after = c.get("/rng/stats").json()
    assert after["reseed_count"] >= before["reseed_count"]


def test_request_id_header(c):
    assert c.get("/trng/health", headers={"X-Request-ID": "abc"}).headers["x-request-id"] == "abc"


def test_health_degraded_when_frames_stall(c):
    col = c.app.state.collector
    saved = col.last_frame_at
    col.last_frame_at = time.monotonic() - 3600
    try:
        assert c.get("/trng/health").json()["status"] == "degraded"
    finally:
        col.last_frame_at = saved


def test_demo_header_only_for_recordings(c):
    deadline = time.time() + 10
    while c.get("/trng/health").json()["available"] < 32 and time.time() < deadline:
        time.sleep(0.05)
    r = c.get("/trng/bytes?count=32")
    assert r.status_code == 200 and "x-trng-demo" not in r.headers
