"""Runtime settings, read from ALEAMARIS_* environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


@dataclass
class Settings:
    # entropy source
    video_path: str | None = None
    use_cam: bool = False
    cam_index: int = 0
    credit_file_source: bool = False     # demo only: credit entropy to a recorded video
    h_claim: float = 1.0                 # max min-entropy credited per 8-bit sample
    max_samples_per_frame: int = 16384
    bits_per_block: int = 512            # assessed bits behind each 256-bit output block
    max_consecutive_failures: int = 30
    stall_timeout_sec: float = 10.0      # health "degraded" if no frame for this long

    # TRNG pool
    pool_cap_bytes: int = 1 << 20
    trng_max_request: int = 4096

    # DRBG
    allow_urandom: bool = False          # may the DRBG run on os.urandom without TRNG entropy?
    boot_timeout_sec: float = 10.0
    reseed_period_sec: float = 60.0
    reseed_bytes: int = 64
    reseed_interval_bytes: int = 1 << 26  # also reseed after 64 MiB of output

    # API
    api_key: str | None = None
    max_stream_bytes: int = 64 << 30      # 64 GiB per request
    max_concurrent_streams: int = 8
    stream_chunk_bytes: int = 1 << 20
    max_json_ints: int = 1_000_000
    max_ingest_bytes: int = 1 << 20
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    @classmethod
    def from_env(cls) -> "Settings":
        video = os.environ.get("ALEAMARIS_VIDEO") or None
        return cls(
            video_path=video,
            use_cam=_env_bool("ALEAMARIS_USE_CAM", False),
            cam_index=_env_int("ALEAMARIS_CAM", 0),
            credit_file_source=_env_bool("ALEAMARIS_CREDIT_FILE_SOURCE", False),
            h_claim=_env_float("ALEAMARIS_H_CLAIM", 1.0),
            max_samples_per_frame=_env_int("ALEAMARIS_MAX_SAMPLES", 16384),
            bits_per_block=_env_int("ALEAMARIS_BITS_PER_BLOCK", 512),
            max_consecutive_failures=_env_int("ALEAMARIS_MAX_HEALTH_FAILURES", 30),
            stall_timeout_sec=_env_float("ALEAMARIS_STALL_TIMEOUT", 10.0),
            pool_cap_bytes=_env_int("ALEAMARIS_POOL_CAP", 1 << 20),
            allow_urandom=_env_bool("ALEAMARIS_ALLOW_URANDOM", False),
            boot_timeout_sec=_env_float("ALEAMARIS_BOOT_TIMEOUT", 10.0),
            reseed_period_sec=_env_float("ALEAMARIS_RESEED_PERIOD", 60.0),
            reseed_bytes=_env_int("ALEAMARIS_RESEED_BYTES", 64),
            reseed_interval_bytes=_env_int("ALEAMARIS_RESEED_INTERVAL_BYTES", 1 << 26),
            api_key=os.environ.get("ALEAMARIS_API_KEY") or None,
            max_stream_bytes=_env_int("ALEAMARIS_MAX_STREAM_BYTES", 64 << 30),
            max_concurrent_streams=_env_int("ALEAMARIS_MAX_STREAMS", 8),
            max_ingest_bytes=_env_int("ALEAMARIS_MAX_INGEST_BYTES", 1 << 20),
            cors_origins=[o.strip() for o in os.environ.get("ALEAMARIS_CORS_ORIGINS", "*").split(",") if o.strip()],
        )
