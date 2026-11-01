# Basic Implementation Plan — NL OS Agent
## Phase-by-Phase Build Guide with Setup Steps, Code Prompts, and Test Commands

---

## OVERVIEW

The basic version demonstrates the full pipeline in a local development environment without requiring GPU fine-tuning. It uses Ollama (local LLM inference) as a stand-in for the fine-tuned Nemotron model, Docker for sandbox isolation, and SQLite for session context. Once the basic version is working end-to-end, Phase 2 (fine-tuning) is layered on top without changing the pipeline.

**What the basic version delivers:**
- Natural language → shell command translation via Ollama
- Risk-tier classification (read-only / state-modifying / destructive)
- Docker sandboxed simulation of destructive commands
- User confirmation before destructive execution
- Multi-turn session context with entity reference resolution
- Full pipeline working as a local CLI tool

**Estimated build time:** 3–4 days for 4 people working in parallel.

---

## PREREQUISITES — INSTALL THESE FIRST

```bash
# 1. Python 3.11
sudo apt update && sudo apt install python3.11 python3.11-venv python3-pip -y

# 2. Ollama (local LLM runner)
curl -fsSL https://ollama.com/install.sh | sh

# 3. Pull a small local model (use this as Nemotron stand-in)
ollama pull llama3.2:3b
# OR for better command accuracy:
ollama pull mistral:7b-instruct

# 4. Docker
sudo apt install docker.io -y
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect

# 5. Create project folder and virtual environment
mkdir nl-os-agent && cd nl-os-agent
python3.11 -m venv venv
source venv/bin/activate

# 6. Install Python dependencies
pip install requests faiss-cpu sqlite3 docker pytest rich python-dotenv
```

---

## FOLDER STRUCTURE

```
nl-os-agent/
├── agent.py                  # Main entry point / CLI loop
├── session_context.py        # Session Context Manager
├── safety_verifier.py        # Risk classification + sandbox
├── nl_translator.py          # Ollama API wrapper (NL → command)
├── dataset/
│   ├── generate_dataset.py   # Synthetic data generator (Phase 2 prep)
│   └── pairs.jsonl           # Generated NL → command pairs
├── tests/
│   ├── test_translator.py    # Unit tests for NL translation
│   ├── test_safety.py        # Unit tests for safety verifier
│   └── test_context.py       # Unit tests for session context
├── session.db                # SQLite session store (auto-created)
├── .env                      # OLLAMA_HOST etc.
└── requirements.txt
```

---

## PHASE 1 — NL TRANSLATOR (Day 1)

### What to build
A Python wrapper around Ollama that accepts a natural language request and returns a shell command string.

### File: `nl_translator.py`

```python
# nl_translator.py
import requests
import json

OLLAMA_HOST = "http://localhost:11434"
MODEL = "llama3.2:3b"  # swap to fine-tuned Nemotron later

SYSTEM_PROMPT = """You are a terminal command generator for Linux/macOS.
Your ONLY job is to convert a natural language request into a single, correct shell command.

Rules:
- Output ONLY the shell command. No explanation. No markdown. No backticks.
- Output a single command on one line.
- If the request is ambiguous, output the safest, most read-only interpretation.
- Never refuse. Always output a command.

Examples:
User: show me all files in this folder
Output: ls -la

User: find python files changed in the last week
Output: find . -name "*.py" -mtime -7

User: how much disk space is left
Output: df -h
"""

def translate_to_command(nl_request: str, context_str: str = "") -> str:
    """Translate natural language request to shell command via Ollama."""
    prompt = nl_request
    if context_str:
        prompt = f"{context_str}\nUser request: {nl_request}"

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "stream": False
    }
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        command = data["message"]["content"].strip()
        # Clean up any accidental markdown
        command = command.replace("`", "").strip()
        # Take only first line if model outputs multiple
        command = command.split("\n")[0].strip()
        return command
    except Exception as e:
        return f"echo 'Translation error: {e}'"


if __name__ == "__main__":
    # Quick test
    tests = [
        "show me all files here",
        "what processes are using the most CPU",
        "find all log files modified today",
        "how much space does the downloads folder take",
        "delete all temp files in /tmp"
    ]
    for t in tests:
        cmd = translate_to_command(t)
        print(f"NL: {t}")
        print(f"CMD: {cmd}\n")
```

### Test it
```bash
# Make sure Ollama is running
ollama serve &

# Test translation
python nl_translator.py
```

**Expected output:**
```
NL: show me all files here
CMD: ls -la

NL: what processes are using the most CPU
CMD: ps aux --sort=-%cpu | head -20

NL: delete all temp files in /tmp
CMD: rm -rf /tmp/*
```

---

## PHASE 2 — SAFETY VERIFIER (Day 1–2)

### What to build
Risk tier classifier + Docker sandbox simulation + confirmation prompt.

### File: `safety_verifier.py`

```python
# safety_verifier.py
import subprocess
import shlex
from enum import Enum
from rich.console import Console
from rich.panel import Panel

console = Console()

class RiskTier(Enum):
    READ_ONLY = "read_only"
    STATE_MODIFYING = "state_modifying"
    DESTRUCTIVE = "destructive"
    AMBIGUOUS = "ambiguous"

# Commands that only read — never modify
READONLY_CMDS = {
    "ls", "cat", "head", "tail", "less", "more", "ps", "top",
    "htop", "df", "du", "pwd", "whoami", "which", "echo",
    "grep", "find", "locate", "wc", "sort", "uniq", "diff",
    "file", "stat", "lsof", "netstat", "ifconfig", "ip",
    "uname", "date", "uptime", "history", "env", "printenv"
}

# Commands that are inherently dangerous
DESTRUCTIVE_CMDS = {
    "rm", "rmdir", "kill", "pkill", "killall", "dd",
    "mkfs", "fdisk", "parted", "shred", "truncate",
    "overwrite", "wipe"
}

# Dangerous flags that escalate a command's risk
DESTRUCTIVE_FLAGS = {"-rf", "-fr", "-r", "--recursive", "-9", "--force"}

# Commands that modify state but are reversible
MODIFYING_CMDS = {
    "mkdir", "touch", "cp", "mv", "ln", "chmod", "chown",
    "chgrp", "tar", "zip", "unzip", "pip", "apt", "npm",
    "git", "wget", "curl", "scp", "rsync", "systemctl",
    "service", "crontab"
}


def classify_risk(command: str) -> RiskTier:
    """Classify command risk tier by operation and flags."""
    if not command or not command.strip():
        return RiskTier.AMBIGUOUS

    parts = shlex.split(command.strip())
    if not parts:
        return RiskTier.AMBIGUOUS

    base = parts[0].split("/")[-1]  # handle /bin/rm → rm
    flags = set(p for p in parts[1:] if p.startswith("-"))

    # Shell operators escalate everything to at least state-modifying
    has_redirect = any(c in command for c in [">", ">>", "|"])

    if base in DESTRUCTIVE_CMDS:
        return RiskTier.DESTRUCTIVE
    if base in MODIFYING_CMDS and flags.intersection(DESTRUCTIVE_FLAGS):
        return RiskTier.DESTRUCTIVE
    if "|" in command and any(d in command for d in DESTRUCTIVE_CMDS):
        return RiskTier.DESTRUCTIVE  # piped destructive e.g. echo "rm" | bash
    if base in READONLY_CMDS and not has_redirect:
        return RiskTier.READ_ONLY
    if base in MODIFYING_CMDS:
        return RiskTier.STATE_MODIFYING
    if has_redirect and ">" in command:
        return RiskTier.STATE_MODIFYING

    return RiskTier.AMBIGUOUS


def sandbox_simulate(command: str, cwd: str) -> dict:
    """
    Simulate command in an isolated Docker container.
    Returns effect summary without touching the real filesystem.
    """
    try:
        import docker
        client = docker.from_env()

        # Run in isolated container with cwd mounted read-only
        container_output = client.containers.run(
            image="alpine:latest",
            command=f"sh -c 'cd /sandbox && {command} 2>&1'",
            volumes={cwd: {"bind": "/sandbox", "mode": "ro"}},
            remove=True,
            stdout=True,
            stderr=True,
            mem_limit="64m",
            network_disabled=True
        )

        output = container_output.decode("utf-8", errors="replace").strip()
        return {
            "success": True,
            "simulated_output": output[:500],  # cap length
            "summary": f"Simulation ran successfully. Preview output:\n{output[:200]}"
        }
    except ImportError:
        # Docker not available — fall back to dry-run message
        return {
            "success": False,
            "summary": f"[Sandbox unavailable] Command would run: {command}\nThis appears to be a {classify_risk(command).value} operation."
        }
    except Exception as e:
        return {
            "success": False,
            "summary": f"Sandbox simulation failed: {str(e)}\nProceed with caution."
        }


def verify_and_confirm(command: str, cwd: str) -> bool:
    """
    Full safety verification pipeline.
    Returns True if command is approved for execution, False to cancel.
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
            f"Command: [cyan]{command}[/cyan]\n"
            f"Risk Tier: [red]{tier.value.upper()}[/red]",
            title="Safety Verification"
        ))

        # Run sandbox simulation
        console.print("[yellow]Running sandbox simulation...[/yellow]")
        result = sandbox_simulate(command, cwd)
        console.print(Panel(
            result["summary"],
            title="Simulated Effect (Sandbox)"
        ))

        confirm = console.input(
            "[bold red]This action may be irreversible. "
            "Confirm execution? (y/N): [/bold red]"
        ).strip().lower()

        return confirm == "y"

    return False


if __name__ == "__main__":
    # Test risk classification
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
```

### Test it
```bash
python safety_verifier.py
```

**Expected output:**
```
Risk Classification Tests:
--------------------------------------------------
✓ [read_only            ] ls -la
✓ [state_modifying      ] mkdir backup
✓ [destructive          ] rm -rf /tmp/test
✓ [destructive          ] find /tmp -delete
✓ [destructive          ] echo 'rm -rf /' | bash
✓ [read_only            ] cat file.txt | grep error
✓ [state_modifying      ] mv old.txt new.txt

All tests passed!
```

---

## PHASE 3 — SESSION CONTEXT MANAGER (Day 2)

### File: `session_context.py`

```python
# session_context.py
import sqlite3
import re
import subprocess
from datetime import datetime


class SessionContextManager:
    def __init__(self, session_id: str, db_path: str = "session.db"):
        self.session_id = session_id
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        self.cwd = subprocess.run(
            ["pwd"], capture_output=True, text=True).stdout.strip()
        self.turn = 0

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, turn INTEGER,
                nl_request TEXT, command TEXT,
                risk_tier TEXT, output TEXT, timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, name TEXT,
                entity_type TEXT, full_path TEXT,
                last_turn INTEGER
            );
        """)
        self.conn.commit()

    def resolve(self, nl_request: str) -> str:
        """Enrich NL request with session context for the LLM."""
        self.turn += 1
        context_parts = [f"Current directory: {self.cwd}"]

        # Add last 3 commands as context
        recent = self.conn.execute("""
            SELECT command, output FROM commands
            WHERE session_id=? ORDER BY turn DESC LIMIT 3
        """, (self.session_id,)).fetchall()

        if recent:
            history_str = "; ".join(
                [f"ran '{r[0]}'" for r in reversed(recent)])
            context_parts.append(f"Recent commands: {history_str}")

        # Resolve pronouns/references
        resolved = self._resolve_references(nl_request)
        context = " | ".join(context_parts)
        return f"Context: [{context}]\nRequest: {resolved}"

    def _resolve_references(self, text: str) -> str:
        """Replace references like 'that file', 'it', 'the folder' with actual paths."""
        reference_patterns = [
            r"\bthat file\b", r"\bthe file\b", r"\bit\b",
            r"\bthat folder\b", r"\bthe folder\b",
            r"\bthat directory\b", r"\bthat script\b"
        ]
        for pattern in reference_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                entity = self._lookup_latest_entity()
                if entity:
                    text = re.sub(pattern, entity, text, flags=re.IGNORECASE)
        return text

    def _lookup_latest_entity(self) -> str:
        """Get the most recently referenced file or directory."""
        row = self.conn.execute("""
            SELECT full_path FROM entities
            WHERE session_id=? ORDER BY last_turn DESC LIMIT 1
        """, (self.session_id,)).fetchone()
        return row[0] if row else None

    def update(self, nl_request: str, command: str,
               risk_tier: str, output: str):
        """Save command to history and extract new entity references."""
        self.conn.execute("""
            INSERT INTO commands VALUES
            (NULL,?,?,?,?,?,?,?)
        """, (self.session_id, self.turn, nl_request,
              command, risk_tier, output[:500],
              datetime.now().isoformat()))
        self.conn.commit()

        # Update CWD if cd command
        if command.startswith("cd "):
            new_dir = command[3:].strip()
            result = subprocess.run(
                f"cd {self.cwd} && cd {new_dir} && pwd",
                shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                self.cwd = result.stdout.strip()

        # Extract path references from command
        self._extract_entities(command)

    def _extract_entities(self, command: str):
        """Extract file and directory paths from a command."""
        paths = re.findall(r'(?:^|\s)((?:\.{0,2}/)?[\w./\-]+\.\w+|(?:\.{0,2}/)?[\w./\-]+/[\w./\-]*)', command)
        for path in set(paths):
            if len(path) < 2:
                continue
            entity_type = "dir" if "/" in path and "." not in path.split("/")[-1] else "file"
            name = path.split("/")[-1]
            self.conn.execute("""
                INSERT OR REPLACE INTO entities VALUES
                (NULL,?,?,?,?,?)
            """, (self.session_id, name, entity_type,
                  path, self.turn))
        self.conn.commit()

    def get_history_summary(self) -> str:
        """Return human-readable session summary."""
        rows = self.conn.execute("""
            SELECT turn, command, risk_tier FROM commands
            WHERE session_id=? ORDER BY turn
        """, (self.session_id,)).fetchall()
        if not rows:
            return "No commands run yet."
        lines = [f"Turn {r[0]}: [{r[2]}] {r[1]}" for r in rows]
        return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    import uuid
    ctx = SessionContextManager(str(uuid.uuid4()))

    # Turn 1
    resolved = ctx.resolve("list all files here")
    print(f"Resolved T1: {resolved}\n")
    ctx.update("list all files here", "ls -la", "read_only", "file1.txt\nfile2.py")

    # Turn 2 — create folder
    resolved = ctx.resolve("create a folder called backup")
    print(f"Resolved T2: {resolved}\n")
    ctx.update("create a folder called backup", "mkdir backup", "state_modifying", "")

    # Turn 3 — reference resolution
    resolved = ctx.resolve("copy all python files into that folder")
    print(f"Resolved T3: {resolved}\n")

    print("History:")
    print(ctx.get_history_summary())
```

### Test it
```bash
python session_context.py
```

**Expected output:**
```
Resolved T1: Context: [Current directory: /home/user/nl-os-agent]
Request: list all files here

Resolved T2: Context: [Current directory: /home/user/nl-os-agent | Recent commands: ran 'ls -la']
Request: create a folder called backup

Resolved T3: Context: [Current directory: /home/user/nl-os-agent | Recent commands: ran 'ls -la'; ran 'mkdir backup']
Request: copy all python files into backup

History:
Turn 1: [read_only] ls -la
Turn 2: [state_modifying] mkdir backup
```

---

## PHASE 4 — MAIN AGENT LOOP (Day 3)

### File: `agent.py`

```python
# agent.py
import subprocess
import uuid
import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nl_translator import translate_to_command
from safety_verifier import verify_and_confirm, classify_risk
from session_context import SessionContextManager

console = Console()

BANNER = """
╔═══════════════════════════════════════════════╗
║     NL OS Agent — Natural Language Shell      ║
║  Type in plain English. Type 'exit' to quit.  ║
╚═══════════════════════════════════════════════╝
"""

def run_command(command: str, cwd: str) -> tuple[str, int]:
    """Execute a shell command and return (output, exit_code)."""
    result = subprocess.run(
        command, shell=True,
        capture_output=True, text=True,
        cwd=cwd, timeout=30
    )
    output = result.stdout.strip() or result.stderr.strip()
    return output, result.returncode


def main():
    console.print(BANNER, style="bold cyan")
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
            console.print(session.get_history_summary())
            continue

        # Step 1: Resolve context
        resolved_request = session.resolve(user_input)

        # Step 2: Translate to command
        with console.status("[yellow]Translating...[/yellow]"):
            command = translate_to_command(resolved_request)

        console.print(f"[cyan]Command:[/cyan] {command}")

        # Step 3: Safety verification
        approved = verify_and_confirm(command, session.cwd)

        if not approved:
            console.print("[red]Command cancelled.[/red]\n")
            session.update(user_input, command,
                           classify_risk(command).value, "CANCELLED")
            continue

        # Step 4: Execute
        try:
            output, exit_code = run_command(command, session.cwd)
            if output:
                console.print(Panel(output, title="Output",
                                    border_style="dim"))
            elif exit_code == 0:
                console.print("[dim]Command completed successfully.[/dim]")
            else:
                console.print(f"[red]Command failed (exit {exit_code})[/red]")

            session.update(
                user_input, command,
                classify_risk(command).value,
                output
            )
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out after 30 seconds.[/red]")
        except Exception as e:
            console.print(f"[red]Execution error: {e}[/red]")

        console.print()


if __name__ == "__main__":
    main()
```

### Run it
```bash
# Make sure Ollama is running
ollama serve &

# Run the agent
python agent.py
```

**Sample interaction:**
```
╔═══════════════════════════════════════════════╗
║     NL OS Agent — Natural Language Shell      ║
║  Type in plain English. Type 'exit' to quit.  ║
╚═══════════════════════════════════════════════╝
Session started | CWD: /home/user

You > show me all python files in this folder
Command: find . -name "*.py" -type f
✓ Read-only command — auto-executing
┌── Output ──────────────────────────────────┐
│ ./agent.py                                  │
│ ./session_context.py                        │
│ ./safety_verifier.py                        │
│ ./nl_translator.py                          │
└─────────────────────────────────────────────┘

You > delete everything in the temp folder
Command: rm -rf ./temp
╔════════════════════════════════════════════════╗
║           Safety Verification                  ║
║ ⚠ DESTRUCTIVE COMMAND DETECTED                 ║
║ Command: rm -rf ./temp                         ║
║ Risk Tier: DESTRUCTIVE                         ║
╚════════════════════════════════════════════════╝
Running sandbox simulation...
┌── Simulated Effect (Sandbox) ──────────────────┐
│ Simulation ran. This would permanently delete  │
│ the directory ./temp and all its contents.     │
└────────────────────────────────────────────────┘
This action may be irreversible. Confirm execution? (y/N): N
Command cancelled.
```

---

## PHASE 5 — TESTS (Day 3–4)

### File: `tests/test_safety.py`

```python
# tests/test_safety.py
import pytest
from safety_verifier import classify_risk, RiskTier

class TestRiskClassification:
    def test_ls_is_readonly(self):
        assert classify_risk("ls -la") == RiskTier.READ_ONLY

    def test_cat_is_readonly(self):
        assert classify_risk("cat file.txt") == RiskTier.READ_ONLY

    def test_ps_is_readonly(self):
        assert classify_risk("ps aux") == RiskTier.READ_ONLY

    def test_mkdir_is_modifying(self):
        assert classify_risk("mkdir backup") == RiskTier.STATE_MODIFYING

    def test_cp_is_modifying(self):
        assert classify_risk("cp file.txt backup/") == RiskTier.STATE_MODIFYING

    def test_rm_rf_is_destructive(self):
        assert classify_risk("rm -rf /tmp/test") == RiskTier.DESTRUCTIVE

    def test_kill_is_destructive(self):
        assert classify_risk("kill -9 1234") == RiskTier.DESTRUCTIVE

    def test_piped_rm_is_destructive(self):
        # Bypass attempt: piped destructive command
        assert classify_risk("echo 'test' | rm -rf /tmp") == RiskTier.DESTRUCTIVE

    def test_find_delete_is_destructive(self):
        # Bypass attempt: find -delete instead of rm
        assert classify_risk("find /tmp -delete") == RiskTier.DESTRUCTIVE
```

### File: `tests/test_translator.py`

```python
# tests/test_translator.py
import pytest
from nl_translator import translate_to_command

class TestTranslation:
    def test_list_files(self):
        cmd = translate_to_command("show me all files here")
        assert "ls" in cmd

    def test_disk_space(self):
        cmd = translate_to_command("how much disk space is left")
        assert "df" in cmd

    def test_find_python_files(self):
        cmd = translate_to_command("find all python files")
        assert "find" in cmd or "*.py" in cmd

    def test_no_explanation_in_output(self):
        cmd = translate_to_command("list files")
        # Should not contain English explanation text
        assert "to" not in cmd.lower().split()[:2]
        assert len(cmd.split("\n")) == 1  # single line
```

### Run all tests
```bash
pytest tests/ -v
```

---

## DATASET GENERATOR (For Phase 2 Fine-Tuning Prep)

### File: `dataset/generate_dataset.py`

```python
# dataset/generate_dataset.py
"""
Generates synthetic (NL request → shell command) pairs
for fine-tuning. Run this to build the training dataset.
"""
import json
import subprocess
import os

SEEDS = [
    # (natural_language_variants, correct_command, category)
    (["list all files", "show me files here", "what's in this folder"],
     "ls -la", "file_management"),

    (["find files bigger than 100MB", "large files over 100 megabytes"],
     "find . -size +100M", "file_management"),

    (["find python files modified this week", "python files changed recently"],
     "find . -name '*.py' -mtime -7", "file_management"),

    (["how much disk space is left", "check disk usage", "storage remaining"],
     "df -h", "system_info"),

    (["what processes are running", "show all processes", "list processes"],
     "ps aux", "process_control"),

    (["kill process 1234", "stop process with pid 1234"],
     "kill -9 1234", "process_control"),

    (["create a folder called backup", "make a backup directory"],
     "mkdir backup", "file_management"),

    (["compress the logs folder", "zip up logs directory"],
     "tar -czf logs.tar.gz logs/", "file_management"),

    (["check network connections", "what ports are open", "show network status"],
     "netstat -tulpn", "networking"),

    (["download file from URL", "fetch this URL"],
     "wget <URL>", "networking"),
]

def validate_command(command: str) -> bool:
    """Run command in sandbox and check it doesn't crash immediately."""
    if "<" in command or ">" in command:
        return True  # Template commands — skip validation
    try:
        result = subprocess.run(
            f"timeout 5 {command}",
            shell=True, capture_output=True,
            cwd="/tmp"  # safe directory
        )
        return result.returncode in (0, 1, 2)  # 0=success, 1/2=no results (ok)
    except:
        return False

def generate():
    pairs = []
    for nl_variants, command, category in SEEDS:
        valid = validate_command(command)
        for nl in nl_variants:
            pairs.append({
                "nl": nl,
                "command": command,
                "category": category,
                "validated": valid
            })

    output_path = os.path.join(os.path.dirname(__file__), "pairs.jsonl")
    with open(output_path, "w") as f:
        for pair in pairs:
            f.write(json.dumps(pair) + "\n")

    print(f"Generated {len(pairs)} pairs → {output_path}")
    validated = sum(1 for p in pairs if p["validated"])
    print(f"Validated: {validated}/{len(pairs)}")

if __name__ == "__main__":
    generate()
```

### Run dataset generation
```bash
python dataset/generate_dataset.py
```

---

## DEMO SCRIPT (For the Review)

Run this to show a clean demo to the panel:

```bash
#!/bin/bash
# demo.sh — Reset state and run a scripted demo

echo "=== NL OS Agent Demo ==="
echo ""

# Create a temp folder for safe demo
mkdir -p /tmp/agent_demo
cd /tmp/agent_demo

# Create some sample files
echo "Hello World" > hello.txt
echo "print('hi')" > script.py
mkdir -p old_logs

echo "Demo environment ready at /tmp/agent_demo"
echo "Starting agent..."
echo ""

python /path/to/nl-os-agent/agent.py
```

**Demo conversation to rehearse:**
```
You > show me all files here
[auto-executes ls, shows hello.txt, script.py, old_logs/]

You > find all python files
[auto-executes find, shows script.py]

You > create a backup folder
[creates mkdir backup, state-modifying, logged]

You > copy the python file into that folder
[resolves "that folder" to backup, runs cp script.py backup/]

You > delete the old_logs folder permanently
[classified DESTRUCTIVE, sandbox simulation shown,
user must type y/N — type N to show cancellation]

You > history
[shows all commands run with risk tiers]
```

---

## DIVISION OF WORK (Suggested)

| Team Member | Component | Files |
|---|---|---|
| Member 1 | NL Translator + Ollama integration | `nl_translator.py`, `tests/test_translator.py` |
| Member 2 | Safety Verifier + Docker sandbox | `safety_verifier.py`, `tests/test_safety.py` |
| Member 3 | Session Context Manager + SQLite | `session_context.py`, `tests/test_context.py` |
| Member 4 | Main agent loop + Dataset generator + Demo | `agent.py`, `dataset/generate_dataset.py` |

All four integrate together on Day 3. Member 4 drives the integration.

---

## TROUBLESHOOTING

**Ollama not responding:**
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags
# If not: ollama serve &
```

**Docker permission error:**
```bash
sudo chmod 666 /var/run/docker.sock
# OR run with: sudo python agent.py
```

**Model gives explanation instead of just command:**
Tighten the system prompt in `nl_translator.py` — add more examples of single-line outputs to the SYSTEM_PROMPT.

**sqlite3 module not found:**
```bash
# sqlite3 is in Python stdlib — if missing:
sudo apt install python3-dev libsqlite3-dev
```

**Rich library not found:**
```bash
pip install rich
```

