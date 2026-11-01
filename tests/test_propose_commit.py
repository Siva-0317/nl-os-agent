"""
tests/test_propose_commit.py - Unit tests for propose, commit, and baseline G1.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock
import pytest

# Ensure src is on sys.path
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.cache.verified_cache import VerifiedCommandCache, make_env_key
from nlagent.context.state import SessionState
from nlagent.daemon.commit import commit
from nlagent.daemon.propose import (
    DEFAULT_L0_SYSTEM_PROMPT,
    extract_json_proposal,
    get_default_env,
    propose,
)
from nlagent.safety.baseline_g1 import RiskTier, classify_risk


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_extract_json_proposal_clean_json():
    raw = '{"command": "ls -la", "targets": ["."], "explanation": "list all files"}'
    parsed = extract_json_proposal(raw)
    assert parsed["command"] == "ls -la"
    assert parsed["targets"] == ["."]
    assert parsed["explanation"] == "list all files"


def test_extract_json_proposal_markdown_fences():
    raw = """```json
    {
        "command": "git status",
        "targets": []
    }
    ```"""
    parsed = extract_json_proposal(raw)
    assert parsed["command"] == "git status"
    assert parsed["targets"] == []


def test_extract_json_proposal_surrounding_prose():
    raw = """Sure, here is your command:
    {"command": "find . -name '*.py'", "targets": ["*.py"]}
    Hope this helps!"""
    parsed = extract_json_proposal(raw)
    assert parsed["command"] == "find . -name '*.py'"
    assert parsed["targets"] == ["*.py"]


def test_extract_json_proposal_missing_command_raises():
    raw = '{"targets": ["file.txt"], "explanation": "no command here"}'
    with pytest.raises(ValueError, match="missing required 'command'"):
        extract_json_proposal(raw)


def test_extract_json_proposal_invalid_json_raises():
    raw = "This is not json at all."
    with pytest.raises(ValueError, match="Failed to parse"):
        extract_json_proposal(raw)


def test_propose_read_only_command(temp_dir):
    session = SessionState(cwd=temp_dir)
    mock_backend = MagicMock()
    mock_backend.complete.return_value = MagicMock(
        content='{"command": "ls -la", "targets": []}'
    )

    result = propose(
        request="list files",
        session_state=session,
        backend=mock_backend,
    )

    assert result["command"] == "ls -la"
    assert result["tier"] == "read_only"
    assert "No filesystem effect" in result["preview"]
    assert result["cached"] is False
    assert mock_backend.complete.called


def test_propose_verified_cache_hit_bypasses_backend(temp_dir):
    session = SessionState(cwd=temp_dir)
    cache = VerifiedCommandCache(threshold=0.90)
    env = get_default_env(cwd=temp_dir)
    env_key = make_env_key(os_name=env["os"], shell_name=env["shell"], cwd=temp_dir)

    cache.record_success(
        request="list files",
        env_key=env_key,
        proposal={"command": "ls -la", "targets": []},
    )

    mock_backend = MagicMock()
    mock_backend.complete.side_effect = RuntimeError("Backend should not be queried on cache hit")

    result = propose(
        request="list files",
        session_state=session,
        backend=mock_backend,
        verified_cache=cache,
        env=env,
    )

    assert result["command"] == "ls -la"
    assert result["cached"] is True
    assert result["tier"] == "read_only"
    assert not mock_backend.complete.called


def test_propose_cached_destructive_proposal_is_still_classified_destructive(temp_dir):
    """Verify safety invariant: cache hits must still be passed to effects.analyse."""
    # Create build directory in temp_dir
    build_dir = os.path.join(temp_dir, "build")
    os.makedirs(build_dir, exist_ok=True)
    with open(os.path.join(build_dir, "app.bin"), "w", encoding="utf-8") as f:
        f.write("0101")

    session = SessionState(cwd=temp_dir)
    cache = VerifiedCommandCache(threshold=0.90)
    env = get_default_env(cwd=temp_dir)
    env_key = make_env_key(os_name=env["os"], shell_name=env["shell"], cwd=temp_dir)

    cache.record_success(
        request="delete build folder",
        env_key=env_key,
        proposal={"command": "rm -rf build", "targets": ["build"]},
    )

    mock_backend = MagicMock()
    result = propose(
        request="delete build folder",
        session_state=session,
        backend=mock_backend,
        verified_cache=cache,
        env=env,
    )

    assert result["cached"] is True
    assert result["command"] == "rm -rf build"
    # Mandatory invariant: must still be classified as destructive!
    assert result["tier"] == "destructive"
    assert "DELETE" in result["preview"]


def test_commit_reruns_effects_and_blocks_privileged(temp_dir):
    """commit() must re-run effects.analyse and block privileged paths."""
    session = SessionState(cwd=temp_dir)
    proposal = {
        "command": "rm -rf /etc/hosts",
        "targets": ["/etc/hosts"],
        "tier": "privileged",
        "request": "remove hosts file",
    }

    output, exit_code, eff = commit(
        proposal=proposal,
        session_state=session,
        allow_privileged=False,
    )

    assert exit_code == 126
    assert "Blocked: Command targets protected system paths" in output
    assert eff.protected_hit is True


def test_commit_successful_execution_updates_session_and_cache(temp_dir):
    session = SessionState(cwd=temp_dir)
    cache = VerifiedCommandCache(threshold=0.90)
    env = get_default_env(cwd=temp_dir)
    env_key = make_env_key(os_name=env["os"], shell_name=env["shell"], cwd=temp_dir)

    proposal = {
        "command": "echo test > output.txt",
        "targets": ["output.txt"],
        "tier": "state_modifying",
        "request": "write test to output.txt",
    }

    mock_runner = MagicMock(return_value=("created", 0))

    output, exit_code, eff = commit(
        proposal=proposal,
        session_state=session,
        verified_cache=cache,
        runner=mock_runner,
        env=env,
    )

    assert exit_code == 0
    assert output == "created"
    assert len(session.effects) == 1
    assert session.effects[0].command == "echo test > output.txt"

    # Cached for future acceleration
    cached = cache.lookup(request="write test to output.txt", env_key=env_key)
    assert cached is not None
    assert cached["command"] == "echo test > output.txt"


def test_baseline_g1_classify_risk_is_independent():
    """Verify G1 baseline works for benchmark comparison, but is absent from runtime."""
    assert classify_risk("rm -rf /tmp/data") == RiskTier.DESTRUCTIVE
    assert classify_risk("ls -la") == RiskTier.READ_ONLY
    assert classify_risk("mkdir newdir") == RiskTier.STATE_MODIFYING
    assert classify_risk("kill -9 999") == RiskTier.DESTRUCTIVE

    # Verify that propose and commit modules DO NOT import classify_risk
    import nlagent.daemon.commit as commit_mod
    import nlagent.daemon.propose as propose_mod

    assert not hasattr(propose_mod, "classify_risk")
    assert not hasattr(commit_mod, "classify_risk")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

