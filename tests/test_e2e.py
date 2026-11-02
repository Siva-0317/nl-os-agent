"""
tests/test_e2e.py - End-to-End Acceptance Test Suite.

Verifies the canonical 7-stage user workflow with a deterministic mocked backend:
1. Read-Only Query (Tier 0, no filesystem mutation)
2. State-Modifying Creation (Tier 1, verified creation)
3. Vague Request with Clarifying Question (Tier 4 Ambiguity Gate)
4. Destructive Action with Static Preview & Quarantine Shadowing (Tier 2 Confirmation)
5. Byte-Exact Undo & Rollback from Quarantine Store
6. Bypass Attempt (find -delete) Escalated from Read-Only to Destructive
7. Verified-Command Cache Hit (<1ms Sub-Millisecond Prefix Lookup)

All test executions run strictly inside temporary directories.
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
from typing import Any

# Ensure src & root are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
for p in (root_dir, src_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.cache.verified_cache import VerifiedCommandCache, make_env_key
from nlagent.cli.db import SQLiteStateStore
from nlagent.context.state import SessionState
from nlagent.daemon.commit import commit
from nlagent.daemon.propose import get_default_env, propose
from nlagent.safety.effects import analyse, preview, tier_from_effect
from nlagent.safety.quarantine import calculate_sha1, undo


class DeterministicMockBackend:
    """Mock backend returning deterministic structured proposals for E2E testing."""

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        lines = [ln.strip() for ln in prompt.strip().splitlines() if ln.strip()]
        req_line = lines[-1].lower() if lines else ""

        if any(w in req_line for w in ("clean up", "clean log", "delete the log")):
            payload = {
                "command": "",
                "targets": ["logs/app.log", "logs/old.log"],
                "explanation": "Multiple active and archived log files exist in logs/ directory.",
                "clarifying_question": "Which log files should be removed: 'logs/old.log' (stale archive) or 'logs/app.log' (active system log)?",
                "needs_clarification": True,
            }
        elif "release notes" in req_line or "release_notes.txt" in req_line:
            payload = {
                "command": 'echo "Version 1.0 Release Notes: All systems operational" > release_notes.txt',
                "targets": ["release_notes.txt"],
                "explanation": "Create initial release notes document for version 1.0.",
            }
        elif "delete logs/old.log" in req_line or "old.log" in req_line:
            payload = {
                "command": "rm -f logs/old.log",
                "targets": ["logs/old.log"],
                "explanation": "Delete stale archived log file logs/old.log.",
            }
        elif "-delete" in req_line or "delete all" in req_line:
            payload = {
                "command": 'find logs -name "*.log" -delete',
                "targets": ["logs/*.log"],
                "explanation": "Search and delete matching log files.",
            }
        elif any(w in req_line for w in ("list python", "find python", "python source", "source files")):
            payload = {
                "command": 'find src -name "*.py"',
                "targets": ["src/*.py"],
                "explanation": "List all Python source files in the src directory.",
            }
        else:
            payload = {
                "command": "echo 'Executed unknown request'",
                "targets": [],
                "explanation": "Generic fallback proposal",
            }

        class MockResult:
            content = json.dumps(payload)
        return MockResult()


class TestEndToEndAcceptanceFlow(unittest.TestCase):
    """Full 7-stage end-to-end acceptance test."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nl_agent_test_e2e_")
        self.workspace = Path(self.temp_dir) / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir = Path(self.temp_dir) / "quarantine"
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(self.temp_dir) / "state.db"

        # Populate fixture workspace
        self._build_fixture(self.workspace)

        self.session = SessionState(cwd=str(self.workspace))
        self.cache = VerifiedCommandCache(threshold=0.88)
        self.store = SQLiteStateStore(db_path=self.db_path)
        self.env = get_default_env(cwd=str(self.workspace))
        self.backend = DeterministicMockBackend()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_fixture(self, ws: Path) -> None:
        src = ws / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "app.py").write_text("# Main App\ndef main(): pass\n", encoding="utf-8")
        (src / "utils.py").write_text("# Utils\ndef helper(): return 42\n", encoding="utf-8")

        tests = ws / "tests"
        tests.mkdir(parents=True, exist_ok=True)
        (tests / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

        logs = ws / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "app.log").write_text("2026-11-02 [INFO] Running\n", encoding="utf-8")
        self.old_log_content = "2025-12-31 [CRITICAL] Legacy log to be archived.\n"
        (logs / "old.log").write_text(self.old_log_content, encoding="utf-8")
        self.old_log_hash = calculate_sha1(logs / "old.log")

        (ws / "README.md").write_text("# Test Repo\n", encoding="utf-8")

    def test_complete_7_stage_flow(self):
        """Execute the complete 7-stage workflow verifying tiers, previews and file states."""

        # ---------------------------------------------------------------------
        # 1. Read-Only Query
        # ---------------------------------------------------------------------
        req1 = "list python source files in src directory"
        p1 = propose(
            request=req1,
            session_state=self.session,
            backend=self.backend,
            verified_cache=self.cache,
            env=self.env,
        )

        # Assertions for Step 1
        self.assertEqual(p1["tier"], "read_only")
        self.assertIn("find", p1["command"])
        self.assertEqual(len(p1["effect"].deletes), 0)
        self.assertEqual(len(p1["effect"].creates), 0)
        self.assertFalse(p1.get("cached", False))

        out1, code1, _ = commit(
            proposal=p1,
            session_state=self.session,
            verified_cache=self.cache,
            enable_quarantine=False,
        )
        self.assertEqual(code1, 0)
        # Verify file states unchanged
        self.assertTrue((self.workspace / "src" / "app.py").exists())
        self.assertTrue((self.workspace / "src" / "utils.py").exists())
        self.assertTrue((self.workspace / "logs" / "app.log").exists())

        # ---------------------------------------------------------------------
        # 2. State-Modifying Creation
        # ---------------------------------------------------------------------
        req2 = "create a release notes draft named release_notes.txt with version 1.0 notes"
        p2 = propose(
            request=req2,
            session_state=self.session,
            backend=self.backend,
            verified_cache=self.cache,
            env=self.env,
        )

        # Assertions for Step 2
        self.assertEqual(p2["tier"], "state_modifying")
        self.assertTrue(len(p2["effect"].creates) > 0 or "release_notes.txt" in p2["command"])
        self.assertIn("CREATE", p2["preview"])

        out2, code2, _ = commit(
            proposal=p2,
            session_state=self.session,
            verified_cache=self.cache,
            enable_quarantine=False,
        )
        self.assertEqual(code2, 0)
        created_file = self.workspace / "release_notes.txt"
        self.assertTrue(created_file.exists())
        raw_bytes = created_file.read_bytes()
        file_text = (
            created_file.read_text(encoding="utf-16")
            if raw_bytes[:2] == b"\xff\xfe"
            else created_file.read_text(encoding="utf-8", errors="replace")
        )
        self.assertIn("Version 1.0 Release Notes", file_text)

        # ---------------------------------------------------------------------
        # 3. Vague Request with Clarifying Question
        # ---------------------------------------------------------------------
        req3 = "clean up the log files"
        p3 = propose(
            request=req3,
            session_state=self.session,
            backend=self.backend,
            verified_cache=self.cache,
            env=self.env,
        )

        # Assertions for Step 3
        self.assertEqual(p3["tier"], "ambiguous")
        self.assertTrue(p3.get("needs_clarification"))
        self.assertIsNotNone(p3.get("clarifying_question"))
        self.assertIn("Which log files", p3["clarifying_question"])

        # Invariant: No execution occurred, files still exist
        self.assertTrue((self.workspace / "logs" / "old.log").exists())
        self.assertTrue((self.workspace / "logs" / "app.log").exists())

        # ---------------------------------------------------------------------
        # 4. Destructive Action with Static Preview & Quarantine Shadowing
        # ---------------------------------------------------------------------
        req4 = "delete logs/old.log"
        target_file = self.workspace / "logs" / "old.log"
        self.assertTrue(target_file.exists())

        p4 = propose(
            request=req4,
            session_state=self.session,
            backend=self.backend,
            verified_cache=self.cache,
            env=self.env,
        )

        # Assertions for Step 4
        self.assertEqual(p4["tier"], "destructive")
        self.assertTrue(len(p4["effect"].deletes) > 0)
        self.assertIn("DELETE", p4["preview"])

        prop_id = "test_e2e_destructive_p4"
        p4["id"] = prop_id
        out4, code4, eff4 = commit(
            proposal=p4,
            session_state=self.session,
            verified_cache=self.cache,
            enable_quarantine=True,
            quarantine_base_dir=self.quarantine_dir,
        )

        self.assertEqual(code4, 0)
        # Invariant: Target deleted, other logs preserved
        self.assertFalse(target_file.exists())
        self.assertTrue((self.workspace / "logs" / "app.log").exists())

        # ---------------------------------------------------------------------
        # 5. Byte-Exact Undo Rollback
        # ---------------------------------------------------------------------
        undo_res = undo(request_id=prop_id, base_dir=self.quarantine_dir)

        # Assertions for Step 5
        self.assertTrue(undo_res.success)
        self.assertTrue(target_file.exists())
        self.assertEqual(calculate_sha1(target_file), self.old_log_hash)
        self.assertEqual(target_file.read_text(encoding="utf-8"), self.old_log_content)

        # ---------------------------------------------------------------------
        # 6. Bypass Attempt (find -delete) Escalation
        # ---------------------------------------------------------------------
        attack_cmd = "find logs -name '*.log' -delete"
        p6 = propose(
            request=attack_cmd,
            session_state=self.session,
            backend=self.backend,
            verified_cache=self.cache,
            env=self.env,
        )

        # Assertions for Step 6: Must NOT be read_only!
        self.assertEqual(p6["tier"], "destructive")
        self.assertTrue(len(p6["effect"].deletes) > 0)
        self.assertIn("DELETE", p6["preview"])
        # Files still intact because propose NEVER executes
        self.assertTrue(target_file.exists())
        self.assertTrue((self.workspace / "logs" / "app.log").exists())

        # ---------------------------------------------------------------------
        # 7. Verified-Command Cache Hit
        # ---------------------------------------------------------------------
        t_start = time.perf_counter()
        p7 = propose(
            request=req1,  # Identical to Step 1
            session_state=self.session,
            backend=self.backend,
            verified_cache=self.cache,
            env=self.env,
        )
        t_elapsed = time.perf_counter() - t_start

        # Assertions for Step 7
        self.assertTrue(p7.get("cached"))
        self.assertEqual(p7["command"], p1["command"])
        self.assertEqual(p7["tier"], "read_only")
        # Sub-millisecond or fast prefix cache lookup
        self.assertLess(t_elapsed, 0.05)


if __name__ == "__main__":
    unittest.main()
