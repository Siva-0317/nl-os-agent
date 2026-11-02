"""
tests/test_exec_eval.py - Unit tests for execution-based evaluator.

Tests fixture materialization, manifest comparison, stdout normalization,
and isolated execution of read-only, state-modifying, and destructive commands.
Uses exclusively tempfile directories.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src and root are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from nlagent.eval.exec_eval import (
    EvalResult,
    TestCase,
    compare_manifests,
    compare_stdout,
    evaluate_batch,
    evaluate_case,
    materialize_fixture,
    normalize_stdout,
    summarize_results,
)


class TestExecutionEvaluator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_normalize_stdout(self):
        raw = "  hello world  \r\n\r\n  line  two   \r\n"
        expected = "hello world\nline two"
        self.assertEqual(normalize_stdout(raw), expected)

    def test_compare_stdout_exact_and_unordered(self):
        s1 = "file_a.txt\nfile_b.txt\nfile_c.txt"
        s2 = "file_a.txt\r\nfile_b.txt\r\nfile_c.txt"
        self.assertTrue(compare_stdout(s1, s2))

        # Unordered line match (e.g. find output in different order)
        s3 = "file_c.txt\nfile_a.txt\nfile_b.txt"
        self.assertTrue(compare_stdout(s1, s3))

        # Distinct output
        s4 = "file_a.txt\nfile_d.txt"
        self.assertFalse(compare_stdout(s1, s4))

    def test_materialize_fixture(self):
        target = Path(self.temp_dir) / "fixture_test"
        spec = {
            "src/main.py": "print('hello')",
            "build/cache.dat": b"\x00\x01\x02",
            "empty_dir": None,
        }
        materialize_fixture(spec, target)

        self.assertTrue((target / "src" / "main.py").is_file())
        self.assertEqual((target / "src" / "main.py").read_text(encoding="utf-8"), "print('hello')")
        self.assertTrue((target / "build" / "cache.dat").is_file())
        self.assertEqual((target / "build" / "cache.dat").read_bytes(), b"\x00\x01\x02")
        self.assertTrue((target / "empty_dir").is_dir())

    def test_compare_manifests(self):
        ref_diff = {"deleted": ["old.log"], "created": ["new.txt"], "modified": []}
        cand_diff = {"deleted": ["old.log"], "created": ["new.txt"], "modified": []}

        ref_after = {"new.txt": (10, "hash123", "0o644")}
        cand_after_matching = {"new.txt": (10, "hash123", "0o644")}
        cand_after_mismatch = {"new.txt": (10, "wronghash", "0o644")}

        # Matching hashes
        ok, reason = compare_manifests(cand_diff, ref_diff, cand_after_matching, ref_after)
        self.assertTrue(ok)
        self.assertIn("match", reason.lower())

        # Hash mismatch
        ok_mismatch, reason_mismatch = compare_manifests(cand_diff, ref_diff, cand_after_mismatch, ref_after)
        self.assertFalse(ok_mismatch)
        self.assertIn("hash mismatch", reason_mismatch.lower())

        # Path list mismatch
        cand_diff_wrong = {"deleted": ["old.log"], "created": ["other.txt"], "modified": []}
        ok_wrong, _ = compare_manifests(cand_diff_wrong, ref_diff, cand_after_matching, ref_after)
        self.assertFalse(ok_wrong)

    def test_evaluate_case_state_modifying_equivalent_command(self):
        case = TestCase(
            case_id="tc_mkdir",
            request="create a directory called build",
            reference_command="mkdir build",
            fixture_spec={"README.md": "# Project"},
            is_read_only=False,
        )

        # Equivalent candidate command: mkdir -p build
        res = evaluate_case(case, candidate_command="mkdir -p build")
        self.assertTrue(res.success)
        self.assertTrue(res.diff_match)
        self.assertFalse(res.is_read_only)
        self.assertEqual(res.cand_diff["created"], ["build"])

    def test_evaluate_case_state_modifying_incorrect_command(self):
        case = TestCase(
            case_id="tc_mkdir_fail",
            request="create a directory called build",
            reference_command="mkdir build",
            fixture_spec={"README.md": "# Project"},
            is_read_only=False,
        )

        # Wrong candidate: creates directory "dist" instead of "build"
        res = evaluate_case(case, candidate_command="mkdir dist")
        self.assertFalse(res.success)
        self.assertFalse(res.diff_match)

    def test_evaluate_case_destructive_deletion(self):
        case = TestCase(
            case_id="tc_remove_logs",
            request="delete all log files in current directory",
            reference_command="Remove-Item *.log",
            fixture_spec={
                "app.log": "log content 1",
                "debug.log": "log content 2",
                "script.py": "print('ok')",
            },
            is_read_only=False,
        )

        # Candidate command using alias rm *.log
        res = evaluate_case(case, candidate_command="rm *.log")
        self.assertTrue(res.success)
        self.assertTrue(res.diff_match)
        deleted = [p.replace("\\", "/") for p in res.cand_diff["deleted"]]
        self.assertEqual(sorted(deleted), ["app.log", "debug.log"])

    def test_evaluate_case_read_only(self):
        case = TestCase(
            case_id="tc_read_only",
            request="display the content of config.txt",
            reference_command="cat config.txt",
            fixture_spec={"config.txt": "host=localhost\nport=8080\n"},
            is_read_only=True,
        )

        # Equivalent candidate command
        res = evaluate_case(case, candidate_command="Get-Content config.txt")
        self.assertTrue(res.success)
        self.assertTrue(res.stdout_match)
        self.assertTrue(res.is_read_only)
        # Verify 0 mutations occurred
        self.assertEqual(res.cand_diff["created"], [])
        self.assertEqual(res.cand_diff["deleted"], [])
        self.assertEqual(res.cand_diff["modified"], [])

    def test_evaluate_case_read_only_mutating_fails(self):
        case = TestCase(
            case_id="tc_ro_pollute",
            request="list all files",
            reference_command="ls",
            fixture_spec={"file.txt": "content"},
            is_read_only=True,
        )

        # Candidate erroneously writes a file while listing
        mutating_candidate = "ls; New-Item -Path polluted.txt -ItemType File"
        res = evaluate_case(case, candidate_command=mutating_candidate)
        self.assertFalse(res.success)
        self.assertIn("mutated", res.reason.lower())

    def test_evaluate_batch_and_summarize(self):
        cases = [
            TestCase(
                case_id=1,
                request="create folder a",
                reference_command="mkdir a",
                fixture_spec={"root.txt": "data"},
                is_read_only=False,
            ),
            TestCase(
                case_id=2,
                request="create folder b",
                reference_command="mkdir b",
                fixture_spec={"root.txt": "data"},
                is_read_only=False,
            ),
            TestCase(
                case_id=3,
                request="read root.txt",
                reference_command="cat root.txt",
                fixture_spec={"root.txt": "data"},
                is_read_only=True,
            ),
        ]

        # Case 1 correct, Case 2 incorrect, Case 3 correct
        candidate_commands = {
            1: "mkdir -p a",
            2: "mkdir wrong_dir",
            3: "Get-Content root.txt",
        }

        results = evaluate_batch(cases, candidate_commands)
        self.assertEqual(len(results), 3)

        summary = summarize_results(results)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertAlmostEqual(summary["accuracy"], 66.67, places=1)
        self.assertEqual(summary["read_only"]["passed"], 1)
        self.assertEqual(summary["state_modifying"]["passed"], 1)


if __name__ == "__main__":
    unittest.main()

