# 🌊🎮 AleaMaris TRNG — True Randomness from Chaos

> *An experimental True Random Number Generator powered by the chaos of nature.*  
> Point a camera at ocean waves, a candle flame or just the dark: the sensor's own noise becomes health-tested, conditioned randomness — plus a fast DRBG that streams gigabytes without breaking a sweat.

---

![Python](https://img.shields.io/badge/python-3.11-blue?style=for-the-badge&logo=python)
![License](https://img.shields.io/badge/license-MIT-black?style=for-the-badge)

---

<p align="center">
  <img src="aleamaris_logo.png" alt="AleaMaris Logo" width="400"/>
</p>

## 🧠 How it works

```
camera frame ─► temporal diff per pixel (native res, sparse grid) ─► raw 8-bit noise samples
             ─► health tests per 512-sample tile (SP 800-90B RCT + APT) ─► drop dead tiles
             ─► min-entropy estimate (MCV, capped at h_claim) ─► SHA-256 (unkeyed) ─► TRNG pool
                                                                                         │
                                       reseed (TRNG + ingested + optional urandom) ◄─────┘
                                                        │
                                        ChaCha20 DRBG (fast key erasure) ─► /rng/* streams
```

- **Where the entropy comes from:** shot/thermal noise of a *live* camera sensor. Two consecutive
  frames of the same scene never match exactly; their per-pixel difference is the raw sample.
- **Honest accounting:** each 32-byte output block is backed by at least `bits_per_block`
  (default 512) bits of *assessed* min-entropy, with at most `h_claim` (default 1.0) bits
  credited per sample.
- **No hiding behind a key:** conditioning is plain SHA-256. If the camera dies (lens cap, frozen
  driver), the health tests fail and output **stops**; it does not keep producing random-looking
  bytes.
- **Recorded videos are not entropy.** Anyone with the file can replay it. A video can still be used
  as a **demo** (`ALEAMARIS_CREDIT_FILE_SOURCE=1` / `--demo`), and the API then reports
  `"physical": false` and health status `"demo"`. Replayed blocks are detected and dropped.
- **DRBG:** ChaCha20 (from `cryptography`/OpenSSL). After every call the key is replaced by fresh
  keystream (backtracking resistance), and reseeds go through HMAC-SHA256. Each big request gets
  its own child DRBG and is streamed from a worker thread, so a 10 GB download never blocks other
  clients.

## ✨ Features

### ✅ Ready
- Live-camera entropy source with SP 800-90B-style RCT/APT health tests and MCV min-entropy estimate
- Unkeyed SHA-256 conditioning with conservative entropy credit
- Raw-sample dump for offline assessment with NIST's [SP800-90B_EntropyAssessment](https://github.com/usnistgov/SP800-90B_EntropyAssessment)
- ChaCha20 DRBG with fast key erasure, periodic and byte-count reseeding
- Unbiased integers for **any** range (rejection sampling, vectorized with numpy)
- Streaming endpoints: hundreds of MB/s, GB-sized responses, concurrency-limited
- API-key protected ingest/reseed, request-id correlated logging
- CLI, web UI, Docker / docker-compose, test suite

### 🔧 In Progress
- Multi-source entropy (audio, sensors, ESP32 via `/trng/ingest`)
- Emulator integration (WRAM mailbox for BGB/Emulicious)
- Game Boy hardware demo (ESP32 + Link Port)
- Prometheus/Grafana metrics
- Transparency logging of reseed events

---

## 🧩 Project Structure

```
src/
├─ trng/
│  ├─ sources.py        # Camera (physical) / video file (demo) sources
│  ├─ features.py       # Frame -> raw noise samples (temporal diff)
│  ├─ health.py         # RCT / APT health tests, MCV min-entropy estimate
│  ├─ conditioners.py   # Unkeyed SHA-256 conditioning
│  ├─ generator.py      # EntropyCollector: the TRNG pipeline (background thread)
│  ├─ queue.py          # Thread-safe TRNG pool
│  ├─ feeders.py        # Reseed material for the DRBG (TRNG + ingest + urandom)
│  ├─ chacha_drbg.py    # ChaCha20 DRBG with fast key erasure
│  ├─ alea.py           # AleaMaris: thread-safe bytes, unbiased ints, child DRBGs
│  ├─ config.py         # Settings from ALEAMARIS_* env vars
│  ├─ utils.py          # Raw sample writer, entropy helpers
│  └─ logging.py        # log4j-style / JSON logging
├─ api/app.py           # FastAPI app
├─ bin/trng_cli.py      # Command-line interface
└─ web/index.html       # Web UI
tests/                  # pytest suite
```

---

## ⚡ Quickstart

### Install
```bash
pip install -r requirements-dev.txt
python -m pytest            # run the tests
```

### Random bytes from a webcam
```bash
python src/bin/trng_cli.py --cam 0 --bytes 4096 --out out.bin
```

### Dump raw noise for a proper entropy assessment
```bash
python src/bin/trng_cli.py --cam 0 --bytes 0 --dump-raw raw.bin --raw-limit 1000000
ea_non_iid -v raw.bin 8      # from NIST SP800-90B_EntropyAssessment
```
Use the result to set `ALEAMARIS_H_CLAIM` (keep it at or below the tool's estimate).

### Pipeline demo with the bundled video (output is NOT secret)
```bash
python src/bin/trng_cli.py --video sample.MP4 --demo --bytes 1024
```

### Run the API
```bash
# real TRNG
ALEAMARIS_USE_CAM=1 ALEAMARIS_API_KEY=changeme uvicorn --app-dir src api.app:app --port 8080
# demo (video + urandom fallback for the DRBG)
./scripts/start.sh -v sample.MP4 --demo --allow-urandom -p 8080
# docker: API on :50000, web UI on :50001
docker compose up --build
```

---

## 🔌 API

| Endpoint | What it does |
|---|---|
| `GET /trng/bytes?count=N` | Up to 4096 bytes of conditioned TRNG output. **503** if the pool doesn't have N yet. |
| `GET /trng/health` | Source state (`ok` / `demo` / `degraded` / `failed`), entropy estimate, health stats. |
| `POST /trng/ingest` | External entropy, **mixed into the DRBG only** (never served). Requires `X-API-Key`. |
| `GET /rng/bytes?count=N` | DRBG bytes; streamed for large N (up to `ALEAMARIS_MAX_STREAM_BYTES`, default 64 GiB). |
| `GET /rng/ints?min&max&count&fmt` | Unbiased integers. `fmt=json` (≤1M), `ndjson` or `bin` (u32 LE, or i64 LE if the range needs it; see `X-Dtype`). Ranges beyond int64 are supported in JSON (values returned as strings). |
| `GET /rng/u32.bin?count=N` | Raw uint32 stream (uniform in either byte order). |
| `GET /rng/u32.jsonl?count=N` | One uint32 per line. |
| `POST /rng/reseed` | Mix caller-provided bytes into the DRBG. Requires `X-API-Key`. |
| `GET /rng/stats` | DRBG + TRNG metrics (used by the web UI). |

The `/rng/*` GET endpoints accept `reseed=true` to pull fresh TRNG entropy into the DRBG first.
Without `ALEAMARIS_API_KEY`, `/trng/ingest` and `/rng/reseed` are disabled (403).

```bash
curl -s "localhost:8080/rng/u32.bin?count=250000000" -o big.bin     # 1 GB
curl -s "localhost:8080/rng/ints?min=1&max=6&count=10000000&fmt=ndjson" | head
```

### Configuration (env)

| Variable | Default | Meaning |
|---|---|---|
| `ALEAMARIS_USE_CAM` / `ALEAMARIS_CAM` | `0` / `0` | Use camera index N as the source |
| `ALEAMARIS_VIDEO` | – | Video file source (demo) |
| `ALEAMARIS_CREDIT_FILE_SOURCE` | `0` | Credit entropy to a video file (demo only) |
| `ALEAMARIS_H_CLAIM` | `1.0` | Max bits of min-entropy credited per sample |
| `ALEAMARIS_ALLOW_URANDOM` | `0` | DRBG may seed/reseed from `os.urandom` (also mixed into every reseed) |
| `ALEAMARIS_API_KEY` | – | Enables ingest/reseed |
| `ALEAMARIS_RESEED_PERIOD` | `60` | Seconds between DRBG reseeds |
| `ALEAMARIS_RESEED_INTERVAL_BYTES` | `67108864` | Also reseed after this much output |
| `ALEAMARIS_MAX_STREAM_BYTES` | `68719476736` | Max bytes in one response |
| `ALEAMARIS_MAX_STREAMS` | `8` | Concurrent large streams (429 beyond) |
| `ALEAMARIS_POOL_CAP` | `1048576` | TRNG pool size |
| `ALEAMARIS_CORS_ORIGINS` | `*` | Comma-separated allowed origins |

---

## ⚠️ Disclaimer

This is an **experimental TRNG**.  
Not certified, not audited. The online health tests catch a dead or stuck source, but they cannot
prove that a source is good. Assess your own camera with the NIST tools before trusting
`h_claim`, and do not use this as your only source of cryptographic entropy in production.
Use it for research, retro-gaming fun, casino demos, and as a learning tool.

---

## ❤️ Credits

Created in **Reimon’s Workshop**, fueled by solder smoke, ocean waves, candle flames, and too much coffee.  
Inspired by the chaos of nature, the elegance of cryptography, and the nostalgia of Pokémon Red.
