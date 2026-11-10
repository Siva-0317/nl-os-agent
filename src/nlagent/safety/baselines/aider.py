"""
Baseline: Aider Interactive Command Confirmation & Safe Execution Guard.

Repository: https://github.com/paul-gauthier/aider
File Path: aider/run_cmd.py
Commit: 24b5d2146e5b5c90d0b005e83ec1bb2df6a09618 (v0.50.0 release)
License: Apache-2.0 License

Differences from Original:
- In Aider's interactive CLI loop, commands requested by the LLM or user are prompted
  via `confirm_ask` before running, unless they match safe developer commands (git queries,
  test suites, linters, non-destructive read operations) or the user specifies `--yes`.
- Here, we reimplement Aider's allowlist-oriented confirmation policy as a deterministic
  evaluator returning 'auto_run' for recognized safe read/test/git inspection commands,
  and 'escalated' for dangerous mutations, deletions, or unverified arbitrary execution.
- Extended to support Windows equivalents (`dir`, `type`, `findstr`, `Get-ChildItem`).
"""

from __future__ import annotations

import re
import time
from nlagent.safety.baselines.autogpt import BaselineDecision

# Safe allowlisted prefixes in Aider-style development workflows
AIDER_SAFE_PREFIXES: tuple[str, ...] = (
    # Git read/inspection
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git tag",
    "git remote",
    "git config --get",
    "git rev-parse",
    # Test runners & linters
    "pytest",
    "python -m pytest",
    "python -m unittest",
    "py.test",
    "npm test",
    "npm run test",
    "cargo test",
    "go test",
    "ctest",
    "ruff",
    "flake8",
    "mypy",
    "pylint",
    "black --check",
    "eslint",
    "prettier --check",
    # POSIX read-only inspection
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "find . -name",
    "wc",
    "pwd",
    "echo",
    "uname",
    "which",
    "where",
    "env",
    # Windows read-only inspection
    "dir",
    "type",
    "findstr",
    "where.exe",
    "get-childitem",
    "get-content",
    "select-string",
    "get-location",
)

# Known destructive commands that must never be auto-run
AIDER_EXPLICIT_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\b", re.IGNORECASE),
    re.compile(r"\bdel\b", re.IGNORECASE),
    re.compile(r"\brd\b", re.IGNORECASE),
    re.compile(r"\bshred\b", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\brunas\b", re.IGNORECASE),
    re.compile(r"\bkill\b", re.IGNORECASE),
    re.compile(r"\btaskkill\b", re.IGNORECASE),
    re.compile(r"\bStop-Process\b", re.IGNORECASE),
    re.compile(r">\s*(?:/dev/|/etc/|C:\\Windows)", re.IGNORECASE),
]


def evaluate_aider_guard(command: str) -> BaselineDecision:
    """
    Evaluate command using Aider's developer allowlist and confirmation model.
    Returns BaselineDecision with action 'auto_run' or 'escalated'.
    """
    t0 = time.perf_counter()
    clean = command.strip()
    lower = clean.lower()

    # 1. Immediate rejection of explicitly destructive operations
    for pat in AIDER_EXPLICIT_DESTRUCTIVE_PATTERNS:
        if pat.search(clean):
            dt = (time.perf_counter() - t0) * 1000.0
            return BaselineDecision(
                baseline_id="aider",
                action="escalated",
                tier="destructive",
                reason=f"Matches Aider high-risk destructive pattern: {pat.pattern}",
                latency_ms=dt,
            )

    # 2. Check compound / chained commands
    # Chained commands like 'pytest; rm test.txt' must not be auto-run just because the first token is safe.
    sub_commands = [s.strip() for s in re.split(r"[;&|\n]+", clean) if s.strip()]
    if not sub_commands:
        dt = (time.perf_counter() - t0) * 1000.0
        return BaselineDecision(
            baseline_id="aider",
            action="auto_run",
            tier="read_only",
            reason="Empty command",
            latency_ms=dt,
        )

    # Every sub-command in pipeline must match an allowlisted safe prefix
    all_safe = True
    violating_sub = ""
    for sub in sub_commands:
        sub_lower = sub.lower()
        matched = False
        for prefix in AIDER_SAFE_PREFIXES:
            if sub_lower == prefix or sub_lower.startswith(prefix + " ") or sub_lower.startswith(prefix + "\t"):
                matched = True
                break
        if not matched:
            all_safe = False
            violating_sub = sub
            break

    dt = (time.perf_counter() - t0) * 1000.0
    if all_safe:
        return BaselineDecision(
            baseline_id="aider",
            action="auto_run",
            tier="read_only",
            reason="Command in Aider safe development allowlist (git/test/inspection)",
            latency_ms=dt,
        )

    return BaselineDecision(
        baseline_id="aider",
        action="escalated",
        tier="state_modifying",
        reason=f"Command '{violating_sub}' requires user confirmation outside Aider safe allowlist",
        latency_ms=dt,
    )

