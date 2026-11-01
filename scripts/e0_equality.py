#!/usr/bin/env python3
"""
scripts/e0_equality.py - Experiment E0: Cached vs Uncached Greedy Output Equality Gate CLI.

For 50 realistic prompts built via layout.build_prompt:
1. Queries llama-server with cache_prompt=False (uncached baseline, temp=0.0).
2. Queries llama-server with cache_prompt=True (cached run, temp=0.0).
3. Compares generated greedy outputs character-by-character.
4. Writes detailed benchmark timings and outputs to results/e0.jsonl.
5. Computes and prints the mismatch rate and latency speedup statistics.

Usage:
    python scripts/e0_equality.py [--endpoint http://127.0.0.1:8080/completion]
                                  [--output results/e0.jsonl]
                                  [--n-prompts 50]
                                  [--mock]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure project root & src are in sys.path when run directly
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)
_src = os.path.join(_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.backends.llama_server import LlamaServerBackend
from nlagent.bench.e0 import (
    ENVIRONMENTS,
    L0_SYSTEM_PROMPT,
    SAMPLE_REQUESTS,
    STATES,
    generate_e0_prompts,
    run_e0_experiment,
)


def main():
    parser = argparse.ArgumentParser(description="Run Experiment E0: Cached vs Uncached Greedy Output Equality.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/completion", help="llama-server endpoint URL")
    parser.add_argument("--output", default="results/e0.jsonl", help="Path to write e0.jsonl results")
    parser.add_argument("--n-prompts", type=int, default=50, help="Number of prompts to evaluate (default 50)")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP request timeout in seconds")
    parser.add_argument("--mock", action="store_true", help="Run with an in-memory mock server for offline verification")
    args = parser.parse_args()

    prompts = generate_e0_prompts(count=args.n_prompts)

    if args.mock:
        from unittest.mock import MagicMock
        mock_session = MagicMock()

        def mock_post(url, json, timeout):
            import requests
            resp = requests.Response()
            resp.status_code = 200
            prompt = json.get("prompt", "")
            cached = json.get("cache_prompt", False)
            content = f'{{"command": "echo {len(prompt)}", "targets": []}}'
            timings = {
                "prompt_n": len(prompt) // 4,
                "prompt_ms": 15.0 if cached else 45.0,
                "cache_n": len(prompt) // 5 if cached else 0,
            }
            resp._content = json_dump({"content": content, "timings": timings}).encode("utf-8")
            return resp

        def json_dump(obj):
            import json
            return json.dumps(obj)

        mock_session.post.side_effect = mock_post
        backend = LlamaServerBackend(endpoint_url=args.endpoint, timeout=args.timeout, session=mock_session)
    else:
        backend = LlamaServerBackend(endpoint_url=args.endpoint, timeout=args.timeout)

    run_e0_experiment(
        backend=backend,
        prompts=prompts,
        output_jsonl_path=args.output,
    )


if __name__ == "__main__":
    main()
