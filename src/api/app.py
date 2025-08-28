# api/app.py
from __future__ import annotations
import os, asyncio, struct
from fastapi import FastAPI, Request, Response, Header, Query
from fastapi.responses import StreamingResponse
from starlette.middleware import Middleware

from trng.queue import TrngQueue
from trng.config import GenConfig
from trng.sources import FileVideoSource, CameraVideoSource
from trng.generator import TRNGGenerator
from trng.alea import AleaMaris
from starlette.middleware.cors import CORSMiddleware


middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=[
            "*"
        ],
        allow_origin_regex=r".*",   # acepta lo que no esté listado (evita sustos)
        allow_credentials=False,    # si lo pones True, no puedes usar '*'
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Count", "X-Available-After"],
    )
]

app = FastAPI(title="AleaMaris TRNG API", middleware=middleware)

RAW_CAP               = int(os.environ.get("ALEAMARIS_RAW_CAP", "100000000"))
BOOT_BYTES            = int(os.environ.get("ALEAMARIS_BOOT_BYTES", "4096"))
ALLOW_URANDOM_BOOT    = os.environ.get("ALEAMARIS_ALLOW_URANDOM", "0").lower() in ("1","true","yes")
VIDEO_PATH            = os.environ.get("ALEAMARIS_VIDEO", "sample2.mp4")
CAM_INDEX             = int(os.environ.get("ALEAMARIS_CAM", "0"))
FILL_LOW_WM           = int(os.environ.get("ALEAMARIS_RAW_LOW_WM",  "2000"))     # low watermark
FILL_HIGH_WM          = int(os.environ.get("ALEAMARIS_RAW_HIGH_WM", "5000"))    # high watermark
FILL_INTERVAL_MS      = int(os.environ.get("ALEAMARIS_FILL_INTERVAL_MS", "200"))  # cada 200ms check
FILL_CHUNK_BYTES      = int(os.environ.get("ALEAMARIS_FILL_CHUNK", "500"))      # tamaño de aportes
RESEED_PERIOD_SEC     = int(os.environ.get("ALEAMARIS_RESEED_PERIOD", "120"))      # reseed cada minuto
RESEED_BYTES          = int(os.environ.get("ALEAMARIS_RESEED_BYTES", "64"))       # bytes por reseed
RESEED_INTERVAL_BYTES = int(os.environ.get("ALEAMARIS_RESEED_INTERVAL_BYTES", "1000000"))  # umbral de bytes antes de reseed
API_KEY               = os.environ.get("ALEAMARIS_API_KEY")                       # opcional

# Pipeline de features para “moler” frames a bytes (igual que tu CLI)
GEN_CFG = GenConfig(bytes_total=FILL_CHUNK_BYTES, resize=64, stride=1, use_diff=True, debug=False)

# -------------------- Cola RAW y DRBG --------------------
q = TrngQueue(cap_bytes=RAW_CAP)

def _try_bytes_from_video_or_cam(n: int) -> bytes:
    """Intenta sacar n bytes desde VIDEO y/o CAM. Rebobina si es fichero."""
    if VIDEO_PATH:
        print("video dump started")
        try:
            src = FileVideoSource(VIDEO_PATH)
            gen = TRNGGenerator(src, GEN_CFG)
            GEN_CFG.bytes_total = n
            return gen.produce()
        except Exception:
            pass
    # Si no hay vídeo o falló, intenta cámara si el env lo dice
    if os.environ.get("ALEAMARIS_USE_CAM", "0").lower() in ("1","true","yes"):
        try:
            src = CameraVideoSource(CAM_INDEX)
            gen = TRNGGenerator(src, GEN_CFG)
            GEN_CFG.bytes_total = n
            return gen.produce()
        except Exception:
            pass
    return b""

def _seed_provider(n: int) -> bytes:
    """Proveedor para el DRBG: prioriza RAW; si no hay, intenta vídeo/cam; si se permite, completa con urandom."""
    if q.available() >= n:
        return q.poll(n)
    # intenta rellenar desde vídeo/cam
    extra = _try_bytes_from_video_or_cam(n)
    if extra:
        return extra if len(extra) >= n else extra + os.urandom(n - len(extra)) if ALLOW_URANDOM_BOOT else extra
    # como último recurso, urandom sólo si se permite
    if ALLOW_URANDOM_BOOT:
        return os.urandom(n)
    return b""  # dejar que el caller decida si petar

_rng = AleaMaris(_seed_provider)  # DRBG ChaCha20 con reseed interval interno
_rng.reseed_interval_bytes = RESEED_INTERVAL_BYTES
_fill_task: asyncio.Task | None = None
_reseed_task: asyncio.Task | None = None

# -------------------- Helpers de reseed y fill --------------------
def _reseed_from_queue(limit_bytes: int = RESEED_BYTES) -> int:
    """Coge hasta limit_bytes de la RAW y reseedea el DRBG. Devuelve bytes usados."""
    avail = q.available()
    if avail <= 0:
        return 0
    n = min(avail, limit_bytes)
    data = q.poll(n)
    if data:
        _rng.reseed(data)
        return len(data)
    return 0

def _maybe_reseed_opportunistic() -> None:
    """Reseed sólo si el DRBG ya expandió suficiente (umbral interno)."""
    try:
        _rng.maybe_reseed()
    except Exception:
        pass

async def _filler_loop():
    """Mantiene la cola RAW entre LOW y HIGH; usa vídeo/cam; si no hay y se permite, urandom; si no, espera."""
    while True:
        try:
            avail = q.available()
            if avail < FILL_LOW_WM:
                need_total = min(FILL_HIGH_WM - avail, FILL_CHUNK_BYTES)
                chunk = _try_bytes_from_video_or_cam(need_total)
                if not chunk and ALLOW_URANDOM_BOOT:
                    chunk = os.urandom(need_total)
                if chunk:
                    print(f"filler: Offered {len(chunk)} to queue")
                    q.offer(chunk)
        except Exception as e:
            # No tiramos la app por fallos de cámara/vídeo; reintentamos en la próxima vuelta
            print(f"filler: failed with exception {e}")
            pass
        await asyncio.sleep(FILL_INTERVAL_MS / 1000.0)

async def _reseed_loop():
    """Reseed periódico del DRBG desde RAW; si no hay y se permite, mezcla urandom para no quedarse seco."""
    while True:
        try:
            used = _reseed_from_queue(RESEED_BYTES)
            if used == 0 and ALLOW_URANDOM_BOOT:
                _rng.reseed(os.urandom(RESEED_BYTES))
        except Exception:
            pass
        await asyncio.sleep(RESEED_PERIOD_SEC)

@app.on_event("startup")
async def _startup():
    print("startup called")

    # 1) Boot: intentar poblar la RAW con BOOT_BYTES
    boot = _try_bytes_from_video_or_cam(BOOT_BYTES)
    if not boot and ALLOW_URANDOM_BOOT:
        print("boot falling back to urandom")
        boot = os.urandom(BOOT_BYTES)
    if not boot:
        # requisito: “si no hay vídeo/cam y no se permite urandom → PETA”
        raise RuntimeError("AleaMaris: no entropy source available at startup and ALLOW_URANDOM disabled")

    q.offer(boot)

    # 2) Lanza los procesos en background
    global _fill_task, _reseed_task
    loop = asyncio.get_event_loop()
    _fill_task = loop.create_task(_filler_loop())
    _reseed_task = loop.create_task(_reseed_loop())

@app.on_event("shutdown")
async def _shutdown():
    for task in (_fill_task, _reseed_task):
        if task:
            task.cancel()

@app.post("/trng/ingest")
async def ingest(request: Request, x_api_key: str | None = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        return Response(content=b'{"error":"unauthorized"}', status_code=401, media_type="application/json")
    data = await request.body()
    written = q.offer(data)
    dropped = len(data) - written
    return {"received": written, "dropped": dropped, "available": q.available()}

@app.get("/trng/bytes")
def get_bytes(count: int = 256):
    c = max(1, min(count, 4096))
    out = q.poll(c)
    headers = {"X-Available-After": str(q.available())}
    return Response(content=out, media_type="application/octet-stream", headers=headers)

@app.get("/trng/raw")
def get_raw(count: int = 256):
    return get_bytes(count)

@app.get("/trng/health")
def health():
    return {"available": q.available(), "status": "ok"}

@app.get("/rng/bytes")
def rng_bytes(count: int = Query(default=256, ge=1, le=1_048_576),
              reseed: bool = Query(default=False)):
    # Reseed manual bajo demanda, si se pide; si no, oportunista
    if reseed:
        _reseed_from_queue()
    else:
        _maybe_reseed_opportunistic()
    data = _rng.random_bytes(count)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"X-Count": str(len(data))})

@app.get("/rng/ints")
def rng_ints(min: int = Query(default=0),
             max: int = Query(default=36),
             count: int = Query(default=10, ge=1, le=100_000),
             reseed: bool = Query(default=False),
             fmt: str = Query(default="json")):
    if min > max:
        return Response(status_code=400, content=b'{"error":"min>max"}', media_type="application/json")
    if reseed:
        _reseed_from_queue()
    else:
        _maybe_reseed_opportunistic()
    print(min)
    print(max)
    vals = [_rng.randint(min, max) for _ in range(count)]
    if fmt == "bin":
        payload = b"".join(struct.pack("<I", v) for v in vals)
        return Response(content=payload, media_type="application/octet-stream",
                        headers={"X-Count": str(len(vals))})
    return {"count": len(vals), "min": min, "max": max, "values": vals}

@app.post("/rng/reseed")
async def rng_reseed(request: Request):
    data = await request.body()
    if not data:
        return {"received": 0, "status": "no-op"}
    _rng.reseed(data)
    return {"received": len(data), "status": "ok"}



def _u32bin_stream(count: int, endian="le", batch=100_000):
    remaining = count
    while remaining > 0:
        take = min(batch, remaining)
        raw = _rng.random_bytes(take * 4)
        if endian == "be":
            # emitir tal cual
            yield raw
        else:
            # swap a little endian
            mv = memoryview(raw)
            out = bytearray(len(raw))
            out[0::4] = mv[3::4]
            out[1::4] = mv[2::4]
            out[2::4] = mv[1::4]
            out[3::4] = mv[0::4]
            yield bytes(out)
        remaining -= take

@app.get("/rng/u32.bin")
def rng_u32_bin(count: int = Query(100_000, ge=1, le=25_000_000),
                endian: str = Query("le", pattern="^(le|be)$"),
                reseed: bool = Query(default=False)):
    if reseed:
        _reseed_from_queue()
    else:
        _maybe_reseed_opportunistic()
    return StreamingResponse(
        _u32bin_stream(count, endian=endian),
        media_type="application/octet-stream",
        headers={"X-Count": str(count)}
    )

def _u32jsonl_stream(count: int, batch=100_000):
    import struct
    remaining = count
    while remaining > 0:
        take = min(batch, remaining)
        raw = _rng.random_bytes(take * 4)
        ints = struct.unpack(">" + "I"*take, raw)
        yield ("\n".join(str(x) for x in ints) + "\n").encode()
        remaining -= take

@app.get("/rng/u32.jsonl")
def rng_u32_jsonl(count: int = Query(100_000, ge=1, le=2_000_000),
                  reseed: bool = Query(default=False)):
    if reseed:
        _reseed_from_queue()
    else:
        _maybe_reseed_opportunistic()
    return StreamingResponse(
        _u32jsonl_stream(count),
        media_type="application/x-ndjson",
        headers={"X-Count": str(count)}
    )


@app.get("/rng/stats")
def rng_stats():
    return {
        "generated_bytes_since_last_reseed": _rng.generated,
        "reseed_interval_bytes": _rng.reseed_interval_bytes,
        "raw_available": q.available(),
        "boot_bytes": BOOT_BYTES,
        "allow_urandom_boot": ALLOW_URANDOM_BOOT,
        "fill_low_wm": FILL_LOW_WM,
        "fill_high_wm": FILL_HIGH_WM,
        "reseed_period_sec": RESEED_PERIOD_SEC,
        "reseed_bytes": RESEED_BYTES
    }
