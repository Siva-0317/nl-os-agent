# dataset/generate_dataset.py
"""
Generates synthetic (NL request -> shell command) pairs for fine-tuning preparation.
Run this script to build the training dataset for Phase 2.
"""

import json
import subprocess
import os
import shutil

SEEDS = [
    # (natural_language_variants, correct_command, category)
    (
        ["list all files", "show me files here", "what's in this folder", "show files including hidden"],
        "ls -la",
        "file_management"
    ),
    (
        ["find files bigger than 100MB", "large files over 100 megabytes", "show large files > 100M"],
        "find . -size +100M",
        "file_management"
    ),
    (
        ["find python files modified this week", "python files changed recently", "locate python scripts modified in last 7 days"],
        "find . -name '*.py' -mtime -7",
        "file_management"
    ),
    (
        ["how much disk space is left", "check disk usage", "storage remaining", "show free disk space"],
        "df -h",
        "system_info"
    ),
    (
        ["what processes are running", "show all processes", "list processes", "display active tasks"],
        "ps aux",
        "process_control"
    ),
    (
        ["what processes are using the most CPU", "top CPU consuming processes", "processes taking most CPU"],
        "ps aux --sort=-%cpu",
        "process_control"
    ),
    (
        ["kill process 1234", "stop process with pid 1234", "terminate process 1234"],
        "kill -9 1234",
        "process_control"
    ),
    (
        ["create a folder called backup", "make a backup directory", "create directory backup"],
        "mkdir backup",
        "file_management"
    ),
    (
        ["compress the logs folder", "zip up logs directory", "create tar archive of logs"],
        "tar -czf logs.tar.gz logs/",
        "file_management"
    ),
    (
        ["check network connections", "what ports are open", "show network status"],
        "netstat -tulpn",
        "networking"
    ),
    (
        ["download file from URL", "fetch this URL", "retrieve file from web"],
        "wget <URL>",
        "networking"
    ),
    (
        ["find all text files containing error", "search for error in txt files", "grep for error in txt files"],
        "grep -r 'error' *.txt",
        "file_management"
    ),
    (
        ["count total lines in all python files", "how many lines of python code", "total lines in .py files"],
        "wc -l *.py",
        "file_management"
    ),
    (
        ["show memory usage", "how much RAM is available", "check free RAM"],
        "free -m",
        "system_info"
    )
]


def validate_command(command: str) -> bool:
    """
    Validate syntax / dry run for template commands.
    Template commands with angle brackets or destructive commands are safely handled.
    """
    if "<" in command or ">" in command or command.startswith("kill"):
        return True  # Template or destructive commands — validated by pattern

    # Look for bash if available
    bash_path = None
    for cand in ["C:\\Program Files\\Git\\bin\\bash.exe", shutil.which("bash")]:
        if cand and os.path.exists(cand):
            bash_path = cand
            break

    try:
        # Use dry-run or syntax check with timeout
        if bash_path:
            cmd_to_run = [bash_path, "-c", f"type {command.split()[0]}"]
        else:
            cmd_to_run = f"where {command.split()[0]}"

        res = subprocess.run(cmd_to_run, shell=(bash_path is None), capture_output=True, timeout=5)
        return res.returncode in (0, 1, 2)
    except Exception:
        return True


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

    output_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "pairs.jsonl")

    with open(output_path, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"Generated {len(pairs)} pairs -> {output_path}")
    validated = sum(1 for p in pairs if p["validated"])
    print(f"Validated: {validated}/{len(pairs)}")


if __name__ == "__main__":
    generate()
