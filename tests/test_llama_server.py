"""tests/test_llama_server.py - Unit tests for LlamaServerBackend and E0 equality runner."""

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
_scripts = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from nlagent.backends.llama_server import (
    CompletionResult,
    LlamaServerBackend,
    LlamaTimings,
)
from e0_equality import generate_e0_prompts, run_e0_experiment


def make_mock_http_response(
    content: str = "ls -la",
    timings: dict | None = None,
    status_code: int = 200,
) -> requests.Response:
    """Helper creating a requests.Response with valid llama-server JSON."""
    resp = requests.Response()
    resp.status_code = status_code
    if timings is None:
        timings = {
            "prompt_n": 64,
            "prompt_ms": 15.5,
            "cache_n": 32,
            "predicted_n": 8,
            "predicted_ms": 25.0,
        }
    data = {"content": content, "timings": timings}
    resp._content = json.dumps(data).encode("utf-8")
    return resp


def test_llama_server_complete_records_timings():
    """Verify LlamaServerBackend parses content and records timings object."""
    mock_session = MagicMock()
    mock_session.post.return_value = make_mock_http_response(
        content="find . -name '*.py'",
        timings={"prompt_n": 128, "cache_n": 64, "prompt_ms": 12.0},
    )

    backend = LlamaServerBackend(
        endpoint_url="http://127.0.0.1:8080/completion",
        session=mock_session,
    )

    result = backend.complete(
        prompt="show python files",
        cache_prompt=True,
        temperature=0.0,
        n_predict=32,
    )

    assert isinstance(result, CompletionResult)
    assert result.status == "success"
    assert result.content == "find . -name '*.py'"
    assert result.timings["cache_n"] == 64
    assert backend.last_timings["prompt_n"] == 128
    assert result.wall_time_s >= 0.0

    # Structured timings helper
    st = result.structured_timings
    assert isinstance(st, LlamaTimings)
    assert st.prompt_n == 128
    assert st.cache_n == 64

    # Verify request payload
    mock_session.post.assert_called_once()
    _, kwargs = mock_session.post.call_args
    assert kwargs["json"]["prompt"] == "show python files"
    assert kwargs["json"]["cache_prompt"] is True
    assert kwargs["json"]["temperature"] == 0.0


def test_llama_server_complete_error_handling():
    """Verify HTTP errors and network connection failures are captured gracefully."""
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ConnectionError("Failed to connect")

    backend = LlamaServerBackend(session=mock_session)
    res = backend.complete("ls")

    assert res.status == "error"
    assert res.content == ""
    assert "Failed to connect" in (res.error or "")
    assert res.timings == {}


def test_llama_server_model_backend_protocol():
    """Verify backend supports save_state, load_state, and eval interface."""
    mock_session = MagicMock()
    mock_session.post.return_value = make_mock_http_response(content="output")

    backend = LlamaServerBackend(session=mock_session)
    # Save empty state
    blob = backend.save_state()
    assert isinstance(blob, bytes)

    # Evaluate tokens
    logits = backend.eval([1, 2, 3])
    assert logits == [1.0, 2.0, 3.0]

    # Save state with evaluated tokens
    blob2 = backend.save_state()
    backend.load_state(b"")
    assert backend._state_tokens == []

    # Restore state
    backend.load_state(blob2)
    assert backend._state_tokens == [1, 2, 3]


def test_e0_prompt_generation_schema():
    """Verify 50 prompts are built via layout.build_prompt with valid structure."""
    prompts = generate_e0_prompts(count=50)
    assert len(prompts) == 50

    for p in prompts:
        assert "prompt_id" in p and isinstance(p["prompt_id"], int)
        assert "request" in p and len(p["request"]) > 0
        assert "category" in p and p["category"] in ("read_only", "state_modifying", "destructive")
        assert "full_prompt" in p and len(p["full_prompt"]) > 0
        assert "prefix_prompt" in p and len(p["prefix_prompt"]) > 0
        assert p["full_prompt"].startswith(p["prefix_prompt"])


def test_e0_experiment_execution_mocked_equality(tmp_path):
    """Verify E0 experiment runner logs JSONL and verifies 0% mismatch when outputs match."""
    def mock_post(url, json, timeout):
        cached = json.get("cache_prompt", False)
        # Consistent output regardless of cache flag (greedy equality)
        return make_mock_http_response(
            content="echo identical",
            timings={"prompt_n": 80, "cache_n": 40 if cached else 0, "prompt_ms": 10.0 if cached else 30.0},
        )

    mock_session = MagicMock()
    mock_session.post.side_effect = mock_post
    backend = LlamaServerBackend(session=mock_session)

    prompts = generate_e0_prompts(count=10)
    jsonl_path = str(tmp_path / "e0_test.jsonl")

    res = run_e0_experiment(
        backend=backend,
        prompts=prompts,
        output_jsonl_path=jsonl_path,
    )

    assert res["total_prompts"] == 10
    assert res["matches"] == 10
    assert res["mismatches"] == 0
    assert res["mismatch_rate"] == 0.0
    assert res["gate_passed"] is True

    # Verify JSONL lines
    with open(jsonl_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    assert len(records) == 10
    for r in records:
        assert r["match"] is True
        assert r["cache_prompt_off"]["content"] == "echo identical"
        assert r["cache_prompt_on"]["content"] == "echo identical"


def test_e0_experiment_detects_deliberate_mismatch(tmp_path):
    """Verify E0 experiment correctly detects and flags output divergence."""
    def mock_post(url, json, timeout):
        cached = json.get("cache_prompt", False)
        # Divergent output simulating cache degradation bug
        content = "echo CACHED" if cached else "echo UNCACHED"
        return make_mock_http_response(content=content)

    mock_session = MagicMock()
    mock_session.post.side_effect = mock_post
    backend = LlamaServerBackend(session=mock_session)

    prompts = generate_e0_prompts(count=5)
    jsonl_path = str(tmp_path / "e0_mismatch.jsonl")

    res = run_e0_experiment(
        backend=backend,
        prompts=prompts,
        output_jsonl_path=jsonl_path,
    )

    assert res["total_prompts"] == 5
    assert res["matches"] == 0
    assert res["mismatches"] == 5
    assert res["mismatch_rate"] == 100.0
    assert res["gate_passed"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
