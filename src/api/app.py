# api/app.py
from __future__ import annotations
import os, asyncio, struct
from fastapi import FastAPI, Request, Response, Header, Query

from trng.queue import TrngQueue
from trng.config import GenConfig
from trng.sources import FileVideoSource, CameraVideoSource
from trng.generator import TRNGGenerator
from trng.alea import AleaMaris

app = FastAPI(title="AleaMaris TRNG API (RAW + ChaCha20-DRBG)")

RAW_CAP               = int(os.environ.get("ALEAMARIS_RAW_CAP", "1000000"))
BOOT_BYTES            = int(os.environ.get("ALEAMARIS_BOOT_BYTES", "65536"))
ALLOW_URANDOM_BOOT    = os.environ.get("ALEAMARIS_ALLOW_URANDOM", "0").lower() in ("1","true","yes")
VIDEO_PATH            = os.environ.get("ALEAMARIS_VIDEO", "")
CAM_INDEX             = int(os.environ.get("ALEAMARIS_CAM", "0"))
FILL_LOW_WM           = int(os.environ.get("ALEAMARIS_RAW_LOW_WM",  "65536"))     # low watermark
FILL_HIGH_WM          = int(os.environ.get("ALEAMARIS_RAW_HIGH_WM", "262144"))    # high watermark
FILL_INTERVAL_MS      = int(os.environ.get("ALEAMARIS_FILL_INTERVAL_MS", "200"))  # cada 200ms check
FILL_CHUNK_BYTES      = int(os.environ.get("ALEAMARIS_FILL_CHUNK", "32768"))      # tamaño de aportes
RESEED_PERIOD_SEC     = int(os.environ.get("ALEAMARIS_RESEED_PERIOD", "10"))      # reseed cada 10s
RESEED_BYTES          = int(os.environ.get("ALEAMARIS_RESEED_BYTES", "64"))       # bytes por reseed
API_KEY               = os.environ.get("ALEAMARIS_API_KEY")                       # opcional

# Pipeline de features para “moler” frames a bytes (igual que tu CLI)
GEN_CFG = GenConfig(bytes_total=FILL_CHUNK_BYTES, resize=64, stride=1, use_diff=True, debug=False)

# -------------------- Cola RAW y DRBG --------------------
q = TrngQueue(cap_bytes=RAW_CAP)

def _try_bytes_from_video_or_cam(n: int) -> bytes:
    """Intenta sacar n bytes desde VIDEO y/o CAM. Rebobina si es fichero."""
    if VIDEO_PATH:
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
                    q.offer(chunk)
        except Exception:
            # No tiramos la app por fallos de cámara/vídeo; reintentamos en la próxima vuelta
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
    # 1) Boot: intentar poblar la RAW con BOOT_BYTES
    boot = _try_bytes_from_video_or_cam(BOOT_BYTES)
    if not boot and ALLOW_URANDOM_BOOT:
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
              reseed: bool = Query(default=True)):
    if reseed:
        _reseed_from_queue()
    data = _rng.random_bytes(count)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"X-Count": str(len(data))})

@app.get("/rng/ints")
def rng_ints(mini: int = Query(default=0),
             maxi: int = Query(default=36),
             count: int = Query(default=10, ge=1, le=100_000),
             reseed: bool = Query(default=True),
             fmt: str = Query(default="json")):
    if mini > maxi:
        return Response(status_code=400, content=b'{"error":"min>max"}', media_type="application/json")
    if reseed:
        _reseed_from_queue()
    vals = [_rng.randint(mini, maxi) for _ in range(count)]
    if fmt == "bin":
        payload = b"".join(struct.pack("<I", v) for v in vals)
        return Response(content=payload, media_type="application/octet-stream",
                        headers={"X-Count": str(len(vals))})
    return {"count": len(vals), "min": mini, "max": maxi, "values": vals}

@app.post("/rng/reseed")
async def rng_reseed(request: Request):
    data = await request.body()
    if not data:
        return {"received": 0, "status": "no-op"}
    _rng.reseed(data)
    return {"received": len(data), "status": "ok"}

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
