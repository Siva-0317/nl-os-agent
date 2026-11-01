"""tests/test_verified_cache.py - Unit tests for VerifiedCommandCache and safety gating."""

from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile
import pytest

# Ensure src is on sys.path
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.cache.verified_cache import (
    VerifiedCommandCache,
    cosine_similarity,
    make_env_key,
    simple_embed,
)
from nlagent.safety.effects import analyse, tier_from_effect


def test_cosine_similarity_math():
    """Verify standard vector operations and edge cases for cosine similarity."""
    assert math.isclose(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0, abs_tol=1e-6)
    assert math.isclose(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-6)
    assert math.isclose(cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0, abs_tol=1e-6)

    # 45-degree angle -> cos(45 deg) = sqrt(2)/2 ~= 0.7071
    cos_45 = cosine_similarity([1.0, 0.0], [1.0, 1.0])
    assert math.isclose(cos_45, math.sqrt(2) / 2, abs_tol=1e-4)

    # Zero magnitude returns 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    # Dimension mismatch
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


def test_simple_embed_properties():
    """Verify deterministic embedding behavior, vector dimension, and normalization."""
    dim = 256
    vec1 = simple_embed("remove all temporary log files", dim=dim)
    assert len(vec1) == dim
    norm1 = math.sqrt(sum(x * x for x in vec1))
    assert math.isclose(norm1, 1.0, abs_tol=1e-5)

    # Identical query produces identical embedding (cos sim = 1.0)
    vec2 = simple_embed("remove all temporary log files", dim=dim)
    assert math.isclose(cosine_similarity(vec1, vec2), 1.0, abs_tol=1e-6)

    # Empty string produces zero vector
    empty_vec = simple_embed("", dim=dim)
    assert len(empty_vec) == dim
    assert all(x == 0.0 for x in empty_vec)


def test_environment_key_normalization_and_isolation():
    """Verify environment keys normalize casing and paths and isolate environments."""
    temp_dir = tempfile.mkdtemp()
    try:
        k1 = make_env_key("Linux", "BASH", temp_dir)
        k2 = make_env_key("linux", "bash", os.path.join(temp_dir, ".", ""))
        assert k1 == k2

        k_other_os = make_env_key("Windows", "bash", temp_dir)
        k_other_shell = make_env_key("Linux", "zsh", temp_dir)
        k_other_cwd = make_env_key("Linux", "bash", os.path.dirname(temp_dir))

        assert k1 != k_other_os
        assert k1 != k_other_shell
        assert k1 != k_other_cwd
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_record_only_after_success():
    """Verify proposals are only recorded upon explicit record_success call."""
    cache = VerifiedCommandCache(threshold=0.85)
    env_key = "linux:bash:/tmp/test"
    request = "list files with details"
    proposal = {"command": "ls -la", "risk_tier": "read_only"}

    # Initially empty
    assert len(cache) == 0
    assert cache.lookup(request, env_key) is None

    # Stored only after success record
    entry = cache.record_success(request=request, env_key=env_key, proposal=proposal)
    assert len(cache) == 1
    assert entry.success_count == 1

    # Second success increments count
    cache.record_success(request=request, env_key=env_key, proposal=proposal)
    assert len(cache) == 1
    assert entry.success_count == 2


def test_cross_environment_isolation_on_lookup():
    """Ensure identical query does NOT hit if OS, shell, or cwd differ."""
    cache = VerifiedCommandCache(threshold=0.80)
    env_a = "linux:bash:/workspace/app"
    env_b = "windows:powershell:c:/workspace/app"
    env_c = "linux:bash:/workspace/other"

    proposal = {"command": "npm test", "risk_tier": "read_only"}
    cache.record_success("run the test suite", env_a, proposal)

    # Hits in exact matching environment
    assert cache.lookup("run the test suite", env_a) is not None

    # Completely misses in different OS/shell/cwd
    assert cache.lookup("run the test suite", env_b) is None
    assert cache.lookup("run the test suite", env_c) is None


def test_similarity_threshold_gating():
    """Verify lookup returns proposal when similarity >= threshold and None when below."""
    cache = VerifiedCommandCache(threshold=0.85)
    env = "linux:bash:/workspace"

    # Stored with orthogonal synthetic vectors for exact threshold control
    vec_stored = [1.0, 0.0, 0.0]
    proposal = {"command": "git status", "risk_tier": "read_only"}
    cache.record_success("status check", env, proposal, embedding=vec_stored)

    # Query with vector at similarity 0.90 -> hit (0.90 >= 0.85)
    # [0.90, sqrt(1 - 0.90^2), 0] has norm 1.0 and dot with [1,0,0] = 0.90
    sim_90_vec = [0.90, math.sqrt(1 - 0.90**2), 0.0]
    hit, score = cache.lookup_with_score("status check", env, embedding=sim_90_vec)
    assert hit is not None
    assert math.isclose(score, 0.90, abs_tol=1e-5)
    assert hit["command"] == "git status"

    # Query with vector at similarity 0.70 -> miss (0.70 < 0.85)
    sim_70_vec = [0.70, math.sqrt(1 - 0.70**2), 0.0]
    miss, score_miss = cache.lookup_with_score("status check", env, embedding=sim_70_vec)
    assert miss is None
    assert math.isclose(score_miss, 0.70, abs_tol=1e-5)


def test_cached_destructive_proposal_is_still_classified_destructive():
    """CRITICAL SAFETY TEST:

    Verify that when a cached proposal (e.g. 'rm -rf old') is returned on a cache hit,
    the caller passing it to effects.analyse still classifies it as 'destructive'.
    Bypassing the LLM must NEVER bypass the safety verification engine.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        # Prepare real directory structure
        target_dir = os.path.join(temp_dir, "old")
        os.makedirs(target_dir, exist_ok=True)
        file_inside = os.path.join(target_dir, "data.log")
        with open(file_inside, "w", encoding="utf-8") as f:
            f.write("temporary log data\n")

        env_key = make_env_key("linux", "bash", temp_dir)
        cache = VerifiedCommandCache(threshold=0.80)

        # Step 1: Simulate a previously approved and executed destructive proposal
        destructive_proposal = {
            "command": "rm -rf old",
            "targets": ["old"],
            "explanation": "Remove old log directory",
        }
        cache.record_success(
            request="delete the old directory",
            env_key=env_key,
            proposal=destructive_proposal,
        )

        # Step 2: Later, user submits an equivalent request: "delete old directory"
        hit = cache.lookup("delete old directory", env_key)
        assert hit is not None, "Semantic cache should hit on equivalent request"
        assert hit["command"] == "rm -rf old"

        # Step 3: CALLER SAFETY GATE
        # The caller MUST pass the cached proposal to effects.analyse before execution
        cmd = hit["command"]
        effect = analyse(cmd, cwd=temp_dir)
        tier = tier_from_effect(effect)

        # Step 4: Verification of safety properties
        assert tier == "destructive", (
            f"Expected 'destructive' tier for cached '{cmd}', but got '{tier}'!"
        )
        assert len(effect.deletes) >= 1
        # The target file inside 'old' must be detected in effect.deletes
        normalized_deletes = [os.path.normpath(d) for d in effect.deletes]
        assert os.path.normpath(target_dir) in normalized_deletes or any(
            os.path.normpath(target_dir) in d for d in normalized_deletes
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_cached_state_modifying_and_read_only_tiers():
    """Verify cached non-destructive commands are also accurately tiered by the effect engine."""
    temp_dir = tempfile.mkdtemp()
    try:
        env_key = make_env_key("linux", "bash", temp_dir)
        cache = VerifiedCommandCache(threshold=0.80)

        # Cache a state-modifying command
        mkdir_proposal = {"command": "mkdir new_folder"}
        cache.record_success("create new folder", env_key, mkdir_proposal)

        # Cache a read-only command
        ls_proposal = {"command": "ls -la"}
        cache.record_success("list files", env_key, ls_proposal)

        # Lookup and run caller safety verification
        hit_mkdir = cache.lookup("create new folder", env_key)
        assert hit_mkdir is not None
        eff_mkdir = analyse(hit_mkdir["command"], cwd=temp_dir)
        assert tier_from_effect(eff_mkdir) == "state_modifying"

        hit_ls = cache.lookup("list files", env_key)
        assert hit_ls is not None
        eff_ls = analyse(hit_ls["command"], cwd=temp_dir)
        assert tier_from_effect(eff_ls) == "read_only"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_cache_invalidation_by_cwd():
    """Verify invalidating a directory clears only its entries."""
    cache = VerifiedCommandCache()
    env_dir1 = make_env_key("linux", "bash", "/workspace/proj1")
    env_dir2 = make_env_key("linux", "bash", "/workspace/proj2")

    cache.record_success("task A", env_dir1, {"command": "echo A"})
    cache.record_success("task B", env_dir2, {"command": "echo B"})
    assert len(cache) == 2

    removed = cache.invalidate_for_cwd("/workspace/proj1")
    assert removed == 1
    assert len(cache) == 1
    assert cache.lookup("task A", env_dir1) is None
    assert cache.lookup("task B", env_dir2) is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
