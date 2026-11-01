"""
commit.py - Safe Command Execution and State Commit Pipeline.

Before executing any proposed command, commit() re-runs effects.analyse
to protect against Time-Of-Check to Time-Of-Use (TOCTOU) mutations or filesystem
state changes that may have occurred since proposal time.

On successful execution (exit code 0):
- Updates SessionState with command effect records and newly created entities.
- Stores verified proposal in VerifiedCommandCache.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from typing import Any, Callable

from nlagent.cache.verified_cache import VerifiedCommandCache, make_env_key
from nlagent.context.state import SessionState
from nlagent.daemon.propose import get_default_env
from nlagent.safety.effects import Effect, analyse, preview, tier_from_effect


def get_preferred_shell() -> str | None:
    """Detect if bash (Git Bash on Windows or native bash) is available."""
    if sys.platform != "win32":
        return shutil.which("bash") or shutil.which("sh")

    git_bash_candidates = [
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
    ]
    for candidate in git_bash_candidates:
        if os.path.exists(candidate):
            return candidate

    return shutil.which("bash")


def default_shell_runner(command: str, cwd: str) -> tuple[str, int]:
    """Execute shell command in target working directory with timeout handling."""
    if not command or not command.strip():
        return "", 0

    bash_exe = get_preferred_shell()

    try:
        if bash_exe:
            proc = subprocess.run(
                [bash_exe, "-c", command],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=45,
            )
        else:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=45,
            )

        output = (proc.stdout or "").strip()
        if not output and proc.stderr:
            output = proc.stderr.strip()

        return output, proc.returncode

    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 45 seconds.", 124
    except Exception as exc:
        return f"Execution error: {exc}", 1


def commit(
    proposal: dict[str, Any],
    session_state: SessionState,
    verified_cache: VerifiedCommandCache | None = None,
    runner: Callable[[str, str], tuple[str, int]] | None = None,
    allow_privileged: bool = False,
    env: dict[str, Any] | None = None,
) -> tuple[str, int, Effect]:
    """
    Commit and execute a previously proposed command.

    Steps:
    1. Re-run effects.analyse(command, session_state.cwd) immediately before running.
    2. Enforce safety invariants: block privileged actions unless explicitly allowed.
    3. Execute the command.
    4. On exit code 0:
       - Update session_state with new command effect and entities.
       - If directory changed, update session_state.cwd.
       - Cache successful proposal in verified_cache.
    5. Return (output, exit_code, fresh_effect).
    """
    command = str(proposal.get("command", "")).strip()
    if not command:
        return "Error: Cannot commit an empty command.", 1, Effect()

    # 1. Re-run static effect analysis immediately before execution
    fresh_effect: Effect = analyse(command=command, cwd=session_state.cwd)
    fresh_tier = tier_from_effect(fresh_effect)

    # 2. Block privileged protected path modifications unless explicitly elevated
    if fresh_tier == "privileged" and not allow_privileged:
        err_msg = (
            f"Blocked: Command targets protected system paths.\n"
            f"{preview(fresh_effect)}"
        )
        return err_msg, 126, fresh_effect

    # 3. Execute
    exec_fn = runner if runner is not None else default_shell_runner
    output, exit_code = exec_fn(command, session_state.cwd)

    # 4. State updates on success
    if exit_code == 0:
        turn = len(session_state.effects) + 1
        lines = len(output.splitlines()) if output else 0
        summary = f"exit 0 ({lines} line{'s' if lines != 1 else ''})"
        session_state.record_effect(turn=turn, command=command, summary=summary)

        # Track directory navigation (cd)
        if command.startswith("cd ") or command == "cd":
            try:
                parts = shlex.split(command)
                if len(parts) >= 2:
                    target_dir = parts[1]
                    new_path = os.path.normpath(
                        target_dir if os.path.isabs(target_dir) else os.path.join(session_state.cwd, target_dir)
                    )
                    if os.path.isdir(new_path):
                        session_state.update_cwd(new_path)
            except Exception:
                pass

        # Record newly created entities
        for created_path in fresh_effect.creates:
            if os.path.exists(created_path):
                name = os.path.basename(created_path)
                ent_type = "dir" if os.path.isdir(created_path) else "file"
                session_state.record_entity(
                    name=name,
                    entity_type=ent_type,
                    path=created_path,
                    turn=turn,
                )

        # Record in verified cache
        if verified_cache is not None:
            active_env = env if env is not None else get_default_env(cwd=session_state.cwd)
            env_key = make_env_key(
                os_name=str(active_env.get("os", "")),
                shell_name=str(active_env.get("shell", "")),
                cwd=session_state.cwd,
            )
            verified_cache.record_success(
                request=str(proposal.get("request", command)),
                env_key=env_key,
                proposal={
                    "command": command,
                    "targets": proposal.get("targets", []),
                    "explanation": proposal.get("explanation", ""),
                },
            )

    return output, exit_code, fresh_effect
