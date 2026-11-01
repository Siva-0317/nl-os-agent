# agent.py
"""
NL OS Agent — Natural Language Operating System Shell.
Main interactive CLI loop combining Context Resolution, Nemotron Translation,
Safety Verification, and Command Execution.
"""

import os
import sys
import uuid
import shutil
import subprocess
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nl_translator import translate_to_command, MODEL, LM_STUDIO_HOST
from safety_verifier import verify_and_confirm, classify_risk
from session_context import SessionContextManager

console = Console()

BANNER = """
╔═══════════════════════════════════════════════╗
║     NL OS Agent — Natural Language Shell      ║
║  Powered by NVIDIA Nemotron (LM Studio Dev)   ║
║  Type in plain English. Type 'exit' to quit.  ║
╚═══════════════════════════════════════════════╝
"""


def get_preferred_shell() -> str | None:
    """Detect if bash (e.g. Git Bash on Windows or native bash) is available."""
    # Look for Git Bash or native bash
    git_bash_candidates = [
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
    ]
    for candidate in git_bash_candidates:
        if os.path.exists(candidate):
            return candidate

    bash_in_path = shutil.which("bash")
    if bash_in_path:
        return bash_in_path

    return None


def run_command(command: str, cwd: str) -> tuple[str, int]:
    """
    Execute a shell command in the specified working directory.
    Supports Unix commands on Windows via Git Bash if available,
    falling back to standard system shell.
    """
    # Don't try to execute empty or echo error commands
    if not command or not command.strip():
        return "", 0

    bash_executable = get_preferred_shell() if sys.platform == "win32" else None

    try:
        if bash_executable:
            # Execute through bash so commands like ls -la, df -h, find work smoothly on Windows
            result = subprocess.run(
                [bash_executable, "-c", command],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=30
            )
        else:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=30
            )

        output = (result.stdout or "").strip()
        if not output and result.stderr:
            output = result.stderr.strip()

        return output, result.returncode

    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds.", 124
    except Exception as e:
        return f"Execution error: {e}", 1


def main():
    console.print(BANNER, style="bold cyan")
    console.print(f"[dim]LLM: {MODEL} | Endpoint: {LM_STUDIO_HOST}[/dim]")

    session = SessionContextManager(session_id=str(uuid.uuid4()))
    console.print(f"[dim]Session started | CWD: {session.cwd}[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold green]You >[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session ended.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "bye"):
            console.print("[dim]Goodbye.[/dim]")
            break

        if user_input.lower() == "history":
            console.print(Panel(
                Text(session.get_history_summary()),
                title="Session Command History",
                border_style="cyan"
            ))
            continue

        # Step 1: Resolve context and references
        resolved_request = session.resolve(user_input)

        # Step 2: Translate to shell command using Nemotron
        with console.status("[yellow]Translating with Nemotron...[/yellow]"):
            command = translate_to_command(resolved_request)

        console.print(f"[cyan]Command:[/cyan] {command}")

        # Step 3: Safety verification & sandbox confirmation
        approved = verify_and_confirm(command, session.cwd)

        if not approved:
            console.print("[red]Command cancelled.[/red]\n")
            session.update(
                user_input, command,
                classify_risk(command).value, "CANCELLED"
            )
            continue

        # Step 4: Execute command
        output, exit_code = run_command(command, session.cwd)

        if output:
            console.print(Panel(
                output,
                title="Output",
                border_style="green" if exit_code == 0 else "red"
            ))
        elif exit_code == 0:
            console.print("[dim]✓ Command completed successfully.[/dim]")
        else:
            console.print(f"[red]✗ Command failed with exit code {exit_code}[/red]")

        # Update session state and entities
        session.update(
            user_input, command,
            classify_risk(command).value,
            output
        )

        console.print()


if __name__ == "__main__":
    main()
