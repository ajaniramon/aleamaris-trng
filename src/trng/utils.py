import math, json
import numpy as np
from typing import Optional

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

def shannon_entropy_per_byte(data: bytes) -> float:
    if not data: return 0.0
    counts = [0]*256
    for b in data: counts[b] += 1
    n = len(data); ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return ent

def save_png_gray(arr: np.ndarray, path: str):
    if HAVE_PIL:
        Image.fromarray(arr, mode='L').save(path)

def dump_debug(frame_idx: int,
               bgr: np.ndarray,
               gray_small: np.ndarray,
               features: bytes,
               digest: bytes,
               resize: int,
               stride: int,
               use_diff: bool,
               prev_gray_small: Optional[np.ndarray]):
    base = f"f{frame_idx:05d}"
    if HAVE_PIL:
        from PIL import Image
        import cv2
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        Image.fromarray(rgb).save(f"{base}_01_frame_rgb.png")
        save_png_gray(gray_small, f"{base}_02_gray_{resize}x{resize}.png")
        if prev_gray_small is not None and use_diff:
            import cv2
            diff = cv2.absdiff(gray_small, prev_gray_small)
            save_png_gray(diff, f"{base}_04_diff_{resize}x{resize}.png")
    with open(f"{base}_11_gray.bin", "wb") as f: f.write(gray_small.tobytes())
    with open(f"{base}_12_features.bin", "wb") as f: f.write(features)
    with open(f"{base}_30_digest.bin", "wb") as f: f.write(digest)
    with open(f"{base}_30_digest.hex.txt", "w") as f: f.write(digest.hex()+"\n")

    rep = {
        "frame": frame_idx,
        "resize": resize,
        "stride": stride,
        "use_diff": bool(use_diff),
        "lens": { "gray": len(gray_small.tobytes()),
                  "features": len(features),
                  "digest": len(digest) },
        "entropy_bits_per_byte": {
            "gray": round(shannon_entropy_per_byte(gray_small.tobytes()), 4),
            "features": round(shannon_entropy_per_byte(features), 4),
            "digest": round(shannon_entropy_per_byte(digest), 4)
        },
        "digest_hex": digest.hex()
    }
    with open(f"{base}_40_report.json", "w") as f:
        json.dump(rep, f, indent=2)
