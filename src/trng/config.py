from dataclasses import dataclass

@dataclass
class GenConfig:
    bytes_total: int = 1024
    resize: int = 64
    stride: int = 1
    use_diff: bool = False
    debug: bool = False
    debug_frames: int = 4