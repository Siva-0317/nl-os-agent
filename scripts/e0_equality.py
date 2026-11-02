#!/usr/bin/env python3
"""
scripts/e0_equality.py - Experiment E0: Cached vs Uncached Greedy Output Equality Gate CLI.

For 50 realistic prompts built via layout.build_prompt (with L0 >= 400 tokens):
1. Strictly refuses to run against mock or fake backends (real-server only).
2. Probes local server endpoints (llama-server on port 8080 or LM Studio on port 1234).
3. Queries server with cache_prompt=False (uncached baseline, temp=0.0).
4. Queries server with cache_prompt=True (cached evaluation, temp=0.0).
5. Compares generated greedy outputs character-by-character.
6. Writes detailed benchmark timings (prompt_n, cache_n, prompt_ms) to results/e0_real.jsonl with full provenance.
7. Computes and prints the exact-match rate and latency speedup statistics.

Usage:
    python scripts/e0_equality.py [--endpoint http://127.0.0.1:8080/completion]
                                  [--output results/e0_real.jsonl]
                                  [--n-prompts 50]
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure project root & src are in sys.path when run directly
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_root, "src")
for p in (_root, _src):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.backends.llama_server import LlamaServerBackend
from nlagent.bench.e0 import generate_e0_prompts, run_e0_experiment


def resolve_endpoint(requested: str) -> str:
    """Probe 8080 (llama-server) and 1234 (LM Studio) to auto-select active local server."""
    if requested != "http://127.0.0.1:8080/completion":
        return requested
    import requests
    try:
        r = requests.get("http://127.0.0.1:8080/health", timeout=0.5)
        if r.status_code == 200:
            return requested
    except Exception:
        pass
    try:
        r = requests.get("http://127.0.0.1:1234/v1/models", timeout=0.5)
        if r.status_code == 200:
            return "http://127.0.0.1:1234/v1/chat/completions"
    except Exception:
        pass
    return requested


def verify_real_backend(backend: LlamaServerBackend) -> None:
    """Refuse execution if backend or session reports a mock/fake name."""
    backend_name = getattr(backend, "name", "").lower()
    if any(m in backend_name for m in ("mock", "fake", "stub")):
        raise RuntimeError(
            f"REFUSED: Backend '{backend.name}' is a mock/fake backend.\n"
            "Experiment E0 requires a live, real server (llama-server or LM Studio) to evaluate equality."
        )
    if hasattr(backend, "session"):
        from unittest.mock import MagicMock
        if isinstance(getattr(backend, "session"), MagicMock):
            raise RuntimeError(
                "REFUSED: Backend session is a MagicMock instance.\n"
                "Experiment E0 requires real HTTP execution against a physical inference server."
            )


def main():
    parser = argparse.ArgumentParser(description="Run Experiment E0: Cached vs Uncached Greedy Output Equality on real server.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/completion", help="llama-server / LM Studio endpoint URL")
    parser.add_argument("--output", default="results/e0_real.jsonl", help="Path to write e0_real.jsonl results (default results/e0_real.jsonl)")
    parser.add_argument("--n-prompts", type=int, default=50, help="Number of prompts to evaluate (default 50)")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP request timeout in seconds")
    parser.add_argument("--mock", action="store_true", help="Disallowed: triggers explicit refusal error")
    args = parser.parse_args()

    # 1. Refusal Gate on Mock/Fake flag
    if args.mock:
        sys.stderr.write(
            "\n" + "=" * 80 + "\n"
            "REFUSAL ERROR: Experiment E0 cannot be evaluated against a mock or fake backend.\n"
            "Mock backends return identical outputs by construction, defeating the equality gate.\n"
            "Please run against a live inference server (llama-server on port 8080 or LM Studio on port 1234).\n"
            + "=" * 80 + "\n\n"
        )
        sys.exit(1)

    # 2. Resolve live endpoint
    active_endpoint = resolve_endpoint(args.endpoint)
    backend = LlamaServerBackend(endpoint_url=active_endpoint, timeout=args.timeout)

    # 3. Refusal Gate on Mock Backend inspection
    verify_real_backend(backend)

    # 4. Generate 50 realistic prompts with L0 >= 400 tokens
    prompts = generate_e0_prompts(count=args.n_prompts)
    print(f"Generated {len(prompts)} prompts with L0 >= 400 tokens (L0 tokens: ~{prompts[0]['l0_tokens']}).")

    # 5. Run Experiment E0 against real server and write results/e0_real.jsonl
    res = run_e0_experiment(
        backend=backend,
        prompts=prompts,
        output_jsonl_path=args.output,
        allow_mock=False,
    )

    if not res["gate_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
