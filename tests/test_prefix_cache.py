"""
test_prefix_cache.py - Unit tests for Hierarchical Prefix-State Cache (Task T4).

Verifies:
1. Fake in-memory ModelBackend implementation (no real model called).
2. SHA-256 boundary key computation.
3. Longest-match prefix lookup (B1 preferred over B0).
4. Fallback hit to B0 when L1 differs.
5. LRU eviction bounded by strict byte budget.
6. Correctness (E0 gate): cached output equals uncached output under greedy decoding.
"""

from __future__ import annotations

import json
import os
import sys
import pytest

# Ensure src is on sys.path
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.cache.prefix_cache import (
    CacheEntry,
    EvalResult,
    HierarchicalPrefixCache,
    ModelBackend,
    compute_boundary_key,
)


class FakeModelBackend:
    """
    Mock LLM backend that accumulates evaluated token IDs into an in-memory state.
    Serializes evaluated sequence into JSON bytes for snapshots.
    """

    def __init__(self) -> None:
        self.state_tokens: list[int] = []
        self.save_calls = 0
        self.load_calls = 0
        self.eval_calls = 0
        self.evaluated_batches: list[list[int]] = []

    def save_state(self) -> bytes:
        self.save_calls += 1
        return json.dumps(self.state_tokens).encode("utf-8")

    def load_state(self, blob: bytes) -> None:
        self.load_calls += 1
        self.state_tokens = json.loads(blob.decode("utf-8")) if blob else []

    def eval(self, tokens: list[int]) -> list[float]:
        self.eval_calls += 1
        self.evaluated_batches.append(list(tokens))
        self.state_tokens.extend(tokens)
        return [float(t) * 1.5 for t in self.state_tokens]


def test_compute_boundary_key():
    """Verify SHA-256 boundary key computation is deterministic and 64 hex characters."""
    k1 = compute_boundary_key([1, 2, 3, 4])
    k2 = compute_boundary_key([1, 2, 3, 4])
    k3 = compute_boundary_key([1, 2, 3, 5])

    assert k1 == k2
    assert len(k1) == 64
    assert k1 != k3


def test_cold_miss_populates_both_boundaries():
    """On cold miss, evaluates full sequence and saves checkpoints at B0 and B1."""
    backend = FakeModelBackend()
    cache = HierarchicalPrefixCache(backend=backend)

    tokens = [101, 102, 201, 202, 301]
    b0_len = 2
    b1_len = 4

    result = cache.process(tokens, b0_len=b0_len, b1_len=b1_len)

    assert result.hit_boundary is None
    assert result.cached_tokens == 0
    assert result.evaluated_tokens == 5
    assert cache.hits == 0
    assert cache.misses == 1

    # Both B0 and B1 snapshots should now be stored in cache
    assert cache.entry_count == 2
    assert cache.get(tokens[:b0_len]) is not None
    assert cache.get(tokens[:b1_len]) is not None


def test_longest_match_hit_b1():
    """Verify that when L0+L1 match, cache hits B1 directly, skipping L0 and L1 evaluation."""
    backend = FakeModelBackend()
    cache = HierarchicalPrefixCache(backend=backend)

    tokens_a = [10, 20, 30, 40, 50]
    b0_len = 2
    b1_len = 4

    # Warm up cache with prompt A
    cache.process(tokens_a, b0_len=b0_len, b1_len=b1_len)

    # Reset call tracker
    backend.evaluated_batches.clear()

    # Prompt B has identical L0 + L1, but distinct L2 token (99)
    tokens_b = [10, 20, 30, 40, 99]
    res_b = cache.process(tokens_b, b0_len=b0_len, b1_len=b1_len)

    assert res_b.hit_boundary == "B1"
    assert res_b.cached_tokens == 4
    assert res_b.evaluated_tokens == 1
    assert cache.hits == 1

    # Evaluated batch must contain ONLY the un-cached L2 token [99]
    assert backend.evaluated_batches == [[99]]
    # Restored backend state must be [10, 20, 30, 40, 99]
    assert backend.state_tokens == [10, 20, 30, 40, 99]


def test_fallback_hit_b0_when_l1_differs():
    """Verify that when L0 matches but L1 differs, cache falls back to B0 and admits new B1."""
    backend = FakeModelBackend()
    cache = HierarchicalPrefixCache(backend=backend)

    tokens_a = [10, 20, 30, 40, 50]
    b0_len = 2
    b1_len = 4

    # Warm up cache with prompt A
    cache.process(tokens_a, b0_len=b0_len, b1_len=b1_len)
    backend.evaluated_batches.clear()

    # Prompt C shares L0 [10, 20], but has new L1 [77, 88] and new L2 [99]
    tokens_c = [10, 20, 77, 88, 99]
    res_c = cache.process(tokens_c, b0_len=b0_len, b1_len=b1_len)

    assert res_c.hit_boundary == "B0"
    assert res_c.cached_tokens == 2
    assert res_c.evaluated_tokens == 3
    assert cache.hits == 1

    # First evaluated B0 -> B1 ([77, 88]), then L2 ([99])
    assert backend.evaluated_batches == [[77, 88], [99]]

    # Now both B1 snapshots exist in cache
    assert cache.get([10, 20, 30, 40]) is not None
    assert cache.get([10, 20, 77, 88]) is not None
    assert cache.entry_count == 3  # 1 B0 + 2 B1s


def test_lru_eviction_by_byte_budget():
    """Verify LRU eviction respects max_bytes and evicts least recently accessed entries first."""
    backend = FakeModelBackend()
    # Set budget to exactly 150 bytes
    cache = HierarchicalPrefixCache(backend=backend, max_bytes=150)

    blob = b"X" * 60  # 60 bytes each

    # Insert 1 and 2: 60 + 60 = 120 <= 150
    cache.put("B0", [1], blob)
    cache.put("B1", [1, 2], blob)
    assert cache.entry_count == 2
    assert cache.current_bytes == 120

    # Insert 3: 120 + 60 = 180 > 150 -> evicts entry [1] (oldest)
    cache.put("B0", [2], blob)
    assert cache.entry_count == 2
    assert cache.current_bytes == 120
    assert cache.get([1]) is None  # [1] was evicted
    assert cache.get([1, 2]) is not None
    assert cache.get([2]) is not None

    # Access [1, 2] to mark it as recently used (now [2] is the oldest!)
    cache.get([1, 2])

    # Insert 4: evicts [2], leaving [1, 2] and [3]
    cache.put("B1", [3], blob)
    assert cache.get([2]) is None  # [2] evicted
    assert cache.get([1, 2]) is not None  # [1, 2] preserved due to LRU refresh
    assert cache.get([3]) is not None


def test_e0_greedy_output_equality_cached_vs_uncached():
    """
    E0 Correctness Gate:
    Proves that outputs generated through HPSC are identical to an uncached baseline.
    """
    tokens_turn1 = [1, 2, 3, 4, 10]
    tokens_turn2 = [1, 2, 3, 4, 20]
    b0_len = 2
    b1_len = 4

    # 1. Uncached baseline
    backend_uncached = FakeModelBackend()
    out1_uncached = backend_uncached.eval(tokens_turn1)

    backend_uncached2 = FakeModelBackend()
    out2_uncached = backend_uncached2.eval(tokens_turn2)

    # 2. HPSC cached execution
    backend_cached = FakeModelBackend()
    cache = HierarchicalPrefixCache(backend=backend_cached)

    res1_cached = cache.process(tokens_turn1, b0_len=b0_len, b1_len=b1_len)
    # Verify Turn 1 equality (both internal state and generated greedy outputs)
    assert backend_cached.state_tokens == backend_uncached.state_tokens
    assert res1_cached.output == out1_uncached

    res2_cached = cache.process(tokens_turn2, b0_len=b0_len, b1_len=b1_len)
    # Verify Turn 2 equality (cached hit at B1 produced exact same state and output as full baseline)
    assert res2_cached.hit_boundary == "B1"
    assert backend_cached.state_tokens == backend_uncached2.state_tokens
    assert res2_cached.output == out2_uncached
