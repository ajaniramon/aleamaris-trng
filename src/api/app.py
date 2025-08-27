from fastapi import FastAPI, Request, Response, Header
from trng.queue import TrngQueue

app = FastAPI(title="TRNG API (binario)")
q = TrngQueue(cap_bytes=1_000_000)

API_KEY = None  # opcional: setea por env o aquí

@app.post("/trng/ingest")
async def ingest(request: Request, x_api_key: str | None = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        return Response(content=b'{"error":"unauthorized"}', status_code=401, media_type="application/json")
    data = await request.body()  # binario crudo
    written = q.offer(data)
    dropped = len(data) - written
    return {"received": written, "dropped": dropped, "available": q.available()}

@app.get("/trng/bytes")
def get_bytes(count: int = 256):
    c = max(1, min(count, 4096))
    out = q.poll(c)
    headers = {"X-Available-After": str(q.available())}
    return Response(content=out, media_type="application/octet-stream", headers=headers)

@app.get("/trng/health")
def health():
    return {"available": q.available(), "status": "ok"}
