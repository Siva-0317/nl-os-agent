"""
baseline_g1.py - Baseline G1: Heuristic / Regex name-based risk classifier.

Retained strictly as a labelled baseline (G1) for the safety benchmark
("DestructiveBench-mini") to compare against proposed effect-derived tiering (G3).
This module is NOT used in the agent's runtime decision path.
"""

from __future__ import annotations

import os
import re
import shlex
from enum import Enum


class RiskTier(Enum):
    """G1 Baseline Risk Tiers."""
    READ_ONLY = "read_only"
    STATE_MODIFYING = "state_modifying"
    DESTRUCTIVE = "destructive"
    AMBIGUOUS = "ambiguous"


# Commands that only read system state — never modify files or processes
READONLY_CMDS = {
    "ls", "cat", "head", "tail", "less", "more", "ps", "top",
    "htop", "df", "du", "pwd", "whoami", "which", "echo",
    "grep", "find", "locate", "wc", "sort", "uniq", "diff",
    "file", "stat", "lsof", "netstat", "ifconfig", "ip",
    "uname", "date", "uptime", "history", "env", "printenv",
}

# Commands that are inherently destructive or terminate processes
DESTRUCTIVE_CMDS = {
    "rm", "rmdir", "kill", "pkill", "killall", "dd",
    "mkfs", "fdisk", "parted", "shred", "truncate",
    "overwrite", "wipe",
}

# Dangerous flags or parameters that escalate a command's risk tier
DESTRUCTIVE_FLAGS = {"-rf", "-fr", "-r", "--recursive", "-9", "--force", "-delete"}

# Commands that modify state (create, move, edit, install) but are typically non-destructive
MODIFYING_CMDS = {
    "mkdir", "touch", "cp", "mv", "ln", "chmod", "chown",
    "chgrp", "tar", "zip", "unzip", "pip", "apt", "npm",
    "git", "wget", "curl", "scp", "rsync", "systemctl",
    "service", "crontab",
}


def _split_pipeline(command: str) -> list[str]:
    """Split composite command into individual pipe or sequence stages."""
    parts = re.split(r"\||&&|;|\|\|", command)
    return [p.strip() for p in parts if p.strip()]


def classify_single_command(cmd_str: str) -> RiskTier:
    """Classify the risk tier of an individual command invocation using G1 heuristics."""
    try:
        parts = shlex.split(cmd_str.strip())
    except Exception:
        parts = cmd_str.strip().split()

    if not parts:
        return RiskTier.AMBIGUOUS

    base = parts[0].split("/")[-1].split("\\")[-1]
    flags = set(p for p in parts[1:] if p.startswith("-"))

    has_redirect = any(c in cmd_str for c in [">", ">>"])

    # Inherently destructive command
    if base in DESTRUCTIVE_CMDS:
        return RiskTier.DESTRUCTIVE

    # Specific dangerous parameters, e.g. 'find ... -delete'
    if "-delete" in parts or any(p in DESTRUCTIVE_FLAGS for p in parts):
        return RiskTier.DESTRUCTIVE

    # Modifying commands with recursive/force destructive flags (e.g. cp -rf overwrites)
    if base in MODIFYING_CMDS and flags.intersection(DESTRUCTIVE_FLAGS):
        return RiskTier.DESTRUCTIVE

    # Read-only commands with redirects become state-modifying
    if base in READONLY_CMDS:
        return RiskTier.STATE_MODIFYING if has_redirect else RiskTier.READ_ONLY

    # State modifying command
    if base in MODIFYING_CMDS:
        return RiskTier.STATE_MODIFYING

    # Any redirect operator modifies file state
    if has_redirect:
        return RiskTier.STATE_MODIFYING

    return RiskTier.AMBIGUOUS


def classify_risk(command: str) -> RiskTier:
    """
    Baseline G1 Risk Classifier.

    NOTE: This is retained solely as a benchmark baseline (G1).
    The agent runtime uses static effect analysis (effects.analyse + tier_from_effect).
    """
    if not command or not command.strip():
        return RiskTier.AMBIGUOUS

    clean_cmd = command.strip()

    # Check for destructive commands embedded anywhere in pipes (e.g. echo "rm" | bash)
    if any(re.search(rf"\b{d}\b", clean_cmd) for d in DESTRUCTIVE_CMDS):
        return RiskTier.DESTRUCTIVE

    # Check for find ... -delete
    if "find" in clean_cmd and "-delete" in clean_cmd:
        return RiskTier.DESTRUCTIVE

    stages = _split_pipeline(clean_cmd)
    stage_tiers = [classify_single_command(s) for s in stages]

    if RiskTier.DESTRUCTIVE in stage_tiers:
        return RiskTier.DESTRUCTIVE

    if RiskTier.AMBIGUOUS in stage_tiers:
        return RiskTier.AMBIGUOUS

    if RiskTier.STATE_MODIFYING in stage_tiers:
        return RiskTier.STATE_MODIFYING

    if all(t == RiskTier.READ_ONLY for t in stage_tiers):
        return RiskTier.READ_ONLY

    return RiskTier.AMBIGUOUS
