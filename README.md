# 🌊🎮 AleaMaris TRNG — True Randomness from Chaos

> *An experimental True Random Number Generator powered by the chaos of nature.*  
> Ocean waves, candle flames, ambient noise, and retro hardware fused into unpredictable randomness — ready for games, emulators, research, and maker demos.

---

![Python](https://img.shields.io/badge/python-3.11-blue?style=for-the-badge&logo=python)
![License](https://img.shields.io/badge/license-MIT-black?style=for-the-badge)

---

<p align="center">
  <img src="aleamaris_logo.png" alt="AleaMaris Logo" width="400"/>
</p>

## ✨ Features

### ✅ Ready
- Entropy extraction from video/camera (grayscale reduction, edges, temporal diff)
- SHA-256 conditioning
- CLI generator (`bin/trng_cli.py`)
- Debug mode (PNG/BIN/JSON dumps of first frames)
- API server (FastAPI)
- Casino-ready RNG methods
- Bias-free rejection sampling for fair dice/roulette
- Advanced extractors (SHAKE256 / BLAKE3)
- Premium DRBG engines (ChaCha20-DRBG, HMAC-DRBG) with reseed

### 🔧 In Progress
- Multi-source entropy (video + audio + sensors)
- Fortuna-style multipools for forward secrecy
- Emulator integration (WRAM mailbox for BGB/Emulicious)
- Game Boy hardware demo (ESP32 + Link Port)
- Observability: entropy metrics, Prometheus/Grafana integration
- Health tests (NIST SP 800-90B RCT/APT online)
- Transparency logging of reseed events
---

## 🧩 Project Structure

```
trng_project/
├─ src/trng/
│  ├─ sources.py        # Webcam / video sources
│  ├─ features.py       # Entropy extraction (gray, edges, diff)
│  ├─ conditioners.py   # Cryptographic whitening
│  ├─ generator.py      # Frame → bytes pipeline
│  ├─ rng.py            # Casino-ready RNG API
│  ├─ csprng.py         # DRBG engines (WIP)
│  ├─ queue.py          # FIFO queue for API server
│  ├─ utils.py          # Helpers (entropy, debug)
│  └─ feeders.py        # Seed provider helpers
├─ api/app.py           # FastAPI API
└─ bin/trng_cli.py      # Command-line interface
```

---

## ⚡ Quickstart

### Install
```bash
pip install -r requirements.txt
export PYTHONPATH=src
```

### Generate random bytes from a video
```bash
python bin/trng_cli.py --video samples/ocean.mp4 --bytes 4096 --out out.bin
```

### Live from a webcam
```bash
python bin/trng_cli.py --cam 0 --bytes 1024 --diff
```

### Debug mode
```bash
python bin/trng_cli.py --video samples/ocean.mp4 --bytes 1024 --debug --debug-frames 5
```

### Run API server
```bash
uvicorn api.app:app --reload --port 8080
```

- Pull bytes:
```bash
curl http://127.0.0.1:8080/trng/bytes?count=512 > out.bin
```

- Check health:
```bash
curl http://127.0.0.1:8080/trng/health
```

---

## 📈 Roadmap

- [x] Frame-based entropy extraction (video/camera)
- [x] SHA-256 conditioning
- [x] CLI & API server
- [ ] Multi-source entropy
- [ ] DRBG with reseeds
- [ ] Emulator & hardware integration
- [ ] Health tests + observability
- [ ] Transparency logs

---

## ⚠️ Disclaimer

This is an **experimental TRNG**.  
Not certified, not audited.  
Do not use it as your only source of cryptographic entropy in production systems.  
Use it for research, retro-gaming fun, casino demos, and as a learning tool.

---

## ❤️ Credits

Created in **Reimon’s Workshop**, fueled by solder smoke, ocean waves, candle flames, and too much coffee.  
Inspired by the chaos of nature, the elegance of cryptography, and the nostalgia of Pokémon Red.
