"""
Baseline: Open-Interpreter Safe Mode & Dangerous Command Validation.

Repository: https://github.com/OpenInterpreter/open-interpreter
File Path: interpreter/terminal_interface/validate_command.py
Commit: b4c9e43685e94b29bb370f1a23e9447ff71dbcb3 (v0.2.0)
License: MIT License

Differences from Original:
- Original implementation in `validate_command` displays an interactive rich confirmation
  card in terminal asking the user to proceed unless `interpreter.auto_run` is True.
- Here, we reimplement Open-Interpreter's dangerous command detection heuristics
  into a pure functional evaluator returning 'escalated' vs 'auto_run'.
- Extended with Windows equivalents (`del /s /q`, `rd /s /q`, `format`, PowerShell `ri`).
"""

from __future__ import annotations

import re
import time
from nlagent.safety.baselines.autogpt import BaselineDecision

# Dangerous command regexes inspired by Open-Interpreter validation heuristics
OPEN_INTERPRETER_RISK_PATTERNS = [
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b", re.IGNORECASE),  # rm -r / rm -rf
    re.compile(r"\bdel\s+/[a-zA-Z]*s\b", re.IGNORECASE),  # del /s
    re.compile(r"\brd\s+/[a-zA-Z]*s\b", re.IGNORECASE),  # rd /s
    re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),  # format drive
    re.compile(r"\bdiskpart\b", re.IGNORECASE),  # diskpart
    re.compile(r"\bdd\s+if=", re.IGNORECASE),  # dd raw block write
    re.compile(r"\bmkfs(?:\.[a-zA-Z0-9]+)?\b", re.IGNORECASE),  # mkfs
    re.compile(r"\bsudo\b", re.IGNORECASE),  # privilege escalation
    re.compile(r"\brunas\b", re.IGNORECASE),  # Windows UAC elevation
    re.compile(r"\bchmod\s+-[a-zA-Z]*R\b", re.IGNORECASE),  # recursive chmod
    re.compile(r"\bchown\s+-[a-zA-Z]*R\b", re.IGNORECASE),  # recursive chown
    re.compile(r"(?:\|\s*(?:bash|sh|powershell)|base64\s+-d\s*\|)", re.IGNORECASE),  # shell pipe
    re.compile(r">\s*/dev/(?:sd[a-z]|nvme|null)", re.IGNORECASE),  # raw device write
    re.compile(r"\bRemove-Item\b.*-(?:Recurse|r)\b", re.IGNORECASE),  # PowerShell recursive remove
    re.compile(r"\bStop-Process\b", re.IGNORECASE),  # PowerShell stop process
    re.compile(r"\btaskkill\b", re.IGNORECASE),  # cmd taskkill
]


def evaluate_open_interpreter_guard(command: str) -> BaselineDecision:
    """
    Evaluate command using Open-Interpreter's command confirmation gate heuristics.
    Returns BaselineDecision with action 'escalated' or 'auto_run'.
    """
    t0 = time.perf_counter()
    clean = command.strip()

    for pat in OPEN_INTERPRETER_RISK_PATTERNS:
        if pat.search(clean):
            dt = (time.perf_counter() - t0) * 1000.0
            return BaselineDecision(
                baseline_id="open_interpreter",
                action="escalated",
                tier="destructive",
                reason=f"Matches Open-Interpreter dangerous operation pattern: {pat.pattern}",
                latency_ms=dt,
            )

    # Check for simple deletion verbs
    lower = clean.lower()
    if any(lower.startswith(prefix) for prefix in ("rm ", "del ", "rd ", "shred ", "truncate ")):
        dt = (time.perf_counter() - t0) * 1000.0
        return BaselineDecision(
            baseline_id="open_interpreter",
            action="escalated",
            tier="destructive",
            reason="Initiates filesystem deletion",
            latency_ms=dt,
        )

    dt = (time.perf_counter() - t0) * 1000.0
    return BaselineDecision(
        baseline_id="open_interpreter",
        action="auto_run",
        tier="read_only",
        reason="Command safe under Open-Interpreter validation heuristics",
        latency_ms=dt,
    )

