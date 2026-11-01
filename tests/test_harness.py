"""
test_harness.py - Unit tests for benchmark harness and interleaved session execution (Task T3).

Verifies:
1. HTTP layer mocked (no real model required).
2. JSONL schema conforms to expected benchmark fields and data types.
3. Interleaving order across N sessions runs round-robin turn-by-turn.
4. cache_prompt toggle is passed in payload and recorded.
5. HTTP errors are captured gracefully and logged to JSONL.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock
import pytest
import requests

# Ensure src is on sys.path
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.bench.harness import (
    BenchmarkHarness,
    BenchmarkRecord,
    BenchmarkTurn,
    SessionScript,
    generate_interleaving_order,
)


def make_mock_response(content: str = "ls -la", timings: dict | None = None, status_code: int = 200) -> requests.Response:
    """Helper to produce a mock requests.Response with llama-server schema."""
    resp = requests.Response()
    resp.status_code = status_code
    if timings is None:
        timings = {
            "prompt_n": 128,
            "prompt_ms": 22.4,
            "cache_n": 80,
            "predicted_n": 12,
            "predicted_ms": 65.1,
        }
    body = {"content": content, "timings": timings}
    resp._content = json.dumps(body).encode("utf-8")
    return resp


def test_generate_interleaving_order():
    """Verify round-robin interleaving order across N sessions of different turn counts."""
    s1 = SessionScript(session_id="sess_1", turns=[BenchmarkTurn("s1_t0"), BenchmarkTurn("s1_t1"), BenchmarkTurn("s1_t2")])
    s2 = SessionScript(session_id="sess_2", turns=[BenchmarkTurn("s2_t0"), BenchmarkTurn("s2_t1")])
    s3 = SessionScript(session_id="sess_3", turns=[BenchmarkTurn("s3_t0")])

    order = generate_interleaving_order([s1, s2, s3])
    expected_sequence = [
        ("sess_1", 0, "s1_t0"),
        ("sess_2", 0, "s2_t0"),
        ("sess_3", 0, "s3_t0"),
        ("sess_1", 1, "s1_t1"),
        ("sess_2", 1, "s2_t1"),
        ("sess_1", 2, "s1_t2"),
    ]

    assert len(order) == len(expected_sequence)
    for (sess_id, turn_idx, turn), (exp_sess, exp_turn, exp_prompt) in zip(order, expected_sequence):
        assert sess_id == exp_sess
        assert turn_idx == exp_turn
        assert turn.prompt == exp_prompt


def test_single_request_mocked(tmp_path):
    """Verify sending a single request records timings and wall time."""
    mock_session = MagicMock()
    mock_session.post.return_value = make_mock_response(content="df -h", timings={"prompt_n": 50, "cache_n": 20})

    jsonl_file = str(tmp_path / "single_test.jsonl")
    harness = BenchmarkHarness(
        endpoint_url="http://127.0.0.1:8080/completion",
        results_dir=str(tmp_path),
        experiment_id="test_exp",
        session=mock_session,
    )

    rec = harness.send_request(
        prompt="how much space",
        session_id="sess_alpha",
        turn_index=0,
        cache_prompt=True,
        output_jsonl_path=jsonl_file,
    )

    assert isinstance(rec, BenchmarkRecord)
    assert rec.status == "success"
    assert rec.session_id == "sess_alpha"
    assert rec.turn_index == 0
    assert rec.response_text == "df -h"
    assert rec.timings["cache_n"] == 20
    assert rec.wall_time_s >= 0.0
    assert rec.error is None
    assert os.path.exists(jsonl_file)


def test_jsonl_schema_validation(tmp_path):
    """Verify that every record appended to JSONL matches the specified schema and data types."""
    mock_session = MagicMock()
    mock_session.post.return_value = make_mock_response()

    jsonl_file = str(tmp_path / "schema_test.jsonl")
    harness = BenchmarkHarness(
        endpoint_url="http://127.0.0.1:8080/completion",
        results_dir=str(tmp_path),
        experiment_id="schema_exp",
        session=mock_session,
    )

    harness.send_request("prompt 1", session_id="s1", turn_index=0, output_jsonl_path=jsonl_file)
    harness.send_request("prompt 2", session_id="s1", turn_index=1, output_jsonl_path=jsonl_file)

    with open(jsonl_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) == 2
    for line in lines:
        data = json.loads(line)
        # Check required fields
        assert "request_id" in data and isinstance(data["request_id"], str) and data["request_id"].startswith("req-")
        assert "session_id" in data and isinstance(data["session_id"], str)
        assert "turn_index" in data and isinstance(data["turn_index"], int)
        assert "prompt" in data and isinstance(data["prompt"], str)
        assert "cache_prompt" in data and isinstance(data["cache_prompt"], bool)
        assert "wall_time_s" in data and isinstance(data["wall_time_s"], float)
        assert "timings" in data and isinstance(data["timings"], dict)
        assert "response_text" in data and isinstance(data["response_text"], str)
        assert "status" in data and data["status"] in ("success", "error")
        assert "timestamp" in data and isinstance(data["timestamp"], str)
        assert "experiment_id" in data and isinstance(data["experiment_id"], str)


def test_run_interleaved_sessions_mocked(tmp_path):
    """Verify that run_interleaved_sessions dispatches calls and writes JSONL in exact interleaved order."""
    dispatched_calls: list[dict] = []

    def mock_post_impl(url, json, timeout):
        dispatched_calls.append(dict(json))
        return make_mock_response(content=f"echo '{json['prompt']}'")

    mock_session = MagicMock()
    mock_session.post.side_effect = mock_post_impl

    s_a = SessionScript(session_id="S_A", turns=[BenchmarkTurn("A_t0"), BenchmarkTurn("A_t1")])
    s_b = SessionScript(session_id="S_B", turns=[BenchmarkTurn("B_t0"), BenchmarkTurn("B_t1")])

    jsonl_file = str(tmp_path / "interleave_test.jsonl")
    harness = BenchmarkHarness(
        endpoint_url="http://127.0.0.1:8080/completion",
        results_dir=str(tmp_path),
        experiment_id="interleave_exp",
        session=mock_session,
    )

    records = harness.run_interleaved_sessions([s_a, s_b], output_jsonl_path=jsonl_file)

    # 1. Verify 4 turns executed
    assert len(records) == 4
    assert len(dispatched_calls) == 4

    # 2. Verify round-robin interleaving order
    expected_order = [("S_A", 0, "A_t0"), ("S_B", 0, "B_t0"), ("S_A", 1, "A_t1"), ("S_B", 1, "B_t1")]
    for rec, (exp_sess, exp_turn, exp_prompt) in zip(records, expected_order):
        assert rec.session_id == exp_sess
        assert rec.turn_index == exp_turn
        assert rec.prompt == exp_prompt

    # 3. Verify JSONL lines match exact same order
    with open(jsonl_file, "r", encoding="utf-8") as f:
        file_records = [json.loads(line) for line in f if line.strip()]

    assert len(file_records) == 4
    for file_rec, (exp_sess, exp_turn, exp_prompt) in zip(file_records, expected_order):
        assert file_rec["session_id"] == exp_sess
        assert file_rec["turn_index"] == exp_turn
        assert file_rec["prompt"] == exp_prompt


def test_cache_prompt_toggle_in_payload(tmp_path):
    """Verify that cache_prompt True vs False is sent in HTTP payload and logged."""
    payloads_received: list[dict] = []

    def mock_post(url, json, timeout):
        payloads_received.append(dict(json))
        return make_mock_response()

    mock_session = MagicMock()
    mock_session.post.side_effect = mock_post

    harness = BenchmarkHarness(results_dir=str(tmp_path), session=mock_session)

    rec_true = harness.send_request("prompt", session_id="s1", cache_prompt=True, output_jsonl_path=str(tmp_path / "c.jsonl"))
    rec_false = harness.send_request("prompt", session_id="s1", cache_prompt=False, output_jsonl_path=str(tmp_path / "c.jsonl"))

    assert payloads_received[0]["cache_prompt"] is True
    assert rec_true.cache_prompt is True

    assert payloads_received[1]["cache_prompt"] is False
    assert rec_false.cache_prompt is False


def test_http_error_gracefully_captured_and_logged(tmp_path):
    """Verify that connection failure or 500 error does not crash the harness and is written to JSONL."""
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ConnectionError("Connection refused by endpoint")

    jsonl_file = str(tmp_path / "error_test.jsonl")
    harness = BenchmarkHarness(results_dir=str(tmp_path), session=mock_session)

    rec = harness.send_request("prompt", session_id="err_sess", turn_index=0, output_jsonl_path=jsonl_file)

    assert rec.status == "error"
    assert rec.error is not None
    assert "Connection refused" in rec.error
    assert rec.timings == {}

    with open(jsonl_file, "r", encoding="utf-8") as f:
        logged = json.loads(f.readline())

    assert logged["status"] == "error"
    assert "Connection refused" in logged["error"]

