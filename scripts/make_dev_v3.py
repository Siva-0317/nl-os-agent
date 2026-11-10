"""
scripts/make_dev_v3.py - Generate 100-case dev dataset for E5 benchmark and loop tuning.

Ensures:
- Strictly separate from sealed test set (zero data contamination).
- N >= 100 cases spanning bash, powershell, and cmd.
- Diverse task families: file operations, read-only inspection, search, and ambiguity/clarifications.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def build_dev_v3_cases() -> list[dict]:
    cases = []
    
    # 1. Bash File Ops (20 cases)
    bash_file_ops = [
        ("mkdir -p build/dist", "create build and dist directories", {"README.md": "hi"}, False),
        ("rm -f temp_*.log", "delete all temporary log files", {"temp_1.log": "a", "temp_2.log": "b", "main.py": "x"}, False),
        ("cp config.yaml config.yaml.bak", "backup config.yaml to config.yaml.bak", {"config.yaml": "k: v"}, False),
        ("mv old_report.txt new_report.txt", "rename old_report.txt to new_report.txt", {"old_report.txt": "data"}, False),
        ("touch .env", "create an empty .env file", {"main.py": "1"}, False),
        ("rm -rf cache", "delete the cache directory and its contents", {"cache/a.tmp": "1", "cache/b.tmp": "2"}, False),
        ("cp -r src/ backup_src/", "copy entire src directory to backup_src", {"src/app.py": "print(1)"}, False),
        ("rm -f *.bak", "remove all backup files ending in .bak", {"a.bak": "1", "b.bak": "2", "c.txt": "3"}, False),
        ("mkdir -p logs/archive", "make logs archive subfolder", {"logs/run.log": "ok"}, False),
        ("mv draft.md published.md", "rename draft.md to published.md", {"draft.md": "# Doc"}, False),
        ("cp data.csv data_copy.csv", "duplicate data.csv as data_copy.csv", {"data.csv": "a,b,c\n1,2,3\n"}, False),
        ("rm -f *.tmp", "delete temporary files ending with .tmp", {"f1.tmp": "1", "f2.tmp": "2", "f3.py": "3"}, False),
        ("mkdir -p tests/unit tests/e2e", "create tests unit and e2e directories", {"conftest.py": "# test"}, False),
        ("mv test.log test_old.log", "rename test.log to test_old.log", {"test.log": "err"}, False),
        ("cp schema.sql schema.v1.sql", "copy schema.sql to schema.v1.sql", {"schema.sql": "CREATE TABLE t;"}, False),
        ("rm -f debug.out", "delete debug.out file", {"debug.out": "stack trace"}, False),
        ("mkdir -p static/css static/js", "make static css and static js folders", {"index.html": "<html/>"}, False),
        ("cp info.json info.backup", "backup info.json to info.backup", {"info.json": "{}"}, False),
        ("mv staging.db prod.db", "rename staging.db to prod.db", {"staging.db": "sqlite"}, False),
        ("rm -f *.swp", "clean up vim swap files ending in .swp", {".main.py.swp": "swap", "main.py": "code"}, False),
    ]
    for idx, (cmd, req, fix, ro) in enumerate(bash_file_ops, 1):
        cases.append({
            "case_id": f"dev_{len(cases)+1:03d}",
            "family": "file_ops",
            "shell": "bash",
            "env": {"os": "Ubuntu 22.04 LTS", "shell": "bash", "path_style": "posix", "cwd": "/home/dev/repo"},
            "request": req,
            "reference_command": cmd,
            "fixture_spec": fix,
            "is_read_only": ro,
            "is_clarification": False,
        })

    # 2. PowerShell File Ops (20 cases)
    ps_file_ops = [
        ("New-Item -ItemType Directory -Force -Path 'build\\dist'", "create build\\dist directory", {"README.md": "hi"}, False),
        ("Remove-Item -Force temp_*.log", "delete all temporary log files", {"temp_1.log": "a", "temp_2.log": "b", "main.py": "x"}, False),
        ("Copy-Item -Path 'config.yaml' -Destination 'config.yaml.bak'", "backup config.yaml to config.yaml.bak", {"config.yaml": "k: v"}, False),
        ("Move-Item -Path 'old_report.txt' -Destination 'new_report.txt'", "rename old_report.txt to new_report.txt", {"old_report.txt": "data"}, False),
        ("New-Item -ItemType File -Force -Path '.env'", "create an empty .env file", {"main.py": "1"}, False),
        ("Remove-Item -Recurse -Force 'cache'", "delete cache folder and contents", {"cache/a.tmp": "1", "cache/b.tmp": "2"}, False),
        ("Copy-Item -Recurse -Path 'src' -Destination 'backup_src'", "copy src folder to backup_src", {"src/app.py": "print(1)"}, False),
        ("Remove-Item -Force *.bak", "remove all backup files with .bak extension", {"a.bak": "1", "b.bak": "2", "c.txt": "3"}, False),
        ("New-Item -ItemType Directory -Force -Path 'logs\\archive'", "make logs archive folder", {"logs/run.log": "ok"}, False),
        ("Move-Item -Path 'draft.md' -Destination 'published.md'", "rename draft.md to published.md", {"draft.md": "# Doc"}, False),
        ("Copy-Item -Path 'data.csv' -Destination 'data_copy.csv'", "duplicate data.csv as data_copy.csv", {"data.csv": "a,b,c\n1,2,3\n"}, False),
        ("Remove-Item -Force *.tmp", "delete temporary files with .tmp extension", {"f1.tmp": "1", "f2.tmp": "2", "f3.py": "3"}, False),
        ("New-Item -ItemType Directory -Force -Path 'tests\\unit'", "create tests unit directory", {"conftest.py": "# test"}, False),
        ("Move-Item -Path 'test.log' -Destination 'test_old.log'", "rename test.log to test_old.log", {"test.log": "err"}, False),
        ("Copy-Item -Path 'schema.sql' -Destination 'schema.v1.sql'", "copy schema.sql to schema.v1.sql", {"schema.sql": "CREATE TABLE t;"}, False),
        ("Remove-Item -Force 'debug.out'", "delete debug.out file", {"debug.out": "stack trace"}, False),
        ("New-Item -ItemType Directory -Force -Path 'static\\css'", "make static css folder", {"index.html": "<html/>"}, False),
        ("Copy-Item -Path 'info.json' -Destination 'info.backup'", "backup info.json to info.backup", {"info.json": "{}"}, False),
        ("Move-Item -Path 'staging.db' -Destination 'prod.db'", "rename staging.db to prod.db", {"staging.db": "sqlite"}, False),
        ("Remove-Item -Force *.swp", "clean up swap files ending in .swp", {".main.py.swp": "swap", "main.py": "code"}, False),
    ]
    for idx, (cmd, req, fix, ro) in enumerate(ps_file_ops, 1):
        cases.append({
            "case_id": f"dev_{len(cases)+1:03d}",
            "family": "file_ops",
            "shell": "powershell",
            "env": {"os": "Windows 11", "shell": "powershell", "path_style": "windows", "cwd": "C:\\repo"},
            "request": req,
            "reference_command": cmd,
            "fixture_spec": fix,
            "is_read_only": ro,
            "is_clarification": False,
        })

    # 3. Read-Only Inspection (Bash & PowerShell & Cmd) (30 cases)
    read_only_cases = [
        ("ls -la", "list all files including hidden ones", {"README.md": "a", ".env": "b"}, "bash"),
        ("Get-ChildItem -Force", "show all files including hidden items", {"README.md": "a", ".env": "b"}, "powershell"),
        ("dir /a", "list all directory entries including hidden", {"README.md": "a", ".env": "b"}, "cmd"),
        ("cat package.json", "display contents of package.json", {"package.json": "{\"name\": \"pkg\"}"}, "bash"),
        ("Get-Content 'package.json'", "read package.json file", {"package.json": "{\"name\": \"pkg\"}"}, "powershell"),
        ("type package.json", "show package.json content", {"package.json": "{\"name\": \"pkg\"}"}, "cmd"),
        ("head -n 5 server.log", "show first 5 lines of server.log", {"server.log": "1\n2\n3\n4\n5\n6\n7\n"}, "bash"),
        ("Get-Content 'server.log' -Head 5", "view top 5 lines of server.log", {"server.log": "1\n2\n3\n4\n5\n6\n7\n"}, "powershell"),
        ("tail -n 10 access.log", "view last 10 lines of access.log", {"access.log": "".join(f"line {i}\n" for i in range(20))}, "bash"),
        ("Get-Content 'access.log' -Tail 10", "display last 10 lines of access.log", {"access.log": "".join(f"line {i}\n" for i in range(20))}, "powershell"),
        ("grep -i 'error' app.log", "search for error in app.log ignoring case", {"app.log": "info\nERROR occurred\nwarn\n"}, "bash"),
        ("Select-String -Path 'app.log' -Pattern 'error'", "find pattern error in app.log", {"app.log": "info\nERROR occurred\nwarn\n"}, "powershell"),
        ("findstr /i 'error' app.log", "search error in app.log case-insensitive", {"app.log": "info\nERROR occurred\nwarn\n"}, "cmd"),
        ("wc -l data.csv", "count lines in data.csv", {"data.csv": "h1\nr1\nr2\nr3\n"}, "bash"),
        ("(Get-Content 'data.csv').Length", "get line count of data.csv", {"data.csv": "h1\nr1\nr2\nr3\n"}, "powershell"),
        ("ls -lh reports/", "show file sizes in reports directory", {"reports/q1.pdf": "data", "reports/q2.pdf": "data2"}, "bash"),
        ("Get-ChildItem 'reports'", "list files inside reports folder", {"reports/q1.pdf": "data", "reports/q2.pdf": "data2"}, "powershell"),
        ("dir reports", "display files in reports folder", {"reports/q1.pdf": "data", "reports/q2.pdf": "data2"}, "cmd"),
        ("find . -name '*.py'", "find all python files in current tree", {"src/app.py": "1", "tests/t.py": "2"}, "bash"),
        ("Get-ChildItem -Recurse -Filter '*.py'", "search all python files recursively", {"src/app.py": "1", "tests/t.py": "2"}, "powershell"),
        ("cat /etc/os-release", "print os release info", {"etc/os-release": "NAME=Ubuntu"}, "bash"),
        ("pwd", "print current working directory", {"a.txt": "1"}, "bash"),
        ("Get-Location", "get current working directory path", {"a.txt": "1"}, "powershell"),
        ("cd", "print current directory", {"a.txt": "1"}, "cmd"),
        ("diff file1.txt file2.txt", "compare file1.txt and file2.txt", {"file1.txt": "a\n", "file2.txt": "b\n"}, "bash"),
        ("Compare-Object (Get-Content file1.txt) (Get-Content file2.txt)", "compare contents of file1.txt and file2.txt", {"file1.txt": "a\n", "file2.txt": "b\n"}, "powershell"),
        ("df -h", "show disk space usage", {"a.txt": "1"}, "bash"),
        ("Get-PSDrive -PSProvider FileSystem", "check disk space on drives", {"a.txt": "1"}, "powershell"),
        ("git status", "check git repository status", {".git/config": "repo"}, "bash"),
        ("git status", "show git working tree status", {".git/config": "repo"}, "powershell"),
    ]
    for cmd, req, fix, sh in read_only_cases:
        os_name = "Ubuntu 22.04 LTS" if sh == "bash" else "Windows 11"
        pstyle = "posix" if sh == "bash" else "windows"
        cwd = "/home/dev/repo" if sh == "bash" else "C:\\repo"
        cases.append({
            "case_id": f"dev_{len(cases)+1:03d}",
            "family": "read_only",
            "shell": sh,
            "env": {"os": os_name, "shell": sh, "path_style": pstyle, "cwd": cwd},
            "request": req,
            "reference_command": cmd,
            "fixture_spec": fix,
            "is_read_only": True,
            "is_clarification": False,
        })

    # 4. Ambiguity & Clarification Scenarios (15 cases)
    clarification_cases = [
        ("delete the database", "Which database would you like to delete? (e.g. dev.db or prod.db)", {"dev.db": "1", "prod.db": "2"}, "bash"),
        ("clean up the project files", "Which files or directories should be cleaned up?", {"dist/": "bin", "temp/": "tmp"}, "powershell"),
        ("reset repository", "Do you want to reset uncommitted changes or reset to a specific commit?", {".git/config": "repo"}, "bash"),
        ("remove the config", "Which configuration file should be removed? (config.json or config.yaml)", {"config.json": "{}", "config.yaml": "k: v"}, "powershell"),
        ("wipe all outputs", "Please confirm which output folder to wipe (build/ or dist/)", {"build/a.out": "1", "dist/b.out": "2"}, "cmd"),
        ("kill the server process", "Which server process should be terminated? Please specify port or PID.", {"server.py": "app"}, "bash"),
        ("rollback the deployment", "Which deployment version should be restored?", {"deploy.yaml": "v2"}, "powershell"),
        ("delete the test logs", "Which test logs should be deleted? (unit.log or e2e.log)", {"unit.log": "1", "e2e.log": "2"}, "bash"),
        ("purge old records", "Specify the date cutoff or record type to purge.", {"records.db": "1"}, "cmd"),
        ("drop the table", "Which database table should be dropped?", {"db.sqlite": "data"}, "powershell"),
        ("clear the cache files", "Do you want to clear application cache or browser cache?", {"app_cache/": "1", "web_cache/": "2"}, "bash"),
        ("update packages", "Do you want to update pip packages, apt packages, or npm packages?", {"requirements.txt": "flask", "package.json": "{}"}, "bash"),
        ("revert changes", "Do you want to discard unstaged changes or revert the last git commit?", {".git/config": "repo"}, "powershell"),
        ("format the disk", "Formatting will destroy all data. Please specify the exact drive letter to format.", {"data/disk.img": "1"}, "cmd"),
        ("uninstall dependencies", "Which dependency group would you like to remove?", {"node_modules/": "1"}, "bash"),
    ]
    for req, prompt, fix, sh in clarification_cases:
        os_name = "Ubuntu 22.04 LTS" if sh == "bash" else "Windows 11"
        pstyle = "posix" if sh == "bash" else "windows"
        cwd = "/home/dev/repo" if sh == "bash" else "C:\\repo"
        cases.append({
            "case_id": f"dev_{len(cases)+1:03d}",
            "family": "clarification",
            "shell": sh,
            "env": {"os": os_name, "shell": sh, "path_style": pstyle, "cwd": cwd},
            "request": req,
            "reference_command": None,
            "fixture_spec": fix,
            "is_read_only": True,
            "is_clarification": True,
            "clarification_prompt": prompt,
        })

    # 5. Additional System, Archive & Process Ops to reach 100+ cases (15 cases)
    extra_ops = [
        ("tar -czf archive.tar.gz data/", "compress data folder into archive.tar.gz", {"data/a.txt": "1", "data/b.txt": "2"}, "bash", False),
        ("Compress-Archive -Path 'data' -DestinationPath 'archive.zip'", "zip data folder to archive.zip", {"data/a.txt": "1", "data/b.txt": "2"}, "powershell", False),
        ("tar -xzf archive.tar.gz", "extract archive.tar.gz", {"archive.tar.gz": "content"}, "bash", False),
        ("Expand-Archive -Path 'archive.zip' -DestinationPath '.'", "extract archive.zip to current folder", {"archive.zip": "content"}, "powershell", False),
        ("chmod +x run.sh", "make run.sh executable", {"run.sh": "#!/bin/bash\necho ok"}, "bash", False),
        ("echo 'test' > output.txt", "write test to output.txt", {"README.md": "a"}, "bash", False),
        ("Set-Content -Path 'output.txt' -Value 'test'", "write test into output.txt", {"README.md": "a"}, "powershell", False),
        ("echo test > output.txt", "output test to output.txt", {"README.md": "a"}, "cmd", False),
        ("find . -size +10M", "find files larger than 10MB", {"big.bin": "x"}, "bash", True),
        ("Get-ChildItem -Recurse | Where-Object Length -gt 10MB", "find files greater than 10MB", {"big.bin": "x"}, "powershell", True),
        ("curl -s https://example.com/api", "fetch api response from example.com", {}, "bash", True),
        ("Invoke-RestMethod -Uri 'https://example.com/api'", "query api endpoint at example.com", {}, "powershell", True),
        ("python3 -m unittest discover", "run python unit tests", {"test_app.py": "import unittest"}, "bash", True),
        ("pytest", "execute pytest suite", {"test_app.py": "def test_ok(): pass"}, "powershell", True),
        ("npm test", "run npm test script", {"package.json": "{\"scripts\": {\"test\": \"echo ok\"}}"}, "bash", True),
    ]
    for cmd, req, fix, sh, ro in extra_ops:
        os_name = "Ubuntu 22.04 LTS" if sh == "bash" else "Windows 11"
        pstyle = "posix" if sh == "bash" else "windows"
        cwd = "/home/dev/repo" if sh == "bash" else "C:\\repo"
        cases.append({
            "case_id": f"dev_{len(cases)+1:03d}",
            "family": "process_mgmt" if ro else "file_ops",
            "shell": sh,
            "env": {"os": os_name, "shell": sh, "path_style": pstyle, "cwd": cwd},
            "request": req,
            "reference_command": cmd,
            "fixture_spec": fix,
            "is_read_only": ro,
            "is_clarification": False,
        })

    return cases


def main():
    cases = build_dev_v3_cases()
    out_path = Path("datasets/dev_v3.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    print(f"Generated {len(cases)} dev benchmark test cases in: {out_path}")


if __name__ == "__main__":
    main()
