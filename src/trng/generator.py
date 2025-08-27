from typing import Optional
from .config import GenConfig
from .sources import VideoSource
from .features import to_gray_small, make_features
from .conditioners import sha256_bytes
from .utils import dump_debug

class TRNGGenerator:
    def __init__(self, source: VideoSource, cfg: GenConfig):
        self.source = source
        self.cfg = cfg

    def produce(self) -> bytes:
        want = max(1, self.cfg.bytes_total)
        produced = bytearray()
        prev_small = None
        frame_idx = 0
        debug_left = self.cfg.debug_frames if self.cfg.debug else 0

        try:
            while len(produced) < want:
                frame = self.source.read()
                if frame is None:
                    # rebobina si es fichero
                    self.source.rewind()
                    prev_small = None
                    continue
                if (frame_idx % self.cfg.stride) != 0:
                    frame_idx += 1
                    continue

                gray_small = to_gray_small(frame, self.cfg.resize)
                feats = make_features(gray_small, prev_small, self.cfg.use_diff)
                dgst = sha256_bytes(feats)  # 32 bytes por frame

                # acumula
                need = want - len(produced)
                if need >= len(dgst):
                    produced.extend(dgst)
                else:
                    produced.extend(dgst[:need])

                # debug opcional solo primeros N frames
                if debug_left > 0:
                    dump_debug(frame_idx, frame, gray_small, feats, dgst,
                               self.cfg.resize, self.cfg.stride, self.cfg.use_diff, prev_small)
                    debug_left -= 1

                prev_small = gray_small
                frame_idx += 1
        finally:
            self.source.release()

        return bytes(produced)
