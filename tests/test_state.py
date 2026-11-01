"""
test_state.py - Acceptance tests for compact semantic state management (Task T2).

Verifies:
1. SessionState tracks CWD, entity table (name, type, path, last_turn), and last 3 effects.
2. 'delete that file' pronoun resolution across a 3-turn dialogue.
3. render(max_tokens=200) produces compact STATE block.
4. Truncates oldest items first when token budget is restricted.
5. Token size bounds enforced via whitespace approximation.
"""

from __future__ import annotations

import os
import sys
import pytest

# Ensure src is on sys.path
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.context.state import SessionState, Entity, EffectRecord, count_tokens_approx


def test_session_state_init_and_cwd():
    state = SessionState(cwd="/home/u/proj")
    assert state.cwd == "/home/u/proj"
    assert len(state.entities) == 0
    assert len(state.effects) == 0

    state.update_cwd("/var/log")
    assert state.cwd == "/var/log"


def test_retains_last_3_effects_only():
    """Verify that only the most recent 3 command effects are preserved."""
    state = SessionState(cwd="/home/u/proj")
    for t in range(1, 6):
        state.record_effect(turn=t, command=f"cmd_{t}", summary=f"effect_{t}")

    assert len(state.effects) == 3
    # Turns 3, 4, 5 should be retained; turns 1 and 2 evicted
    assert [e.turn for e in state.effects] == [3, 4, 5]
    assert [e.command for e in state.effects] == ["cmd_3", "cmd_4", "cmd_5"]


def test_delete_that_file_resolution_across_3_turns():
    """
    Core test requirement:
    Turn 1: file entity created (report.pdf).
    Turn 2: directory entity created (backup).
    Turn 3: 'delete that file' resolves correctly to report.pdf (not backup),
            while 'that folder' resolves to backup.
    """
    state = SessionState(cwd="/home/u/proj")

    # Turn 1: create file
    state.record_entity(name="report.pdf", entity_type="file", path="./report.pdf", turn=1)
    state.record_effect(turn=1, command="touch report.pdf", summary="created 1")

    # Turn 2: create directory
    state.record_entity(name="backup", entity_type="dir", path="./backup", turn=2)
    state.record_effect(turn=2, command="mkdir backup", summary="created 1")

    # Turn 3: follow-up requests with referring expressions
    query_file = "delete that file"
    resolved_file = state.resolve_reference(query_file)
    assert resolved_file == "delete ./report.pdf"

    query_folder = "copy everything into that folder"
    resolved_folder = state.resolve_reference(query_folder)
    assert resolved_folder == "copy everything into ./backup"

    query_both = "copy that file into that folder"
    resolved_both = state.resolve_reference(query_both)
    assert resolved_both == "copy ./report.pdf into ./backup"

    # Pronoun 'it' resolves to the most recent entity regardless of type (backup from turn 2)
    query_it = "remove it"
    resolved_it = state.resolve_reference(query_it)
    assert resolved_it == "remove ./backup"


def test_render_compact_state_format():
    """Verifies that render() returns the exact compact structure from Section 3.2."""
    state = SessionState(cwd="/home/u/proj")
    state.record_entity(name="report.pdf", entity_type="file", path="./report.pdf", turn=3)
    state.record_entity(name="backup", entity_type="dir", path="./backup", turn=2)
    state.record_effect(turn=4, command="mkdir backup", summary="created 1")
    state.record_effect(turn=5, command="cp *.pdf backup/", summary="created 3")

    rendered = state.render(max_tokens=200)
    lines = rendered.splitlines()

    assert len(lines) == 3
    assert lines[0] == "STATE cwd=/home/u/proj"
    assert "ENT" in lines[1]
    assert "f1=./report.pdf(last:t3)" in lines[1]
    assert "d1=./backup(last:t2)" in lines[1]
    assert lines[2] == "LAST t4: mkdir backup -> created 1; t5: cp *.pdf backup/ -> created 3"


def test_render_size_bound_truncates_oldest_items_first():
    """
    Verifies that when token budget is constrained:
    1. Resulting text has whitespace token count <= max_tokens.
    2. Oldest effects are truncated first.
    3. Oldest entities are truncated next.
    4. Most recent entities/effects are preserved.
    """
    state = SessionState(cwd="/home/u/proj")

    # Add 5 entities spanning turns 1 through 5
    for t in range(1, 6):
        state.record_entity(name=f"file_{t}.txt", entity_type="file", path=f"./file_{t}.txt", turn=t)

    # Add 3 effects
    state.record_effect(turn=3, command="touch file_3.txt", summary="created 1")
    state.record_effect(turn=4, command="touch file_4.txt", summary="created 1")
    state.record_effect(turn=5, command="touch file_5.txt", summary="created 1")

    # 1. Full render with large token budget
    full_rendered = state.render(max_tokens=200)
    full_token_count = count_tokens_approx(full_rendered)
    assert full_token_count > 15

    # 2. Restrict token budget to force truncation of effects
    restricted_budget = 16
    rendered_restricted = state.render(max_tokens=restricted_budget)
    assert count_tokens_approx(rendered_restricted) <= restricted_budget

    # 3. Restrict budget further to force truncation of oldest entities
    tight_budget = 6
    rendered_tight = state.render(max_tokens=tight_budget)
    assert count_tokens_approx(rendered_tight) <= tight_budget
    assert rendered_tight.startswith("STATE cwd=/home/u/proj")

    # 3. Intermediate budget where ENT is guaranteed to fit with truncation
    inter_budget = 5
    rendered_inter = state.render(max_tokens=inter_budget)
    assert count_tokens_approx(rendered_inter) <= inter_budget
    assert "ENT" in rendered_inter
    # Non-vacuous: newest entity (file_5.txt) is preserved, oldest (file_1.txt) is evicted
    assert "file_5.txt" in rendered_inter
    assert "file_1.txt" not in rendered_inter


def test_render_minimal_fallback_on_tiny_budget():
    """Verifies that an extremely low max_tokens does not crash, respects budget, and produces non-empty output."""
    state = SessionState(cwd="/home/u/proj")
    state.record_entity(name="test.txt", entity_type="file", path="./test.txt", turn=1)
    state.record_effect(turn=1, command="echo test", summary="printed")

    rendered = state.render(max_tokens=2)
    assert count_tokens_approx(rendered) <= 2
    assert len(rendered) > 0
    assert "STATE" in rendered
