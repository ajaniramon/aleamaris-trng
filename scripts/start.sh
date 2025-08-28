#!/usr/bin/env bash
# AleaMaris TRNG — init script (Linux/macOS)
# - Busca un puerto libre aleatorio
# - Permite configurar variables de entorno clave
# - Lanza uvicorn con el app_dir=src y app=api.app:app
#
# Uso rápido:
#   ./scripts/start.sh
#
# Flags opcionales:
#   -H, --host <host>           Host a escuchar (def: 0.0.0.0)
#   -p, --port <port>           Puerto fijo (si no, escoge uno libre aleatorio)
#   -v, --video <path>          Ruta vídeo (export ALEAMARIS_VIDEO)
#   -c, --cam <index>           Índice de cámara (export ALEAMARIS_USE_CAM=1 y ALEAMARIS_CAM)
#   -k, --api-key <key>         X-API-Key para proteger ingest/reseed (export ALEAMARIS_API_KEY)
#   --allow-urandom             Permite /dev/urandom en boot (export ALEAMARIS_ALLOW_URANDOM=1)
#   --reseed-period <sec>       Periodo de reseed DRBG (export ALEAMARIS_RESEED_PERIOD)
#   --reseed-bytes <n>          Bytes por reseed DRBG (export ALEAMARIS_RESEED_BYTES)
#   --boot-bytes <n>            Bytes de boot del DRBG (export ALEAMARIS_BOOT_BYTES)
#   --log-level <level>         Log level uvicorn (info, warning, error, debug)
#   --reload                    Habilita reload (dev)
#
# Ejemplos:
#   ./scripts/start.sh -v sample.mp4 --reload
#   ./scripts/start.sh -c 0 -k secret123 --reseed-period 60 --reseed-bytes 64
set -euo pipefail

HOST="0.0.0.0"
PORT=""
VIDEO=""
USE_CAM=""
CAM_INDEX=""
API_KEY=""
ALLOW_URANDOM=0
RESEED_PERIOD=""
RESEED_BYTES=""
BOOT_BYTES=""
LOG_LEVEL="info"
RELOAD=0

# Pick a free random port in [10240, 65535]
choose_port() {
  python - "$@" <<'PY'
import random, socket
for _ in range(200):
    port = random.randint(10240, 65535)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            print(port)
            raise SystemExit(0)
        except OSError:
            pass
print(0)
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -H|--host) HOST="$2"; shift 2;;
    -p|--port) PORT="$2"; shift 2;;
    -v|--video) VIDEO="$2"; shift 2;;
    -c|--cam) USE_CAM=1; CAM_INDEX="$2"; shift 2;;
    -k|--api-key) API_KEY="$2"; shift 2;;
    --allow-urandom) ALLOW_URANDOM=1; shift 1;;
    --reseed-period) RESEED_PERIOD="$2"; shift 2;;
    --reseed-bytes) RESEED_BYTES="$2"; shift 2;;
    --boot-bytes) BOOT_BYTES="$2"; shift 2;;
    --log-level) LOG_LEVEL="$2"; shift 2;;
    --reload) RELOAD=1; shift 1;;
    -h|--help)
      sed -n '1,80p' "$0"; exit 0;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

if [[ -z "$PORT" ]]; then
  PORT="$(choose_port)"
  if [[ "$PORT" == "0" || -z "$PORT" ]]; then
    echo "[start.sh] No se pudo elegir puerto libre aleatorio" >&2
    exit 1
  fi
fi

export ALEAMARIS_API_KEY="$API_KEY"
if [[ -n "$VIDEO" ]]; then
  export ALEAMARIS_VIDEO="$VIDEO"
  unset ALEAMARIS_USE_CAM ALEAMARIS_CAM || true
fi
if [[ -n "$USE_CAM" ]]; then
  export ALEAMARIS_USE_CAM=1
  export ALEAMARIS_CAM="${CAM_INDEX:-0}"
fi
if [[ "$ALLOW_URANDOM" == "1" ]]; then export ALEAMARIS_ALLOW_URANDOM=1; fi
if [[ -n "$RESEED_PERIOD" ]]; then export ALEAMARIS_RESEED_PERIOD="$RESEED_PERIOD"; fi
if [[ -n "$RESEED_BYTES" ]]; then export ALEAMARIS_RESEED_BYTES="$RESEED_BYTES"; fi
if [[ -n "$BOOT_BYTES" ]]; then export ALEAMARIS_BOOT_BYTES="$BOOT_BYTES"; fi

APP="api.app:app"
APP_DIR="src"

echo "[AleaMaris] Host: $HOST  Port: $PORT" >&2
if [[ -n "$VIDEO" ]]; then echo "[AleaMaris] Video: $VIDEO" >&2; fi
if [[ -n "$USE_CAM" ]]; then echo "[AleaMaris] Camera index: ${CAM_INDEX:-0}" >&2; fi
if [[ -n "$API_KEY" ]]; then echo "[AleaMaris] API key: (set)" >&2; fi

UV_FLAGS=("--app-dir" "$APP_DIR" "$APP" "--host" "$HOST" "--port" "$PORT" "--log-level" "$LOG_LEVEL")
if [[ "$RELOAD" == "1" ]]; then UV_FLAGS+=("--reload"); fi

# Lanzar uvicorn
exec uvicorn "${UV_FLAGS[@]}"
