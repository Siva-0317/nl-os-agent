"""
tests/test_reasoning_breakdown.py - Unit tests for reasoning control and latency breakdown.

Tests reasoning toggle in backend, layout, and config, token logging, tag stripping,
and breakdown script execution with mocked HTTP layer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure src and root are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from nlagent.backends.llama_server import CompletionResult, LlamaServerBackend
from nlagent.bench.provenance import validate_provenance
from nlagent.config import AgentConfig
from nlagent.context.layout import build_prompt
from nlagent.context.state import SessionState
from nlagent.daemon.propose import extract_json_proposal, propose
from scripts.run_breakdown import MockLlamaServerSession, run_latency_breakdown


class TestReasoningControl(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_agent_config_defaults_and_env(self):
        # Default config
        cfg = AgentConfig()
        self.assertTrue(cfg.reasoning)
        self.assertEqual(cfg.model_name, "nvidia/nemotron-3-nano-4b")

        # Config from env override
        with patch.dict(os.environ, {"NLAGENT_REASONING": "false", "NLAGENT_N_PREDICT": "128"}):
            env_cfg = AgentConfig.from_env()
            self.assertFalse(env_cfg.reasoning)
            self.assertEqual(env_cfg.n_predict, 128)

        # Dictionary serialization
        d = cfg.to_dict()
        self.assertEqual(d["reasoning"], True)
        from_d = AgentConfig.from_dict(d)
        self.assertEqual(from_d.reasoning, True)

    def test_layout_build_prompt_reasoning_toggle(self):
        l0 = "You are an OS agent."
        l1 = {"os": "Linux", "shell": "bash"}
        state = "STATE: cwd=/tmp"
        request = "list files"

        # Reasoning ON (default)
        prompt_on = build_prompt(l0=l0, l1=l1, state=state, request=request, reasoning=True)
        self.assertTrue(prompt_on.reasoning)
        self.assertNotIn("<think></think>", prompt_on.full_text)

        # Reasoning OFF
        prompt_off = build_prompt(l0=l0, l1=l1, state=state, request=request, reasoning=False)
        self.assertFalse(prompt_off.reasoning)
        self.assertIn("<think></think>", prompt_off.full_text)

    def test_llama_server_backend_token_logging_and_tags(self):
        # Mock HTTP session returning thinking trace + JSON
        raw_llm_output = (
            "<think>\n"
            "Analyzing file inspection request.\n"
            "Recommended tool is ls -la.\n"
            "</think>\n"
            '{"command": "ls -la", "targets": ["."], "explanation": "List directory"}'
        )
        mock_payload = {
            "choices": [{"message": {"role": "assistant", "content": raw_llm_output}}],
            "timings": {
                "prompt_n": 820,
                "prompt_ms": 38.5,
                "predicted_n": 48,
                "predicted_ms": 1440.0,
            },
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        backend = LlamaServerBackend(
            endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
            session=mock_session,
            reasoning=True,
        )

        res = backend.complete("show files")

        # 1. Tag stripper verification: content should NOT contain <think>
        self.assertNotIn("<think>", res.content)
        self.assertNotIn("</think>", res.content)
        self.assertIn('"command": "ls -la"', res.content)

        # 2. Token logging verification
        self.assertEqual(res.prompt_tokens, 820)
        self.assertEqual(res.output_tokens, 48)
        self.assertEqual(res.prefill_ms, 38.5)
        self.assertEqual(res.decode_ms, 1440.0)

        # 3. Reasoning tokens calculation
        self.assertGreater(res.reasoning_tokens, 5)
        self.assertIn("Analyzing file inspection request", res.reasoning_content)

    def test_llama_server_backend_reasoning_off_payload(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": '{"command": "ps"}'}}],
            "timings": {"prompt_n": 500, "predicted_n": 10, "prompt_ms": 20.0, "predicted_ms": 300.0},
        }
        mock_session.post.return_value = mock_resp

        # Chat completions API: reasoning=False should set chat_template_kwargs
        backend = LlamaServerBackend(
            endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
            session=mock_session,
            reasoning=False,
        )
        backend.complete("show processes")

        call_kwargs = mock_session.post.call_args[1]["json"]
        self.assertEqual(call_kwargs.get("chat_template_kwargs"), {"enable_thinking": False})

    def test_tag_stripper_in_propose(self):
        # Ensure tag stripper in propose() is intact and functions properly
        raw_with_tags = (
            "<think>Thinking about git checkout...</think>\n"
            '```json\n{"command": "git checkout main", "targets": ["main"], "explanation": "Switch branch"}\n```'
        )
        parsed = extract_json_proposal(raw_with_tags)
        self.assertEqual(parsed["command"], "git checkout main")
        self.assertEqual(parsed["targets"], ["main"])

    def test_propose_wires_reasoning_and_config(self):
        state = SessionState(cwd="/home/user")
        mock_backend = MagicMock()
        mock_backend.complete.return_value = CompletionResult(
            content='{"command": "pwd", "targets": [], "explanation": "Print working directory"}',
            timings={"prompt_n": 100, "predicted_n": 15},
            wall_time_s=0.1,
            status="success",
        )

        cfg = AgentConfig(reasoning=False)
        proposal = propose(
            request="where am i",
            session_state=state,
            backend=mock_backend,
            config=cfg,
        )

        self.assertEqual(proposal["command"], "pwd")
        # Assert backend was called with reasoning=False
        call_kwargs = mock_backend.complete.call_args[1]
        self.assertEqual(call_kwargs.get("reasoning"), False)

    def test_run_breakdown_script_mock(self):
        out_jsonl = os.path.join(self.temp_dir, "breakdown.jsonl")
        out_md = os.path.join(self.temp_dir, "breakdown.md")

        records = run_latency_breakdown(
            count=5,
            output_path=out_jsonl,
            mock=True,
            seed=42,
        )

        # 5 paired requests -> 10 records total
        self.assertEqual(len(records), 10)
        self.assertTrue(os.path.exists(out_jsonl))
        self.assertTrue(os.path.exists(out_md))

        # Validate every record has all 5 required token/latency fields and provenance
        for r in records:
            self.assertIn("prompt_tokens", r)
            self.assertIn("output_tokens", r)
            self.assertIn("prefill_ms", r)
            self.assertIn("decode_ms", r)
            self.assertIn("reasoning_tokens", r)
            validate_provenance(r)

        # Verify reasoning ON emitted reasoning tokens, OFF did not
        on_rows = [r for r in records if r["condition"] == "reasoning_on"]
        off_rows = [r for r in records if r["condition"] == "reasoning_off"]

        self.assertEqual(len(on_rows), 5)
        self.assertEqual(len(off_rows), 5)
        self.assertTrue(all(r["reasoning_tokens"] > 0 for r in on_rows))
        self.assertTrue(all(r["reasoning_tokens"] == 0 for r in off_rows))


if __name__ == "__main__":
    unittest.main()

