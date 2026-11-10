"""
tests/test_quarantine.py - Acceptance Tests for Quarantine & Byte-Exact Undo Engine.

Verifies:
1. Delete one file: shadows into quarantine, undo restores byte-identically.
2. Delete directory tree: shadows nested hierarchy, undo restores tree structure and files.
3. Overwrite via mv: shadows target before overwrite, undo restores original contents.
4. Name clash on restore: detects pre-existing target, raises NameClashError or backs up conflict.
5. Undo after partial failure: restores successful items without corrupting state.
6. Payload size threshold (>500 MB): requires explicit confirmation, otherwise raises QuarantineLimitExceededError.
7. Purge: safely cleans expired quarantine records based on retention window.

All tests run strictly inside tempfile directories.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src is on sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
for p in (root_dir, src_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.context.state import SessionState
from nlagent.daemon.commit import commit
from nlagent.safety.effects import Effect, analyse
from nlagent.safety.quarantine import (
    MAX_QUARANTINE_BYTES,
    NameClashError,
    QuarantineLimitExceededError,
    QuarantineNotFoundError,
    calculate_sha1,
    get_quarantine_root,
    purge,
    quarantine_effect,
    undo,
)


class TestQuarantineAndUndo(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir) / "workspace"
        self.quarantine_dir = Path(self.temp_dir) / "quarantine_store"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.session = SessionState(cwd=str(self.workspace))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_delete_one_file_quarantine_and_undo(self):
        """Scenario 1: Delete a single file and verify byte-exact rollback."""
        target_file = self.workspace / "important_report.pdf"
        sample_bytes = b"%PDF-1.4 header\nImportant Financial Data 2026\n%%EOF"
        target_file.write_bytes(sample_bytes)
        orig_sha = calculate_sha1(target_file)

        # 1. Analyse effect of rm command
        eff = analyse("rm important_report.pdf", cwd=str(self.workspace))
        self.assertIn(str(target_file), [os.path.normpath(p) for p in eff.deletes])

        # 2. Quarantine the file
        manifest = quarantine_effect(
            effect=eff,
            request_id="req_del_single",
            command="rm important_report.pdf",
            base_dir=self.quarantine_dir,
        )
        self.assertIsNotNone(manifest)
        self.assertEqual(len(manifest.items), 1)
        self.assertEqual(manifest.items[0].sha1, orig_sha)

        # 3. Simulate file deletion
        target_file.unlink()
        self.assertFalse(target_file.exists())

        # 4. Perform Undo
        undo_res = undo(request_id="req_del_single", base_dir=self.quarantine_dir)
        self.assertTrue(undo_res.success)
        self.assertIn(str(target_file), undo_res.restored_paths)

        # 5. Verify byte-exact restoration
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), sample_bytes)
        self.assertEqual(calculate_sha1(target_file), orig_sha)

    def test_delete_directory_tree_quarantine_and_undo(self):
        """Scenario 2: Delete a nested directory tree and verify full recursive restoration."""
        project_dir = self.workspace / "my_project"
        nested_dir = project_dir / "src" / "modules"
        nested_dir.mkdir(parents=True, exist_ok=True)

        file1 = project_dir / "README.md"
        file2 = nested_dir / "app.py"
        file3 = nested_dir / "config.json"

        file1.write_text("# Project Docs", encoding="utf-8")
        file2.write_text("def run(): return 'running'", encoding="utf-8")
        file3.write_text('{"debug": true}', encoding="utf-8")

        # Effect analysis
        eff = analyse("rm -rf my_project", cwd=str(self.workspace))

        # Quarantine directory tree
        manifest = quarantine_effect(
            effect=eff,
            request_id="req_del_tree",
            command="rm -rf my_project",
            base_dir=self.quarantine_dir,
        )
        self.assertIsNotNone(manifest)

        # Delete entire tree
        shutil.rmtree(project_dir)
        self.assertFalse(project_dir.exists())

        # Undo
        undo_res = undo(request_id="req_del_tree", base_dir=self.quarantine_dir)
        self.assertTrue(undo_res.success)

        # Verify all nested files and directories are restored
        self.assertTrue(project_dir.is_dir())
        self.assertTrue(file1.is_file())
        self.assertTrue(file2.is_file())
        self.assertTrue(file3.is_file())
        self.assertEqual(file1.read_text(encoding="utf-8"), "# Project Docs")
        self.assertEqual(file2.read_text(encoding="utf-8"), "def run(): return 'running'")

    def test_overwrite_via_mv_quarantine_and_undo(self):
        """Scenario 3: Overwriting a file via mv shadows the original target and restores it."""
        dest_file = self.workspace / "settings.json"
        dest_file.write_text('{"version": 1, "owner": "alice"}', encoding="utf-8")
        orig_content = dest_file.read_text(encoding="utf-8")

        new_file = self.workspace / "settings_v2.json"
        new_file.write_text('{"version": 2, "owner": "bob"}', encoding="utf-8")

        # Effect: mv new_file dest_file -> dest_file will be overwritten
        eff = analyse("mv settings_v2.json settings.json", cwd=str(self.workspace))
        self.assertIn(str(dest_file), [os.path.normpath(p) for p in eff.overwrites])

        # Quarantine
        manifest = quarantine_effect(
            effect=eff,
            request_id="req_mv_overwrite",
            command="mv settings_v2.json settings.json",
            base_dir=self.quarantine_dir,
        )
        self.assertIsNotNone(manifest)

        # Execute mv
        shutil.move(str(new_file), str(dest_file))
        self.assertEqual(dest_file.read_text(encoding="utf-8"), '{"version": 2, "owner": "bob"}')

        # Undo
        undo_res = undo(request_id="req_mv_overwrite", base_dir=self.quarantine_dir, on_clash="overwrite")
        self.assertTrue(undo_res.success)

        # Target restored to version 1
        self.assertEqual(dest_file.read_text(encoding="utf-8"), orig_content)

    def test_name_clash_on_restore_raises_and_backs_up(self):
        """Scenario 4: Name clash detection prevents overwriting newly created files."""
        target_file = self.workspace / "notes.txt"
        target_file.write_text("Original Note 1", encoding="utf-8")

        eff = analyse("rm notes.txt", cwd=str(self.workspace))
        quarantine_effect(
            effect=eff,
            request_id="req_clash_test",
            command="rm notes.txt",
            base_dir=self.quarantine_dir,
        )

        # Delete original
        target_file.unlink()

        # Before undo, a new conflicting file is created at the same path!
        target_file.write_text("NEW CONFLICTING FILE CREATED LATER", encoding="utf-8")

        # 1. on_clash="error" must raise NameClashError
        with self.assertRaises(NameClashError):
            undo(request_id="req_clash_test", base_dir=self.quarantine_dir, on_clash="error")

        # 2. on_clash="backup" must preserve the conflicting file and restore the original
        res = undo(request_id="req_clash_test", base_dir=self.quarantine_dir, on_clash="backup")
        self.assertTrue(res.success)
        self.assertEqual(target_file.read_text(encoding="utf-8"), "Original Note 1")

        # Conflict file was backed up with timestamp
        conflict_files = list(self.workspace.glob("notes.txt.conflict.*"))
        self.assertEqual(len(conflict_files), 1)
        self.assertEqual(conflict_files[0].read_text(encoding="utf-8"), "NEW CONFLICTING FILE CREATED LATER")

    def test_undo_after_partial_failure(self):
        """Scenario 5: Partial failure during restore preserves healthy items and reports failure."""
        file_a = self.workspace / "file_a.txt"
        file_b = self.workspace / "file_b.txt"
        file_a.write_text("content A", encoding="utf-8")
        file_b.write_text("content B", encoding="utf-8")

        eff = analyse("rm file_a.txt file_b.txt", cwd=str(self.workspace))
        quarantine_effect(
            effect=eff,
            request_id="req_partial_test",
            command="rm file_a.txt file_b.txt",
            base_dir=self.quarantine_dir,
        )

        file_a.unlink()
        file_b.unlink()

        # Simulate copy failure on file_a only
        orig_copy2 = shutil.copy2

        def mock_copy2(src, dst, **kwargs):
            if "file_a" in str(dst):
                raise PermissionError("Simulated disk write failure on file_a")
            return orig_copy2(src, dst, **kwargs)

        with patch("shutil.copy2", side_effect=mock_copy2):
            undo_res = undo(request_id="req_partial_test", base_dir=self.quarantine_dir)

        # Assert: Overall success is False, but file_b is restored and file_a is listed in failed_paths
        self.assertFalse(undo_res.success)
        self.assertIn(str(file_b), undo_res.restored_paths)
        self.assertIn(str(file_a), undo_res.failed_paths)
        self.assertTrue(file_b.exists())
        self.assertFalse(file_a.exists())

    def test_quarantine_payload_above_500mb_requires_confirmation(self):
        """Scenario 6: Size > 500 MB raises QuarantineLimitExceededError unless confirmed."""
        dummy_file = self.workspace / "huge_data.bin"
        dummy_file.write_text("small placeholder", encoding="utf-8")

        eff = Effect(deletes=[str(dummy_file)], bytes_affected=600 * 1024 * 1024)

        # Mock get_path_size to simulate 600 MB
        with patch("nlagent.safety.quarantine.get_path_size", return_value=600 * 1024 * 1024):
            # Without confirmation: raises QuarantineLimitExceededError
            with self.assertRaises(QuarantineLimitExceededError):
                quarantine_effect(
                    effect=eff,
                    request_id="req_large",
                    command="rm huge_data.bin",
                    base_dir=self.quarantine_dir,
                    confirmed_large=False,
                )

            # With confirmation: proceeds without raising
            manifest = quarantine_effect(
                effect=eff,
                request_id="req_large_confirmed",
                command="rm huge_data.bin",
                base_dir=self.quarantine_dir,
                confirmed_large=True,
            )
            self.assertIsNotNone(manifest)

    def test_commit_integration_auto_quarantines_destructive_command(self):
        """Verify commit() automatically shadows files into quarantine before executing."""
        sample_file = self.workspace / "audit.log"
        sample_file.write_text("2026-11-02 AUDIT EVENT", encoding="utf-8")

        proposal = {
            "request_id": "commit_auto_q_1",
            "command": "rm audit.log",
            "targets": ["audit.log"],
            "tier": "destructive",
        }

        # Mock runner that simulates successful deletion
        def mock_runner(cmd, cwd):
            (Path(cwd) / "audit.log").unlink(missing_ok=True)
            return "deleted", 0

        output, exit_code, eff = commit(
            proposal=proposal,
            session_state=self.session,
            runner=mock_runner,
            quarantine_base_dir=self.quarantine_dir,
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(sample_file.exists())
        self.assertEqual(proposal["quarantine_id"], "commit_auto_q_1")

        # Now verify undo restores it
        undo_res = undo(request_id="commit_auto_q_1", base_dir=self.quarantine_dir)
        self.assertTrue(undo_res.success)
        self.assertTrue(sample_file.exists())
        self.assertEqual(sample_file.read_text(encoding="utf-8"), "2026-11-02 AUDIT EVENT")

    def test_commit_blocks_unconfirmed_large_quarantine(self):
        """Verify commit() aborts execution with code 126 when payload exceeds 500 MB without confirmation."""
        big_file = self.workspace / "large_disk.iso"
        big_file.write_text("iso data", encoding="utf-8")

        proposal = {
            "request_id": "large_commit_refused",
            "command": "rm large_disk.iso",
            "targets": ["large_disk.iso"],
            "tier": "destructive",
        }

        mock_runner = MagicMock()

        with patch("nlagent.safety.quarantine.get_path_size", return_value=700 * 1024 * 1024):
            output, exit_code, eff = commit(
                proposal=proposal,
                session_state=self.session,
                runner=mock_runner,
                quarantine_base_dir=self.quarantine_dir,
                confirmed_large_quarantine=False,
            )

        # Execution blocked
        self.assertEqual(exit_code, 126)
        self.assertIn("Blocked", output)
        self.assertIn("exceeds safety threshold of 500 MB", output)
        mock_runner.assert_not_called()

    def test_purge_expired_records(self):
        """Scenario 7: Purge removes expired records while keeping active ones."""
        # Create mock quarantine records
        rec1 = self.quarantine_dir / "old_req"
        rec2 = self.quarantine_dir / "recent_req"
        rec1.mkdir(parents=True, exist_ok=True)
        rec2.mkdir(parents=True, exist_ok=True)

        # rec1 manifest with timestamp 30 days ago
        old_time = time.time() - (30 * 86400)
        m1 = {"request_id": "old_req", "timestamp": "2026-01-01T00:00:00+00:00", "items": []}
        with open(rec1 / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(m1, f)
        os.utime(rec1, (old_time, old_time))

        # rec2 manifest with timestamp now
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        m2 = {"request_id": "recent_req", "timestamp": now_iso, "items": []}
        with open(rec2 / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(m2, f)

        # Purge records older than 7 days
        purged = purge(older_than_days=7.0, base_dir=self.quarantine_dir)
        self.assertEqual(purged, 1)
        self.assertFalse(rec1.exists())
        self.assertTrue(rec2.exists())


if __name__ == "__main__":
    unittest.main()

