#!/usr/bin/env python3
"""
agent.py - NL OS Agent Interactive CLI.

Production entry point featuring a two-stage safety architecture:
1. propose(): Translates natural language into a candidate shell command,
   resolves session entities, consults VerifiedCommandCache, validates JSON,
   and computes static effects & risk tiers. (No execution).
2. commit(): Re-runs static effect analysis immediately prior to execution
   to prevent TOCTOU safety regressions, executes via the OS shell,
   and updates SessionState and VerifiedCommandCache on success.

NOTE: Baseline G1 (classify_risk) is excluded from the runtime decision path;
safety tiering is strictly derived from static effect analysis.
"""

from __future__ import annotations

import argparse
import os
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Ensure src is on sys.path
_root = os.path.dirname(os.path.abspath(__file__))
_src = os.path.join(_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.backends.llama_server import LlamaServerBackend
from nlagent.cache.verified_cache import VerifiedCommandCache
from nlagent.context.state import SessionState
from nlagent.daemon.commit import commit
from nlagent.daemon.propose import get_default_env, propose

console = Console()

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║             NL OS Agent — AI-Native Shell Agent              ║
║         Two-Stage Verified Execution (Propose / Commit)      ║
║        Type in plain English. Type 'exit' to quit.           ║
╚══════════════════════════════════════════════════════════════╝
"""


def format_tier_badge(tier: str) -> str:
    """Return colored rich markup for a risk tier."""
    styles = {
        "read_only": "[bold green]READ-ONLY[/bold green]",
        "state_modifying": "[bold yellow]STATE-MODIFYING[/bold yellow]",
        "destructive": "[bold red]DESTRUCTIVE[/bold red]",
        "privileged": "[bold white on red] PRIVILEGED [/bold white on red]",
        "ambiguous": "[bold magenta]AMBIGUOUS[/bold magenta]",
    }
    return styles.get(tier, f"[dim]{tier}[/dim]")


def display_history(session: SessionState) -> None:
    """Render historical command effects table."""
    if not session.effects:
        console.print("[dim]No command history recorded yet.[/dim]")
        return

    table = Table(title="Session Effect History", border_style="cyan")
    table.add_column("Turn", justify="right", style="cyan", no_wrap=True)
    table.add_column("Command", style="green")
    table.add_column("Effect Summary", style="white")

    for eff in session.effects:
        table.add_row(f"t{eff.turn}", eff.command, eff.summary)

    console.print(table)


def display_entities(session: SessionState) -> None:
    """Render tracked entity table."""
    if not session.entities:
        console.print("[dim]No entities tracked yet.[/dim]")
        return

    table = Table(title="Tracked Session Entities", border_style="cyan")
    table.add_column("Type", style="cyan")
    table.add_column("Path", style="green")
    table.add_column("Last Turn", justify="right", style="yellow")

    for ent in sorted(session.entities.values(), key=lambda e: e.last_turn, reverse=True):
        table.add_row(ent.type, ent.path, f"t{ent.last_turn}")

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="NL OS Agent Interactive Shell")
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:8080/completion",
        help="llama-server HTTP endpoint",
    )
    parser.add_argument(
        "--cwd",
        default=os.getcwd(),
        help="Initial working directory",
    )
    parser.add_argument(
        "--cache-threshold",
        type=float,
        default=0.90,
        help="Cosine similarity threshold for verified cache hits",
    )
    args = parser.parse_args()

    console.print(BANNER, style="bold cyan")

    # Initialize runtime components
    session = SessionState(cwd=args.cwd)
    verified_cache = VerifiedCommandCache(threshold=args.cache_threshold)
    backend = LlamaServerBackend(endpoint_url=args.endpoint)
    env = get_default_env(cwd=session.cwd)

    console.print(f"[dim]OS: {env['os']} | Shell: {env['shell']} | User: {env['user']}[/dim]")
    console.print(f"[dim]Endpoint: {backend.endpoint_url}[/dim]")
    console.print(f"[dim]CWD: {session.cwd}[/dim]\n")

    while True:
        try:
            cwd_display = os.path.basename(session.cwd) or session.cwd
            user_input = console.input(f"[bold cyan]{cwd_display}[/bold cyan] [bold green]>[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session terminated.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "bye"):
            console.print("[dim]Goodbye.[/dim]")
            break

        if user_input.lower() == "history":
            display_history(session)
            continue

        if user_input.lower() == "entities":
            display_entities(session)
            continue

        if user_input.lower().startswith("cd "):
            target_dir = user_input[3:].strip()
            new_path = os.path.normpath(
                target_dir if os.path.isabs(target_dir) else os.path.join(session.cwd, target_dir)
            )
            if os.path.isdir(new_path):
                session.update_cwd(new_path)
                console.print(f"[dim]Directory changed to: {session.cwd}[/dim]")
            else:
                console.print(f"[red]Directory not found: {new_path}[/red]")
            continue

        # -----------------------------------------------------------------
        # Stage 1: Propose (No execution)
        # -----------------------------------------------------------------
        try:
            with console.status("[yellow]Generating proposal & analysing static effects...[/yellow]"):
                proposal = propose(
                    request=user_input,
                    session_state=session,
                    backend=backend,
                    verified_cache=verified_cache,
                    env=env,
                )
        except Exception as exc:
            console.print(f"[red]Proposal error:[/red] {exc}\n")
            continue

        command = proposal["command"]
        tier = proposal["tier"]
        preview_text = proposal["preview"]
        is_cached = proposal.get("cached", False)

        cache_badge = "[bold green][VERIFIED CACHE HIT][/bold green]" if is_cached else "[dim][LLM PROPOSAL][/dim]"
        console.print(f"\n{cache_badge} {format_tier_badge(tier)}: [bold white]{command}[/bold white]")

        if proposal.get("explanation"):
            console.print(f"[dim]Rationale: {proposal['explanation']}[/dim]")

        # -----------------------------------------------------------------
        # Stage 1.5: Policy / Confirmation Gate
        # -----------------------------------------------------------------
        allow_privileged = False

        if tier == "privileged":
            console.print(Panel(
                preview_text,
                title="[bold white on red] PRIVILEGED SYSTEM OPERATION BLOCKED [/bold white on red]",
                border_style="red",
            ))
            confirm = console.input("[bold red]Explicitly elevate and execute this command? (type 'yes' to proceed): [/bold red]").strip()
            if confirm.lower() != "yes":
                console.print("[red]Command aborted.[/red]\n")
                continue
            allow_privileged = True

        elif tier == "destructive":
            console.print(Panel(
                preview_text,
                title="[bold red] DESTRUCTIVE OPERATION PREVIEW [/bold red]",
                border_style="red",
            ))
            confirm = console.input("[bold red]Execute destructive command? [y/N]: [/bold red]").strip()
            if confirm.lower() not in ("y", "yes"):
                console.print("[red]Command cancelled.[/red]\n")
                continue

        elif tier == "ambiguous":
            console.print(Panel(
                preview_text,
                title="[bold magenta] AMBIGUOUS / EXTERNAL SIDE EFFECTS [/bold magenta]",
                border_style="magenta",
            ))
            confirm = console.input("[bold yellow]Proceed with unverified command? [y/N]: [/bold yellow]").strip()
            if confirm.lower() not in ("y", "yes"):
                console.print("[red]Command cancelled.[/red]\n")
                continue

        # -----------------------------------------------------------------
        # Stage 2: Commit (Re-analyses effects immediately before execution)
        # -----------------------------------------------------------------
        output, exit_code, fresh_eff = commit(
            proposal=proposal,
            session_state=session,
            verified_cache=verified_cache,
            allow_privileged=allow_privileged,
            env=env,
        )

        if output:
            console.print(Panel(
                output,
                title=f"Output (Exit {exit_code})",
                border_style="green" if exit_code == 0 else "red",
            ))
        elif exit_code == 0:
            console.print("[dim green]✓ Command executed successfully (no output).[/dim green]")
        else:
            console.print(f"[red]✗ Command failed with exit code {exit_code}[/red]")

        console.print()


if __name__ == "__main__":
    main()
