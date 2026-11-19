#!/usr/bin/env python3
"""
scripts/make_finetune_dataset.py - V2 Cross-Shell Dataset Generator for Fine-Tuning.

Generates diverse, leak-checked training and validation datasets (`datasets/finetune_v2_train.jsonl`,
`datasets/finetune_v2_val.jsonl`, or V1 paths via `--version v1`) in OpenAI / Unsloth chat JSONL format:
1. Full 4-Shell Coverage: `powershell` (Windows 11), `cmd` (Windows 11 cmd.exe),
   `bash` (Ubuntu Linux & Git Bash on Windows), and `zsh` (macOS).
2. Prompt & Chat Alignment: Uses exact runtime system prompts (`SHORT_L0_PROMPT`,
   `L0_SYSTEM_PROMPT`, `DEFAULT_L0_SYSTEM_PROMPT`) + deterministic L1 blocks (`format_l1`)
   and compact `STATE` / `<file>` grounding blocks in user messages.
3. Broad Skill & Paraphrase Coverage: 55+ cross-shell templates covering file creation,
   copy/move/rename (`Rename-Item`, `Set-Content`, `Add-Content`), deletion (`Remove-Item`,
   `del`, `rd`, `rm`), inspection (`Get-Content -Head/-Tail/(...).Length`, `Select-String`,
   `findstr`, `Compare-Object`, `Expand-Archive`, `Get-PSDrive`), git, process/ports,
   archives, and HTTP/test runners.
4. Clarification Calibration: Paired ambiguous positives (`command: null`, concrete
   `clarifying_question`) plus specific hard-negatives so the model never guesses on
   vague deletions and never over-clarifies on explicit targets.
5. Partition Hygiene: Enforces zero exact-query overlap against `datasets/dev_v3.jsonl`
   and `datasets/sealed_test.jsonl`, and uses disjoint phrasing/entity banks between
   train and val splits to measure real generalization.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure src and root are importable when invoked as a script
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
for _p in (_ROOT, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from nlagent.bench.e0 import L0_SYSTEM_PROMPT
from nlagent.context.layout import format_l1
from nlagent.daemon.propose import DEFAULT_L0_SYSTEM_PROMPT

SHORT_L0_PROMPT = (
    "You are an AI-native operating system shell agent.\n"
    "Convert the user's natural language request into an exact terminal command matching the active environment.\n"
    "Output ONLY a single valid JSON object with keys 'command', 'targets', and 'clarifying_question' "
    "(use relative paths in cwd unless an absolute path is requested; set command to null and provide clarifying_question only when ambiguous)."
)

# -----------------------------------------------------------------------------
# Shell Environments
# -----------------------------------------------------------------------------

WINDOWS_TOOLS = ["cmd", "curl", "findstr", "git", "pip", "powershell", "python", "robocopy", "tar"]
POSIX_TOOLS = ["awk", "bash", "cat", "curl", "docker", "find", "git", "grep", "pip", "python3", "sed", "tar", "zsh"]

ENV_PROFILES: list[tuple[dict[str, str], float]] = [
    ({"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\Users\\alex\\workspace", "user": "alex"}, 0.40),
    ({"os": "Windows 11", "shell": "cmd", "path_style": "windows", "cwd": "C:\\work\\app", "user": "alex"}, 0.20),
    ({"os": "Ubuntu 24.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/alex/workspace", "user": "alex"}, 0.27),
    ({"os": "macOS 14 Sonoma", "shell": "zsh", "path_style": "posix", "cwd": "/Users/alex/projects", "user": "alex"}, 0.08),
    ({"os": "Windows 11", "shell": "bash", "path_style": "posix", "cwd": "/c/Users/alex/workspace", "user": "alex"}, 0.05),
]


@dataclass(frozen=True)
class CommandTemplate:
    family: str
    train_requests: tuple[str, ...]
    val_requests: tuple[str, ...]
    bash_cmd: str | None
    ps_cmd: str | None
    cmd_cmd: str | None
    targets: tuple[str, ...] = ()
    grounded_file_keys: tuple[str, ...] = ()


# -----------------------------------------------------------------------------
# Disjoint Variable Pools (also kept distinct from dev_v3 & sealed_test tokens)
# -----------------------------------------------------------------------------

VARS_TRAIN = {
    "dirname": ["artifacts_out", "staging_tmp", "metrics", "snapshots", "bundle_dir", "exports_v2", "obj_cache", "scratch_pad"],
    "subdir": ["daily", "v2", "raw", "processed", "nested", "archive_2026"],
    "sibling_a": ["assets", "fixtures", "handlers", "models_out"],
    "sibling_b": ["locales", "schemas", "helpers", "views_out"],
    "ext": ["log", "cache", "bak2", "dump", "part", "orig"],
    "prefix": ["worker", "trace", "session", "batch", "cron"],
    "filename": ["notes.md", "settings.toml", "events.log", "metrics.csv", "manifest.json", "worker.py", "compose.yaml", "orders.sql"],
    "src_file": ["notes_old.txt", "metrics_raw.csv", "settings_v1.toml", "events_prev.log", "ledger_draft.md", "summary_v1.txt"],
    "dst_file": ["notes_final.txt", "metrics_clean.csv", "settings_prod.toml", "events_cur.log", "ledger_pub.md", "summary_v2.txt"],
    "copy_dst": ["notes.md.copy", "settings.toml.save", "manifest.json.orig", "orders.sql.save"],
    "cmp_a": ["left.cfg", "alpha.txt", "before.ini"],
    "cmp_b": ["right.cfg", "beta.txt", "after.ini"],
    "pattern": ["WARNING", "timeout", "deprecated", "FATAL", "RETRY", "auth_fail"],
    "line_n": [5, 10, 15, 25],
    "size_mb": [25, 50, 100, 250],
    "branch": ["feat-gui", "fix-parser", "chore-cleanup", "rel-candidate", "ops-logging"],
    "domain": ["api.internal.local", "github.com", "pypi.org", "cdn.example.org"],
    "port": [3000, 5432, 6379, 8080, 9000],
    "pid": [2410, 8842, 19304, 41220, 55012],
    "text_val": ["ready", "enabled", "ok_v2", "healthy"],
    "script": ["deploy_step.sh", "setup_env.sh", "verify.sh"],
    "archive_name": ["export_bundle", "pkg_release", "logs_pack"],
}

VARS_VAL = {
    "dirname": ["tmp_exports", "qa_artifacts", "audit_dump", "stage_dir"],
    "subdir": ["monthly", "outbox", "filtered"],
    "sibling_a": ["controllers", "templates"],
    "sibling_b": ["middlewares", "validators"],
    "ext": ["swp2", "dmp", "idx", "lck"],
    "prefix": ["daemon", "ingest", "replay"],
    "filename": ["changelog.txt", "inventory.csv", "trace.log", "pipeline.toml", "users.sql", "runner.py"],
    "src_file": ["old_spec.md", "raw_dump.csv", "v1_rules.ini"],
    "dst_file": ["new_spec.md", "clean_dump.csv", "v2_rules.ini"],
    "copy_dst": ["changelog.txt.copy", "inventory.csv.save", "users.sql.save"],
    "cmp_a": ["env_stage.txt", "spec_a.md"],
    "cmp_b": ["env_prod.txt", "spec_b.md"],
    "pattern": ["CRITICAL", "panic", "refused", "socket_err"],
    "line_n": [8, 12, 20],
    "size_mb": [15, 75, 200],
    "branch": ["bugfix-tray", "feat-installer", "ci-hardening"],
    "domain": ["status.github.com", "registry.npmjs.org", "httpbin.org"],
    "port": [4000, 7070, 8443],
    "pid": [3190, 14220, 39811],
    "text_val": ["active", "verified", "standby"],
    "script": ["migrate_db.sh", "smoke_check.sh"],
    "archive_name": ["backup_pack", "dist_bundle"],
}


# -----------------------------------------------------------------------------
# Expanded Cross-Shell Command Templates (55+ skills)
# -----------------------------------------------------------------------------

COMMAND_TEMPLATES: list[CommandTemplate] = [
    # 1. Directory Creation (Single, Nested, Multiple Siblings)
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "create directory named {dirname}",
            "make a new folder called {dirname}",
            "ensure the {dirname} directory exists",
        ),
        val_requests=(
            "initialize an empty folder named {dirname}",
            "set up a directory called {dirname}",
        ),
        bash_cmd="mkdir -p {dirname}",
        ps_cmd="New-Item -ItemType Directory -Force -Path '{dirname}'",
        cmd_cmd="mkdir {dirname}",
        targets=("{dirname}",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "create nested subdirectory {dirname}/{subdir}",
            "make a {subdir} subfolder inside {dirname}",
            "create {dirname} {subdir} nested path",
        ),
        val_requests=(
            "create subfolder {subdir} inside directory {dirname}",
            "build directory hierarchy {dirname}/{subdir}",
        ),
        bash_cmd="mkdir -p {dirname}/{subdir}",
        ps_cmd="New-Item -ItemType Directory -Force -Path '{dirname}\\{subdir}'",
        cmd_cmd="mkdir {dirname}\\{subdir}",
        targets=("{dirname}/{subdir}",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "create two separate directories {sibling_a} and {sibling_b}",
            "make sibling folders {sibling_a} and {sibling_b}",
        ),
        val_requests=(
            "create both {sibling_a} and {sibling_b} folders in workspace",
        ),
        bash_cmd="mkdir -p {sibling_a} {sibling_b}",
        ps_cmd="New-Item -ItemType Directory -Force -Path '{sibling_a}','{sibling_b}'",
        cmd_cmd="mkdir {sibling_a} {sibling_b}",
        targets=("{sibling_a}", "{sibling_b}"),
    ),
    # 2. File Creation, Writing & Appending
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "create an empty file named {filename}",
            "touch a blank {filename} file",
        ),
        val_requests=(
            "initialize zero-byte file {filename}",
        ),
        bash_cmd="touch {filename}",
        ps_cmd="New-Item -ItemType File -Force -Path '{filename}'",
        cmd_cmd="type nul > {filename}",
        targets=("{filename}",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "write {text_val} into {filename}",
            "save text {text_val} to {filename}",
            "overwrite {filename} with {text_val}",
        ),
        val_requests=(
            "put the string {text_val} inside {filename}",
        ),
        bash_cmd="echo '{text_val}' > {filename}",
        ps_cmd="Set-Content -Path '{filename}' -Value '{text_val}'",
        cmd_cmd="echo {text_val} > {filename}",
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "append {text_val} to the end of {filename}",
            "add line {text_val} into {filename}",
        ),
        val_requests=(
            "append text {text_val} onto {filename}",
        ),
        bash_cmd="echo '{text_val}' >> {filename}",
        ps_cmd="Add-Content -Path '{filename}' -Value '{text_val}'",
        cmd_cmd="echo {text_val} >> {filename}",
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    # 3. Copy, Move & Rename (Explicit Rename-Item coverage!)
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "copy {filename} to {copy_dst}",
            "duplicate {filename} as {copy_dst}",
            "make a backup copy of {filename} named {copy_dst}",
        ),
        val_requests=(
            "clone file {filename} into {copy_dst}",
        ),
        bash_cmd="cp {filename} {copy_dst}",
        ps_cmd="Copy-Item -Path '{filename}' -Destination '{copy_dst}'",
        cmd_cmd="copy /Y {filename} {copy_dst}",
        targets=("{filename}", "{copy_dst}"),
        grounded_file_keys=("filename",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "copy the entire {dirname} directory recursively to {dirname}_bak",
            "recursively copy folder {dirname} into {dirname}_bak",
            "duplicate directory tree {dirname} as {dirname}_bak",
        ),
        val_requests=(
            "back up entire {dirname} folder to {dirname}_bak",
        ),
        bash_cmd="cp -r {dirname}/ {dirname}_bak/",
        ps_cmd="Copy-Item -Recurse -Path '{dirname}' -Destination '{dirname}_bak'",
        cmd_cmd="xcopy /E /I /Y {dirname} {dirname}_bak",
        targets=("{dirname}", "{dirname}_bak"),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "rename {src_file} to {dst_file}",
            "change filename {src_file} to {dst_file}",
            "rename file {src_file} as {dst_file}",
        ),
        val_requests=(
            "rename existing file {src_file} into {dst_file}",
        ),
        bash_cmd="mv {src_file} {dst_file}",
        ps_cmd="Rename-Item -Path '{src_file}' -NewName '{dst_file}'",
        cmd_cmd="ren {src_file} {dst_file}",
        targets=("{src_file}", "{dst_file}"),
        grounded_file_keys=("src_file",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "move {src_file} into {dirname}",
            "relocate file {src_file} to folder {dirname}",
        ),
        val_requests=(
            "transfer {src_file} inside {dirname} directory",
        ),
        bash_cmd="mv {src_file} {dirname}/",
        ps_cmd="Move-Item -Path '{src_file}' -Destination '{dirname}'",
        cmd_cmd="move /Y {src_file} {dirname}\\",
        targets=("{src_file}", "{dirname}"),
        grounded_file_keys=("src_file",),
    ),
    # 4. Deletion & Cleanup (Explicit, non-ambiguous targets)
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "delete the file {filename}",
            "remove {filename} from workspace",
            "permanently erase {filename}",
        ),
        val_requests=(
            "unlink file {filename}",
        ),
        bash_cmd="rm -f {filename}",
        ps_cmd="Remove-Item -Force '{filename}'",
        cmd_cmd="del /f /q {filename}",
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "delete directory {dirname} and all its contents",
            "recursively remove folder {dirname}",
            "remove {dirname} directory recursively",
        ),
        val_requests=(
            "wipe folder {dirname} and everything inside it",
        ),
        bash_cmd="rm -rf {dirname}",
        ps_cmd="Remove-Item -Recurse -Force '{dirname}'",
        cmd_cmd="rd /s /q {dirname}",
        targets=("{dirname}",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "delete all files with .{ext} extension in current folder",
            "remove every .{ext} file here",
            "clean up files ending with .{ext}",
        ),
        val_requests=(
            "purge all .{ext} files in the working directory",
        ),
        bash_cmd="rm -f *.{ext}",
        ps_cmd="Remove-Item -Force *.{ext}",
        cmd_cmd="del /f /q *.{ext}",
        targets=("*.{ext}",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "delete all {prefix} files ending in .{ext}",
            "remove temporary {prefix}_*.{ext} files",
            "clean up all {prefix} .{ext} files",
        ),
        val_requests=(
            "erase {prefix} files with .{ext} extension",
        ),
        bash_cmd="rm -f {prefix}_*.{ext}",
        ps_cmd="Remove-Item -Force {prefix}_*.{ext}",
        cmd_cmd="del /f /q {prefix}_*.{ext}",
        targets=("{prefix}_*.{ext}",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "recursively delete all hidden or normal files ending in .{ext}",
            "find and delete every *.{ext} file including hidden ones",
        ),
        val_requests=(
            "remove all .{ext} files recursively across subdirectories",
        ),
        bash_cmd="find . -name '*.{ext}' -type f -delete",
        ps_cmd="Get-ChildItem -Recurse -Force -Filter '*.{ext}' | Remove-Item -Force",
        cmd_cmd="del /s /f /q /a *.{ext}",
        targets=("*.{ext}",),
    ),
    # 5. Archives & Permissions
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "compress {dirname} folder into archive {archive_name}",
            "zip directory {dirname} to {archive_name}",
        ),
        val_requests=(
            "archive folder {dirname} into {archive_name}",
        ),
        bash_cmd="tar -czf {archive_name}.tar.gz {dirname}/",
        ps_cmd="Compress-Archive -Path '{dirname}' -DestinationPath '{archive_name}.zip'",
        cmd_cmd="tar -czf {archive_name}.tar.gz {dirname}",
        targets=("{dirname}", "{archive_name}"),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "extract {archive_name} archive into current folder",
            "unzip {archive_name} here",
        ),
        val_requests=(
            "unpack archive {archive_name} in working directory",
        ),
        bash_cmd="tar -xzf {archive_name}.tar.gz",
        ps_cmd="Expand-Archive -Path '{archive_name}.zip' -DestinationPath '.'",
        cmd_cmd="tar -xf {archive_name}.zip",
        targets=("{archive_name}",),
    ),
    CommandTemplate(
        family="file_ops",
        train_requests=(
            "make script {script} executable",
            "grant execute permission to {script}",
        ),
        val_requests=(
            "set executable bit on {script}",
        ),
        bash_cmd="chmod +x {script}",
        ps_cmd=None,
        cmd_cmd=None,
        targets=("{script}",),
    ),
    # 6. Read-Only Listing, Content Reading & Head/Tail
    CommandTemplate(
        family="read_only",
        train_requests=(
            "list all non-hidden files in current directory",
            "show files and directories here",
        ),
        val_requests=(
            "display current folder entries",
        ),
        bash_cmd="ls",
        ps_cmd="Get-ChildItem",
        cmd_cmd="dir",
        targets=(".",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "list all files including hidden entries",
            "show hidden dotfiles and regular files here",
        ),
        val_requests=(
            "display every item including hidden files in workspace",
        ),
        bash_cmd="ls -la",
        ps_cmd="Get-ChildItem -Force",
        cmd_cmd="dir /a",
        targets=(".",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "list files inside {dirname} directory",
            "show contents of {dirname} folder",
        ),
        val_requests=(
            "inspect directory listing of {dirname}",
        ),
        bash_cmd="ls -lh {dirname}/",
        ps_cmd="Get-ChildItem '{dirname}'",
        cmd_cmd="dir {dirname}",
        targets=("{dirname}",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "display full contents of {filename}",
            "print text inside {filename}",
            "read file {filename}",
        ),
        val_requests=(
            "show everything inside {filename}",
        ),
        bash_cmd="cat {filename}",
        ps_cmd="Get-Content '{filename}'",
        cmd_cmd="type {filename}",
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "show the first {line_n} lines of {filename}",
            "view top {line_n} lines of {filename}",
        ),
        val_requests=(
            "print leading {line_n} lines from {filename}",
        ),
        bash_cmd="head -n {line_n} {filename}",
        ps_cmd="Get-Content '{filename}' -Head {line_n}",
        cmd_cmd=None,
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "show the last {line_n} lines of {filename}",
            "display trailing {line_n} lines of {filename}",
        ),
        val_requests=(
            "tail last {line_n} lines of {filename}",
        ),
        bash_cmd="tail -n {line_n} {filename}",
        ps_cmd="Get-Content '{filename}' -Tail {line_n}",
        cmd_cmd=None,
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    # 7. Searching, Counting, Comparing, Recursive Find, PWD, Disk Space
    CommandTemplate(
        family="read_only",
        train_requests=(
            "search for pattern {pattern} inside {filename} case-insensitively",
            "find {pattern} in {filename} ignoring case",
        ),
        val_requests=(
            "grep case-insensitively for {pattern} within {filename}",
        ),
        bash_cmd="grep -i '{pattern}' {filename}",
        ps_cmd="Select-String -Path '{filename}' -Pattern '{pattern}'",
        cmd_cmd='findstr /i "{pattern}" {filename}',
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "search recursively for text {pattern} inside {dirname}",
            "find occurrences of {pattern} across all files under {dirname}",
        ),
        val_requests=(
            "scan directory {dirname} for string {pattern}",
        ),
        bash_cmd="grep -rn '{pattern}' {dirname}/",
        ps_cmd="Get-ChildItem -Path '{dirname}' -Recurse | Select-String -Pattern '{pattern}'",
        cmd_cmd='findstr /s /n "{pattern}" {dirname}\\*',
        targets=("{dirname}",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "count number of lines in {filename}",
            "get line count of {filename}",
        ),
        val_requests=(
            "check total lines inside {filename}",
        ),
        bash_cmd="wc -l {filename}",
        ps_cmd="(Get-Content '{filename}').Length",
        cmd_cmd='find /c /v "" {filename}',
        targets=("{filename}",),
        grounded_file_keys=("filename",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "compare {cmp_a} and {cmp_b}",
            "show differences between {cmp_a} and {cmp_b}",
        ),
        val_requests=(
            "diff contents of {cmp_a} against {cmp_b}",
        ),
        bash_cmd="diff {cmp_a} {cmp_b}",
        ps_cmd="Compare-Object (Get-Content '{cmp_a}') (Get-Content '{cmp_b}')",
        cmd_cmd="fc {cmp_a} {cmp_b}",
        targets=("{cmp_a}", "{cmp_b}"),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "find all files matching *.{ext} recursively",
            "search recursively for *.{ext} files",
        ),
        val_requests=(
            "list every *.{ext} file under current tree",
        ),
        bash_cmd="find . -name '*.{ext}'",
        ps_cmd="Get-ChildItem -Recurse -Filter '*.{ext}'",
        cmd_cmd="dir /s /b *.{ext}",
        targets=(".",),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "print current working directory path",
            "show full path of current folder",
        ),
        val_requests=(
            "display present working directory",
        ),
        bash_cmd="pwd",
        ps_cmd="Get-Location",
        cmd_cmd="cd",
        targets=(),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "check available disk space on filesystem drives",
            "show disk usage and free space",
        ),
        val_requests=(
            "display remaining storage space on mounted drives",
        ),
        bash_cmd="df -h",
        ps_cmd="Get-PSDrive -PSProvider FileSystem",
        cmd_cmd="wmic logicaldisk get caption,freespace,size",
        targets=(),
    ),
    # 8. Process, Size Filtering, Network, Git & Test Suites (`process_mgmt` / `git_vcs`)
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "find files larger than {size_mb}MB in workspace",
            "list files greater than {size_mb} megabytes",
        ),
        val_requests=(
            "search recursively for files exceeding {size_mb}MB",
        ),
        bash_cmd="find . -type f -size +{size_mb}M",
        ps_cmd="Get-ChildItem -Recurse -File | Where-Object {{ $_.Length -gt {size_mb}MB }}",
        cmd_cmd=None,
        targets=(".",),
    ),
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "show running processes sorted by memory consumption",
            "list top 15 processes by RAM usage",
        ),
        val_requests=(
            "inspect highest-memory running processes",
        ),
        bash_cmd="ps aux --sort=-%mem | head -n 15",
        ps_cmd="Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 15",
        cmd_cmd="tasklist",
        targets=(),
    ),
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "find process ID listening on port {port}",
            "check which PID uses TCP port {port}",
        ),
        val_requests=(
            "identify process bound to port {port}",
        ),
        bash_cmd="lsof -ti:{port}",
        ps_cmd="(Get-NetTCPConnection -LocalPort {port}).OwningProcess",
        cmd_cmd="netstat -ano | findstr :{port}",
        targets=(),
    ),
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "terminate process with PID {pid}",
            "force stop process ID {pid}",
        ),
        val_requests=(
            "kill running PID {pid}",
        ),
        bash_cmd="kill -9 {pid}",
        ps_cmd="Stop-Process -Id {pid} -Force",
        cmd_cmd="taskkill /F /PID {pid}",
        targets=(),
    ),
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "fetch JSON/API payload from https://{domain}/health",
            "query HTTP endpoint https://{domain}/health",
        ),
        val_requests=(
            "retrieve API response from https://{domain}/status",
        ),
        bash_cmd="curl -s https://{domain}/health",
        ps_cmd="Invoke-RestMethod -Uri 'https://{domain}/health'",
        cmd_cmd="curl -s https://{domain}/health",
        targets=(),
    ),
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "run python pytest test suite",
            "execute all pytest unit tests",
        ),
        val_requests=(
            "run automated tests via pytest",
        ),
        bash_cmd="pytest",
        ps_cmd="pytest",
        cmd_cmd="pytest",
        targets=(),
    ),
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "run python unittest discovery suite",
            "execute standard unittest tests",
        ),
        val_requests=(
            "launch python unittest runner",
        ),
        bash_cmd="python3 -m unittest discover",
        ps_cmd="python -m unittest discover",
        cmd_cmd="python -m unittest discover",
        targets=(),
    ),
    CommandTemplate(
        family="process_mgmt",
        train_requests=(
            "run package.json test command via npm",
            "execute node project tests using npm",
        ),
        val_requests=(
            "run npm test suite",
        ),
        bash_cmd="npm test",
        ps_cmd="npm test",
        cmd_cmd="npm test",
        targets=(),
    ),
    CommandTemplate(
        family="read_only",
        train_requests=(
            "check current git repository working tree status",
            "show git status",
        ),
        val_requests=(
            "display modified files in git repository",
        ),
        bash_cmd="git status",
        ps_cmd="git status",
        cmd_cmd="git status",
        targets=(),
    ),
    CommandTemplate(
        family="git_vcs",
        train_requests=(
            "create and switch to new git branch {branch}",
            "checkout new branch named {branch}",
        ),
        val_requests=(
            "start feature branch {branch} in git",
        ),
        bash_cmd="git checkout -b {branch}",
        ps_cmd="git checkout -b {branch}",
        cmd_cmd="git checkout -b {branch}",
        targets=(),
    ),
    CommandTemplate(
        family="git_vcs",
        train_requests=(
            "show last 5 git commits in short oneline view",
        ),
        val_requests=(
            "list latest 5 commit hashes and titles",
        ),
        bash_cmd="git log -n 5 --oneline",
        ps_cmd="git log -n 5 --oneline",
        cmd_cmd="git log -n 5 --oneline",
        targets=(),
    ),
    CommandTemplate(
        family="git_vcs",
        train_requests=(
            "discard all tracked uncommitted git changes forcefully",
        ),
        val_requests=(
            "hard reset git working tree to HEAD",
        ),
        bash_cmd="git reset --hard HEAD",
        ps_cmd="git reset --hard HEAD",
        cmd_cmd="git reset --hard HEAD",
        targets=(),
    ),
]

# Explicit Windows drive / grounded external path scenarios
WINDOWS_GROUNDED_SCENARIOS: list[tuple[str, str, str, str, list[str]]] = [
    # (shell, request, grounded_path, expected_cmd, targets)
    ("powershell", "list hidden files in System32 folder from C:", "C:/Windows/System32", "Get-ChildItem -Path 'C:\\Windows\\System32' -Hidden", ["C:\\Windows\\System32"]),
    ("powershell", "show hidden entries inside C:\\ProgramData", "C:/ProgramData", "Get-ChildItem -Path 'C:\\ProgramData' -Hidden", ["C:\\ProgramData"]),
    ("powershell", "display hidden items in drive C:", "C:/", "Get-ChildItem -Path 'C:\\' -Hidden", ["C:\\"]),
    ("bash", "list hidden files in System32 folder from C:", "C:/Windows/System32", "ls -la /c/Windows/System32", ["/c/Windows/System32"]),
    ("bash", "show hidden files inside C:\\ProgramData", "C:/ProgramData", "ls -la /c/ProgramData", ["/c/ProgramData"]),
    ("cmd", "list all hidden items in C:\\ProgramData", "C:/ProgramData", "dir /a C:\\ProgramData", ["C:\\ProgramData"]),
    ("cmd", "show hidden files in System32 folder from C:", "C:/Windows/System32", "dir /a C:\\Windows\\System32", ["C:\\Windows\\System32"]),
]

# Ambiguous requests that MUST pause with command=null + non-empty clarifying_question
CLARIFICATION_TRAIN: list[tuple[str, str, list[str]]] = [
    ("delete that database", "Multiple databases exist (qa.db, analytics.db). Which database should be removed?", ["qa.db", "analytics.db"]),
    ("clean up old project artifacts", "Should I remove build artifacts (dist_out/) or cached packages (.cache/)?", ["dist_out/", ".cache/"]),
    ("wipe the output directories", "Found 'artifacts_out/' and 'release_out/'. Which output folder should be wiped?", ["artifacts_out/", "release_out/"]),
    ("remove the config file", "Multiple configuration files found ('settings.toml', 'app.ini'). Which config should be deleted?", ["settings.toml", "app.ini"]),
    ("reset the git repository", "Do you want a soft reset preserving changes, or a hard reset erasing local edits?", [".git/config"]),
    ("delete all log files for the test run", "Found 'unit_run.log' and 'integration_run.log'. Which log file(s) should be deleted?", ["unit_run.log", "integration_run.log"]),
    ("purge stale records", "Specify the target table/file and date cutoff before purging records.", ["archive.sqlite"]),
    ("drop the table", "Which SQL table name should be dropped from the database?", ["local.sqlite"]),
    ("clear application caches", "Should I clear browser cache, build cache, or temp cache files?", ["app_cache/", "web_cache/"]),
    ("upgrade installed packages", "Should I update Python pip packages, Node npm packages, or OS packages?", ["requirements.txt", "package.json"]),
    ("revert my recent changes", "Do you want to discard uncommitted edits in the working tree or revert the latest commit?", [".git/config"]),
    ("format the drive", "Formatting erases all data permanently. Specify the exact drive letter or volume to format.", []),
    ("uninstall dependencies", "Which dependency manager (pip, npm, or system packages) should be uninstalled?", ["package.json", "requirements.txt"]),
    ("restart the background service", "Which service should be restarted (nginx, redis, or postgres)?", []),
    ("stop the web server", "Multiple processes are listening (ports 3000 and 8080). Which PID or port should be stopped?", ["server.py"]),
    ("deploy the application", "Which target environment should be deployed to: staging or production?", ["deploy.yaml"]),
    ("truncate the log", "Found 'access.log' and 'error.log'. Which log file should be truncated?", ["access.log", "error.log"]),
    ("overwrite the backup", "Which backup destination file should be overwritten?", ["backup.zip"]),
]

CLARIFICATION_VAL: list[tuple[str, str, list[str]]] = [
    ("erase the database file", "Which database should I erase ('staging_v2.db' or 'demo.db')?", ["staging_v2.db", "demo.db"]),
    ("remove old configuration", "Do you want to delete 'prod.ini' or 'dev.yaml'?", ["prod.ini", "dev.yaml"]),
    ("wipe out generated outputs", "Specify whether to wipe 'out_bin/' or 'reports_out/'.", ["out_bin/", "reports_out/"]),
    ("kill the running daemon", "Specify the daemon process name or PID to terminate.", []),
    ("purge cached dependencies", "Should I purge Python '.venv/' or Node 'node_modules/'?", [".venv/", "node_modules/"]),
    ("reset workspace changes", "Confirm whether to run a hard reset or restore a single file.", [".git/config"]),
]


# -----------------------------------------------------------------------------
# Heldout Leakage Filter
# -----------------------------------------------------------------------------

def _norm_req(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_heldout_normalized_requests(repo_root: str = _ROOT) -> set[str]:
    """Collect normalized user requests from dev_v3 and sealed_test to enforce zero leakage."""
    heldout: set[str] = set()
    for rel in ("datasets/dev_v3.jsonl", "datasets/sealed_test.jsonl"):
        p = Path(repo_root) / rel
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                req = row.get("request", "")
                if req:
                    heldout.add(_norm_req(req))
    return heldout


def _pick_env(rng: random.Random) -> dict[str, str]:
    r = rng.random()
    acc = 0.0
    for env, weight in ENV_PROFILES:
        acc += weight
        if r <= acc:
            return dict(env)
    return dict(ENV_PROFILES[0][0])


def _render_system_and_user(
    rng: random.Random,
    env_meta: dict[str, str],
    request_text: str,
    grounded_paths: list[str] | None = None,
    state_block: str = "",
) -> tuple[str, str]:
    """Assemble realistic chat system + user messages matching runtime layout.build_prompt."""
    mode_roll = rng.random()
    l1_dict: dict[str, Any] = {
        "cwd": env_meta["cwd"],
        "os": env_meta["os"],
        "path_style": env_meta["path_style"],
        "shell": env_meta["shell"],
    }

    if mode_roll < 0.58:
        # Short prompt layout (mirrors run_e5.py SHORT_L0_PROMPT + format_l1)
        l0 = SHORT_L0_PROMPT
        sys_msg = f"{l0}\n\n{format_l1(l1_dict)}"
    elif mode_roll < 0.80:
        # Full E5 / interactive L0 prompt
        l0 = L0_SYSTEM_PROMPT if rng.random() < 0.5 else DEFAULT_L0_SYSTEM_PROMPT
        if l0 == DEFAULT_L0_SYSTEM_PROMPT:
            l1_dict["available_tools"] = WINDOWS_TOOLS if env_meta["path_style"] == "windows" else POSIX_TOOLS
            l1_dict["user"] = env_meta["user"]
            del l1_dict["cwd"]
        sys_msg = f"{l0}\n\n{format_l1(l1_dict)}"
    else:
        # Grounded Desktop / REPL interactive layout
        l1_dict["available_tools"] = WINDOWS_TOOLS if env_meta["path_style"] == "windows" else POSIX_TOOLS
        l1_dict["user"] = env_meta["user"]
        del l1_dict["cwd"]
        sys_msg = f"{DEFAULT_L0_SYSTEM_PROMPT}\n\n{format_l1(l1_dict)}"

    user_parts: list[str] = []
    if state_block:
        user_parts.append(state_block.strip())
    elif mode_roll >= 0.80:
        user_parts.append(f"STATE cwd={env_meta['cwd']}")

    if grounded_paths:
        file_lines = ["FILES (verified local paths):"] + [f"- <file>{p}</file>" for p in grounded_paths[:5]]
        user_parts.append("\n".join(file_lines))

    user_parts.append(request_text)
    return sys_msg, "\n\n".join(user_parts)


def format_example(
    env_meta: dict[str, str],
    request_text: str,
    command: str | None,
    targets: list[str],
    clarifying_question: str | None,
    family: str,
    system_override: str | None = None,
    user_override: str | None = None,
) -> dict[str, Any]:
    """Format one training/validation record matching OpenAI / ShareGPT chat schema."""
    if system_override is not None:
        sys_content = system_override
    else:
        l1_text = format_l1({
            "cwd": env_meta["cwd"],
            "os": env_meta["os"],
            "path_style": env_meta["path_style"],
            "shell": env_meta["shell"],
        })
        sys_content = f"{SHORT_L0_PROMPT}\n\n{l1_text}"

    user_content = user_override if user_override is not None else request_text
    assistant_obj = {
        "command": command,
        "targets": targets,
        "clarifying_question": clarifying_question,
    }
    return {
        "family": family,
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": json.dumps(assistant_obj)},
        ],
    }


def _build_split_records(
    count: int,
    is_val: bool,
    rng: random.Random,
    heldout: set[str],
) -> list[dict[str, Any]]:
    var_pool = VARS_VAL if is_val else VARS_TRAIN
    clar_bank = CLARIFICATION_VAL if is_val else CLARIFICATION_TRAIN
    records: list[dict[str, Any]] = []

    num_clar = max(1, int(count * 0.09))
    num_win_grounded = max(1, int(count * 0.04))
    num_anaphora = max(1, int(count * 0.04))
    num_regular = count - num_clar - num_win_grounded - num_anaphora

    # 1. Core cross-shell templates
    attempts = 0
    while len(records) < num_regular and attempts < num_regular * 10:
        attempts += 1
        env = _pick_env(rng)
        tmpl = rng.choice(COMMAND_TEMPLATES)

        # Pick matching shell command
        sh = env["shell"]
        cmd_tmpl = tmpl.ps_cmd if sh == "powershell" else (tmpl.cmd_cmd if sh == "cmd" else tmpl.bash_cmd)
        if not cmd_tmpl:
            continue

        subs = {k: rng.choice(vals) for k, vals in var_pool.items()}
        req_choices = tmpl.val_requests if is_val else tmpl.train_requests
        req = rng.choice(req_choices).format(**subs)
        if _norm_req(req) in heldout:
            continue

        cmd = cmd_tmpl.format(**subs)
        tgts = [t.format(**subs) for t in tmpl.targets]
        grounded = [subs[k] for k in tmpl.grounded_file_keys if k in subs] if rng.random() < 0.4 else None
        sys_msg, user_msg = _render_system_and_user(rng, env, req, grounded_paths=grounded)

        records.append(format_example(env, req, cmd, tgts, None, tmpl.family, system_override=sys_msg, user_override=user_msg))

    # 2. Explicit Windows drive / system directory grounding examples
    win_added = 0
    while win_added < num_win_grounded:
        sh, req, gpath, cmd, tgts = rng.choice(WINDOWS_GROUNDED_SCENARIOS)
        if _norm_req(req) in heldout:
            continue
        env = {
            "os": "Windows 11",
            "shell": sh,
            "path_style": "posix" if sh == "bash" else "windows",
            "cwd": "/c/Users/alex/workspace" if sh == "bash" else "C:\\Users\\alex\\workspace",
            "user": "alex",
        }
        l1_dict = {
            "available_tools": WINDOWS_TOOLS if sh != "bash" else POSIX_TOOLS,
            "os": "Windows 11",
            "path_style": env["path_style"],
            "shell": sh,
            "user": "alex",
        }
        sys_msg = f"{DEFAULT_L0_SYSTEM_PROMPT}\n\n{format_l1(l1_dict)}"
        user_msg = f"STATE cwd={env['cwd']}\n\nFILES (verified local paths):\n- <file>{gpath}</file>\n\n{req}"
        records.append(format_example(env, req, cmd, tgts, None, "read_only", system_override=sys_msg, user_override=user_msg))
        win_added += 1

    # 3. Multi-turn anaphora & STATE examples ("delete that file", "inspect that folder")
    ana_added = 0
    while ana_added < num_anaphora:
        env = _pick_env(rng)
        sh = env["shell"]
        fname = rng.choice(var_pool["filename"])
        dst = rng.choice(var_pool["dst_file"])
        op_choice = rng.randrange(3)
        if op_choice == 0:
            req = "delete that file" if not is_val else "remove the referenced file"
            cmd = f"Remove-Item -Force '{fname}'" if sh == "powershell" else (f"del /f /q {fname}" if sh == "cmd" else f"rm -f {fname}")
        elif op_choice == 1:
            req = "display contents of that file" if not is_val else "read the active file"
            cmd = f"Get-Content '{fname}'" if sh == "powershell" else (f"type {fname}" if sh == "cmd" else f"cat {fname}")
        else:
            req = f"rename it to {dst}" if not is_val else f"move that file to {dst}"
            cmd = f"Rename-Item -Path '{fname}' -NewName '{dst}'" if sh == "powershell" else (f"ren {fname} {dst}" if sh == "cmd" else f"mv {fname} {dst}")

        if _norm_req(req) in heldout:
            continue
        state_block = f"STATE cwd={env['cwd']}\nENT f1=./{fname}(last:t1)\nLAST t1: touch {fname} -> created 1"
        sys_msg, user_msg = _render_system_and_user(rng, env, req, grounded_paths=[fname], state_block=state_block)
        records.append(format_example(env, req, cmd, [fname], None, "file_ops", system_override=sys_msg, user_override=user_msg))
        ana_added += 1

    # 4. Clarification calibration examples (command=None, non-null clarifying_question)
    clar_added = 0
    while clar_added < num_clar:
        env = _pick_env(rng)
        req, question, files_hint = rng.choice(clar_bank)
        if _norm_req(req) in heldout:
            continue
        sys_msg, user_msg = _render_system_and_user(rng, env, req, grounded_paths=files_hint or None)
        records.append(format_example(env, req, None, [], question, "clarification", system_override=sys_msg, user_override=user_msg))
        clar_added += 1

    rng.shuffle(records)
    return records[:count]


def generate_finetune_dataset(
    total_count: int = 12000,
    val_ratio: float = 0.1,
    train_out: str = "datasets/finetune_v2_train.jsonl",
    val_out: str = "datasets/finetune_v2_val.jsonl",
    seed: int = 42,
) -> tuple[int, int]:
    """Generate template-disjoint, leak-checked V2 training and validation datasets."""
    rng = random.Random(seed)
    heldout = load_heldout_normalized_requests()

    val_count = int(total_count * val_ratio)
    train_count = total_count - val_count

    train_data = _build_split_records(train_count, is_val=False, rng=rng, heldout=heldout)
    val_data = _build_split_records(val_count, is_val=True, rng=rng, heldout=heldout)

    os.makedirs(os.path.dirname(os.path.abspath(train_out)), exist_ok=True)
    with open(train_out, "w", encoding="utf-8") as f:
        for r in train_data:
            f.write(json.dumps(r) + "\n")

    os.makedirs(os.path.dirname(os.path.abspath(val_out)), exist_ok=True)
    with open(val_out, "w", encoding="utf-8") as f:
        for r in val_data:
            f.write(json.dumps(r) + "\n")

    print(f"Generated {len(train_data)} training examples in '{train_out}'.")
    print(f"Generated {len(val_data)} validation examples in '{val_out}'.")
    print(f"Leakage filter active against {len(heldout)} held-out dev_v3/sealed_test queries.")
    return len(train_data), len(val_data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate cross-shell fine-tuning dataset for Unsloth.")
    parser.add_argument("--count", type=int, default=12000, help="Total number of examples (default 12000)")
    parser.add_argument("--val-ratio", type=float, default=0.1, help="Validation split fraction (default 0.1)")
    parser.add_argument("--train-out", default="datasets/finetune_v2_train.jsonl", help="Output train JSONL path")
    parser.add_argument("--val-out", default="datasets/finetune_v2_val.jsonl", help="Output val JSONL path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    generate_finetune_dataset(
        total_count=args.count,
        val_ratio=args.val_ratio,
        train_out=args.train_out,
        val_out=args.val_out,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
