"""
tests/test_cli.py - Subprocess Acceptance Tests for nlctl CLI and Shell Hooks.

Verifies:
1. 'nlctl propose' outputs command/preview, records to SQLite, and NEVER executes commands.
2. 'nlctl commit' executes command, triggers quarantine, and respects safety blocks (exit code 126).
3. 'nlctl commit --emit-shell' outputs raw shell command for parent shell eval.
4. 'nlctl undo' restores deleted/modified files byte-identically.
5. 'nlctl history' lists historical proposals and execution statuses.
6. Strict Invariant: Multiple destructive proposes never touch the filesystem without commit.

All tests run strictly inside tempfile directories via subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src is on sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
for p in (root_dir, src_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.cli.db import SQLiteStateStore


class TestNlctlCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir) / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(self.temp_dir) / "test_state.db"
        self.quarantine_dir = Path(self.temp_dir) / "test_quarantine"
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{root_dir}"
        self.env["NLAGENT_STATE_DB"] = str(self.db_path)
        self.env["NLAGENT_QUARANTINE_DIR"] = str(self.quarantine_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_nlctl(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run nlctl CLI as a subprocess using python -m nlagent.cli.main."""
        cmd = [sys.executable, "-m", "nlagent.cli.main"] + args
        return subprocess.run(
            cmd,
            cwd=cwd or str(self.workspace),
            capture_output=True,
            text=True,
            env=self.env,
            timeout=15,
        )

    def test_propose_read_only_invariants(self):
        """Verify propose outputs preview, persists record, and NEVER runs command."""
        proc = self._run_nlctl([
            "propose", "list all files",
            "--mock", "ls -la",
            "--db", str(self.db_path),
        ])

        self.assertEqual(proc.returncode, 0)
        self.assertIn("ls -la", proc.stdout)
        self.assertIn("[READ-ONLY]", proc.stderr)
        self.assertIn("Proposal ID:", proc.stderr)

        # Verify proposal recorded in SQLite
        store = SQLiteStateStore(db_path=self.db_path)
        record = store.get_latest_proposal()
        self.assertIsNotNone(record)
        self.assertEqual(record["command"], "ls -la")
        self.assertEqual(record["tier"], "read_only")
        self.assertEqual(record["status"], "proposed")

    def test_propose_destructive_invariants_no_execution(self):
        """CRITICAL: Propose on destructive query must NEVER delete files before commit."""
        sensitive_file = self.workspace / "important.log"
        sensitive_file.write_text("SENSITIVE LOG DATA 2026", encoding="utf-8")

        proc = self._run_nlctl([
            "propose", "delete important log file",
            "--mock", "rm -f important.log",
            "--db", str(self.db_path),
        ])

        self.assertEqual(proc.returncode, 0)
        self.assertIn("[DESTRUCTIVE]", proc.stderr)
        self.assertIn("DELETE", proc.stderr)

        # INVARIANT: File MUST NOT be deleted by propose!
        self.assertTrue(sensitive_file.exists())
        self.assertEqual(sensitive_file.read_text(encoding="utf-8"), "SENSITIVE LOG DATA 2026")

        store = SQLiteStateStore(db_path=self.db_path)
        record = store.get_latest_proposal()
        self.assertEqual(record["status"], "proposed")

    def test_propose_emit_shell_flag(self):
        """Verify --emit-shell produces eval wrapper for shell hook."""
        proc = self._run_nlctl([
            "propose", "show system info",
            "--mock", "uname -a",
            "--emit-shell",
            "--db", str(self.db_path),
        ])

        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.startswith('eval "$(nlctl commit p_'))
        self.assertTrue(proc.stdout.endswith('--emit-shell)"'))

    def test_commit_executes_destructive_and_quarantines(self):
        """Verify commit executes command, shadows to quarantine, and updates status."""
        test_file = self.workspace / "audit.log"
        test_file.write_text("AUDIT TRAIL RECORD", encoding="utf-8")

        # 1. Propose
        proc_prop = self._run_nlctl([
            "propose", "delete audit log",
            "--mock", "rm -f audit.log",
            "--json",
            "--db", str(self.db_path),
        ])
        self.assertEqual(proc_prop.returncode, 0)
        prop_data = json.loads(proc_prop.stdout.strip())
        prop_id = prop_data["id"]

        # Invariant: File still exists
        self.assertTrue(test_file.exists())

        # 2. Commit
        proc_commit = self._run_nlctl([
            "commit", prop_id,
            "--db", str(self.db_path),
        ])
        self.assertEqual(proc_commit.returncode, 0)

        # File is now deleted
        self.assertFalse(test_file.exists())

        # Verify SQLite status
        store = SQLiteStateStore(db_path=self.db_path)
        record = store.get_proposal(prop_id)
        self.assertEqual(record["status"], "committed")
        self.assertEqual(record["exit_code"], 0)

        # 3. Undo restores file
        proc_undo = self._run_nlctl([
            "undo", prop_id,
            "--db", str(self.db_path),
        ])
        self.assertEqual(proc_undo.returncode, 0)
        self.assertIn("Undo successful", proc_undo.stdout)

        # File restored byte-identically
        self.assertTrue(test_file.exists())
        self.assertEqual(test_file.read_text(encoding="utf-8"), "AUDIT TRAIL RECORD")

    def test_commit_emit_shell_outputs_raw_command(self):
        """Verify commit --emit-shell outputs raw command for parent shell eval without child execution."""
        proc_prop = self._run_nlctl([
            "propose", "change into backup folder",
            "--mock", "cd backup",
            "--json",
            "--db", str(self.db_path),
        ])
        prop_id = json.loads(proc_prop.stdout.strip())["id"]

        proc_commit = self._run_nlctl([
            "commit", prop_id,
            "--emit-shell",
            "--db", str(self.db_path),
        ])
        self.assertEqual(proc_commit.returncode, 0)
        self.assertEqual(proc_commit.stdout.strip(), "cd backup")

    def test_commit_blocks_privileged_system_paths(self):
        """Verify commit blocks privileged operations with returncode 126."""
        proc_prop = self._run_nlctl([
            "propose", "remove system hosts",
            "--mock", "rm -rf /etc/hosts",
            "--json",
            "--db", str(self.db_path),
        ])
        prop_id = json.loads(proc_prop.stdout.strip())["id"]

        proc_commit = self._run_nlctl([
            "commit", prop_id,
            "--db", str(self.db_path),
        ])

        self.assertEqual(proc_commit.returncode, 126)
        self.assertIn("Blocked", proc_commit.stdout + proc_commit.stderr)

    def test_history_subcommand(self):
        """Verify nlctl history displays past proposals."""
        self._run_nlctl(["propose", "list files", "--mock", "ls", "--db", str(self.db_path)])
        self._run_nlctl(["propose", "delete logs", "--mock", "rm *.log", "--db", str(self.db_path)])

        proc_hist = self._run_nlctl(["history", "--db", str(self.db_path)])
        self.assertEqual(proc_hist.returncode, 0)
        self.assertIn("ID", proc_hist.stdout)
        self.assertIn("COMMAND", proc_hist.stdout)
        self.assertIn("ls", proc_hist.stdout)
        self.assertIn("rm *.log", proc_hist.stdout)

        # JSON history
        proc_json = self._run_nlctl(["history", "--json", "--db", str(self.db_path)])
        self.assertEqual(proc_json.returncode, 0)
        data = json.loads(proc_json.stdout.strip())
        self.assertEqual(len(data), 2)

    def test_stress_invariant_no_command_runs_without_commit(self):
        """Strict Stress Test: Running 5 destructive proposals never alters files until committed."""
        vault = self.workspace / "secure_vault.dat"
        initial_payload = b"CRITICAL SECURE ENCRYPTED PAYLOAD 2026"
        vault.write_bytes(initial_payload)

        destructive_queries = [
            ("delete vault", "rm -f secure_vault.dat"),
            ("truncate vault", "cat /dev/null > secure_vault.dat"),
            ("shred vault", "shred -u secure_vault.dat"),
            ("unlink vault", "unlink secure_vault.dat"),
            ("zero out vault", "> secure_vault.dat"),
        ]

        # Call propose for all 5 queries
        for req, cmd in destructive_queries:
            proc = self._run_nlctl([
                "propose", req,
                "--mock", cmd,
                "--db", str(self.db_path),
            ])
            self.assertEqual(proc.returncode, 0)
            # Invariant check after EACH propose
            self.assertTrue(vault.exists())
            self.assertEqual(vault.read_bytes(), initial_payload)

        # File is still 100% intact
        self.assertEqual(vault.read_bytes(), initial_payload)


if __name__ == "__main__":
    unittest.main()

