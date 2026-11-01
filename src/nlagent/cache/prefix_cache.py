"""
prefix_cache.py - Hierarchical Prefix-State Cache (HPSC) for hybrid/recurrent LLMs.

Follows Section 3.3 of Research_Plan_Context_and_Latency.md:
- Cache entries: (SHA-256 of token-ids at boundaries B0/B1) -> opaque snapshot blob.
- Boundaries:
    B0 = end of L0 (static prompt/schema/safety rules)
    B1 = end of L1 (session environment metadata)
- Longest-prefix lookup: tests B1 first, then B0, else cold miss.
- LRU eviction: strictly bounded by byte budget (RAM limit).
- ModelBackend interface: decouples LLM engine (save_state, load_state, eval).
"""

from __future__ import annotations

import collections
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, Sequence


class ModelBackend(Protocol):
    """
    Interface for LLM model backend runtime state operations.
    Enables testing with in-memory mock backends without calling a live model.
    """

    def save_state(self) -> bytes:
        """Capture opaque binary snapshot of current sequence/KV/SSM state."""
        ...

    def load_state(self, blob: bytes) -> None:
        """Restore model sequence/KV/SSM state from an opaque snapshot blob."""
        ...

    def eval(self, tokens: list[int]) -> list[float]:
        """Evaluate a sequence of token IDs and return outputs/logits."""
        ...


def compute_boundary_key(token_ids: Sequence[int]) -> str:
    """Compute a deterministic SHA-256 hash of token-id sequence at boundary."""
    payload = ",".join(str(t) for t in token_ids).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class CacheEntry:
    """An opaque snapshot entry in the HPSC."""
    key: str
    boundary_name: str  # "B0" or "B1"
    token_count: int
    state_blob: bytes
    size_bytes: int


@dataclass
class EvalResult:
    """Metrics and output from a prefix-cached evaluation turn."""
    output: Any
    hit_boundary: str | None  # "B1", "B0", or None (cold miss)
    cached_tokens: int
    evaluated_tokens: int


class HierarchicalPrefixCache:
    """
    Hierarchical Prefix-State Cache (HPSC) with boundary-aware lookup
    and LRU byte budget eviction.
    """

    def __init__(
        self,
        backend: ModelBackend,
        max_bytes: int = 512 * 1024 * 1024,  # default 512 MB
    ) -> None:
        self.backend = backend
        self.max_bytes = max_bytes
        self.current_bytes = 0
        self._entries: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self.hits = 0
        self.misses = 0

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def put(
        self,
        boundary_name: str,
        token_ids: Sequence[int],
        state_blob: bytes,
    ) -> CacheEntry | None:
        """
        Store a snapshot at boundary. Evicts LRU entries if byte budget is exceeded.
        """
        size_bytes = len(state_blob)
        if size_bytes > self.max_bytes:
            return None

        key = compute_boundary_key(token_ids)

        if key in self._entries:
            old_entry = self._entries.pop(key)
            self.current_bytes -= old_entry.size_bytes

        self.evict_to_budget(needed_bytes=size_bytes)

        entry = CacheEntry(
            key=key,
            boundary_name=boundary_name,
            token_count=len(token_ids),
            state_blob=state_blob,
            size_bytes=size_bytes,
        )

        self._entries[key] = entry
        self.current_bytes += size_bytes
        return entry

    def get(self, token_ids: Sequence[int]) -> CacheEntry | None:
        """
        Retrieve a snapshot for token sequence. Marks entry as most recently used.
        """
        key = compute_boundary_key(token_ids)
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key]
        return None

    def evict_to_budget(self, needed_bytes: int = 0) -> list[str]:
        """
        Evict LRU entries until current_bytes + needed_bytes <= max_bytes.
        Returns list of evicted keys.
        """
        evicted: list[str] = []
        while self._entries and (self.current_bytes + needed_bytes > self.max_bytes):
            evicted_key, entry = self._entries.popitem(last=False)
            self.current_bytes -= entry.size_bytes
            evicted.append(evicted_key)
        return evicted

    def lookup_longest_prefix(
        self,
        token_ids: list[int],
        b0_len: int,
        b1_len: int,
    ) -> tuple[str | None, int, CacheEntry | None]:
        """
        Longest-match lookup: check B1 first, then B0, else None.
        Returns (boundary_name, boundary_len, cache_entry).
        """
        n = len(token_ids)

        # 1. Check B1 boundary first (longest match)
        if 0 < b1_len <= n:
            b1_tokens = token_ids[:b1_len]
            entry = self.get(b1_tokens)
            if entry is not None:
                self.hits += 1
                return "B1", b1_len, entry

        # 2. Check B0 boundary second
        if 0 < b0_len <= n:
            b0_tokens = token_ids[:b0_len]
            entry = self.get(b0_tokens)
            if entry is not None:
                self.hits += 1
                return "B0", b0_len, entry

        # 3. Cold miss
        self.misses += 1
        return None, 0, None

    def process(
        self,
        token_ids: list[int],
        b0_len: int,
        b1_len: int,
    ) -> EvalResult:
        """
        Boundary-aware evaluation:
        1. Look up longest matching snapshot (B1, then B0).
        2. Restore matched snapshot and evaluate only un-cached tokens.
        3. Take and store snapshots at missing boundaries as execution advances.
        4. Return output with cached vs evaluated token accounting.
        """
        n = len(token_ids)
        valid_boundaries = (0 < b0_len <= b1_len <= n)

        if not valid_boundaries:
            out = self.backend.eval(token_ids)
            return EvalResult(
                output=out,
                hit_boundary=None,
                cached_tokens=0,
                evaluated_tokens=n,
            )

        boundary_name, cached_len, entry = self.lookup_longest_prefix(token_ids, b0_len, b1_len)

        if boundary_name == "B1" and entry is not None:
            self.backend.load_state(entry.state_blob)
            remaining_tokens = token_ids[b1_len:]
            out = self.backend.eval(remaining_tokens) if remaining_tokens else []
            return EvalResult(
                output=out,
                hit_boundary="B1",
                cached_tokens=b1_len,
                evaluated_tokens=len(remaining_tokens),
            )

        elif boundary_name == "B0" and entry is not None:
            self.backend.load_state(entry.state_blob)

            # Evaluate B0 -> B1
            b0_to_b1_tokens = token_ids[b0_len:b1_len]
            if b0_to_b1_tokens:
                self.backend.eval(b0_to_b1_tokens)

            # Checkpoint B1
            b1_blob = self.backend.save_state()
            self.put("B1", token_ids[:b1_len], b1_blob)

            # Evaluate remaining L2 tokens
            remaining_tokens = token_ids[b1_len:]
            out = self.backend.eval(remaining_tokens) if remaining_tokens else []
            return EvalResult(
                output=out,
                hit_boundary="B0",
                cached_tokens=b0_len,
                evaluated_tokens=len(token_ids) - b0_len,
            )

        else:
            # Cold miss: evaluate 0 -> B0 -> B1 -> L2
            self.backend.eval(token_ids[:b0_len])
            b0_blob = self.backend.save_state()
            self.put("B0", token_ids[:b0_len], b0_blob)

            b0_to_b1_tokens = token_ids[b0_len:b1_len]
            if b0_to_b1_tokens:
                self.backend.eval(b0_to_b1_tokens)

            b1_blob = self.backend.save_state()
            self.put("B1", token_ids[:b1_len], b1_blob)

            remaining_tokens = token_ids[b1_len:]
            out = self.backend.eval(remaining_tokens) if remaining_tokens else []
            return EvalResult(
                output=out,
                hit_boundary=None,
                cached_tokens=0,
                evaluated_tokens=n,
            )

    def clear(self) -> None:
        """Clear all cache entries and reset counters."""
        self._entries.clear()
        self.current_bytes = 0
        self.hits = 0
        self.misses = 0

