#!/usr/bin/env python3
"""
scripts/e0_equality.py - Experiment E0: Cached vs Uncached Greedy Output Equality Gate.

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
from datetime import datetime, timezone
from typing import Any

# Ensure project root is in sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)
_src = os.path.join(_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.backends.llama_server import LlamaServerBackend
from nlagent.context.layout import build_prompt

L0_SYSTEM_PROMPT = (
    "You are an AI-native operating system shell agent for Linux and macOS.\n"
    "Convert user natural language requests into exact, safe terminal commands.\n"
    "JSON Schema: {\"command\": string, \"targets\": string[]}"
)

SAMPLE_REQUESTS = [
    ("list all files including hidden ones", "read_only"),
    ("find files bigger than 100MB in current directory", "read_only"),
    ("show python files modified in the last 7 days", "read_only"),
    ("how much disk space is left on the root drive", "read_only"),
    ("display all running processes sorted by memory usage", "read_only"),
    ("find process using port 8080", "read_only"),
    ("count total lines in all python files under src", "read_only"),
    ("show recent git commits with author and date", "read_only"),
    ("check active network connections", "read_only"),
    ("display system uptime and load average", "read_only"),
    ("search for TODO comments across all source files", "read_only"),
    ("show environment variables", "read_only"),
    ("view last 50 lines of application log", "read_only"),
    ("check docker container status", "read_only"),
    ("find empty directories inside workspace", "read_only"),
    ("show memory usage in gigabytes", "read_only"),
    ("display current user and group memberships", "read_only"),
    ("list listening TCP ports with process IDs", "read_only"),
    ("check git status in short format", "read_only"),
    ("show file sizes in human-readable format", "read_only"),
    ("create a directory named backups", "state_modifying"),
    ("make a nested folder structure logs/2026/nov", "state_modifying"),
    ("touch a new file named config.yaml", "state_modifying"),
    ("copy main.py to main_backup.py", "state_modifying"),
    ("compress all log files into archive.tar.gz", "state_modifying"),
    ("extract contents of archive.zip into output/", "state_modifying"),
    ("append current timestamp to audit.log", "state_modifying"),
    ("create an empty git branch called feature-cache", "state_modifying"),
    ("install dependencies from requirements.txt", "state_modifying"),
    ("build docker container image tagged nlagent:latest", "state_modifying"),
    ("clone repository from github", "state_modifying"),
    ("create symlink from config.json to app_config.json", "state_modifying"),
    ("download release tarball with curl", "state_modifying"),
    ("format python code using ruff", "state_modifying"),
    ("initialize new git repository", "state_modifying"),
    ("delete temporary build directory", "destructive"),
    ("remove all .pyc files recursively", "destructive"),
    ("clean up unused docker containers and images", "destructive"),
    ("truncate error.log to zero bytes", "destructive"),
    ("remove file report.pdf", "destructive"),
    ("terminate process with PID 4321", "destructive"),
    ("kill all background node processes", "destructive"),
    ("overwrite config.json with empty object", "destructive"),
    ("reset git staging area discarding changes", "destructive"),
    ("remove cached package wheels", "destructive"),
    ("delete that file", "destructive"),
    ("delete old directory", "destructive"),
    ("stop and remove stopped containers", "destructive"),
    ("remove trailing whitespace from all markdown docs", "state_modifying"),
    ("show differences between main and staging branch", "read_only"),
]

ENVIRONMENTS = [
    {
        "os": "Ubuntu 22.04 LTS (x86_64)",
        "shell": "bash",
        "user": "sivab",
        "path_style": "posix",
        "available_tools": ["bash", "curl", "docker", "find", "git", "grep", "python3", "tar"],
    },
    {
        "os": "Debian 12 Bookworm",
        "shell": "zsh",
        "user": "developer",
        "path_style": "posix",
        "available_tools": ["awk", "curl", "git", "grep", "pip", "sed", "zsh"],
    },
    {
        "os": "macOS 14.4 Sonoma",
        "shell": "zsh",
        "user": "admin",
        "path_style": "posix",
        "available_tools": ["brew", "curl", "docker", "git", "lsof", "python3", "zsh"],
    },
    {
        "os": "Arch Linux (Rolling)",
        "shell": "bash",
        "user": "builder",
        "path_style": "posix",
        "available_tools": ["bash", "docker", "find", "git", "pacman", "python3", "systemctl"],
    },
]

STATES = [
    "STATE cwd=/home/sivab/project\nENT f1=./report.pdf(last:t1)",
    "STATE cwd=/var/log/nginx\nENT f1=./access.log(last:t2) f2=./error.log(last:t2)",
    "STATE cwd=/home/developer/workspace/app\nENT d1=./build(last:t3) f1=./app.py(last:t2)",
    "STATE cwd=/opt/services/api\nENT d1=./backups(last:t4)",
    "STATE cwd=/tmp/scratch\nENT f1=./temp.dat(last:t1)",
]


def generate_e0_prompts(count: int = 50) -> list[dict[str, Any]]:
    """Build N diverse, realistic prompts using layout.build_prompt."""
    prompts: list[dict[str, Any]] = []
    total_samples = len(SAMPLE_REQUESTS)

    for i in range(count):
        req_text, category = SAMPLE_REQUESTS[i % total_samples]
        env = ENVIRONMENTS[i % len(ENVIRONMENTS)]
        state = STATES[i % len(STATES)]

        parts = build_prompt(
            l0=L0_SYSTEM_PROMPT,
            l1=env,
            state=state,
            request=req_text,
        )

        prompts.append({
            "prompt_id": i + 1,
            "request": req_text,
            "category": category,
            "env_os": env["os"],
            "state_cwd": state.split("\n")[0],
            "full_prompt": parts.full_text,
            "prefix_prompt": parts.prefix_text,
        })

    return prompts


def run_e0_experiment(
    backend: LlamaServerBackend,
    prompts: list[dict[str, Any]],
    output_jsonl_path: str = "results/e0.jsonl",
    temperature: float = 0.0,
    n_predict: int = 64,
) -> dict[str, Any]:
    """Execute E0 experiment: compare greedy outputs for cache_prompt on vs off."""
    os.makedirs(os.path.dirname(os.path.abspath(output_jsonl_path)), exist_ok=True)

    results: list[dict[str, Any]] = []
    matches = 0
    mismatches = 0
    total_uncached_ms = 0.0
    total_cached_ms = 0.0

    print(f"\n{'='*75}")
    print(f" EXPERIMENT E0: CACHED VS UNCACHED GREEDY OUTPUT EQUALITY GATE")
    print(f"{'='*75}")
    print(f"Total Prompts:      {len(prompts)}")
    print(f"Temperature:        {temperature} (Greedy)")
    print(f"Output JSONL:       {output_jsonl_path}")
    print(f"Endpoint:           {backend.endpoint_url}")
    print(f"{'-'*75}")

    with open(output_jsonl_path, "w", encoding="utf-8") as f_out:
        for p in prompts:
            p_id = p["prompt_id"]
            prompt_str = p["full_prompt"]

            # 1. Query with cache_prompt=False (uncached baseline)
            res_off = backend.complete(
                prompt=prompt_str,
                cache_prompt=False,
                temperature=temperature,
                n_predict=n_predict,
            )

            # 2. Query with cache_prompt=True (cached evaluation)
            res_on = backend.complete(
                prompt=prompt_str,
                cache_prompt=True,
                temperature=temperature,
                n_predict=n_predict,
            )

            # Compare outputs character-by-character
            is_match = (res_off.content.strip() == res_on.content.strip())
            if is_match:
                matches += 1
                status_flag = "PASS"
            else:
                mismatches += 1
                status_flag = "MISMATCH"

            off_ms = res_off.structured_timings.prompt_ms
            on_ms = res_on.structured_timings.prompt_ms
            total_uncached_ms += off_ms
            total_cached_ms += on_ms

            record = {
                "prompt_id": p_id,
                "request": p["request"],
                "category": p["category"],
                "match": is_match,
                "cache_prompt_off": {
                    "content": res_off.content,
                    "wall_time_s": res_off.wall_time_s,
                    "timings": res_off.timings,
                    "status": res_off.status,
                    "error": res_off.error,
                },
                "cache_prompt_on": {
                    "content": res_on.content,
                    "wall_time_s": res_on.wall_time_s,
                    "timings": res_on.timings,
                    "status": res_on.status,
                    "error": res_on.error,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            f_out.write(json.dumps(record) + "\n")
            f_out.flush()
            results.append(record)

            print(
                f"[{status_flag:8s}] Prompt {p_id:02d}/50 | "
                f"Uncached: {res_off.wall_time_s*1000:6.1f}ms | "
                f"Cached: {res_on.wall_time_s*1000:6.1f}ms | "
                f"{p['request'][:35]}"
            )

    mismatch_rate = (mismatches / len(prompts)) * 100.0 if prompts else 0.0

    print(f"\n{'-'*75}")
    print(f" E0 GATE RESULTS SUMMARY")
    print(f"{'-'*75}")
    print(f"Total Evaluated:    {len(prompts)}")
    print(f"Exact Matches:      {matches}")
    print(f"Mismatches:         {mismatches}")
    print(f"Mismatch Rate:      {mismatch_rate:.2f}%")
    if matches == len(prompts):
        print(f"Gate Status:        PASSED (Outputs from cached and uncached runs match)")
    else:
        print(f"Gate Status:        FAILED (Mismatch rate > 0.0%)")
    print(f"Log written to:     {output_jsonl_path}")
    print(f"{'='*75}\n")

    return {
        "total_prompts": len(prompts),
        "matches": matches,
        "mismatches": mismatches,
        "mismatch_rate": mismatch_rate,
        "gate_passed": (mismatches == 0),
        "records": results,
    }


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
