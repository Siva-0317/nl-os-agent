"""
main.py - nlctl Unified CLI for Natural Language OS Agent.

Subcommands:
  nlctl propose "<request>"  - Propose safe terminal command, preview risk tier, persist to SQLite.
  nlctl commit <id>          - Execute proposed command with TOCTOU check & quarantine shadow.
  nlctl undo [id]            - Byte-exact rollback of quarantined file changes.
  nlctl history              - Display audit log of past proposals, tiers, and statuses.

Invariants:
- propose NEVER executes commands.
- commit re-runs static analysis and creates quarantine backups before disk writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Ensure project root & src are in sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_src = os.path.join(_root, "src")
for p in (_root, _src):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.backends.llama_server import CompletionResult, LlamaServerBackend, resolve_local_endpoint
from nlagent.cache.verified_cache import VerifiedCommandCache
from nlagent.cli.db import SQLiteStateStore
from nlagent.context.state import SessionState
from nlagent.daemon.commit import commit
from nlagent.daemon.propose import get_default_env, propose
from nlagent.safety.effects import analyse, preview, tier_from_effect
from nlagent.safety.quarantine import (
    NameClashError,
    QuarantineLimitExceededError,
    QuarantineNotFoundError,
    undo as quarantine_undo,
)

# ANSI terminal formatting
ANSI_RED = "\033[91m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"
ANSI_BLUE = "\033[94m"
ANSI_BOLD = "\033[1m"
ANSI_RESET = "\033[0m"


def colorize_tier(tier: str) -> str:
    """Format risk tier label with standard ANSI colors."""
    t = tier.lower()
    if t == "read_only":
        return f"{ANSI_GREEN}{ANSI_BOLD}[READ-ONLY]{ANSI_RESET}"
    elif t == "state_modifying":
        return f"{ANSI_BLUE}{ANSI_BOLD}[STATE-MODIFYING]{ANSI_RESET}"
    elif t in ("destructive", "privileged"):
        return f"{ANSI_RED}{ANSI_BOLD}[{tier.upper()}]{ANSI_RESET}"
    else:
        return f"{ANSI_YELLOW}{ANSI_BOLD}[{tier.upper()}]{ANSI_RESET}"


# -----------------------------------------------------------------------------
# Subcommand: propose
# -----------------------------------------------------------------------------

def handle_propose(args: argparse.Namespace) -> int:
    request = args.request.strip()
    if not request:
        sys.stderr.write("Error: Request string cannot be empty.\n")
        return 1

    cwd = os.path.abspath(args.cwd) if args.cwd else os.getcwd()
    db = SQLiteStateStore(db_path=args.db)
    session = SessionState(cwd=cwd)
    cache = VerifiedCommandCache(threshold=0.88)
    env = get_default_env(cwd=cwd)

    proposal_id = f"p_{uuid.uuid4().hex[:8]}"

    # Mock backend mode for offline testing / CI
    if args.mock:
        mock_cmd = str(args.mock).strip()
        mock_backend = lambda prompt: json.dumps({
            "command": mock_cmd,
            "targets": [mock_cmd.split()[-1]] if len(mock_cmd.split()) > 1 else [],
            "explanation": "Mock proposal for testing",
        })
        backend = mock_backend
    else:
        # Check environment variable override or live backend
        mock_env = os.environ.get("NLAGENT_MOCK_PROPOSAL")
        if mock_env:
            backend = lambda prompt: mock_env
        else:
            backend = LlamaServerBackend(endpoint_url=resolve_local_endpoint())

    try:
        res = propose(
            request=request,
            session_state=session,
            backend=backend,
            verified_cache=cache,
            env=env,
        )
    except Exception as exc:
        sys.stderr.write(f"\n{ANSI_RED}Error proposing command:{ANSI_RESET} {exc}\n")
        return 1

    cmd = res.get("command", "").strip()
    tier = res.get("tier", "read_only")
    preview_text = res.get("preview", "")
    targets = res.get("targets", [])
    explanation = res.get("explanation", "")

    # Persist in SQLite store
    db.save_proposal(
        proposal_id=proposal_id,
        request=request,
        command=cmd,
        tier=tier,
        cwd=cwd,
        preview_text=preview_text,
        explanation=explanation,
        targets=targets,
        status="proposed",
    )

    # 1. Emit preview on stderr
    tier_badge = colorize_tier(tier)
    sys.stderr.write(f"\n{tier_badge} {ANSI_BOLD}{cmd}{ANSI_RESET}\n")
    if preview_text and preview_text != "No filesystem effect detected.":
        sys.stderr.write(f"{preview_text}\n")
    if explanation:
        sys.stderr.write(f"Explanation: {explanation}\n")
    sys.stderr.write(f"Proposal ID: {ANSI_BOLD}{proposal_id}{ANSI_RESET}\n\n")

    # 2. Output on stdout according to format flags
    if args.json:
        out_data = {
            "id": proposal_id,
            "command": cmd,
            "tier": tier,
            "targets": targets,
            "preview": preview_text,
            "explanation": explanation,
            "cwd": cwd,
        }
        sys.stdout.write(json.dumps(out_data) + "\n")
    elif args.emit_shell:
        # Shell hook replacement: places eval command on current line buffer
        eval_line = f'eval "$(nlctl commit {proposal_id} --emit-shell)"'
        sys.stdout.write(eval_line)
    elif args.raw:
        sys.stdout.write(cmd)
    else:
        sys.stdout.write(cmd + "\n")

    sys.stdout.flush()
    return 0


# -----------------------------------------------------------------------------
# Subcommand: commit
# -----------------------------------------------------------------------------

def handle_commit(args: argparse.Namespace) -> int:
    proposal_id = args.id.strip()
    db = SQLiteStateStore(db_path=args.db)
    record = db.get_proposal(proposal_id)

    if not record:
        sys.stderr.write(f"Error: Proposal '{proposal_id}' not found in state database.\n")
        return 1

    if record.get("status") == "committed":
        sys.stderr.write(f"Warning: Proposal '{proposal_id}' has already been committed.\n")

    cmd = record["command"]
    cwd = record["cwd"]
    session = SessionState(cwd=cwd)

    # If --emit-shell: shadow to quarantine first, then output the raw shell command for parent eval
    if args.emit_shell:
        eff = analyse(cmd, cwd=cwd)
        tier = tier_from_effect(eff)

        if tier == "privileged" and not args.allow_privileged:
            sys.stderr.write(f"\n{ANSI_RED}Blocked: Command targets protected system paths.{ANSI_RESET}\n")
            sys.stderr.write(f"{preview(eff)}\n")
            db.update_proposal_status(proposal_id, status="blocked", exit_code=126)
            return 126

        # Shadow to quarantine if destructive or overwriting
        quarantine_id = None
        if not args.no_quarantine and (eff.deletes or eff.overwrites):
            from nlagent.safety.quarantine import quarantine_effect
            try:
                q_manifest = quarantine_effect(
                    effect=eff,
                    request_id=proposal_id,
                    command=cmd,
                    confirmed_large=args.confirmed_large,
                )
                if q_manifest:
                    quarantine_id = proposal_id
            except QuarantineLimitExceededError as qe:
                sys.stderr.write(f"\n{ANSI_RED}Blocked: {qe}{ANSI_RESET}\n")
                db.update_proposal_status(proposal_id, status="blocked", exit_code=126)
                return 126

        # Mark committed in SQLite
        db.update_proposal_status(
            proposal_id=proposal_id,
            status="committed",
            exit_code=0,
            quarantine_id=quarantine_id,
        )

        # Output raw command string to stdout so calling shell can eval it (e.g. cd persists)
        sys.stdout.write(cmd)
        sys.stdout.flush()
        return 0

    # Normal commit execution
    output, exit_code, fresh_effect = commit(
        proposal=record,
        session_state=session,
        allow_privileged=args.allow_privileged,
        enable_quarantine=not args.no_quarantine,
        confirmed_large_quarantine=args.confirmed_large,
    )

    q_id = record.get("quarantine_id") or (proposal_id if fresh_effect.deletes or fresh_effect.overwrites else None)
    status = "committed" if exit_code == 0 else "failed" if exit_code != 126 else "blocked"

    db.update_proposal_status(
        proposal_id=proposal_id,
        status=status,
        exit_code=exit_code,
        output=output,
        quarantine_id=q_id,
    )

    if output:
        sys.stdout.write(output + "\n")
        sys.stdout.flush()

    return exit_code


# -----------------------------------------------------------------------------
# Subcommand: undo
# -----------------------------------------------------------------------------

def handle_undo(args: argparse.Namespace) -> int:
    db = SQLiteStateStore(db_path=args.db)

    # Target ID or latest committed destructive proposal
    target_id = args.id.strip() if args.id else None
    if not target_id:
        latest = db.get_latest_proposal(status="committed")
        if latest and latest.get("quarantine_id"):
            target_id = latest["quarantine_id"]
        elif latest:
            target_id = latest["id"]
        else:
            sys.stderr.write("No executed proposal found to undo.\n")
            return 1

    try:
        undo_res = quarantine_undo(
            request_id=target_id,
            on_clash=args.on_clash,
        )
    except NameClashError as ce:
        sys.stderr.write(f"\n{ANSI_RED}Undo Clash Error:{ANSI_RESET} {ce}\n")
        return 1
    except QuarantineNotFoundError as qe:
        sys.stderr.write(f"\n{ANSI_RED}Error:{ANSI_RESET} {qe}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"\n{ANSI_RED}Undo failed:{ANSI_RESET} {exc}\n")
        return 1

    db.update_proposal_status(proposal_id=target_id, status="undone", output=undo_res.message)

    if undo_res.success:
        sys.stdout.write(f"{ANSI_GREEN}{ANSI_BOLD}Undo successful:{ANSI_RESET} {undo_res.message}\n")
        for p in undo_res.restored_paths:
            sys.stdout.write(f"  Restored: {p}\n")
        return 0
    else:
        sys.stderr.write(f"{ANSI_YELLOW}Undo completed with errors:{ANSI_RESET} {undo_res.message}\n")
        for p in undo_res.failed_paths:
            sys.stderr.write(f"  Failed: {p}\n")
        return 1


# -----------------------------------------------------------------------------
# Subcommand: history
# -----------------------------------------------------------------------------

def handle_history(args: argparse.Namespace) -> int:
    db = SQLiteStateStore(db_path=args.db)
    rows = db.list_history(limit=args.limit)

    if args.json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return 0

    if not rows:
        sys.stdout.write("No command proposals recorded yet in history.\n")
        return 0

    sys.stdout.write(f"\n{ANSI_BOLD}{'ID':<12} {'STATUS':<12} {'TIER':<18} {'COMMAND':<32} {'REQUEST'}{ANSI_RESET}\n")
    sys.stdout.write("-" * 90 + "\n")

    for r in rows:
        pid = r["id"]
        status = r.get("status", "proposed")
        tier = r.get("tier", "read_only")
        cmd = r.get("command", "")
        req = r.get("request", "")

        status_str = f"{ANSI_GREEN}committed{ANSI_RESET}" if status == "committed" else (
            f"{ANSI_RED}blocked{ANSI_RESET}" if status == "blocked" else (
                f"{ANSI_YELLOW}undone{ANSI_RESET}" if status == "undone" else status
            )
        )
        tier_str = colorize_tier(tier)

        cmd_display = cmd[:30] + ".." if len(cmd) > 30 else cmd
        req_display = req[:28] + ".." if len(req) > 28 else req

        sys.stdout.write(f"{pid:<12} {status_str:<21} {tier_str:<27} {cmd_display:<32} {req_display}\n")

    sys.stdout.write("\n")
    return 0


# -----------------------------------------------------------------------------
# CLI Parser Setup
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=None, help="Custom SQLite state database path")

    parser = argparse.ArgumentParser(
        prog="nlctl",
        description="NL OS Agent: Safety-Verified Terminal Command Engine.",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # propose
    p_propose = subparsers.add_parser(
        "propose",
        help="Propose a safe terminal command from natural language",
        parents=[common],
    )
    p_propose.add_argument("request", help="Natural language user request")
    p_propose.add_argument("--cwd", default=None, help="Working directory context")
    p_propose.add_argument("--emit-shell", action="store_true", help="Emit eval commit wrapper for shell keybinding")
    p_propose.add_argument("--raw", action="store_true", help="Emit raw command string directly")
    p_propose.add_argument("--json", action="store_true", help="Emit structured JSON output")
    p_propose.add_argument("--mock", default=None, help="Mock command output for testing")

    # commit
    p_commit = subparsers.add_parser(
        "commit",
        help="Commit and execute a proposed command",
        parents=[common],
    )
    p_commit.add_argument("id", help="Proposal ID to commit")
    p_commit.add_argument("--emit-shell", action="store_true", help="Emit raw command string for parent shell eval")
    p_commit.add_argument("--allow-privileged", action="store_true", help="Allow execution of privileged paths")
    p_commit.add_argument("--no-quarantine", action="store_true", help="Disable quarantine shadow backup")
    p_commit.add_argument("--confirmed-large", action="store_true", help="Acknowledge and proceed if payload > 500 MB")

    # undo
    p_undo = subparsers.add_parser(
        "undo",
        help="Roll back filesystem state from quarantine",
        parents=[common],
    )
    p_undo.add_argument("id", nargs="?", default=None, help="Proposal ID or Quarantine ID to rollback (default: latest)")
    p_undo.add_argument("--on-clash", choices=["error", "backup", "overwrite"], default="error", help="Conflict resolution")

    # history
    p_history = subparsers.add_parser(
        "history",
        help="List recent proposals and execution history",
        parents=[common],
    )
    p_history.add_argument("--limit", type=int, default=20, help="Maximum number of history records to show")
    p_history.add_argument("--json", action="store_true", help="Emit history as JSON")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "propose": handle_propose,
        "commit": handle_commit,
        "undo": handle_undo,
        "history": handle_history,
    }

    handler = handlers.get(args.subcommand)
    if not handler:
        parser.print_help()
        sys.exit(1)

    exit_code = handler(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
