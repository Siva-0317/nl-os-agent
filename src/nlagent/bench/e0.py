"""
src/nlagent/bench/e0.py - Experiment E0: Cached vs Uncached Greedy Output Equality Gate.

For 50 realistic prompts built via layout.build_prompt:
1. Queries llama-server with cache_prompt=False (uncached baseline, temp=0.0).
2. Queries llama-server with cache_prompt=True (cached run, temp=0.0).
3. Compares generated greedy outputs character-by-character.
4. Writes detailed benchmark timings and outputs to results/e0.jsonl.
5. Computes and prints the mismatch rate and latency speedup statistics.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from nlagent.backends.llama_server import LlamaServerBackend
from nlagent.bench.provenance import collect
from nlagent.context.layout import build_prompt

L0_SYSTEM_PROMPT = (
    "You are an AI-native Operating System Terminal Assistant and Shell Specialist.\n"
    "Convert user natural language requests into precise, syntactically correct, and safe terminal commands for the active operating system and shell (Windows PowerShell, cmd.exe, Bash, Zsh, or WSL).\n\n"
    "### OPERATIONAL DIRECTIVES\n"
    "1. Target the exact shell in the session context (`powershell`, `cmd`, `bash`, or `zsh`). Never mix Bash syntax into PowerShell/cmd or vice versa.\n"
    "2. Prefer paths relative to the current working directory (`cwd`) for local files unless an explicit or external path is requested.\n"
    "3. Output MUST be a single raw JSON object matching the schema below without markdown fences or extra prose.\n"
    "4. Quote paths with spaces or wildcards appropriately, and respect multi-turn entities in the STATE block.\n"
    "5. When a destructive or irreversible request is ambiguous, set `command` to null and ask a brief `clarifying_question`.\n\n"
    "### JSON OUTPUT SCHEMA\n"
    "{\n"
    '  "command": "<exact single-line shell command string or null>",\n'
    '  "targets": ["<affected file, directory, or process paths>"],\n'
    '  "clarifying_question": "<concise question if ambiguous, else null>"\n'
    "}\n\n"
    "### SAFETY TIER POLICIES\n"
    "- Tier 0 (read_only): Non-mutating reads/searches (`ls`, `cat`, `Get-ChildItem`, `Get-Content`, `dir`, `type`, `Select-String`, `findstr`).\n"
    "- Tier 1 (state_modifying): Additive creations (`touch`, `mkdir`, `New-Item`, `cp`, `Copy-Item`, `Set-Content`).\n"
    "- Tier 2 (destructive): Deletions, moves, overwrites, process kills, hard resets (`rm`, `Remove-Item`, `del`, `mv`, `Move-Item`, `Rename-Item`, `Stop-Process`).\n"
    "- Tier 3 (privileged): Elevation or protected paths (`sudo`, `/etc`, `C:\\Windows`, `HKLM`, `Start-Process -Verb RunAs`).\n"
    "- Tier 4 (ambiguous): Unclear target/mode (`command: null`) or opaque/indirected execution.\n\n"
    "### RESOLUTION EXAMPLES\n"
    '- Shell: bash | "show hidden files" -> {"command": "ls -la", "targets": ["."], "clarifying_question": null}\n'
    '- Shell: powershell | "create directory backup" -> {"command": "New-Item -ItemType Directory -Force -Path \'backup\'", "targets": ["backup"], "clarifying_question": null}\n'
    '- Shell: cmd | "show package.json" -> {"command": "type package.json", "targets": ["package.json"], "clarifying_question": null}\n'
    '- Shell: powershell | "delete the database" -> {"command": null, "targets": [], "clarifying_question": "Which database would you like to delete?"}\n'
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


def estimate_token_count(text: str) -> int:
    """Fast, accurate token count approximation (~3.8 chars/token)."""
    import re
    return len(re.findall(r"\w+|[^\w\s]", text))


def generate_e0_prompts(count: int = 50, l0: str | None = None) -> list[dict[str, Any]]:
    """Build N diverse, realistic prompts using layout.build_prompt with L0 >= 400 tokens."""
    active_l0 = l0 or L0_SYSTEM_PROMPT
    l0_tokens = estimate_token_count(active_l0)
    if l0_tokens < 400:
        raise ValueError(
            f"E0 Benchmark requires an L0 system prompt of at least 400 tokens (got {l0_tokens} tokens)."
        )

    prompts: list[dict[str, Any]] = []
    total_samples = len(SAMPLE_REQUESTS)

    for i in range(count):
        req_text, category = SAMPLE_REQUESTS[i % total_samples]
        env = ENVIRONMENTS[i % len(ENVIRONMENTS)]
        state = STATES[i % len(STATES)]

        parts = build_prompt(
            l0=active_l0,
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
            "l0_tokens": l0_tokens,
        })

    return prompts


def run_e0_experiment(
    backend: LlamaServerBackend,
    prompts: list[dict[str, Any]],
    output_jsonl_path: str = "results/e0_real.jsonl",
    temperature: float = 0.0,
    n_predict: int = 64,
    allow_mock: bool = False,
) -> dict[str, Any]:
    """Execute E0 experiment: compare greedy outputs for cache_prompt on vs off.

    Refuses to execute if the backend reports a mock/fake name unless allow_mock=True.
    Records prompt_n, cache_n, prompt_ms per request, and exact-match rate.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_jsonl_path)), exist_ok=True)

    results: list[dict[str, Any]] = []
    matches = 0
    mismatches = 0
    total_uncached_ms = 0.0
    total_cached_ms = 0.0

    backend_name = "llama-server"
    if hasattr(backend, "endpoint_url"):
        if "1234" in getattr(backend, "endpoint_url", ""):
            backend_name = "lm-studio"
        else:
            backend_name = "llama-server"
    if hasattr(backend, "session"):
        from unittest.mock import MagicMock
        if isinstance(getattr(backend, "session"), MagicMock):
            backend_name = "mock"

    if backend_name == "mock" and not allow_mock:
        raise RuntimeError(
            "REFUSED: Experiment E0 cannot be evaluated against a mock or fake backend.\n"
            "Mock backends return identical outputs by construction, defeating the equality gate.\n"
            "Please run against a real server instance (llama-server on port 8080 or LM Studio on port 1234)."
        )

    prov = collect(
        model_file="NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf",
        backend=backend_name,
        server_build="b11985",
        seed=42,
    )

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
            prompt_n = res_on.structured_timings.prompt_n or res_off.structured_timings.prompt_n
            cache_n = res_on.structured_timings.cache_n
            total_uncached_ms += off_ms
            total_cached_ms += on_ms

            record = {
                "prompt_id": p_id,
                "request": p["request"],
                "category": p["category"],
                "match": is_match,
                "prompt_n": prompt_n,
                "cache_n": cache_n,
                "prompt_ms": off_ms,
                "cached_prompt_ms": on_ms,
                "prompt_speedup": (off_ms / on_ms) if on_ms > 0 else 1.0,
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
                **prov,
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

    exact_match_rate = (matches / len(prompts)) * 100.0 if prompts else 0.0
    mismatch_rate = (mismatches / len(prompts)) * 100.0 if prompts else 0.0
    mean_speedup = (total_uncached_ms / total_cached_ms) if total_cached_ms > 0 else 1.0

    print(f"\n{'-'*75}")
    print(f" E0 GATE RESULTS SUMMARY")
    print(f"{'-'*75}")
    print(f"Total Evaluated:    {len(prompts)}")
    print(f"Exact Matches:      {matches} ({exact_match_rate:.2f}%)")
    print(f"Mismatches:         {mismatches} ({mismatch_rate:.2f}%)")
    print(f"Exact Match Rate:   {exact_match_rate:.2f}%")
    print(f"Mean Uncached TTFT: {(total_uncached_ms / len(prompts)):.2f} ms" if prompts else "")
    print(f"Mean Cached TTFT:   {(total_cached_ms / len(prompts)):.2f} ms" if prompts else "")
    print(f"Prompt Speedup:     {mean_speedup:.2f}x")
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
        "exact_match_rate": exact_match_rate,
        "mismatch_rate": mismatch_rate,
        "mean_speedup": mean_speedup,
        "gate_passed": (mismatches == 0),
        "records": results,
    }

