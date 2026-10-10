"""Near-duplicate detection with MinHash over word shingles.

Signatures are lists of 64-bit-safe integers so they fit a PostgreSQL bigint[].
"""

from __future__ import annotations

import hashlib
import random

from .normalize import normalize_text

NUM_PERM = 64
SHINGLE = 3
THRESHOLD = 0.8
_PRIME = (1 << 61) - 1
_rng = random.Random(20261009)  # fixed: signatures must be stable across runs
_PERMS = [(_rng.randrange(1, _PRIME), _rng.randrange(0, _PRIME)) for _ in range(NUM_PERM)]


def shingles(text: str, k: int = SHINGLE) -> set[str]:
    words = normalize_text(text).split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big") % _PRIME


def minhash(text: str) -> list[int]:
    hashes = [_h(s) for s in shingles(text)]
    if not hashes:
        return [_PRIME] * NUM_PERM
    return [min((a * x + b) % _PRIME for x in hashes) for a, b in _PERMS]


def similarity(a: list[int], b: list[int]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def find_duplicate(
    signature: list[int], candidates: list[tuple[int, list[int]]], threshold: float = THRESHOLD
) -> int | None:
    """Id of the most similar candidate at or above the threshold."""
    best, best_sim = None, threshold
    for cid, sig in candidates:
        sim = similarity(signature, sig)
        if sim >= best_sim:
            best, best_sim = cid, sim
    return best
