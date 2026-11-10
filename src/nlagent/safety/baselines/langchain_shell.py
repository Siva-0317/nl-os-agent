"""
Baseline: LangChain Shell Tool Disallowed Commands & Approval Guard.

Repository: https://github.com/langchain-ai/langchain
File Path: libs/community/langchain_community/tools/shell/tool.py
Commit: d9c9b563606f7bb8a8fa6a81bf6d7b420f4c0a51
License: MIT License

Differences from Original:
- In LangChain's `ShellTool` and `BashProcess`, execution allows configurable
  `disallowed_commands` and optional `ask_human_input`. Default and production
  deployments typically specify a denylist of dangerous verbs and privilege escalation.
- Here, we reimplement LangChain's shell validation logic as a deterministic evaluator
  that inspects pipeline tokens and compound command sub-expressions.
- Extended to support cross-shell Windows commands (cmd.exe `del`, `rd`, `format`,
  PowerShell `Remove-Item`, `Stop-Process`).
"""

from __future__ import annotations

import re
import time
from nlagent.safety.baselines.autogpt import BaselineDecision

# Standard disallowed command verbs in LangChain shell tooling
LANGCHAIN_DISALLOWED_COMMANDS: set[str] = {
    # File / storage destruction
    "rm",
    "rmdir",
    "del",
    "rd",
    "erase",
    "shred",
    "unlink",
    "truncate",
    "wipefs",
    "mkfs",
    "fdisk",
    "parted",
    "format",
    "diskpart",
    "dd",
    "remove-item",
    # Privilege escalation / administrative control
    "sudo",
    "su",
    "doas",
    "runas",
    "chmod",
    "chown",
    "chgrp",
    # System control
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "init",
    # Process termination
    "kill",
    "pkill",
    "killall",
    "taskkill",
    "stop-process",
}

# Dangerous patterns for command injection or pipe execution
LANGCHAIN_DANGEROUS_PATTERNS = [
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.IGNORECASE),  # fork bomb
    re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*", re.IGNORECASE),  # rm -r / rm -f / rm -rf
    re.compile(r"\bdel\s+/[a-zA-Z]*[sqf]", re.IGNORECASE),  # del /s /q /f
    re.compile(r"\brd\s+/[a-zA-Z]*[sq]", re.IGNORECASE),  # rd /s /q
    re.compile(r"\bRemove-Item\b.*-(?:Recurse|r|Force|f)\b", re.IGNORECASE),  # Remove-Item -Recurse
    re.compile(r"(?:\|\s*(?:bash|sh|powershell|cmd)|base64\s+-d\s*\|)", re.IGNORECASE),  # pipe to shell
    re.compile(r">\s*(?:/dev/|/etc/|C:\\Windows)", re.IGNORECASE),  # protected targets
]


def evaluate_langchain_guard(command: str) -> BaselineDecision:
    """
    Evaluate command using LangChain ShellTool disallowed command rules.
    Returns BaselineDecision with action 'escalated' or 'auto_run'.
    """
    t0 = time.perf_counter()
    clean = command.strip()
    lower = clean.lower()

    # 1. Regex pattern check for dangerous constructs
    for pat in LANGCHAIN_DANGEROUS_PATTERNS:
        if pat.search(clean):
            dt = (time.perf_counter() - t0) * 1000.0
            return BaselineDecision(
                baseline_id="langchain",
                action="escalated",
                tier="destructive",
                reason=f"Matches LangChain disallowed pattern: {pat.pattern}",
                latency_ms=dt,
            )

    # 2. Tokenize compound pipelines / command sequences
    # Splits on ;, &&, ||, |, &, \n
    sub_commands = re.split(r"[;&|\n]+", clean)
    for sub in sub_commands:
        sub_str = sub.strip()
        if not sub_str:
            continue
        # Split tokens
        tokens = sub_str.split()
        if not tokens:
            continue
        # Get lead token / verb
        verb_tok = tokens[0].strip("'\"")
        # Handle path prefix e.g. /bin/rm or C:\Windows\System32\taskkill.exe
        verb_base = verb_tok.replace("\\", "/").split("/")[-1]
        verb_base = re.sub(r"\.(exe|cmd|bat|ps1)$", "", verb_base, flags=re.IGNORECASE).lower()

        if verb_base in LANGCHAIN_DISALLOWED_COMMANDS:
            dt = (time.perf_counter() - t0) * 1000.0
            tier = "privileged" if verb_base in ("sudo", "su", "runas", "shutdown", "format") else "destructive"
            return BaselineDecision(
                baseline_id="langchain",
                action="escalated",
                tier=tier,
                reason=f"Matches LangChain disallowed command verb: {verb_base}",
                latency_ms=dt,
            )

        # Check any argument token that could be a disguised verb or sensitive keyword
        for tok in tokens[1:]:
            tok_clean = tok.strip("'\"")
            tok_base = tok_clean.replace("\\", "/").split("/")[-1].lower()
            if tok_base in {"sudo", "runas", "mkfs", "wipefs", "format", "diskpart"}:
                dt = (time.perf_counter() - t0) * 1000.0
                return BaselineDecision(
                    baseline_id="langchain",
                    action="escalated",
                    tier="privileged",
                    reason=f"Disallowed command token in arguments: {tok_base}",
                    latency_ms=dt,
                )

    dt = (time.perf_counter() - t0) * 1000.0
    return BaselineDecision(
        baseline_id="langchain",
        action="auto_run",
        tier="read_only",
        reason="Command passed LangChain shell tool disallowed command checks",
        latency_ms=dt,
    )

