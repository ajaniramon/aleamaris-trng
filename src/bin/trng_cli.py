#!/usr/bin/env python3
"""AleaMaris TRNG from the command line.

Examples:
  python src/bin/trng_cli.py --cam 0 --bytes 4096 --out out.bin
  python src/bin/trng_cli.py --cam 0 --dump-raw raw.bin --raw-limit 1000000   # for NIST ea_non_iid
  python src/bin/trng_cli.py --video sample.MP4 --demo --bytes 1024           # pipeline demo, NOT random
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from trng.generator import BLOCK_BYTES, EntropyCollector  # noqa: E402
from trng.queue import TrngQueue  # noqa: E402
from trng.sources import CameraVideoSource, FileVideoSource  # noqa: E402
from trng.utils import RawSampleWriter  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="TRNG from camera noise (CLI).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cam", type=int, help="Camera index (live sensor noise).")
    src.add_argument("--video", help="Video file. Deterministic: only useful with --demo or --dump-raw.")
    ap.add_argument("--demo", action="store_true",
                    help="Credit entropy to a video file anyway (demo only: output is NOT secret).")
    ap.add_argument("--bytes", type=int, default=1024, help="Conditioned bytes to produce.")
    ap.add_argument("--h-claim", type=float, default=1.0, help="Max min-entropy credited per sample (bits).")
    ap.add_argument("--max-samples", type=int, default=16384, help="Raw samples taken per frame.")
    ap.add_argument("--max-frames", type=int, default=100_000, help="Give up after this many frames.")
    ap.add_argument("--dump-raw", help="Write raw 8-bit noise samples here (for SP 800-90B tools).")
    ap.add_argument("--raw-limit", type=int, default=1_000_000, help="Max raw samples to dump.")
    ap.add_argument("--out", help="Binary output file. Default: print hex.")
    args = ap.parse_args()

    if args.video:
        factory = lambda: FileVideoSource(args.video)  # noqa: E731
    else:
        factory = lambda: CameraVideoSource(args.cam)  # noqa: E731

    raw = RawSampleWriter(args.dump_raw, args.raw_limit) if args.dump_raw else None
    # the collector only admits whole 32-byte blocks: round the pool up to fit them
    pool = TrngQueue(cap_bytes=max(1, -(-args.bytes // BLOCK_BYTES)) * BLOCK_BYTES)
    col = EntropyCollector(factory, pool, h_claim=args.h_claim, max_samples=args.max_samples,
                           credit_non_physical=args.demo, raw_sink=raw)
    try:
        for _ in range(args.max_frames):
            done_bytes = pool.available() >= args.bytes
            done_raw = raw is None or raw.written >= args.raw_limit
            if done_bytes and done_raw:
                break
            if col.step() < 0:
                break
    finally:
        col.stop()
        if raw:
            raw.close()

    st = col.status()
    print(json.dumps(st, indent=2), file=sys.stderr)
    if raw:
        print(f"[raw] {raw.written} samples -> {args.dump_raw}", file=sys.stderr)
    if not st["entropy_credited"]:
        print("[!] source not credited (recorded video without --demo): no output produced", file=sys.stderr)

    data = pool.poll(args.bytes)
    if args.out:
        with open(args.out, "wb") as f:
            f.write(data)
        print(f"[OK] {len(data)} bytes -> {args.out}", file=sys.stderr)
    elif data:
        print(data.hex())
    if len(data) < args.bytes:
        print(f"[!] only {len(data)}/{args.bytes} bytes produced (state: {st['state']})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
