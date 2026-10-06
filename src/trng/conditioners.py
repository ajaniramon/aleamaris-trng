import hashlib
import struct

DOMAIN = b"AleaMaris/condition/v1"


def sha256_condition(samples: bytes, counter: int) -> bytes:
    """Unkeyed SHA-256 conditioning (vetted conditioner, SP 800-90B 3.1.5.1.1).

    No secret key on purpose: the output depends only on the noise, so a dead
    source cannot hide behind a random key. The counter is public and only
    provides domain separation; it is never credited with entropy.
    """
    h = hashlib.sha256(DOMAIN)
    h.update(struct.pack(">Q", counter))
    h.update(samples)
    return h.digest()
