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
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Callable
from pathlib import Path

from nlagent.cache.verified_cache import VerifiedCommandCache, make_env_key
from nlagent.context.state import SessionState
from nlagent.daemon.propose import get_default_env
from nlagent.safety.effects import Effect, analyse, preview, tier_from_effect
from nlagent.safety.quarantine import QuarantineLimitExceededError, quarantine_effect


_bash_available: bool | None = None


def get_preferred_shell() -> str | None:
    """Detect if bash (Git Bash on Windows or native bash) is available and functional."""
    global _bash_available
    if _bash_available is False:
        return None

    if sys.platform != "win32":
        return shutil.which("bash") or shutil.which("sh")

    if _bash_available is True:
        candidates = [
            "C:\\Program Files\\Git\\bin\\bash.exe",
            "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return shutil.which("bash")

    git_bash_candidates = [
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
    ]
    candidate = None
    for c in git_bash_candidates:
        if os.path.exists(c):
            candidate = c
            break
    if not candidate:
        candidate = shutil.which("bash")

    if not candidate:
        _bash_available = False
        return None

    # Test if bash actually executes without MSYS2 sandbox fatal error (0xC0000022)
    try:
        proc = subprocess.run([candidate, "-c", "exit 0"], capture_output=True, timeout=3)
        if proc.returncode == 0:
            _bash_available = True
            return candidate
    except Exception:
        pass

    _bash_available = False
    return None


def _should_use_bash_on_windows(command: str) -> bool:
    """Return True if a Windows command explicitly uses POSIX tools without PowerShell/drive syntax."""
    import re
    stripped = command.strip()
    if os.environ.get("NLAGENT_SHELL", "").strip().lower() == "bash":
        return True
    # Explicit PowerShell cmdlets or Windows drive paths must run in PowerShell/cmd
    if re.search(r"\b(Get|Set|New|Remove|Copy|Move|Rename|Select|Test|Where|ForEach|Format|Out)-[A-Za-z]+\b", stripped, re.IGNORECASE):
        return False
    if re.search(r"[A-Za-z]:\\", stripped):
        return False
    first_word = stripped.split()[0].lower() if stripped.split() else ""
    return first_word in ("ls", "rm", "find", "grep", "cat", "head", "tail", "wc", "touch", "chmod", "chown", "ln")


def default_shell_runner(command: str, cwd: str, shell: str | None = None) -> tuple[str, int]:
    """Execute shell command in target working directory with timeout handling."""
    if not command or not command.strip():
        return "", 0

    req_shell = (shell or os.environ.get("NLAGENT_SHELL", "")).strip().lower()
    bash_exe = get_preferred_shell() if req_shell != "cmd" and req_shell != "powershell" else (get_preferred_shell() if req_shell == "bash" else None)
    if req_shell == "bash":
        use_bash = bool(bash_exe)
    elif req_shell in ("cmd", "powershell"):
        use_bash = False
    else:
        use_bash = bool(bash_exe and (sys.platform != "win32" or _should_use_bash_on_windows(command)))

    cf = 0x08000000 if sys.platform == "win32" else 0
    try:
        if use_bash and bash_exe:
            proc = subprocess.run(
                [bash_exe, "-c", command],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=45,
                creationflags=cf,
            )
        else:
            # On Windows without functional bash, handle common POSIX file operations or fallback to cmd/powershell
            if sys.platform == "win32":
                stripped = command.strip()
                # Emulate basic POSIX rm on Windows if cmd.exe wouldn't recognize it
                if stripped.startswith("rm ") or stripped == "rm":
                    import glob
                    parts = shlex.split(stripped)
                    targets = [p for p in parts[1:] if not p.startswith("-")]
                    deleted_count = 0
                    for t in targets:
                        target_path = Path(cwd) / t if not os.path.isabs(t) else Path(t)
                        pattern = str(target_path)
                        matches = glob.glob(pattern) if any(ch in pattern for ch in "*?[]") else ([str(target_path)] if target_path.exists() else [])
                        for m in matches:
                            mp = Path(m)
                            if mp.is_dir():
                                shutil.rmtree(mp, ignore_errors=True)
                                deleted_count += 1
                            elif mp.exists():
                                mp.unlink(missing_ok=True)
                                deleted_count += 1
                    return f"Removed {deleted_count} items." if deleted_count > 0 else "", 0

                # Also emulate basic touch
                if stripped.startswith("touch "):
                    parts = shlex.split(stripped)
                    for t in parts[1:]:
                        if not t.startswith("-"):
                            tp = Path(cwd) / t if not os.path.isabs(t) else Path(t)
                            tp.touch(exist_ok=True)
                    return "", 0

                # Emulate basic POSIX find on Windows (avoiding C:\Windows\System32\find.exe conflict)
                if stripped.startswith("find "):
                    import fnmatch
                    parts = shlex.split(stripped)
                    root = "."
                    pattern = "*"
                    is_delete = "-delete" in parts
                    skip_next = False
                    for i in range(1, len(parts)):
                        if skip_next:
                            skip_next = False
                            continue
                        p = parts[i]
                        if p == "-name" and i + 1 < len(parts):
                            pattern = parts[i + 1]
                            skip_next = True
                        elif p == "-delete":
                            is_delete = True
                        elif not p.startswith("-"):
                            root = p

                    matches = []
                    target_root = Path(cwd) / root if not os.path.isabs(root) else Path(root)
                    if target_root.exists():
                        if target_root.is_dir():
                            for r, _, fnames in os.walk(target_root):
                                for fn in fnames:
                                    if fnmatch.fnmatch(fn, pattern):
                                        matches.append(os.path.join(r, fn))
                        elif fnmatch.fnmatch(target_root.name, pattern):
                            matches.append(str(target_root))

                    if is_delete:
                        deleted = 0
                        for m in matches:
                            try:
                                Path(m).unlink(missing_ok=True)
                                deleted += 1
                            except OSError:
                                pass
                        return f"Deleted {deleted} items." if deleted > 0 else "", 0
                    else:
                        rel_matches = [os.path.relpath(m, cwd).replace("\\", "/") for m in matches]
                        return "\n".join(rel_matches), 0

                if req_shell == "cmd":
                    proc = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        cwd=cwd,
                        timeout=45,
                        creationflags=cf,
                    )
                else:
                    # Execute via PowerShell
                    proc = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                        capture_output=True,
                        text=True,
                        cwd=cwd,
                        timeout=45,
                        creationflags=cf,
                    )
                    # Only fallback to cmd.exe if shell preference is not powershell and command is not a PowerShell cmdlet
                    if proc.returncode != 0 and req_shell != "powershell":
                        is_ps_cmdlet = bool(re.match(r"^[A-Z][a-zA-Z]+-[A-Z][a-zA-Z]+", command.strip()))
                        if not is_ps_cmdlet:
                            proc = subprocess.run(
                                command,
                                shell=True,
                                capture_output=True,
                                text=True,
                                cwd=cwd,
                                timeout=45,
                                creationflags=cf,
                            )
            else:
                proc = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=45,
                    creationflags=cf,
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
    allow_tier_escalation: bool = False,
    enable_quarantine: bool = True,
    confirmed_large_quarantine: bool = False,
    quarantine_base_dir: str | Path | None = None,
    env: dict[str, Any] | None = None,
    shell: str | None = None,
) -> tuple[str, int, Effect]:
    """
    Commit and execute a previously proposed command.

    Steps:
    1. Re-run effects.analyse(command, session_state.cwd) immediately before running.
    2. Enforce safety invariants: block privileged actions unless explicitly allowed.
    3. Enforce TOCTOU invariants: block execution if risk tier escalated from proposal time.
    4. Pre-execution Quarantine: shadow all files scheduled for deletion or overwrite.
    5. Execute the command.
    6. On exit code 0:
       - Update session_state with new command effect and entities.
       - If directory changed, update session_state.cwd.
       - Cache successful proposal in verified_cache.
    7. Return (output, exit_code, fresh_effect).
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

    # 3. Block execution if risk tier escalated from proposal time (TOCTOU race protection)
    proposed_tier = proposal.get("tier")
    tier_order = {"read_only": 0, "state_modifying": 1, "ambiguous": 2, "destructive": 3, "privileged": 4}
    if (
        proposed_tier
        and not allow_tier_escalation
        and tier_order.get(fresh_tier, 0) > tier_order.get(proposed_tier, 0)
    ):
        err_msg = (
            f"Blocked: TOCTOU state mutation detected! "
            f"Risk tier escalated from '{proposed_tier}' at proposal time to '{fresh_tier}' at commit time.\n"
            f"{preview(fresh_effect)}"
        )
        return err_msg, 126, fresh_effect

    # 4. Pre-execution Quarantine: shadow items before destructive mutation
    if enable_quarantine and (fresh_effect.deletes or fresh_effect.overwrites or fresh_effect.appends):
        req_id = str(proposal.get("request_id") or proposal.get("id") or f"req_{int(time.time() * 1000)}")
        try:
            quarantine_manifest = quarantine_effect(
                effect=fresh_effect,
                request_id=req_id,
                command=command,
                base_dir=quarantine_base_dir,
                confirmed_large=confirmed_large_quarantine,
            )
            proposal["quarantine_id"] = req_id
        except QuarantineLimitExceededError as qe:
            err_msg = (
                f"Blocked: {qe}\n"
                f"Destructive action requires explicit confirmation to proceed without undo."
            )
            return err_msg, 126, fresh_effect

    # 5. Execute
    if runner is not None:
        output, exit_code = runner(command, session_state.cwd)
    elif shell:
        output, exit_code = default_shell_runner(command, session_state.cwd, shell=shell)
    else:
        output, exit_code = default_shell_runner(command, session_state.cwd)

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

