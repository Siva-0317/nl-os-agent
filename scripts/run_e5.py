#!/usr/bin/env python3
"""
scripts/run_e5.py - 2x2 Factorial Evaluation Matrix Runner (Experiment E5).

Evaluates the live Nemotron-3-Nano model against base / zero-shot prompts:
  Model:  {base, tuned}
  Prompt: {full, short}

Evaluation Contracts & Governance:
1. Strict Dev Split Partitioning:
   - Default split: 'dev' pointing strictly to datasets/dev_v3.jsonl (N >= 100).
   - Rejects execution with ValueError if dev split count N < 100.
   - Sealed test set (datasets/sealed_test.jsonl) remains untouched unless --split sealed is explicitly requested.
2. Mock Backend Exclusion:
   - Official benchmark runs exclude mock backends by default (allow_mock=False).
   - Requires live Nemotron-3-Nano model server (llama-server or LM Studio on 127.0.0.1:8080/1234).
   - allow_mock=True is restricted to programmatic synthetic unit tests.
3. Rigorous Paired Statistics:
   - Computes 95% Wilson Score Confidence Intervals for all binomial accuracy and JSON validity rates.
   - Performs exact two-tailed McNemar tests (binomial when discordant < 25, continuity-corrected chi2 when >= 25).
   - Computes paired difference confidence intervals (Delta = p_tuned - p_base with 95% CI).
4. Provenance & Artifacts:
   - Records full provenance (run_id, git_sha, model_file, model_sha256, backend, server_build, gpu, driver, timestamp, seed) into results/e5_dev.jsonl and results/e5_dev_summary.md.
"""

from __future__ import annotations

import argparse
import json as _json
import math
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# Ensure src and root are in sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_root, "src")
for p in (_root, _src):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.backends.llama_server import (
    CompletionResult,
    LlamaServerBackend,
    estimate_token_count,
    resolve_local_endpoint,
)
from nlagent.bench.e0 import L0_SYSTEM_PROMPT
from nlagent.bench.provenance import collect, validate_provenance
from nlagent.bench.stats import (
    mcnemar_test as _mcnemar_test,
    paired_difference_interval as _paired_difference_interval,
    wilson_interval as _wilson_interval,
)
from nlagent.context.layout import DEFAULT_SHORT_L0, build_prompt
from nlagent.eval.exec_eval import TestCase, evaluate_case

SHORT_L0_PROMPT = (
    "You are an AI shell agent. Convert user requests into exact terminal commands matching the active environment.\n"
    "Output ONLY a single valid JSON object: {\"command\": \"...\", \"targets\": [...], \"clarifying_question\": null}."
)

ZERO_SHOT_L0_PROMPT = (
    "Convert user requests into exact terminal commands matching the active environment.\n"
    "Output ONLY a single valid JSON object: {\"command\": \"...\", \"targets\": [...], \"clarifying_question\": null}."
)


# -----------------------------------------------------------------------------
# Statistical Formulations: Wilson Score Interval, McNemar's Test & Paired Diff
# -----------------------------------------------------------------------------

def calculate_wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float, float]:
    """
    Compute the Wilson score confidence interval for a binomial proportion.

    Returns:
        (point_estimate_pct, lower_bound_pct, upper_bound_pct)
    """
    if total <= 0:
        return 0.0, 0.0, 0.0

    low_pct, high_pct = _wilson_interval(successes, total, confidence=confidence, as_percent=True)
    p_hat = round((successes / total) * 100.0, 2)
    return p_hat, round(low_pct, 2), round(high_pct, 2)


def calculate_mcnemar_test(results_a: Sequence[bool], results_b: Sequence[bool]) -> dict[str, Any]:
    """
    Compute McNemar's test for paired binary outcomes on the exact same test cases.

    Contingency table:
      - both_correct: Condition A=Pass, Condition B=Pass
      - b:           Condition A=Pass, Condition B=Fail  (discordant)
      - c:           Condition A=Fail, Condition B=Pass  (discordant)
      - both_failed:  Condition A=Fail, Condition B=Fail

    Uses Edwards' continuity correction when (b + c) >= 25.
    Uses exact two-tailed binomial test when (b + c) < 25.
    """
    if len(results_a) != len(results_b):
        raise ValueError(f"Mismatched result lengths for paired test: {len(results_a)} vs {len(results_b)}")

    both_correct = sum(1 for a, b in zip(results_a, results_b) if a and b)
    both_failed = sum(1 for a, b in zip(results_a, results_b) if not a and not b)

    raw_res = _mcnemar_test(results_a, results_b, alpha=0.05)
    b = raw_res["b"]
    c = raw_res["c"]
    discordant = raw_res["discordant_total"]
    method = raw_res["method"]

    chi2 = raw_res["statistic"] if method == "continuity_corrected_chi2" else 0.0
    p_val = raw_res["p_value"]

    return {
        "chi2": round(chi2, 4),
        "p_value": round(p_val, 6),
        "b": b,
        "c": c,
        "both_correct": both_correct,
        "both_failed": both_failed,
        "discordant_total": discordant,
        "significant_at_05": p_val < 0.05,
        "method": method,
    }


def calculate_paired_difference(
    results_test: Sequence[bool],
    results_ref: Sequence[bool],
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """
    Compute paired difference in accuracy: Delta = p_test - p_ref with 95% CI.

    Returns:
        (delta_pct, lower_ci_pct, upper_ci_pct)
    """
    n = len(results_test)
    if n == 0 or n != len(results_ref):
        return 0.0, 0.0, 0.0

    b = sum(1 for t, r in zip(results_test, results_ref) if t and not r)
    c = sum(1 for t, r in zip(results_test, results_ref) if not t and r)
    delta = (b - c) / float(n)

    low, high = _paired_difference_interval(results_test, results_ref, confidence=confidence)
    return round(delta * 100.0, 2), round(low * 100.0, 2), round(high * 100.0, 2)


# -----------------------------------------------------------------------------
# Proposal Extraction & Response Parsing
# -----------------------------------------------------------------------------

def parse_model_response(raw_text: str) -> tuple[bool, str | None, list[str], str | None, str | None]:
    """
    Extract structured proposal from raw model output.

    Returns:
        (is_valid_json, command, targets, clarifying_question, error_message)
    """
    if not raw_text or not raw_text.strip():
        return False, None, [], None, "Empty response from model"

    text = raw_text.strip()

    # Strip reasoning tags (<think>...</think>)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    # Locate outermost braces { ... } if text contains surrounding commentary
    if not (text.startswith("{") and text.endswith("}")):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

    try:
        data = _json.loads(text)
    except Exception as exc:
        return False, None, [], None, f"JSON parse error: {exc}"

    if not isinstance(data, dict):
        return False, None, [], None, f"JSON root must be an object, got {type(data).__name__}"

    command = data.get("command")
    if command is not None:
        command = str(command).strip()

    raw_targets = data.get("targets", [])
    targets = [str(t) for t in raw_targets] if isinstance(raw_targets, list) else []

    clarifying_q = data.get("clarifying_question") or data.get("clarification")
    if clarifying_q is not None:
        clarifying_q = str(clarifying_q).strip()

    # A valid output must supply either a command or a clarifying question
    if not command and not clarifying_q:
        return False, None, targets, None, "Neither 'command' nor 'clarifying_question' was provided"

    return True, command, targets, clarifying_q, None


# -----------------------------------------------------------------------------
# Dataset Loaders & Split Governance
# -----------------------------------------------------------------------------

def load_dev_v3_dataset(path: str = "datasets/dev_v3.jsonl", enforce_min_n: bool = True) -> list[dict[str, Any]]:
    """
    Load dev_v3 dataset and enforce strict N >= 100 sample size constraint.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dev split dataset not found: {path}")

    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line_s = line.strip()
            if line_s:
                records.append(_json.loads(line_s))

    if enforce_min_n and len(records) < 100:
        raise ValueError(
            f"Dev split evaluation strictly requires N >= 100 test cases, but found {len(records)} in {path}. "
            "Sealed test set access is forbidden."
        )

    return records


SEALED_TEST_CASES_SPEC = [
    {
        "case_id": "tc_01",
        "family": "file_ops",
        "shell": "powershell",
        "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
        "request": "create directory named release_build",
        "reference_command": "New-Item -ItemType Directory -Force -Path 'release_build'",
        "fixture_spec": {"README.md": "# Project Repo"},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_02",
        "family": "file_ops",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "make a new folder called artifacts",
        "reference_command": "mkdir -p artifacts",
        "fixture_spec": {"main.py": "print('ok')"},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_03",
        "family": "file_ops",
        "shell": "powershell",
        "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
        "request": "remove all log files from current directory",
        "reference_command": "Remove-Item *.log",
        "fixture_spec": {"debug.log": "test error", "audit.log": "ok", "app.py": "x=1"},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_04",
        "family": "file_ops",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "delete all log files in current directory",
        "reference_command": "rm -f *.log",
        "fixture_spec": {"server.log": "warn", "worker.log": "crit", "index.js": "console.log(1)"},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_05",
        "family": "file_ops",
        "shell": "powershell",
        "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
        "request": "copy config.json to config.backup.json",
        "reference_command": "Copy-Item -Path 'config.json' -Destination 'config.backup.json'",
        "fixture_spec": {"config.json": '{"host": "localhost", "port": 8080}'},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_06",
        "family": "file_ops",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "copy settings.yaml to settings.yaml.bak",
        "reference_command": "cp settings.yaml settings.yaml.bak",
        "fixture_spec": {"settings.yaml": "env: production\nworkers: 4\n"},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_07",
        "family": "read_only",
        "shell": "powershell",
        "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
        "request": "display content of package.json",
        "reference_command": "Get-Content 'package.json'",
        "fixture_spec": {"package.json": '{\n  "name": "nlagent",\n  "version": "1.0.0"\n}\n'},
        "is_read_only": True,
        "is_clarification": False,
    },
    {
        "case_id": "tc_08",
        "family": "read_only",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "print the first 5 lines of access.log",
        "reference_command": "head -n 5 access.log",
        "fixture_spec": {"access.log": "line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\n"},
        "is_read_only": True,
        "is_clarification": False,
    },
    {
        "case_id": "tc_09",
        "family": "file_ops",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "rename old_name.txt to new_name.txt",
        "reference_command": "mv old_name.txt new_name.txt",
        "fixture_spec": {"old_name.txt": "persisted content"},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_10",
        "family": "file_ops",
        "shell": "powershell",
        "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
        "request": "rename legacy.txt to current.txt",
        "reference_command": "Move-Item -Path 'legacy.txt' -Destination 'current.txt'",
        "fixture_spec": {"legacy.txt": "important state data"},
        "is_read_only": False,
        "is_clarification": False,
    },
    {
        "case_id": "tc_11",
        "family": "clarification",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "delete the database",
        "reference_command": None,
        "fixture_spec": {"dev.db": "data", "prod.db": "data"},
        "is_read_only": True,
        "is_clarification": True,
        "clarification_prompt": "Which database would you like to delete?",
    },
    {
        "case_id": "tc_12",
        "family": "clarification",
        "shell": "powershell",
        "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
        "request": "clean up the build files",
        "reference_command": None,
        "fixture_spec": {"dist/app.exe": "bin", "node_modules/pkg": "bin"},
        "is_read_only": True,
        "is_clarification": True,
        "clarification_prompt": "Which build artifacts would you like to remove?",
    },
    {
        "case_id": "tc_13",
        "family": "clarification",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "reset the repository",
        "reference_command": None,
        "fixture_spec": {".git/config": "repo config"},
        "is_read_only": True,
        "is_clarification": True,
        "clarification_prompt": "Do you want a soft reset or a hard reset?",
    },
    {
        "case_id": "tc_14",
        "family": "read_only",
        "shell": "powershell",
        "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
        "request": "show all files and folders in current directory",
        "reference_command": "Get-ChildItem",
        "fixture_spec": {"file1.txt": "a", "file2.txt": "b", "src/sub.py": "c"},
        "is_read_only": True,
        "is_clarification": False,
    },
    {
        "case_id": "tc_15",
        "family": "read_only",
        "shell": "bash",
        "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/project"},
        "request": "list all files including hidden ones",
        "reference_command": "ls -la",
        "fixture_spec": {".env": "SECRET=1", "README.md": "docs"},
        "is_read_only": True,
        "is_clarification": False,
    },
]


def generate_sealed_test_set(path: str = "datasets/sealed_test.jsonl", count: int = 50) -> list[dict[str, Any]]:
    """Generate or expand sealed test dataset with diverse real-world command cases."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    records: list[dict[str, Any]] = []

    base_cases = SEALED_TEST_CASES_SPEC
    for i in range(count):
        spec = base_cases[i % len(base_cases)].copy()
        spec["case_id"] = f"case_{i + 1:03d}"
        records.append(spec)

    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(_json.dumps(r) + "\n")

    return records


def load_or_create_sealed_test_set(path: str = "datasets/sealed_test.jsonl", count: int = 50) -> list[dict[str, Any]]:
    """Load sealed test set if it exists, or generate standard suite."""
    if os.path.exists(path):
        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(_json.loads(line))
        return records
    return generate_sealed_test_set(path, count=count)


# -----------------------------------------------------------------------------
# Mock Backend for Testing & Standalone Verification
# -----------------------------------------------------------------------------

class MockLlamaBackend:
    """
    Deterministic mock backend simulating Base vs Tuned model behavior on Full vs Short prompts.
    RESTRICTED TO UNIT TESTING ONLY (allow_mock=True).
    """

    def __init__(self, model_type: str = "base", prompt_mode: str = "full"):
        self.model_type = model_type.lower()
        self.prompt_mode = prompt_mode.lower()
        self.call_count = 0

    def complete(self, prompt: str, temperature: float = 0.0, max_tokens: int = 128, **kwargs: Any) -> CompletionResult:
        self.call_count += 1
        prompt_tokens = estimate_token_count(prompt)

        prefill_ms = 120.0 if self.prompt_mode == "full" else 25.0
        decode_ms = 45.0

        is_clarification = (
            "Which database" in prompt
            or "delete the database" in prompt
            or "clean up the build" in prompt
            or "which build artifacts" in prompt
            or "reset the repository" in prompt
            or "soft reset or a hard reset" in prompt
            or "which branch" in prompt
            or "all sessions" in prompt
            or "which service" in prompt
            or "which target" in prompt
        )

        if self.model_type == "tuned":
            # Tuned model: high accuracy in both Full and Short prompts
            if is_clarification:
                content = '{"command": null, "targets": [], "clarifying_question": "Which database would you like to delete?"}'
            elif "build/dist" in prompt or "create build and dist" in prompt:
                content = '{"command": "mkdir -p build/dist", "targets": ["build/dist"], "clarifying_question": null}'
            elif "temp_*.log" in prompt or "delete all temporary log" in prompt:
                if "powershell" in prompt.lower() or "windows" in prompt.lower():
                    content = '{"command": "Remove-Item -Force temp_*.log", "targets": ["temp_*.log"], "clarifying_question": null}'
                else:
                    content = '{"command": "rm -f temp_*.log", "targets": ["temp_*.log"], "clarifying_question": null}'
            elif "config.yaml.bak" in prompt or "backup config.yaml" in prompt:
                if "powershell" in prompt.lower() or "windows" in prompt.lower():
                    content = '{"command": "Copy-Item -Path config.yaml -Destination config.yaml.bak", "targets": ["config.yaml.bak"], "clarifying_question": null}'
                else:
                    content = '{"command": "cp config.yaml config.yaml.bak", "targets": ["config.yaml.bak"], "clarifying_question": null}'
            elif "old_report" in prompt:
                if "powershell" in prompt.lower() or "windows" in prompt.lower():
                    content = '{"command": "Move-Item -Path old_report.txt -Destination new_report.txt", "targets": ["new_report.txt"], "clarifying_question": null}'
                else:
                    content = '{"command": "mv old_report.txt new_report.txt", "targets": ["new_report.txt"], "clarifying_question": null}'
            elif "touch .env" in prompt or "create an empty .env" in prompt:
                if "powershell" in prompt.lower() or "windows" in prompt.lower():
                    content = '{"command": "New-Item -ItemType File -Force -Path \'.env\'", "targets": [".env"], "clarifying_question": null}'
                else:
                    content = '{"command": "touch .env", "targets": [".env"], "clarifying_question": null}'
            elif "release_build" in prompt or "artifacts" in prompt:
                content = '{"command": "mkdir -p artifacts", "targets": ["artifacts"], "clarifying_question": null}'
            elif "Remove-Item" in prompt or "delete all log" in prompt or "remove all log" in prompt:
                content = '{"command": "Remove-Item *.log", "targets": ["*.log"], "clarifying_question": null}'
            elif "package.json" in prompt:
                if "powershell" in prompt.lower() or "windows" in prompt.lower():
                    content = '{"command": "Get-Content package.json", "targets": ["package.json"], "clarifying_question": null}'
                else:
                    content = '{"command": "cat package.json", "targets": ["package.json"], "clarifying_question": null}'
            elif "access.log" in prompt:
                content = '{"command": "head -n 5 access.log", "targets": ["access.log"], "clarifying_question": null}'
            elif "old_name.txt" in prompt:
                content = '{"command": "mv old_name.txt new_name.txt", "targets": ["old_name.txt"], "clarifying_question": null}'
            elif "legacy.txt" in prompt:
                content = '{"command": "Move-Item -Path legacy.txt -Destination current.txt", "targets": ["legacy.txt"], "clarifying_question": null}'
            else:
                if "powershell" in prompt.lower() or "windows" in prompt.lower():
                    content = '{"command": "Get-ChildItem", "targets": ["."], "clarifying_question": null}'
                else:
                    content = '{"command": "ls -la", "targets": ["."], "clarifying_question": null}'
        else:
            # Base model
            if self.prompt_mode == "full":
                if is_clarification:
                    content = '{"command": null, "targets": [], "clarifying_question": "Which target should be affected?"}'
                elif "remove all log" in prompt or "temp_*.log" in prompt:
                    content = '{"command": "rm -f temp_*.log", "targets": ["temp_*.log"], "clarifying_question": null}'
                elif "package.json" in prompt:
                    content = '{"command": "cat package.json", "targets": ["package.json"], "clarifying_question": null}'
                elif "artifacts" in prompt or "build/dist" in prompt:
                    content = '{"command": "mkdir -p build/dist", "targets": ["build/dist"], "clarifying_question": null}'
                else:
                    content = '{"command": "ls", "targets": ["."], "clarifying_question": null}'
            else:
                # Base model with Short / Zero-shot prompt degrades heavily
                if self.call_count % 3 == 0:
                    content = 'Sure, here is the command: `rm -rf /` to remove everything'
                elif self.call_count % 3 == 1:
                    content = '{"command": "echo done", "targets": []}'
                else:
                    content = '{"command": "ls", "targets": []}'

        output_tokens = estimate_token_count(content)

        return CompletionResult(
            content=content,
            timings={
                "prompt_n": prompt_tokens,
                "predicted_n": output_tokens,
                "prompt_ms": prefill_ms,
                "predicted_ms": decode_ms,
            },
            wall_time_s=(prefill_ms + decode_ms) / 1000.0,
            status="success",
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            prefill_ms=prefill_ms,
            decode_ms=decode_ms,
        )


# -----------------------------------------------------------------------------
# 2x2 Matrix Evaluation Core
# -----------------------------------------------------------------------------

@dataclass
class ConditionSummary:
    """Aggregated evaluation metrics for a single (model, prompt_mode) cell."""

    model: str
    prompt_mode: str
    total_cases: int
    execution_successes: int
    accuracy_pct: float
    accuracy_interval: tuple[float, float]
    json_valid_count: int
    json_valid_pct: float
    json_valid_interval: tuple[float, float]
    clarification_successes: int
    clarification_total: int
    clarification_acc_pct: float
    mean_prompt_tokens: float
    mean_output_tokens: float
    mean_prefill_ms: float
    mean_decode_ms: float
    total_wall_time_s: float
    case_success_vector: list[bool] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        del d["case_success_vector"]
        return d


def evaluate_single_case(
    case_spec: dict[str, Any],
    backend: Any,
    prompt_mode: str = "full",
) -> dict[str, Any]:
    """
    Evaluate one test case under the given backend and prompt mode.
    """
    case_id = case_spec["case_id"]
    family = case_spec.get("family", "unknown")
    shell = case_spec.get("shell", "auto")
    request = case_spec["request"]
    reference_command = case_spec.get("reference_command")
    is_clarification = case_spec.get("is_clarification", False)
    is_read_only = case_spec.get("is_read_only", False)
    fixture_spec = case_spec.get("fixture_spec", {})

    # Construct prompt
    if prompt_mode == "full":
        l0 = L0_SYSTEM_PROMPT
    elif prompt_mode == "zero_shot":
        l0 = ZERO_SHOT_L0_PROMPT
    else:
        l0 = SHORT_L0_PROMPT

    l1 = case_spec.get("env", {"shell": shell})
    prompt_parts = build_prompt(l0=l0, l1=l1, state="", request=request, reasoning=False, mode="full" if prompt_mode == "full" else "short")

    t0 = time.perf_counter()
    resp = backend.complete(prompt=prompt_parts.full_text, temperature=0.0)
    wall_time_s = time.perf_counter() - t0

    # Parse response
    json_valid, cand_command, targets, clarifying_q, parse_err = parse_model_response(resp.content)

    execution_success = False
    diff_match = False
    stdout_match = False
    eval_reason = ""

    if not json_valid:
        execution_success = False
        eval_reason = f"Invalid JSON response: {parse_err}"
    elif is_clarification:
        if clarifying_q and not cand_command:
            execution_success = True
            eval_reason = f"Correctly requested clarification: '{clarifying_q}'"
        else:
            execution_success = False
            eval_reason = f"Failed clarification: Emitted command '{cand_command}' without clarifying"
    else:
        if not cand_command:
            execution_success = False
            eval_reason = "Model returned empty command string"
        else:
            test_case = TestCase(
                case_id=case_id,
                request=request,
                reference_command=reference_command or "echo ok",
                fixture_spec=fixture_spec,
                is_read_only=is_read_only,
                shell=shell,
            )
            eval_res = evaluate_case(test_case, candidate_command=cand_command)
            execution_success = eval_res.success
            diff_match = eval_res.diff_match
            stdout_match = eval_res.stdout_match
            eval_reason = eval_res.reason

    return {
        "case_id": case_id,
        "family": family,
        "shell": shell,
        "request": request,
        "reference_command": reference_command,
        "is_clarification": is_clarification,
        "is_read_only": is_read_only,
        "prompt_mode": prompt_mode,
        "candidate_raw": resp.content,
        "candidate_command": cand_command,
        "clarifying_question": clarifying_q,
        "json_valid": json_valid,
        "execution_success": execution_success,
        "diff_match": diff_match,
        "stdout_match": stdout_match,
        "reason": eval_reason,
        "prompt_tokens": resp.prompt_tokens,
        "output_tokens": resp.output_tokens,
        "prefill_ms": resp.prefill_ms,
        "decode_ms": resp.decode_ms,
        "wall_time_s": wall_time_s,
    }


def run_condition_evaluation(
    model_name: str,
    prompt_mode: str,
    test_cases: Sequence[dict[str, Any]],
    backend: Any,
    provenance_meta: dict[str, Any],
) -> tuple[ConditionSummary, list[dict[str, Any]]]:
    """Run full evaluation for one cell of the 2x2 matrix."""
    case_records: list[dict[str, Any]] = []
    success_vector: list[bool] = []

    json_valid_count = 0
    clarification_successes = 0
    clarification_total = 0

    total_prompt_tokens = 0
    total_output_tokens = 0
    total_prefill_ms = 0.0
    total_decode_ms = 0.0
    t_start = time.perf_counter()

    for idx, case_spec in enumerate(test_cases, 1):
        res = evaluate_single_case(case_spec, backend, prompt_mode=prompt_mode)
        res["model"] = model_name
        res.update(provenance_meta)
        case_records.append(res)

        success = res["execution_success"]
        success_vector.append(success)

        if res["json_valid"]:
            json_valid_count += 1
        if case_spec.get("is_clarification", False):
            clarification_total += 1
            if success:
                clarification_successes += 1

        total_prompt_tokens += res["prompt_tokens"]
        total_output_tokens += res["output_tokens"]
        total_prefill_ms += res["prefill_ms"]
        total_decode_ms += res["decode_ms"]

    wall_time = time.perf_counter() - t_start
    n = len(test_cases)
    succ = sum(success_vector)

    acc_point, acc_low, acc_high = calculate_wilson_interval(succ, n)
    jv_point, jv_low, jv_high = calculate_wilson_interval(json_valid_count, n)
    clar_acc = round((clarification_successes / clarification_total * 100.0), 2) if clarification_total > 0 else 0.0

    summary = ConditionSummary(
        model=model_name,
        prompt_mode=prompt_mode,
        total_cases=n,
        execution_successes=succ,
        accuracy_pct=acc_point,
        accuracy_interval=(acc_low, acc_high),
        json_valid_count=json_valid_count,
        json_valid_pct=jv_point,
        json_valid_interval=(jv_low, jv_high),
        clarification_successes=clarification_successes,
        clarification_total=clarification_total,
        clarification_acc_pct=clar_acc,
        mean_prompt_tokens=round(total_prompt_tokens / n, 1) if n > 0 else 0.0,
        mean_output_tokens=round(total_output_tokens / n, 1) if n > 0 else 0.0,
        mean_prefill_ms=round(total_prefill_ms / n, 2) if n > 0 else 0.0,
        mean_decode_ms=round(total_decode_ms / n, 2) if n > 0 else 0.0,
        total_wall_time_s=round(wall_time, 2),
        case_success_vector=success_vector,
    )

    return summary, case_records


def generate_e5_markdown_report(
    summaries: Mapping[str, ConditionSummary],
    mcnemar_results: Mapping[str, dict[str, Any]],
    paired_diff_results: Mapping[str, tuple[float, float, float]],
    provenance_meta: dict[str, Any],
    split_name: str = "dev",
) -> str:
    """
    Format academic evaluation markdown report (Table 8.2 & paired comparisons).
    """
    lines = [
        f"# Experiment E5: Live Nemotron-3-Nano Benchmark Evaluation ({split_name.upper()} split)",
        "",
        "## Executive Summary & Headline Results",
        "",
        f"Evaluates the live `Nemotron-3-Nano-4B` model across a rigorous paired factorial matrix on the **{split_name}** benchmark split.",
        "Strictly tests whether instruction fine-tuning preserves execution accuracy, JSON validity, and clarification adherence",
        "when system prompt context is reduced from full (~450 tokens) to short (<= 100 tokens), tested against baseline/zero-shot prompts.",
        "",
        f"- **Benchmark Split**: `{split_name}`",
        f"- **Evaluated Test Cases**: `{next(iter(summaries.values())).total_cases if summaries else 0}`",
        f"- **Backend Server**: `{provenance_meta.get('backend')}`",
        f"- **Model Under Test**: `{provenance_meta.get('model_file', 'Nemotron-3-Nano-4B')}`",
        "",
        "### Table 8.2: 2x2 Factorial Performance Matrix",
        "",
        "| Model | Prompt Mode | Execution Accuracy (%) [95% Wilson CI] | JSON Validity (%) | Clarification Acc (%) | Mean Prompt Tokens | Mean TTFT (ms) | Mean Decode (ms) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for key in ("base_full", "base_short", "tuned_full", "tuned_short"):
        s = summaries.get(key)
        if not s:
            continue
        acc_str = f"**{s.accuracy_pct:.1f}%** [{s.accuracy_interval[0]:.1f}%, {s.accuracy_interval[1]:.1f}%]"
        jv_str = f"{s.json_valid_pct:.1f}%"
        clar_str = f"{s.clarification_acc_pct:.1f}%"
        lines.append(
            f"| **{s.model.capitalize()}** | `{s.prompt_mode}` | {acc_str} | {jv_str} | {clar_str} | {s.mean_prompt_tokens:.0f} t | {s.mean_prefill_ms:.1f} ms | {s.mean_decode_ms:.1f} ms |"
        )

    lines.extend([
        "",
        "### Statistical Significance (McNemar's Paired Tests)",
        "",
        "Paired comparisons evaluate discordant outcomes and paired difference confidence intervals on identical benchmark queries:",
        "",
        "| Comparison Pair | Condition A Pass / B Fail (b) | Condition A Fail / B Pass (c) | Paired Accuracy Diff (Δ) [95% CI] | Chi-Square (χ²) / Test Stat | p-value | Statistically Significant (p < 0.05) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for comp_name, res in mcnemar_results.items():
        sig_str = "**YES** (p < 0.05)" if res["significant_at_05"] else "No"
        p_str = f"{res['p_value']:.4e}" if res["p_value"] < 0.001 else f"{res['p_value']:.4f}"
        diff_tuple = paired_diff_results.get(comp_name, (0.0, 0.0, 0.0))
        diff_str = f"**{diff_tuple[0]:+.1f}%** [{diff_tuple[1]:+.1f}%, {diff_tuple[2]:+.1f}%]"
        lines.append(
            f"| `{comp_name}` | {res['b']} | {res['c']} | {diff_str} | {res['chi2']:.2f} | {p_str} | {sig_str} |"
        )

    # Prefill and Prompt Savings
    tf = summaries.get("tuned_full")
    ts = summaries.get("tuned_short")
    if tf and ts and tf.mean_prompt_tokens > 0:
        token_savings = ((tf.mean_prompt_tokens - ts.mean_prompt_tokens) / tf.mean_prompt_tokens) * 100.0
        ttft_speedup = (tf.mean_prefill_ms / ts.mean_prefill_ms) if ts.mean_prefill_ms > 0 else 1.0
        lines.extend([
            "",
            "### Efficiency & Latency Gains",
            f"- **Prompt Token Reduction**: **{token_savings:.1f}%** ({tf.mean_prompt_tokens:.0f} -> {ts.mean_prompt_tokens:.0f} tokens)",
            f"- **TTFT Prefill Speedup**: **{ttft_speedup:.2f}x** ({tf.mean_prefill_ms:.1f} ms -> {ts.mean_prefill_ms:.1f} ms)",
            f"- **Accuracy Preservation**: **{ts.accuracy_pct:.1f}%** (Tuned Short) vs **{tf.accuracy_pct:.1f}%** (Tuned Full)",
        ])

    lines.extend([
        "",
        "### Provenance & Environment",
        f"- **Run ID**: `{provenance_meta.get('run_id')}`",
        f"- **Git SHA**: `{provenance_meta.get('git_sha')}`",
        f"- **GPU / Hardware**: `{provenance_meta.get('gpu')}`",
        f"- **Driver / Server**: `{provenance_meta.get('driver')}` / `{provenance_meta.get('backend')}`",
        f"- **Model File**: `{provenance_meta.get('model_file')}`",
        f"- **Timestamp**: `{provenance_meta.get('timestamp')}`",
        f"- **Seed**: `{provenance_meta.get('seed')}`",
        "",
    ])

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Main Experiment Coordinator
# -----------------------------------------------------------------------------

def run_e5_experiment(
    test_file: str | None = None,
    output_jsonl: str | None = None,
    output_md: str | None = None,
    split: str = "dev",
    allow_mock: bool = False,
    count: int | None = None,
    base_backend: Any = None,
    tuned_backend: Any = None,
    seed: int = 42,
    url: str | None = None,
    base_url: str | None = None,
    tuned_url: str | None = None,
) -> tuple[dict[str, ConditionSummary], dict[str, dict[str, Any]]]:
    """
    Run full 2x2 matrix evaluation and write results with provenance.

    Parameters:
        test_file: Explicit dataset path, or None for split default.
        output_jsonl: Explicit output JSONL path, or None for split default.
        output_md: Explicit output Markdown report path, or None for split default.
        split: 'dev' (datasets/dev_v3.jsonl, N >= 100) or 'sealed' (datasets/sealed_test.jsonl).
        allow_mock: If False, strictly excludes mock backends and requires live server.
        count: Number of cases to evaluate (must be >= 100 for dev split).
        base_backend: Optional custom backend for base model.
        tuned_backend: Optional custom backend for tuned model.
        seed: Random seed for reproducibility.
        url: Shared URL for live model server.
        base_url: Optional URL for base model endpoint.
        tuned_url: Optional URL for tuned model endpoint.
    """
    random.seed(seed)

    # 1. Dataset Partition & Strict Dev Verification
    if test_file and "sealed" in test_file:
        split_norm = "sealed"
    else:
        split_norm = split.lower().strip()

    if split_norm == "dev":
        target_test_file = test_file or "datasets/dev_v3.jsonl"
        target_output_jsonl = output_jsonl or "results/e5_dev.jsonl"
        target_output_md = output_md or "results/e5_dev_summary.md"
        test_cases = load_dev_v3_dataset(target_test_file, enforce_min_n=not allow_mock)
        if count is not None:
            if not allow_mock and count < 100:
                raise ValueError(
                    f"Dev split evaluation strictly requires N >= 100 test cases, but count={count} was requested."
                )
            test_cases = test_cases[:count]
    elif split_norm == "sealed":
        target_test_file = test_file or "datasets/sealed_test.jsonl"
        target_output_jsonl = output_jsonl or "results/e5_matrix.jsonl"
        target_output_md = output_md or "results/e5_matrix.md"
        test_cases = load_or_create_sealed_test_set(target_test_file, count=count or 50)
        if count is not None and count < len(test_cases):
            test_cases = test_cases[:count]
    else:
        raise ValueError(f"Unknown split: '{split}'. Supported splits are 'dev' and 'sealed'.")

    # 2. Strict Mock Backend Enforcement
    if not allow_mock:
        # Check explicit custom backends
        for b in (base_backend, tuned_backend):
            if b is not None:
                b_name = b.__class__.__name__.lower()
                if "mock" in b_name or "demo" in b_name:
                    raise RuntimeError(
                        f"Mock backends are strictly excluded from live benchmark runs (got {b.__class__.__name__}). "
                        "A live Nemotron-3-Nano model server is required."
                    )

        # Probe live server reachability
        live_endpoint = url or resolve_local_endpoint()
        try:
            import requests
            probe_url = (
                live_endpoint.replace("/completion", "/health")
                if "/completion" in live_endpoint
                else live_endpoint.replace("/chat/completions", "/models")
            )
            r = requests.get(probe_url, timeout=1.5)
            if r.status_code not in (200, 404):
                raise ConnectionError(f"Server at {live_endpoint} returned HTTP status {r.status_code}")
        except Exception as exc:
            raise ConnectionError(
                f"Failed to connect to live model server at {live_endpoint}: {exc}. "
                "Mock backends are strictly excluded from live benchmark runs. "
                "Please start llama-server or LM Studio with Nemotron-3-Nano on 127.0.0.1:8080 or 127.0.0.1:1234, or specify --url."
            ) from exc

        backend_label = "llama-server"
        base_backend = base_backend or LlamaServerBackend(endpoint_url=base_url or live_endpoint)
        tuned_backend = tuned_backend or LlamaServerBackend(endpoint_url=tuned_url or live_endpoint)
    else:
        if base_backend is not None and isinstance(base_backend, LlamaServerBackend):
            backend_label = getattr(base_backend, "backend_name", "llama-server")
        elif base_backend is not None:
            backend_label = getattr(base_backend, "backend_name", getattr(base_backend, "name", "mock"))
        else:
            backend_label = "mock"

    # 3. Collect Full Provenance
    prov = collect(backend=backend_label, seed=seed)

    # 4. Initialize Backends
    if allow_mock and (base_backend is None or tuned_backend is None):
        print(f"[MOCK MODE] Running 2x2 matrix with MockLlamaBackend on {split_norm} split...")
        backends = {
            "base_full": MockLlamaBackend("base", "full"),
            "base_short": MockLlamaBackend("base", "short"),
            "tuned_full": MockLlamaBackend("tuned", "full"),
            "tuned_short": MockLlamaBackend("tuned", "short"),
        }
    else:
        backends = {
            "base_full": base_backend,
            "base_short": base_backend,
            "tuned_full": tuned_backend,
            "tuned_short": tuned_backend,
        }

    summaries: dict[str, ConditionSummary] = {}
    all_case_records: list[dict[str, Any]] = []

    cells = [
        ("base_full", "base", "full"),
        ("base_short", "base", "short"),
        ("tuned_full", "tuned", "full"),
        ("tuned_short", "tuned", "short"),
    ]

    for key, model_name, pmode in cells:
        print(f"Evaluating Condition: {model_name.upper()} + {pmode.upper()} ({len(test_cases)} cases)...")
        backend = backends[key]
        summary, records = run_condition_evaluation(
            model_name=model_name,
            prompt_mode=pmode,
            test_cases=test_cases,
            backend=backend,
            provenance_meta=prov,
        )
        summaries[key] = summary
        all_case_records.extend(records)
        print(
            f"  -> Accuracy: {summary.accuracy_pct:.1f}% [CI: {summary.accuracy_interval[0]:.1f}%, {summary.accuracy_interval[1]:.1f}%] | TTFT: {summary.mean_prefill_ms:.1f}ms"
        )

    # 5. Compute McNemar's Paired Tests & Paired Difference Intervals
    mcnemar_pairs = {
        "Tuned_Short_vs_Base_Short": calculate_mcnemar_test(
            summaries["tuned_short"].case_success_vector,
            summaries["base_short"].case_success_vector,
        ),
        "Tuned_Short_vs_Base_Full": calculate_mcnemar_test(
            summaries["tuned_short"].case_success_vector,
            summaries["base_full"].case_success_vector,
        ),
        "Tuned_Short_vs_Tuned_Full": calculate_mcnemar_test(
            summaries["tuned_short"].case_success_vector,
            summaries["tuned_full"].case_success_vector,
        ),
        "Base_Full_vs_Base_Short": calculate_mcnemar_test(
            summaries["base_full"].case_success_vector,
            summaries["base_short"].case_success_vector,
        ),
    }

    paired_diff_pairs = {
        "Tuned_Short_vs_Base_Short": calculate_paired_difference(
            summaries["tuned_short"].case_success_vector,
            summaries["base_short"].case_success_vector,
        ),
        "Tuned_Short_vs_Base_Full": calculate_paired_difference(
            summaries["tuned_short"].case_success_vector,
            summaries["base_full"].case_success_vector,
        ),
        "Tuned_Short_vs_Tuned_Full": calculate_paired_difference(
            summaries["tuned_short"].case_success_vector,
            summaries["tuned_full"].case_success_vector,
        ),
        "Base_Full_vs_Base_Short": calculate_paired_difference(
            summaries["base_full"].case_success_vector,
            summaries["base_short"].case_success_vector,
        ),
    }

    # 6. Write Output JSONL Records
    os.makedirs(os.path.dirname(os.path.abspath(target_output_jsonl)), exist_ok=True)
    with open(target_output_jsonl, "w", encoding="utf-8") as f:
        for rec in all_case_records:
            missing_prov = validate_provenance(rec)
            if missing_prov:
                raise ValueError(f"Record {rec.get('case_id')} missing required provenance fields: {missing_prov}")
            f.write(_json.dumps(rec) + "\n")
    print(f"\nWritten {len(all_case_records)} per-case evaluation records to: {target_output_jsonl}")

    # 7. Generate Markdown Summary Report
    os.makedirs(os.path.dirname(os.path.abspath(target_output_md)), exist_ok=True)
    report_md = generate_e5_markdown_report(summaries, mcnemar_pairs, paired_diff_pairs, prov, split_name=split_norm)
    with open(target_output_md, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"Summary Markdown report written to: {target_output_md}")

    return summaries, mcnemar_pairs


def main():
    parser = argparse.ArgumentParser(description="Run Experiment E5: 2x2 Factorial Evaluation Matrix.")
    parser.add_argument("--split", choices=["dev", "sealed"], default="dev", help="Dataset split ('dev' or 'sealed')")
    parser.add_argument("--test-file", default=None, help="Explicit path to test cases JSONL")
    parser.add_argument("--output-jsonl", default=None, help="Output JSONL records path")
    parser.add_argument("--output-md", default=None, help="Output Markdown report path")
    parser.add_argument("--count", "--n", type=int, default=None, help="Number of benchmark test cases to evaluate")
    parser.add_argument("--url", default=None, help="Shared HTTP URL for live model server")
    parser.add_argument("--base-url", default=None, help="HTTP URL for base llama-server endpoint")
    parser.add_argument("--tuned-url", default=None, help="HTTP URL for tuned llama-server endpoint")
    parser.add_argument("--allow-mock", action="store_true", default=False, help="Allow mock backend (synthetic testing only)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    run_e5_experiment(
        test_file=args.test_file,
        output_jsonl=args.output_jsonl,
        output_md=args.output_md,
        split=args.split,
        allow_mock=args.allow_mock,
        count=args.count,
        url=args.url,
        base_url=args.base_url,
        tuned_url=args.tuned_url,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
