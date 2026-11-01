#!/usr/bin/env python3
"""
scripts/make_finetune_dataset.py - Dataset Generator for Task E5 (8k+ Examples).

Generates training and validation datasets in JSONL format for fine-tuning
Nemotron 3 Nano / small LLMs via Unsloth.

Key features required by Research Plan:
1. Multi-Template Families:
   - File manipulation, navigation, and storage
   - Process management and signals
   - Git workflow and version control
   - Networking, diagnostics, and ports
   - System monitoring and environment
   - Packaging, docker, and build tools
2. Cross-Environment & Cross-Shell Conditioning:
   - Bash (Linux/macOS POSIX paths)
   - PowerShell (Windows cmdlets and path styles)
3. Ambiguity & Clarification Handling:
   - Commands requiring clarification emit `{"command": null, "clarifying_question": "..."}`
4. Target Entity Extraction:
   - Generates explicit `targets` list for static effect verification.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from typing import Any

SHELLS = [
    {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/developer/project"},
    {"os": "macOS 14 Sonoma", "shell": "zsh", "path_style": "posix", "cwd": "/Users/developer/workspace"},
    {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\Users\\developer\\project"},
]

# -----------------------------------------------------------------------------
# Base Templates: (Request Pattern, Bash Command, PowerShell Command, Targets, Family)
# -----------------------------------------------------------------------------
CORE_TEMPLATES = [
    # File Management
    (
        "list all files including hidden ones in current directory",
        "ls -la",
        "Get-ChildItem -Force",
        ["."],
        "file_ops",
    ),
    (
        "find all files larger than 100MB",
        "find . -type f -size +100M",
        "Get-ChildItem -Recurse | Where-Object {{ $_.Length -gt 100MB }}",
        ["."],
        "file_ops",
    ),
    (
        "create a new directory named {dirname}",
        "mkdir -p {dirname}",
        "New-Item -ItemType Directory -Force -Path '{dirname}'",
        ["{dirname}"],
        "file_ops",
    ),
    (
        "delete the directory {dirname} recursively",
        "rm -rf {dirname}",
        "Remove-Item -Recurse -Force -Path '{dirname}'",
        ["{dirname}"],
        "file_ops",
    ),
    (
        "copy file {src} to {dst}",
        "cp {src} {dst}",
        "Copy-Item -Path '{src}' -Destination '{dst}'",
        ["{src}", "{dst}"],
        "file_ops",
    ),
    (
        "move file {src} to {dst}",
        "mv {src} {dst}",
        "Move-Item -Path '{src}' -Destination '{dst}'",
        ["{src}", "{dst}"],
        "file_ops",
    ),
    (
        "show the first 20 lines of {filename}",
        "head -n 20 {filename}",
        "Get-Content '{filename}' -TotalCount 20",
        ["{filename}"],
        "file_ops",
    ),
    (
        "search for text '{pattern}' across all files in src",
        "grep -rn '{pattern}' src/",
        "Select-String -Path 'src\\*' -Pattern '{pattern}'",
        ["src/"],
        "file_ops",
    ),
    (
        "compress folder {dirname} into tar archive",
        "tar -czf {dirname}.tar.gz {dirname}/",
        "Compress-Archive -Path '{dirname}' -DestinationPath '{dirname}.zip'",
        ["{dirname}", "{dirname}.tar.gz"],
        "file_ops",
    ),
    # Process Control
    (
        "display top running processes sorted by memory",
        "ps aux --sort=-%mem | head -n 15",
        "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 15",
        [],
        "process_control",
    ),
    (
        "find process ID listening on port {port}",
        "lsof -ti:{port}",
        "(Get-NetTCPConnection -LocalPort {port}).OwningProcess",
        [],
        "process_control",
    ),
    (
        "terminate process with PID {pid}",
        "kill -9 {pid}",
        "Stop-Process -Id {pid} -Force",
        [],
        "process_control",
    ),
    (
        "kill all running python processes",
        "pkill -f python3",
        "Stop-Process -Name python -Force",
        [],
        "process_control",
    ),
    # Git & Version Control
    (
        "show current git branch and short status",
        "git status -s",
        "git status -s",
        [],
        "git_vcs",
    ),
    (
        "switch to a new branch called {branch}",
        "git checkout -b {branch}",
        "git checkout -b {branch}",
        [],
        "git_vcs",
    ),
    (
        "discard all local uncommitted changes",
        "git reset --hard HEAD",
        "git reset --hard HEAD",
        [],
        "git_vcs",
    ),
    (
        "show the last 5 git commits with oneline summary",
        "git log -n 5 --oneline",
        "git log -n 5 --oneline",
        [],
        "git_vcs",
    ),
    # System & Environment
    (
        "check remaining disk space on primary drive",
        "df -h /",
        "Get-PSDrive -PSProvider FileSystem",
        ["/"],
        "sys_info",
    ),
    (
        "show current system uptime and load average",
        "uptime",
        "(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime",
        [],
        "sys_info",
    ),
    (
        "display current logged in username",
        "whoami",
        "whoami",
        [],
        "sys_info",
    ),
    (
        "show all environment variables",
        "env",
        "Get-ChildItem Env:",
        [],
        "sys_info",
    ),
    # Networking
    (
        "test connectivity to {domain} with 4 pings",
        "ping -c 4 {domain}",
        "Test-Connection -ComputerName '{domain}' -Count 4",
        [],
        "networking",
    ),
    (
        "download file from {url} and save as {filename}",
        "curl -L '{url}' -o '{filename}'",
        "Invoke-WebRequest -Uri '{url}' -OutFile '{filename}'",
        ["{filename}"],
        "networking",
    ),
    (
        "list all active listening network sockets",
        "ss -tuln",
        "Get-NetTCPConnection -State Listen",
        [],
        "networking",
    ),
]

# Ambiguous requests that require clarification instead of guessing
CLARIFICATION_TEMPLATES = [
    (
        "delete the database",
        "Which database would you like to delete? Found 'dev.db', 'staging.db', and 'production.db'.",
        "clarification",
    ),
    (
        "deploy the app",
        "Which target environment should I deploy to: staging or production?",
        "clarification",
    ),
    (
        "clean up the build files",
        "Would you like to delete only compiler artifacts (dist/) or complete cache dependencies (node_modules/)?",
        "clarification",
    ),
    (
        "kill the server",
        "Multiple servers are running (Flask on 5000, Vite on 3000). Which port or PID should be stopped?",
        "clarification",
    ),
    (
        "reset the repository",
        "Do you want a soft reset keeping changes staged, or a hard reset permanently wiping uncommitted changes?",
        "clarification",
    ),
    (
        "restart the service",
        "Which service do you wish to restart (nginx, postgresql, or redis)?",
        "clarification",
    ),
]

DIRNAMES = ["backup", "logs", "dist", "temp", "cache", "build", "archive", "exports", "releases", "debug"]
FILENAMES = ["app.log", "config.yaml", "report.pdf", "data.csv", "index.html", "main.py", "server.js", "docker-compose.yml"]
PATTERNS = ["TODO", "FIXME", "error", "SECRET_KEY", "deprecated", "Exception", "Traceback", "password"]
BRANCHES = ["feature-cache", "hotfix-auth", "bugfix-leak", "refactor-api", "experiment-e5", "release-v1"]
DOMAINS = ["google.com", "github.com", "cloudflare.com", "huggingface.co", "wikipedia.org"]
PORTS = [8080, 3000, 5000, 5432, 27017, 8000, 9090]


def format_example(env_meta: dict[str, str], request_text: str, command: str | None, targets: list[str], clarifying_question: str | None, family: str) -> dict[str, Any]:
    """Format one conversation training record following ShareGPT / OpenAI chat schema."""
    l1_text = (
        f"OS: {env_meta['os']}\n"
        f"Shell: {env_meta['shell']}\n"
        f"Path Style: {env_meta['path_style']}\n"
        f"CWD: {env_meta['cwd']}"
    )

    system_content = (
        "You are an AI-native operating system shell agent.\n"
        "Convert the user's natural language request into an exact terminal command matching the active environment.\n"
        "Output ONLY a single valid JSON object with keys 'command', 'targets', and 'clarifying_question'.\n\n"
        f"Environment:\n{l1_text}"
    )

    assistant_obj = {
        "command": command,
        "targets": targets,
        "clarifying_question": clarifying_question,
    }

    return {
        "family": family,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": request_text},
            {"role": "assistant", "content": json.dumps(assistant_obj)},
        ],
    }


def generate_finetune_dataset(total_count: int = 8000, val_ratio: float = 0.1) -> tuple[int, int]:
    """Generate training and validation splits for fine-tuning."""
    records: list[dict[str, Any]] = []

    # 1. Generate regular command examples across shells and variables
    num_regular = int(total_count * 0.90)
    for _ in range(num_regular):
        env = random.choice(SHELLS)
        req_tmpl, bash_cmd, ps_cmd, targets_tmpl, fam = random.choice(CORE_TEMPLATES)

        # Randomize template variables
        dname = random.choice(DIRNAMES)
        fname = random.choice(FILENAMES)
        patt = random.choice(PATTERNS)
        branch = random.choice(BRANCHES)
        dom = random.choice(DOMAINS)
        port = random.choice(PORTS)
        pid = random.randint(1000, 65535)

        sub_kwargs = {
            "dirname": dname,
            "filename": fname,
            "src": fname,
            "dst": f"{dname}_{fname}",
            "pattern": patt,
            "branch": branch,
            "domain": dom,
            "port": port,
            "pid": pid,
            "url": f"https://{dom}/{fname}",
        }

        req = req_tmpl.format(**sub_kwargs)
        if env["shell"] == "powershell":
            cmd = ps_cmd.format(**sub_kwargs)
        else:
            cmd = bash_cmd.format(**sub_kwargs)

        targets = [t.format(**sub_kwargs) for t in targets_tmpl]

        rec = format_example(
            env_meta=env,
            request_text=req,
            command=cmd,
            targets=targets,
            clarifying_question=None,
            family=fam,
        )
        records.append(rec)

    # 2. Generate clarification examples
    num_clarification = total_count - num_regular
    for _ in range(num_clarification):
        env = random.choice(SHELLS)
        req, question, fam = random.choice(CLARIFICATION_TEMPLATES)
        rec = format_example(
            env_meta=env,
            request_text=req,
            command=None,
            targets=[],
            clarifying_question=question,
            family=fam,
        )
        records.append(rec)

    random.shuffle(records)

    val_count = int(total_count * val_ratio)
    train_count = total_count - val_count

    train_data = records[:train_count]
    val_data = records[train_count:]

    os.makedirs("datasets", exist_ok=True)
    with open("datasets/finetune_train.jsonl", "w", encoding="utf-8") as f:
        for r in train_data:
            f.write(json.dumps(r) + "\n")

    with open("datasets/finetune_val.jsonl", "w", encoding="utf-8") as f:
        for r in val_data:
            f.write(json.dumps(r) + "\n")

    print(f"Generated {train_count} training examples in 'datasets/finetune_train.jsonl'.")
    print(f"Generated {val_count} validation examples in 'datasets/finetune_val.jsonl'.")
    return train_count, val_count


def main():
    parser = argparse.ArgumentParser(description="Generate fine-tuning dataset for Unsloth.")
    parser.add_argument("--count", type=int, default=8000, help="Total number of examples (default 8000)")
    parser.add_argument("--val-ratio", type=float, default=0.1, help="Validation split fraction (default 0.1)")
    args = parser.parse_args()

    generate_finetune_dataset(total_count=args.count, val_ratio=args.val_ratio)


if __name__ == "__main__":
    main()
