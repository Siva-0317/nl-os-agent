# safety_verifier.py
"""
Safety Verifier for NL OS Agent.
Classifies command risk tiers, simulates destructive commands in a Docker sandbox
(with intelligent fallback simulation), and enforces user confirmation.
"""

import os
import shlex
import re
from enum import Enum
from rich.console import Console
from rich.panel import Panel

console = Console()


class RiskTier(Enum):
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
    "uname", "date", "uptime", "history", "env", "printenv"
}

# Commands that are inherently destructive or terminate processes
DESTRUCTIVE_CMDS = {
    "rm", "rmdir", "kill", "pkill", "killall", "dd",
    "mkfs", "fdisk", "parted", "shred", "truncate",
    "overwrite", "wipe"
}

# Dangerous flags or parameters that escalate a command's risk tier
DESTRUCTIVE_FLAGS = {"-rf", "-fr", "-r", "--recursive", "-9", "--force", "-delete"}

# Commands that modify state (create, move, edit, install) but are typically non-destructive
MODIFYING_CMDS = {
    "mkdir", "touch", "cp", "mv", "ln", "chmod", "chown",
    "chgrp", "tar", "zip", "unzip", "pip", "apt", "npm",
    "git", "wget", "curl", "scp", "rsync", "systemctl",
    "service", "crontab"
}


def _split_pipeline(command: str) -> list[str]:
    """Split composite command into individual pipe or sequence stages."""
    # Split by pipes and sequence operators
    parts = re.split(r"\||&&|;|\|\|", command)
    return [p.strip() for p in parts if p.strip()]


def classify_single_command(cmd_str: str) -> RiskTier:
    """Classify the risk tier of an individual command invocation."""
    try:
        parts = shlex.split(cmd_str.strip())
    except Exception:
        parts = cmd_str.strip().split()

    if not parts:
        return RiskTier.AMBIGUOUS

    base = parts[0].split("/")[-1].split("\\")[-1]  # normalize paths e.g. /bin/rm -> rm
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
    Classify overall command risk tier.
    Evaluates individual pipeline stages and dangerous shell constructs.
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

    # If any stage is destructive, the whole command is destructive
    if RiskTier.DESTRUCTIVE in stage_tiers:
        return RiskTier.DESTRUCTIVE

    # If any stage is ambiguous, escalate to ambiguous
    if RiskTier.AMBIGUOUS in stage_tiers:
        return RiskTier.AMBIGUOUS

    # If any stage modifies state, whole command is state modifying
    if RiskTier.STATE_MODIFYING in stage_tiers:
        return RiskTier.STATE_MODIFYING

    # All stages read-only
    if all(t == RiskTier.READ_ONLY for t in stage_tiers):
        return RiskTier.READ_ONLY

    return RiskTier.AMBIGUOUS


def sandbox_simulate(command: str, cwd: str) -> dict:
    """
    Simulate command in an isolated Docker container if available,
    or provide an intelligent impact analysis fallback.
    """
    try:
        import docker
        client = docker.from_env()
        # Verify Docker daemon is alive
        client.ping()

        # Run inside isolated container with cwd mounted read-only
        container_output = client.containers.run(
            image="alpine:latest",
            command=f"sh -c 'cd /sandbox && {command} 2>&1'",
            volumes={os.path.abspath(cwd): {"bind": "/sandbox", "mode": "ro"}},
            remove=True,
            stdout=True,
            stderr=True,
            mem_limit="64m",
            network_disabled=True
        )

        output = container_output.decode("utf-8", errors="replace").strip()
        return {
            "success": True,
            "simulated_output": output[:500],
            "summary": f"Docker simulation executed successfully.\nSandbox output:\n{output[:250]}"
        }
    except Exception as e:
        # Fallback to intelligent impact analysis
        sim_summary = _generate_dry_run_summary(command, cwd)
        return {
            "success": False,
            "summary": f"[Simulated Effect (Sandbox Analysis)]\n{sim_summary}"
        }


def _generate_dry_run_summary(command: str, cwd: str) -> str:
    """Produce human-readable impact explanation for commands when Docker is offline."""
    parts = command.strip().split()
    base = parts[0].split("/")[-1].split("\\")[-1] if parts else ""

    if base in ("rm", "rmdir") or "find" in command and "-delete" in command:
        targets = [p for p in parts[1:] if not p.startswith("-")]
        target_str = ", ".join(targets) if targets else "specified target"
        return (
            f"Action: PERMANENT FILE DELETION\n"
            f"Targets: {target_str}\n"
            f"Impact: Files and subdirectories will be unlinked and permanently deleted from disk."
        )
    elif base in ("kill", "pkill", "killall"):
        pids = [p for p in parts[1:] if not p.startswith("-")]
        return (
            f"Action: PROCESS TERMINATION\n"
            f"Targets: {', '.join(pids) if pids else 'active process'}\n"
            f"Impact: Will send kill signal to terminate running processes immediately."
        )
    elif base in ("dd", "mkfs", "fdisk", "parted", "shred"):
        return (
            f"Action: DISK / BLOCK LEVEL MODIFICATION\n"
            f"Impact: Low-level data write or partition modification. High potential for severe data loss."
        )
    else:
        tier = classify_risk(command).value
        return (
            f"Action: {tier.upper()} OPERATION\n"
            f"Command: {command}\n"
            f"Impact: State changes or side-effects will occur in directory: {cwd}"
        )


def verify_and_confirm(command: str, cwd: str) -> bool:
    """
    Full safety verification pipeline.
    Auto-executes safe commands; prompts user confirmation for destructive ones.
    """
    tier = classify_risk(command)

    if tier == RiskTier.READ_ONLY:
        console.print(f"[dim]✓ Read-only command — auto-executing[/dim]")
        return True

    if tier == RiskTier.STATE_MODIFYING:
        console.print(f"[yellow]⚠ State-modifying command — logging and executing[/yellow]")
        return True

    if tier in (RiskTier.DESTRUCTIVE, RiskTier.AMBIGUOUS):
        console.print(Panel(
            f"[bold red]⚠ DESTRUCTIVE / AMBIGUOUS COMMAND DETECTED[/bold red]\n"
            f"Command:   [cyan]{command}[/cyan]\n"
            f"Risk Tier: [red]{tier.value.upper()}[/red]",
            title="Safety Verification",
            border_style="red"
        ))

        # Run sandbox simulation or impact preview
        console.print("[yellow]Running sandbox simulation...[/yellow]")
        result = sandbox_simulate(command, cwd)
        console.print(Panel(
            result["summary"],
            title="Simulated Effect (Sandbox)",
            border_style="yellow"
        ))

        confirm = console.input(
            "[bold red]This action may be irreversible. Confirm execution? (y/N): [/bold red]"
        ).strip().lower()

        return confirm in ("y", "yes")

    return False


if __name__ == "__main__":
    test_commands = [
        ("ls -la", RiskTier.READ_ONLY),
        ("mkdir backup", RiskTier.STATE_MODIFYING),
        ("rm -rf /tmp/test", RiskTier.DESTRUCTIVE),
        ("find /tmp -delete", RiskTier.DESTRUCTIVE),
        ("echo 'rm -rf /' | bash", RiskTier.DESTRUCTIVE),
        ("cat file.txt | grep error", RiskTier.READ_ONLY),
        ("mv old.txt new.txt", RiskTier.STATE_MODIFYING),
    ]

    print("Risk Classification Tests:")
    print("-" * 50)
    all_pass = True
    for cmd, expected in test_commands:
        result = classify_risk(cmd)
        status = "✓" if result == expected else "✗"
        if result != expected:
            all_pass = False
        print(f"{status} [{result.value:20s}] {cmd}")

    print(f"\n{'All tests passed!' if all_pass else 'Some tests failed!'}")
