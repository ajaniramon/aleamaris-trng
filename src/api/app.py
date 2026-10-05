# api/app.py
from __future__ import annotations

import asyncio
import hmac
import os
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Iterator, Optional

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware

from trng.alea import INT64_MAX, INT64_MIN, AleaMaris, randbelow, randints
from trng.chacha_drbg import ChaCha20DRBG
from trng.config import Settings
from trng.feeders import ReseedFeeder
from trng.generator import EntropyCollector
from trng.logging import get_logger, request_id_ctx, setup_logging
from trng.queue import TrngQueue
from trng.sources import CameraVideoSource, FileVideoSource, VideoSource

setup_logging()
log = get_logger("api")

SEED_BYTES = 48
MAX_BIGINT_COUNT = 10_000  # ranges beyond int64 use the scalar (arbitrary precision) path
NDJSON_BATCH = 65_536


def default_source_factory(settings: Settings) -> Optional[Callable[[], VideoSource]]:
    if settings.use_cam:
        return lambda: CameraVideoSource(settings.cam_index)
    if settings.video_path:
        return lambda: FileVideoSource(settings.video_path)
    return None


class _StreamSlots:
    """Caps concurrent big streams so a handful of clients cannot pin every CPU."""

    def __init__(self, n: int):
        self._sem = threading.BoundedSemaphore(n)

    def acquire(self) -> None:
        if not self._sem.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="too many concurrent streams, retry later")

    def guard(self, it: Iterator[bytes]) -> "_Guarded":
        return _Guarded(it, self._sem.release)


class _Guarded:
    """Iterator that releases its stream slot exactly once: when exhausted, on
    error, or when dropped unstarted (e.g. the client disconnected early)."""

    def __init__(self, it: Iterator[bytes], release: Callable[[], None]):
        self._it = iter(it)
        self._release = release
        self._done = False

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        try:
            return next(self._it)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self._done:
            self._done = True
            self._release()

    __del__ = close


def _byte_chunks(drbg: ChaCha20DRBG, total: int, chunk: int) -> Iterator[bytes]:
    remaining = total
    while remaining > 0:
        take = min(chunk, remaining)
        yield drbg.generate(take)
        remaining -= take


def _int_batches(drbg: ChaCha20DRBG, lo: int, hi: int, count: int, batch: int) -> Iterator[np.ndarray]:
    remaining = count
    while remaining > 0:
        take = min(batch, remaining)
        yield randints(drbg.generate, lo, hi, take)
        remaining -= take


def create_app(settings: Optional[Settings] = None,
               source_factory: Optional[Callable[[], VideoSource]] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if source_factory is None:
        source_factory = default_source_factory(settings)

    pool = TrngQueue(cap_bytes=settings.pool_cap_bytes)
    collector = EntropyCollector(
        source_factory, pool,
        h_claim=settings.h_claim,
        max_samples=settings.max_samples_per_frame,
        bits_per_block=settings.bits_per_block,
        credit_non_physical=settings.credit_file_source,
        max_consecutive_failures=settings.max_consecutive_failures,
    )
    feeder = ReseedFeeder(pool, allow_urandom=settings.allow_urandom)
    slots = _StreamSlots(settings.max_concurrent_streams)
    state: dict = {"rng": None, "seeded_from": None}

    async def _reseed_loop(rng: AleaMaris):
        while True:
            await asyncio.sleep(settings.reseed_period_sec)
            try:
                await asyncio.to_thread(rng.reseed_from_source)
            except Exception:
                log.warning("periodic reseed failed", exc_info=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        collector.start()
        got = await asyncio.to_thread(collector.wait_for, SEED_BYTES, settings.boot_timeout_sec)
        seed = pool.poll(SEED_BYTES, exact=True) if got else b""
        if seed:
            state["seeded_from"] = "trng"
            if settings.allow_urandom:
                seed += os.urandom(32)  # mixing in more never hurts
                state["seeded_from"] = "trng+urandom"
        elif settings.allow_urandom:
            log.warning("no TRNG entropy at boot; seeding DRBG from os.urandom", extra=collector.status())
            seed = os.urandom(SEED_BYTES)
            state["seeded_from"] = "urandom"
        else:
            collector.stop()
            raise RuntimeError("AleaMaris: no TRNG entropy at startup and ALEAMARIS_ALLOW_URANDOM is off "
                               f"(collector state: {collector.state})")
        rng = AleaMaris(seed,
                        reseed_interval_bytes=settings.reseed_interval_bytes,
                        reseed_bytes=settings.reseed_bytes,
                        entropy_source=feeder)
        state["rng"] = rng
        task = asyncio.create_task(_reseed_loop(rng))
        log.info("startup completed", extra={"seeded_from": state["seeded_from"], **collector.status()})
        try:
            yield
        finally:
            task.cancel()
            await asyncio.to_thread(collector.stop)
            log.info("shutdown completed")

    app = FastAPI(title="AleaMaris TRNG API", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.state.collector = collector
    app.state.feeder = feeder

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["X-Count", "X-Dtype", "X-Available-After", "X-Request-ID"],
    )

    def rng() -> AleaMaris:
        r = state["rng"]
        if r is None:
            raise HTTPException(status_code=503, detail="DRBG not seeded yet")
        return r

    def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if not settings.api_key:
            raise HTTPException(status_code=403, detail="endpoint disabled: set ALEAMARIS_API_KEY to enable it")
        if not x_api_key or not hmac.compare_digest(x_api_key.encode(), settings.api_key.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")

    async def read_limited(request: Request) -> bytes:
        limit = settings.max_ingest_bytes
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > limit:
            raise HTTPException(status_code=413, detail=f"body larger than {limit} bytes")
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > limit:
                raise HTTPException(status_code=413, detail=f"body larger than {limit} bytes")
        return bytes(buf)

    def maybe_reseed(reseed: bool) -> None:
        if reseed:
            rng().reseed_from_source()

    def check_size(nbytes: int) -> None:
        if nbytes > settings.max_stream_bytes:
            raise HTTPException(status_code=413,
                                detail=f"request is {nbytes} bytes; max is {settings.max_stream_bytes}")

    def stream(chunks: Iterator[bytes], media_type: str, headers: dict) -> StreamingResponse:
        slots.acquire()
        # sync iterator: Starlette runs it in a worker thread, the event loop never blocks
        return StreamingResponse(slots.guard(chunks), media_type=media_type, headers=headers)

    # ------------------------------------------------------------------ TRNG
    @app.post("/trng/ingest", dependencies=[Depends(require_api_key)])
    async def ingest(request: Request):
        """External entropy (e.g. an ESP32 noise source). It is only *mixed into*
        the DRBG state; it is never handed out to clients as random bytes."""
        data = await read_limited(request)
        accepted = feeder.add_ingested(data)
        log.info("ingest", extra={"received": accepted, "dropped": len(data) - accepted})
        return {"received": accepted, "dropped": len(data) - accepted,
                "available": pool.available(), "status": "queued-for-drbg-reseed"}

    @app.get("/trng/bytes")
    def trng_bytes(count: int = Query(default=256, ge=1)):
        if count > settings.trng_max_request:
            raise HTTPException(status_code=400, detail=f"count must be <= {settings.trng_max_request}; "
                                                        "use /rng/* for large amounts")
        out = pool.poll(count, exact=True)
        if not out:
            return JSONResponse(status_code=503, headers={"Retry-After": "1"}, content={
                "error": "not enough TRNG entropy available yet",
                "available": pool.available(), "requested": count, "source_state": collector.state})
        return Response(content=out, media_type="application/octet-stream",
                        headers={"X-Count": str(len(out)), "X-Available-After": str(pool.available())})

    @app.get("/trng/raw")
    def trng_raw(count: int = Query(default=256, ge=1)):
        return trng_bytes(count)

    @app.get("/trng/health")
    def health():
        st = collector.status()
        if st["state"] == "failed":
            status = "failed"
        elif st["state"] == "running" and st["entropy_credited"]:
            # a recorded video in demo mode works, but its output is not secret
            status = "ok" if st["physical"] else "demo"
        else:
            status = "degraded"  # no source, exhausted, unavailable or not credited
        return {"status": status, "available": pool.available(), "pool_cap": pool.cap,
                "drbg_seeded_from": state["seeded_from"], "collector": st}

    # ------------------------------------------------------------------ DRBG
    @app.get("/rng/bytes")
    def rng_bytes(count: int = Query(default=256, ge=1), reseed: bool = False):
        check_size(count)
        maybe_reseed(reseed)
        headers = {"X-Count": str(count)}
        if count <= settings.stream_chunk_bytes:
            return Response(content=rng().random_bytes(count), media_type="application/octet-stream",
                            headers=headers)
        child = rng().fork()
        return stream(_byte_chunks(child, count, settings.stream_chunk_bytes),
                      "application/octet-stream", headers)

    @app.get("/rng/ints")
    def rng_ints(min: int = Query(default=0), max: int = Query(default=36),
                 count: int = Query(default=10, ge=1),
                 fmt: str = Query(default="json", pattern="^(json|ndjson|bin)$"),
                 reseed: bool = False):
        lo, hi = min, max
        if lo > hi:
            raise HTTPException(status_code=400, detail="min must be <= max")
        maybe_reseed(reseed)

        if lo < INT64_MIN or hi > INT64_MAX:
            # arbitrary precision: exact and unbiased, but scalar
            if fmt != "json" or count > MAX_BIGINT_COUNT:
                raise HTTPException(status_code=400, detail=f"bounds beyond int64 support fmt=json and "
                                                            f"count <= {MAX_BIGINT_COUNT}")
            r = rng()
            vals = [lo + randbelow(r.random_bytes, hi - lo + 1) for _ in range(count)]
            return {"count": count, "min": lo, "max": hi, "values": [str(v) for v in vals]}

        if fmt == "json":
            if count > settings.max_json_ints:
                raise HTTPException(status_code=400, detail=f"fmt=json supports count <= {settings.max_json_ints}; "
                                                            "use fmt=ndjson or fmt=bin for more")
            vals = rng().randints(lo, hi, count)
            body = (f'{{"count":{count},"min":{lo},"max":{hi},"values":['
                    + ",".join(map(str, vals.tolist())) + "]}")
            return Response(content=body, media_type="application/json", headers={"X-Count": str(count)})

        child = rng().fork()
        if fmt == "ndjson":
            check_size(count * 21)  # worst case "-9223372036854775808\n"
            chunks = ("\n".join(map(str, a.tolist())).encode() + b"\n"
                      for a in _int_batches(child, lo, hi, count, NDJSON_BATCH))
            return stream(chunks, "application/x-ndjson", {"X-Count": str(count)})

        # bin: u32 LE when everything fits, otherwise i64 LE
        dtype = "<u4" if lo >= 0 and hi <= 0xFFFFFFFF else "<i8"
        itemsize = np.dtype(dtype).itemsize
        check_size(count * itemsize)
        batch = settings.stream_chunk_bytes // itemsize
        chunks = (a.astype(dtype).tobytes() for a in _int_batches(child, lo, hi, count, batch))
        return stream(chunks, "application/octet-stream",
                      {"X-Count": str(count), "X-Dtype": "u32le" if dtype == "<u4" else "i64le"})

    @app.get("/rng/u32.bin")
    def rng_u32_bin(count: int = Query(100_000, ge=1),
                    endian: str = Query("le", pattern="^(le|be)$"),
                    reseed: bool = False):
        # Uniform random bytes are uniform u32s in either byte order, so `endian`
        # only documents how the client should read them; no swapping needed.
        nbytes = count * 4
        check_size(nbytes)
        maybe_reseed(reseed)
        child = rng().fork()
        return stream(_byte_chunks(child, nbytes, settings.stream_chunk_bytes),
                      "application/octet-stream", {"X-Count": str(count), "X-Dtype": f"u32{endian}"})

    @app.get("/rng/u32.jsonl")
    def rng_u32_jsonl(count: int = Query(100_000, ge=1), reseed: bool = False):
        check_size(count * 11)
        maybe_reseed(reseed)
        child = rng().fork()

        def chunks():
            remaining = count
            while remaining > 0:
                take = min(NDJSON_BATCH, remaining)
                a = np.frombuffer(child.generate(take * 4), dtype="<u4")
                yield ("\n".join(map(str, a.tolist())) + "\n").encode()
                remaining -= take

        return stream(chunks(), "application/x-ndjson", {"X-Count": str(count)})

    @app.post("/rng/reseed", dependencies=[Depends(require_api_key)])
    async def rng_reseed(request: Request):
        data = await read_limited(request)
        if not data:
            return {"received": 0, "status": "no-op"}
        rng().reseed(data)
        return {"received": len(data), "status": "ok"}

    @app.get("/rng/stats")
    def rng_stats():
        st = collector.status()
        return {
            **rng().stats(),
            "drbg_seeded_from": state["seeded_from"],
            "reseed_period_sec": settings.reseed_period_sec,
            "reseed_sources_bytes": dict(feeder.used),
            "allow_urandom": settings.allow_urandom,
            "raw_available": pool.available(),
            "fill_high_wm": pool.cap,
            "trng_total_bytes": pool.total_in,
            "trng_state": st["state"],
            "trng_source": st["source"],
            "trng_physical": st["physical"],
            "trng_entropy_credited": st["entropy_credited"],
            "trng_min_entropy_estimate": st["last_min_entropy_estimate"],
            "trng_h_claim": st["h_claim_bits_per_sample"],
            "trng_frames": st["frames"],
            "trng_frames_failed": st["frames_failed"],
            "max_stream_bytes": settings.max_stream_bytes,
        }

    # ------------------------------------------------- request-id correlation
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_ctx.set(rid)
        try:
            resp: Response = await call_next(request)
            resp.headers["X-Request-ID"] = rid
            return resp
        finally:
            request_id_ctx.reset(token)

    return app


app = create_app()
