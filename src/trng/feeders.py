# src/trng/feeders.py
from typing import Callable
from .generator import TRNGGenerator

def make_seed_provider_from_generator(gen: TRNGGenerator) -> Callable[[int], bytes]:
    """
    Devuelve provider(n) que pide al generador al menos n bytes (rebobinando vídeo si hace falta).
    """
    def provider(n: int) -> bytes:
        # ajusta bytes_total al vuelo para pedir lo necesario
        want = max(n, 1024)
        old = gen.cfg.bytes_total
        gen.cfg.bytes_total = want
        try:
            return gen.produce()
        finally:
            gen.cfg.bytes_total = old
    return provider
