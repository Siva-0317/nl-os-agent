"""verified_cache.py - Semantic verified-command cache.

Stores (request embedding, env key, structured proposal) only after a proposal
has executed successfully. On lookup:
1. Environment key (OS, shell, cwd) must match exactly.
2. Cosine similarity between query embedding and stored embedding >= threshold.
3. Every cache hit MUST still be passed to static effect analysis (effects.analyse)
   by the caller to guarantee no safety regressions.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


def make_env_key(os_name: str, shell_name: str, cwd: str) -> str:
    """Generate a canonical environment key from (OS, shell, cwd).

    Normalizes cwd path and lowercases environment components to prevent
    cross-environment cache contamination.
    """
    clean_os = (os_name or "").strip().lower()
    clean_shell = (shell_name or "").strip().lower()
    norm_cwd = os.path.normpath(os.path.abspath(cwd))
    if os.name == "nt":
        norm_cwd = os.path.normcase(norm_cwd)
    return f"{clean_os}:{clean_shell}:{norm_cwd}"


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns a float in [-1.0, 1.0]. If either vector has zero magnitude,
    returns 0.0.
    """
    if len(vec_a) != len(vec_b):
        raise ValueError(
            f"Vector dimension mismatch: len(a)={len(vec_a)} != len(b)={len(vec_b)}"
        )
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0

    sim = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    return max(-1.0, min(1.0, sim))


def simple_embed(text: str, dim: int = 256) -> list[float]:
    """Lightweight deterministic feature-hashing embedder.

    Produces an L2-normalized dense embedding vector from word tokens
    and character 3-grams without requiring heavy external deep learning models.
    """
    tokens = re.findall(r"\w+", (text or "").lower())
    if not tokens:
        return [0.0] * dim

    vec = [0.0] * dim
    for token in tokens:
        # Full token hash
        h_tok = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[h_tok] += 1.0

        # Character tri-grams for subword morphological similarity
        if len(token) >= 3:
            for i in range(len(token) - 2):
                tri = token[i : i + 3]
                h_tri = int(hashlib.md5(tri.encode("utf-8")).hexdigest(), 16) % dim
                vec[h_tri] += 0.5

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [x / norm for x in vec]
    return vec


@dataclass
class CacheEntry:
    """Verified proposal cache entry."""

    request: str
    embedding: list[float]
    env_key: str
    proposal: dict[str, Any]
    success_count: int = 1
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


class VerifiedCommandCache:
    """Semantic verified-command cache.

    Guarantees:
    - Entries are only inserted after successful execution (`record_success`).
    - Entries are strictly isolated by canonical env_key (OS, shell, cwd).
    - Cache hits return structured proposals only when cosine similarity >= threshold.
    - Callers must pass any returned proposal to `effects.analyse` for safety verification.
    """

    def __init__(
        self,
        threshold: float = 0.90,
        embedding_fn: Callable[[str], list[float]] | None = None,
        max_entries: int = 1000,
    ) -> None:
        self.threshold = threshold
        self.embedding_fn = embedding_fn or simple_embed
        self.max_entries = max_entries
        # env_key -> list[CacheEntry]
        self._entries: dict[str, list[CacheEntry]] = {}

    def record_success(
        self,
        request: str,
        env_key: str,
        proposal: dict[str, Any],
        embedding: Sequence[float] | None = None,
    ) -> CacheEntry:
        """Store a verified proposal after successful execution.

        Only proposals that the user approved and that completed successfully
        may be recorded.
        """
        if not isinstance(proposal, dict):
            raise TypeError(f"proposal must be a dict, got {type(proposal).__name__}")

        emb = list(embedding) if embedding is not None else self.embedding_fn(request)

        bucket = self._entries.setdefault(env_key, [])

        # Check if identical request or near-duplicate already exists in this env
        for existing in bucket:
            sim = cosine_similarity(existing.embedding, emb)
            if sim >= 0.999 or existing.request.strip().lower() == request.strip().lower():
                existing.proposal = copy.deepcopy(proposal)
                existing.success_count += 1
                existing.last_used = time.time()
                return existing

        # Evict oldest entry globally if budget exceeded
        if len(self) >= self.max_entries:
            self._evict_lru()

        entry = CacheEntry(
            request=request,
            embedding=emb,
            env_key=env_key,
            proposal=copy.deepcopy(proposal),
            success_count=1,
            created_at=time.time(),
            last_used=time.time(),
        )
        bucket.append(entry)
        return entry

    def lookup(
        self,
        request: str,
        env_key: str,
        embedding: Sequence[float] | None = None,
        threshold: float | None = None,
    ) -> dict[str, Any] | None:
        """Lookup a cached proposal matching env_key and similarity threshold.

        Returns deepcopy of proposal on hit, or None on miss.
        """
        proposal, _ = self.lookup_with_score(
            request=request,
            env_key=env_key,
            embedding=embedding,
            threshold=threshold,
        )
        return proposal

    def lookup_with_score(
        self,
        request: str,
        env_key: str,
        embedding: Sequence[float] | None = None,
        threshold: float | None = None,
    ) -> tuple[dict[str, Any] | None, float]:
        """Lookup a proposal and return (proposal, similarity_score)."""
        bucket = self._entries.get(env_key)
        if not bucket:
            return None, 0.0

        target_threshold = self.threshold if threshold is None else threshold
        emb = list(embedding) if embedding is not None else self.embedding_fn(request)

        best_entry: CacheEntry | None = None
        best_sim = -1.0

        for entry in bucket:
            sim = cosine_similarity(emb, entry.embedding)
            if sim > best_sim:
                best_sim = sim
                best_entry = entry

        if best_entry is not None and best_sim >= target_threshold:
            best_entry.last_used = time.time()
            return copy.deepcopy(best_entry.proposal), best_sim

        return None, max(0.0, best_sim)

    def invalidate_for_cwd(self, cwd: str) -> int:
        """Invalidate all cached proposals associated with a specific cwd path."""
        norm_cwd = os.path.normpath(os.path.abspath(cwd))
        raw_norm = os.path.normpath(cwd)
        if os.name == "nt":
            norm_cwd = os.path.normcase(norm_cwd)
            raw_norm = os.path.normcase(raw_norm)

        keys_to_remove = [
            k for k in self._entries.keys()
            if k.endswith(f":{norm_cwd}") or k.endswith(f":{raw_norm}")
        ]
        removed_count = sum(len(self._entries[k]) for k in keys_to_remove)
        for k in keys_to_remove:
            del self._entries[k]
        return removed_count

    def clear(self) -> None:
        """Clear all cached entries."""
        self._entries.clear()

    def _evict_lru(self) -> None:
        """Evict the least recently used entry across all buckets."""
        oldest_entry: CacheEntry | None = None
        oldest_key: str | None = None

        for key, bucket in self._entries.items():
            for entry in bucket:
                if oldest_entry is None or entry.last_used < oldest_entry.last_used:
                    oldest_entry = entry
                    oldest_key = key

        if oldest_key and oldest_entry:
            self._entries[oldest_key].remove(oldest_entry)
            if not self._entries[oldest_key]:
                del self._entries[oldest_key]

    def __len__(self) -> int:
        return sum(len(b) for b in self._entries.values())
