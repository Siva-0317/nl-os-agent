"""
test_layout.py - Acceptance tests for cache-aware prompt layout (Task T1).

Verifies:
1. Two calls with different state/request produce byte-identical L0+L1 prefixes.
2. Changing one L1 field changes only L1+L2 (leaving L0 unchanged).
3. Deterministic key sorting in L1.
4. Prohibition of volatile keys and timestamps in L0/L1.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
import pytest

# Ensure src is on sys.path
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.context.layout import PromptParts, build_prompt


L0_SAMPLE = (
    "You are a terminal command generator for Linux/macOS.\n"
    "Your ONLY job is to convert natural language requests into a single shell command.\n"
    "JSON Schema: {\"command\": string, \"targets\": string[]}"
)

L1_SAMPLE = {
    "os": "Ubuntu 22.04 LTS (WSL2)",
    "shell": "bash",
    "user": "sivab",
    "path_style": "posix",
    "available_tools": ["git", "find", "grep", "docker"],
}


def test_prompt_parts_structure():
    """Verify PromptParts dataclass attributes."""
    parts = build_prompt(L0_SAMPLE, L1_SAMPLE, "STATE cwd=/home/u", "list files")
    assert isinstance(parts, PromptParts)
    assert isinstance(parts.l0_text, str)
    assert isinstance(parts.l1_text, str)
    assert isinstance(parts.l2_text, str)
    assert isinstance(parts.full_text, str)
    assert parts.l0_text in parts.full_text
    assert parts.l1_text in parts.full_text
    assert parts.l2_text in parts.full_text


def test_byte_identical_l0_l1_prefixes_different_state_and_request():
    """
    Test 1: Proves that two calls with different state/request produce byte-identical L0+L1 prefixes.
    """
    parts1 = build_prompt(
        l0=L0_SAMPLE,
        l1=L1_SAMPLE,
        state="STATE cwd=/home/u/project\nENT f1=./report.pdf(last:t3)",
        request="delete that file"
    )

    parts2 = build_prompt(
        l0=L0_SAMPLE,
        l1=L1_SAMPLE,
        state="STATE cwd=/var/log\nENT d1=./backup(last:t1)",
        request="list log files modified today"
    )

    # L0 and L1 segments must be identical at string and byte level
    assert parts1.l0_text == parts2.l0_text
    assert parts1.l0_text.encode("utf-8") == parts2.l0_text.encode("utf-8")

    assert parts1.l1_text == parts2.l1_text
    assert parts1.l1_text.encode("utf-8") == parts2.l1_text.encode("utf-8")

    # prefix_text (L0 + L1 combined with separator) must be byte-identical
    prefix1_bytes = parts1.prefix_text.encode("utf-8")
    prefix2_bytes = parts2.prefix_text.encode("utf-8")
    assert prefix1_bytes == prefix2_bytes

    # Both full_text outputs start with the exact same byte slice
    assert parts1.full_text.encode("utf-8").startswith(prefix1_bytes)
    assert parts2.full_text.encode("utf-8").startswith(prefix2_bytes)
    prefix_len = len(parts1.prefix_text)
    assert parts1.full_text[:prefix_len] == parts2.full_text[:prefix_len]

    # Verify that L2 and full texts are different across turns
    assert parts1.l2_text != parts2.l2_text
    assert parts1.full_text != parts2.full_text


def test_changing_one_l1_field_changes_only_l1_and_downstream():
    """
    Test 2: Proves that changing one L1 field changes only L1+L2 (the downstream tail of the prompt),
    leaving L0 completely unchanged.
    """
    l1_orig = dict(L1_SAMPLE)
    l1_mod = dict(L1_SAMPLE)
    l1_mod["shell"] = "zsh"  # Modify exactly one L1 field

    state = "STATE cwd=/home/u/project"
    req = "find python files"

    parts_orig = build_prompt(L0_SAMPLE, l1_orig, state, req)
    parts_mod = build_prompt(L0_SAMPLE, l1_mod, state, req)

    # 1. L0 is byte-identical
    assert parts_orig.l0_text == parts_mod.l0_text
    assert parts_orig.l0_text.encode("utf-8") == parts_mod.l0_text.encode("utf-8")
    l0_len = len(parts_orig.l0_text)
    assert parts_orig.full_text[:l0_len] == parts_mod.full_text[:l0_len]

    # 2. L1 is changed
    assert parts_orig.l1_text != parts_mod.l1_text
    assert parts_orig.prefix_text != parts_mod.prefix_text

    # 3. L2 isolated content itself is unchanged
    assert parts_orig.l2_text == parts_mod.l2_text

    # 4. In full_text, the downstream portion (from L1 onwards) differs
    assert parts_orig.full_text[l0_len:] != parts_mod.full_text[l0_len:]


def test_deterministic_key_sorting_in_l1():
    """
    Test 3: Proves that dictionary key order in L1 does not change the prefix bytes.
    """
    l1_order_a = {
        "user": "alice",
        "shell": "bash",
        "os": "Linux",
        "available_tools": ["git", "curl"],
    }
    l1_order_b = {
        "available_tools": ["curl", "git"],  # different order in list
        "os": "Linux",
        "shell": "bash",
        "user": "alice",
    }

    parts_a = build_prompt(L0_SAMPLE, l1_order_a, "STATE cwd=/tmp", "ls")
    parts_b = build_prompt(L0_SAMPLE, l1_order_b, "STATE cwd=/tmp", "ls")

    assert parts_a.l1_text == parts_b.l1_text
    assert parts_a.l1_text.encode("utf-8") == parts_b.l1_text.encode("utf-8")
    assert parts_a.prefix_text.encode("utf-8") == parts_b.prefix_text.encode("utf-8")
    assert parts_a.full_text.encode("utf-8") == parts_b.full_text.encode("utf-8")


def test_prohibits_volatile_keys_and_timestamps_in_l1():
    """
    Test 4: Rules verification - timestamps and volatile identifiers in L1 are rejected
    to protect prefix cache stability.
    """
    # Timestamp key
    with pytest.raises(ValueError, match="Volatile key 'timestamp' is forbidden in L1"):
        build_prompt(L0_SAMPLE, {"timestamp": 1730000000}, "STATE", "pwd")

    # Time key
    with pytest.raises(ValueError, match="Volatile key 'time' is forbidden in L1"):
        build_prompt(L0_SAMPLE, {"time": "2026-11-01T12:00:00"}, "STATE", "pwd")

    # Date / datetime objects
    with pytest.raises(ValueError, match="Timestamp/date object is forbidden in L1"):
        build_prompt(L0_SAMPLE, {"created_at": datetime.now()}, "STATE", "pwd")

    # Request ID
    with pytest.raises(ValueError, match="Volatile key 'request_id' is forbidden in L1"):
        build_prompt(L0_SAMPLE, {"request_id": "req-1234"}, "STATE", "pwd")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


