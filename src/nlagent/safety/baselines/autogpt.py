"""
Baseline: AutoGPT Command Execution Denylist & Approval Guard.

Repository: https://github.com/Significant-Gravitas/AutoGPT
File Path: classic/original_autogpt/autogpt/commands/execute_code.py
Commit: 7a174f9d2a63ffb0c531d0eb2742dcfe54d193ea (v0.5.0 release)
License: MIT License

Differences from Original:
- The original AutoGPT implementation checks commands during plan execution in
  `execute_shell` and prompts the user via CLI input() unless continuous mode is active.
- Here, we reimplement the static disallowed commands rules and dangerous shell token
  patterns as a deterministic classifier returning 'escalated' vs 'auto_run'.
- Extended to support cross-shell equivalents (cmd.exe `del`, `rd`, PowerShell `Remove-Item`).
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass


@dataclass
class BaselineDecision:
    baseline_id: str
    action: str  # "auto_run" | "escalated" | "hard_blocked"
    tier: str  # "read_only" | "state_modifying" | "destructive" | "privileged" | "ambiguous"
    reason: str
    latency_ms: float = 0.0


# AutoGPT command denylist and dangerous patterns
AUTOGPT_DENYLIST_KEYWORDS: set[str] = {
    "shutdown",
    "reboot",
    "init",
    "poweroff",
    "halt",
    "mkfs",
    "fdisk",
    "parted",
    "format",
    "dd",
    "wipefs",
    "shred",
    "sudo",
    "su",
    "doas",
    "runas",
}

# Dangerous regex patterns from AutoGPT code execution guards
AUTOGPT_DANGEROUS_PATTERNS = [
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.IGNORECASE),  # fork bomb
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b", re.IGNORECASE),  # rm -rf
    re.compile(r"\bdel\s+/[a-zA-Z]*s\b", re.IGNORECASE),  # del /s
    re.compile(r"\brd\s+/[a-zA-Z]*s\b", re.IGNORECASE),  # rd /s
    re.compile(r"\bRemove-Item\b.*-[rR]ecurse", re.IGNORECASE),  # Remove-Item -Recurse
    re.compile(r">\s*(?:/dev/|/etc/|C:\\Windows)", re.IGNORECASE),  # protected write
    re.compile(r"(?:\|\s*(?:bash|sh|powershell)|base64\s+-d\s*\|)", re.IGNORECASE),  # pipe to shell
]


def evaluate_autogpt_guard(command: str) -> BaselineDecision:
    """
    Evaluate command using AutoGPT's disallowed command denylist logic.
    Returns BaselineDecision with action 'escalated' or 'auto_run'.
    """
    t0 = time.perf_counter()
    clean = command.strip()
    lower = clean.lower()

    # 1. Regex pattern check
    for pat in AUTOGPT_DANGEROUS_PATTERNS:
        match = pat.search(clean)
        if match:
            dt = (time.perf_counter() - t0) * 1000.0
            return BaselineDecision(
                baseline_id="autogpt",
                action="escalated",
                tier="destructive",
                reason=f"Matches AutoGPT dangerous pattern: {pat.pattern}",
                latency_ms=dt,
            )

    # 2. Tokenize and check denylist keywords
    tokens = re.split(r"[;&|\s]+", lower)
    for tok in tokens:
        clean_tok = tok.strip("'\"")
        # Strip path prefix e.g. /sbin/shutdown -> shutdown
        base = clean_tok.split("/")[-1].split("\\")[-1]
        if base in AUTOGPT_DENYLIST_KEYWORDS:
            dt = (time.perf_counter() - t0) * 1000.0
            tier = "privileged" if base in ("sudo", "su", "shutdown", "format") else "destructive"
            return BaselineDecision(
                baseline_id="autogpt",
                action="escalated",
                tier=tier,
                reason=f"Command matches AutoGPT denylist keyword: {base}",
                latency_ms=dt,
            )

    # 3. Check destructive file deletion verbs
    if any(lower.startswith(prefix) for prefix in ("rm ", "del ", "rd ", "remove-item ")):
        dt = (time.perf_counter() - t0) * 1000.0
        return BaselineDecision(
            baseline_id="autogpt",
            action="escalated",
            tier="destructive",
            reason="Command initiates file or directory deletion",
            latency_ms=dt,
        )

    dt = (time.perf_counter() - t0) * 1000.0
    return BaselineDecision(
        baseline_id="autogpt",
        action="auto_run",
        tier="read_only",
        reason="Command passed AutoGPT denylist checks",
        latency_ms=dt,
    )

