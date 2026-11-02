"""
tests/test_toctou.py - Acceptance Tests for Time-Of-Check to Time-Of-Use (TOCTOU) Defenses.

Verifies:
1. When propose() evaluates a glob command on an initial directory, an empty match
   yields tier="read_only".
2. When directory contents change before commit() (files added), commit re-analysis
   detects the mutation, changes the tier to "destructive", and blocks execution.
3. When a target path is replaced with a symlink pointing to a protected system path,
   commit re-analysis detects protected_hit=True, escalates tier to "privileged", and blocks execution.
4. When symlinks or target files are manipulated during race windows, session state
   and runner execution remain completely uncompromised.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Ensure src is on sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
for p in (root_dir, src_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.context.state import SessionState
from nlagent.daemon.commit import commit
from nlagent.daemon.propose import propose
from nlagent.safety.effects import analyse, tier_from_effect


class TestTOCTOUDefenses(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.session = SessionState(cwd=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_toctou_files_added_escalates_tier_and_blocks_commit(self):
        """
        Scenario:
        1. Directory starts with no *.bak files.
        2. propose() for 'rm -f *.bak' assesses zero deletes -> tier='read_only'.
        3. Between propose and commit, files matching *.bak are added.
        4. commit() re-runs effects.analyse, detects tier escalation ('destructive'),
           and blocks execution with returncode 126.
        """
        # Step 1: Create initial safe directory contents (no .bak files)
        readme = Path(self.temp_dir) / "README.md"
        readme.write_text("# Test Project", encoding="utf-8")

        mock_backend = MagicMock()
        mock_backend.complete.return_value = MagicMock(
            content='{"command": "rm -f *.bak", "targets": ["*.bak"], "explanation": "clean backup files"}'
        )

        # Step 2: Call propose()
        proposal = propose(
            request="delete all backup files matching *.bak",
            session_state=self.session,
            backend=mock_backend,
        )

        # Invariant: Because no .bak files exist, static analysis sees 0 deletions -> read_only
        self.assertEqual(proposal["command"], "rm -f *.bak")
        self.assertEqual(proposal["tier"], "read_only")
        self.assertEqual(proposal["effect"].deletes, [])

        # Step 3: TOCTOU Mutation - files matching the glob appear before commit()
        important_bak = Path(self.temp_dir) / "critical_database.bak"
        important_bak.write_text("CRITICAL CUSTOMER DATA", encoding="utf-8")
        secret_bak = Path(self.temp_dir) / "credentials.bak"
        secret_bak.write_text("API_SECRET_KEY=12345", encoding="utf-8")

        # Mock runner to ensure execution is never actually invoked
        mock_runner = MagicMock()

        # Step 4: Call commit()
        output, exit_code, fresh_effect = commit(
            proposal=proposal,
            session_state=self.session,
            runner=mock_runner,
        )

        # Assert: Re-analysis caught the filesystem change
        fresh_tier = tier_from_effect(fresh_effect)
        self.assertEqual(fresh_tier, "destructive")
        self.assertNotEqual(fresh_tier, proposal["tier"])
        self.assertGreater(len(fresh_effect.deletes), 0)

        # Assert: Execution was blocked due to TOCTOU tier escalation
        self.assertEqual(exit_code, 126)
        self.assertIn("Blocked: TOCTOU state mutation detected", output)
        self.assertIn("read_only", output)
        self.assertIn("destructive", output)

        # Invariant: Mock runner was NEVER executed, and files remain intact
        mock_runner.assert_not_called()
        self.assertTrue(important_bak.exists())
        self.assertTrue(secret_bak.exists())

    def test_toctou_symlink_swap_to_protected_system_path_blocks_execution(self):
        """
        Scenario:
        1. A command targets a local scratch directory 'temp_data'.
        2. At propose time, 'temp_data' does not exist -> assessed as read_only.
        3. Before commit, an attacker creates 'temp_data' as a symlink pointing to
           a protected operating system path (e.g. C:\\Windows or /etc).
        4. commit() re-analysis resolves realpath, flags protected_hit=True,
           escalates tier to 'privileged', and immediately aborts execution.
        """
        mock_backend = MagicMock()
        mock_backend.complete.return_value = MagicMock(
            content='{"command": "rm -rf temp_*", "targets": ["temp_*"], "explanation": "remove temporary folders"}'
        )

        proposal = propose(
            request="remove temp data folder matching temp_*",
            session_state=self.session,
            backend=mock_backend,
        )

        # Invariant: At propose time, no temp_* files exist -> read_only
        self.assertEqual(proposal["tier"], "read_only")

        # Select protected path based on platform
        is_windows = sys.platform == "win32"
        protected_target = "C:\\Windows" if is_windows else "/etc"

        # Create symlink matching the glob, pointing to protected system path
        symlink_path = os.path.join(self.temp_dir, "temp_syslink")
        try:
            os.symlink(protected_target, symlink_path, target_is_directory=True)
        except OSError:
            # Fallback if unprivileged on older OS configuration
            self.skipTest("Symlink creation requires administrative privileges on this host")

        mock_runner = MagicMock()

        output, exit_code, fresh_effect = commit(
            proposal=proposal,
            session_state=self.session,
            runner=mock_runner,
            allow_privileged=False,
        )

        # Assert: Fresh effect caught the protected path hit
        self.assertTrue(fresh_effect.protected_hit)
        fresh_tier = tier_from_effect(fresh_effect)
        self.assertEqual(fresh_tier, "privileged")

        # Assert: Commit blocked execution
        self.assertEqual(exit_code, 126)
        self.assertIn("Blocked", output)
        mock_runner.assert_not_called()

    def test_toctou_glob_expansion_tracks_newly_populated_targets(self):
        """
        Scenario:
        Verify that commit() re-analysis accurately computes bytes affected
        and target list when new files populate a wildcard glob.
        """
        # Initially empty logs directory
        log_dir = Path(self.temp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        eff_before = analyse("rm -f logs/*.log", cwd=self.temp_dir)
        self.assertEqual(eff_before.deletes, [])
        self.assertEqual(eff_before.bytes_affected, 0)
        self.assertEqual(tier_from_effect(eff_before), "read_only")

        # TOCTOU drift: 3 large log files created
        file_a = log_dir / "audit.log"
        file_b = log_dir / "system.log"
        file_a.write_text("A" * 5000, encoding="utf-8")
        file_b.write_text("B" * 5000, encoding="utf-8")

        # Re-analysis immediately before commit
        eff_after = analyse("rm -f logs/*.log", cwd=self.temp_dir)
        self.assertEqual(len(eff_after.deletes), 2)
        self.assertEqual(eff_after.bytes_affected, 10000)
        self.assertEqual(tier_from_effect(eff_after), "destructive")

    def test_toctou_override_allows_authorized_execution(self):
        """
        Verify that allow_tier_escalation=True allows execution when the user
        explicitly approves the updated tier.
        """
        proposal = {
            "command": "rm -f *.cache",
            "targets": ["*.cache"],
            "tier": "read_only",
            "request": "clean cache",
        }

        # Add cache file
        cache_file = Path(self.temp_dir) / "app.cache"
        cache_file.write_text("cached data", encoding="utf-8")

        mock_runner = MagicMock(return_value=("removed cache", 0))

        output, exit_code, eff = commit(
            proposal=proposal,
            session_state=self.session,
            runner=mock_runner,
            allow_tier_escalation=True,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "removed cache")
        self.assertEqual(tier_from_effect(eff), "destructive")
        mock_runner.assert_called_once()


    def test_toctou_combined_add_files_and_symlink_swap_blocks_execution(self):
        """
        Direct acceptance test for the prompt scenario:
        1. Create initial files.
        2. Call propose() for a read-only-looking glob command ('rm -f run_*.log').
        3. Change directory contents: add files and replace/create a path with a symlink to protected path.
        4. Call commit(); assert that commit re-analysis changes the tier and blocks execution.
        """
        # 1. Create initial files
        (Path(self.temp_dir) / "app.py").write_text("print('running')", encoding="utf-8")
        (Path(self.temp_dir) / "data.csv").write_text("id,val\n1,10", encoding="utf-8")

        mock_backend = MagicMock()
        mock_backend.complete.return_value = MagicMock(
            content='{"command": "rm -f run_*.log", "targets": ["run_*.log"], "explanation": "clean run logs"}'
        )

        # 2. Call propose() for read-only-looking glob
        proposal = propose(
            request="clean up run logs matching run_*.log",
            session_state=self.session,
            backend=mock_backend,
        )
        self.assertEqual(proposal["tier"], "read_only")
        self.assertEqual(proposal["effect"].deletes, [])

        # 3. Change directory contents:
        # 3a. Add a new file matching the glob
        normal_log = Path(self.temp_dir) / "run_normal.log"
        normal_log.write_text("ordinary log content", encoding="utf-8")

        # 3b. Create a path as a symlink pointing to protected OS directory
        is_windows = sys.platform == "win32"
        protected_target = "C:\\Windows" if is_windows else "/etc"
        symlink_log = os.path.join(self.temp_dir, "run_system.log")
        try:
            os.symlink(protected_target, symlink_log, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation requires administrative privileges on this host")

        # 4. Call commit()
        mock_runner = MagicMock()
        output, exit_code, fresh_effect = commit(
            proposal=proposal,
            session_state=self.session,
            runner=mock_runner,
            allow_privileged=False,
        )

        # Assert: Re-analysis changed the tier and blocked execution
        fresh_tier = tier_from_effect(fresh_effect)
        self.assertEqual(fresh_tier, "privileged")
        self.assertNotEqual(fresh_tier, proposal["tier"])
        self.assertTrue(fresh_effect.protected_hit)
        self.assertEqual(exit_code, 126)
        self.assertIn("Blocked", output)
        mock_runner.assert_not_called()
        self.assertTrue(normal_log.exists())


if __name__ == "__main__":
    unittest.main()
