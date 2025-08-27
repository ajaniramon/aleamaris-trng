from typing import Optional
from .config import GenConfig
from .sources import VideoSource, FileVideoSource
from .features import to_gray_small, make_features
from .utils import dump_debug
import struct, secrets, hmac, hashlib
import cv2
import numpy as np

def hkdf_mix(key: bytes, data: bytes, out_len: int = 32) -> bytes:
    """
    HKDF-like mixer (HMAC-SHA256 extract+expand). Suficiente para rotar la key interna.
    """
    if not key:
        key = b"\x00" * 32
    prk = hmac.new(key, data, hashlib.sha256).digest()
    t = b""; out = b""; counter = 1
    while len(out) < out_len:
        t = hmac.new(prk, t + b"" + bytes([counter]), hashlib.sha256).digest()
        out += t; counter += 1
    return out[:out_len]

def blake2b_keyed(key: bytes, *parts: bytes, digest_size: int = 32) -> bytes:
    h = hashlib.blake2b(key=key, digest_size=digest_size)
    for p in parts:
        if not p:
            continue
        h.update(p)
    return h.digest()

class TRNGGenerator:
    def __init__(self, source: VideoSource, cfg: GenConfig):
        self.source = source
        self.cfg = cfg
        # Salt único por sesión/boot
        self.epoch_salt = secrets.token_bytes(32)
        # Contadores anti-patrón
        self.pass_counter = 0         # sube al terminar una pasada/permuta o al rebobinar
        self.global_counter = 0       # sube cada frame procesado
        # Keyed whitening interna que rotamos periódicamente
        self.key = secrets.token_bytes(32)
        self.key_frames_since_reseed = 0
        self.key_reseed_interval_frames = 512  # ajustable si quieres
        # Detector de repetición sencillo (ventana LRU)
        self._recent = set()
        self._recent_order = []  # lista para poder purgar en FIFO
        self._recent_cap = 4096

    # ---------- utilidades de permutación (seekable file sources) ----------

    def _is_seekable_file(self) -> bool:
        return isinstance(self.source, FileVideoSource) and hasattr(self.source, "cap")

    def _get_frame_count(self) -> int:
        try:
            return int(self.source.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        except Exception:
            return -1

    def _permute_indices(self, n: int) -> list[int]:
        # Fisher-Yates usando secrets.randbelow (criptográficamente fuerte)
        idx = list(range(0, n, self.cfg.stride))
        for i in range(len(idx) - 1, 0, -1):
            r = secrets.randbelow(i + 1)
            idx[i], idx[r] = idx[r], idx[i]
        return idx

    def _read_frame_at(self, i: int) -> Optional[np.ndarray]:
        # Seek y read en fuentes de fichero
        try:
            self.source.cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, frame = self.source.cap.read()
            if not ok:
                return None
            return frame
        except Exception:
            return None

    # ---------- rotación de clave y detector ----------

    def _rotate_key(self, material: bytes):
        salt = secrets.token_bytes(32)
        self.key = hkdf_mix(self.key, material + salt + struct.pack(">II", self.pass_counter, self.global_counter), 32)
        self.key_frames_since_reseed = 0

    def _recent_add_and_check(self, digest: bytes) -> bool:
        """Devuelve True si el digest ya estaba (repetición)."""
        rep = digest in self._recent
        if not rep:
            self._recent.add(digest)
            self._recent_order.append(digest)
            if len(self._recent_order) > self._recent_cap:
                # Purga FIFO
                old = self._recent_order.pop(0)
                if old in self._recent:
                    self._recent.remove(old)
        return rep

    # ---------- pipeline principal ----------

    def _process_frame_bytes(self, frame: np.ndarray, prev_small: Optional[np.ndarray], frame_idx: int) -> tuple[bytes, np.ndarray]:
        gray_small = to_gray_small(frame, self.cfg.resize)
        feats = make_features(gray_small, prev_small, self.cfg.use_diff)
        # Mezcla anti-patrón: epoch_salt + contadores + features + frame_idx
        header = (
            self.epoch_salt +
            struct.pack(">III", self.pass_counter, self.global_counter, frame_idx)
        )
        # Keyed whitening con blake2b (key interna que vamos rotando)
        dgst = blake2b_keyed(self.key, header, feats, digest_size=32)  # 32 bytes por frame

        # Detector de repetición (si algo raro pasa)
        _ = self._recent_add_and_check(dgst)
        # Reseed de la key periódicamente con material fresco
        self.key_frames_since_reseed += 1
        if self.key_frames_since_reseed >= self.key_reseed_interval_frames:
            self._rotate_key(dgst)

        return dgst, gray_small

    def produce(self) -> bytes:
        want = max(1, self.cfg.bytes_total)
        produced = bytearray()

        # Ruta 1: fichero seekable con permutación de frames
        if self._is_seekable_file():
            prev_small = None
            debug_left = self.cfg.debug_frames if self.cfg.debug else 0
            try:
                total = self._get_frame_count()
                if total <= 0:
                    # Fallback a ruta 2 si no podemos contar frames
                    raise RuntimeError("non-positive frame count")
                # Genera una permutación inicial
                indices = self._permute_indices(total)
                p = 0  # puntero dentro de la permutación
                frame_idx = 0
                while len(produced) < want:
                    if p >= len(indices):
                        # Fin de la permutación: nueva pasada
                        self.pass_counter += 1
                        self.epoch_salt = secrets.token_bytes(32)  # nueva época por pasada
                        indices = self._permute_indices(total)
                        p = 0
                        prev_small = None  # resetea ref para diffs

                    i = indices[p]; p += 1
                    frame = self._read_frame_at(i)
                    if frame is None:
                        continue

                    dgst, gray_small = self._process_frame_bytes(frame, prev_small, frame_idx)

                    # acumula
                    need = want - len(produced)
                    if need >= len(dgst):
                        produced.extend(dgst)
                    else:
                        produced.extend(dgst[:need])

                    # debug opcional solo primeros N frames
                    if debug_left > 0:
                        dump_debug(frame_idx, frame, gray_small, b"", dgst,
                                   self.cfg.resize, self.cfg.stride, self.cfg.use_diff, prev_small)
                        debug_left -= 1

                    prev_small = gray_small
                    frame_idx += 1
                    self.global_counter += 1
            finally:
                self.source.release()

            print(f"gen: Video processed (seekable). Generated {len(produced)} bytes")
            return bytes(produced)

        # Ruta 2: fuente no seekable (cámara o lectura lineal)
        prev_small = None
        frame_idx = 0
        debug_left = self.cfg.debug_frames if self.cfg.debug else 0
        try:
            while len(produced) < want:
                frame = self.source.read()
                if frame is None:
                    # rebobina si es fichero (si tiene rewind); en cámara no hace nada
                    print("gen: rewinding/non-seekable source loop.")
                    self.source.rewind()
                    prev_small = None
                    self.pass_counter += 1
                    # refresca epoch salt por pasada
                    self.epoch_salt = secrets.token_bytes(32)
                    continue
                if (frame_idx % self.cfg.stride) != 0:
                    frame_idx += 1
                    continue

                dgst, gray_small = self._process_frame_bytes(frame, prev_small, frame_idx)

                # acumula
                need = want - len(produced)
                if need >= len(dgst):
                    produced.extend(dgst)
                else:
                    produced.extend(dgst[:need])

                # debug opcional solo primeros N frames
                if debug_left > 0:
                    dump_debug(frame_idx, frame, gray_small, b"", dgst,
                               self.cfg.resize, self.cfg.stride, self.cfg.use_diff, prev_small)
                    debug_left -= 1

                prev_small = gray_small
                frame_idx += 1
                self.global_counter += 1
        finally:
            self.source.release()

        print(f"gen: Video processed (linear). Generated {len(produced)} bytes")
        return bytes(produced)
