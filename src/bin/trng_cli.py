#!/usr/bin/env python3
import argparse, sys
from trng.config import GenConfig
from trng.sources import FileVideoSource, CameraVideoSource
from trng.generator import TRNGGenerator

def main():
    ap = argparse.ArgumentParser(description="TRNG desde video/cámara (CLI).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="Ruta a archivo de vídeo.")
    src.add_argument("--cam", type=int, help="Índice de cámara (0 por defecto).")
    ap.add_argument("--bytes", type=int, default=1024, help="Bytes a generar.")
    ap.add_argument("--resize", type=int, default=64, help="Reducción NxN.")
    ap.add_argument("--stride", type=int, default=1, help="Procesa 1 de cada N frames.")
    ap.add_argument("--diff", action="store_true", help="Añade diferencia temporal.")
    ap.add_argument("--debug", action="store_true", help="Dumpea artefactos de los primeros frames.")
    ap.add_argument("--debug-frames", type=int, default=4, help="Cuántos frames dumpear en debug.")
    ap.add_argument("--out", help="Fichero de salida binario. Si no, imprime hex.")
    args = ap.parse_args()

    cfg = GenConfig(bytes_total=args.bytes, resize=args.resize, stride=args.stride,
                    use_diff=args.diff, debug=args.debug, debug_frames=args.debug_frames)

    source = FileVideoSource(args.video) if args.video else CameraVideoSource(args.cam if args.cam is not None else 0)
    gen = TRNGGenerator(source, cfg)
    data = gen.produce()

    if args.out:
        with open(args.out, "wb") as f:
            f.write(data)
        print(f"[OK] {len(data)} bytes -> {args.out}")
    else:
        print(data.hex())
        print(f"[OK] {len(data)} bytes (hex arriba)")

if __name__ == "__main__":
    main()
