"""
tests/test_audit_dataset.py - Unit tests for dataset audit and leakage analysis.

Supports both pytest and standard library python -m unittest.
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

# Ensure src and root are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from scripts.audit_dataset import (
    audit_datasets,
    compute_token_statistics,
    extract_root_utility,
    generate_markdown_report,
    get_word_3grams,
    jaccard_similarity,
    normalize_text,
    parse_dataset_row,
)


class TestAuditDataset(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_normalize_text(self):
        self.assertEqual(normalize_text("  List ALL Files, Please!  "), "list all files please")
        self.assertEqual(normalize_text("git   checkout   -b test"), "git checkout b test")
        self.assertEqual(normalize_text(""), "")

    def test_get_word_3grams(self):
        # >= 3 words
        grams = get_word_3grams("list all files in directory")
        expected = {
            ("list", "all", "files"),
            ("all", "files", "in"),
            ("files", "in", "directory"),
        }
        self.assertEqual(grams, expected)

        # 2 words fallback
        grams_2 = get_word_3grams("git status")
        self.assertEqual(grams_2, {("git", "status")})

        # 1 word fallback
        grams_1 = get_word_3grams("ls")
        self.assertEqual(grams_1, {("ls",)})

        # Empty
        self.assertEqual(get_word_3grams(""), set())

    def test_jaccard_similarity(self):
        s1 = {("a", "b", "c"), ("b", "c", "d")}
        s2 = {("a", "b", "c"), ("b", "c", "d")}
        self.assertEqual(jaccard_similarity(s1, s2), 1.0)

        s3 = {("x", "y", "z")}
        self.assertEqual(jaccard_similarity(s1, s3), 0.0)

        s4 = {("a", "b", "c"), ("x", "y", "z")}
        # intersection: 1, union: 3 -> 1/3
        self.assertAlmostEqual(jaccard_similarity(s1, s4), 1.0 / 3.0, places=5)

    def test_extract_root_utility(self):
        self.assertEqual(extract_root_utility("ls -la /tmp"), "ls")
        self.assertEqual(extract_root_utility("git reset --hard HEAD"), "git")
        self.assertEqual(extract_root_utility("(Get-NetTCPConnection -LocalPort 80).OwningProcess"), "get-nettcpconnection")
        self.assertEqual(extract_root_utility("FOO=bar python script.py"), "python")
        self.assertEqual(extract_root_utility(None), "<none>")
        self.assertEqual(extract_root_utility(""), "<none>")

    def test_compute_token_statistics(self):
        data = [10, 20, 30, 40, 50]
        stats = compute_token_statistics(data)
        self.assertEqual(stats["count"], 5)
        self.assertEqual(stats["min"], 10)
        self.assertEqual(stats["max"], 50)
        self.assertEqual(stats["median"], 30)
        self.assertEqual(stats["mean"], 30.0)

        empty_stats = compute_token_statistics([])
        self.assertEqual(empty_stats["count"], 0)
        self.assertEqual(empty_stats["mean"], 0.0)

    def _create_synthetic_datasets(self) -> tuple[str, str]:
        train_rows = [
            {
                "family": "file_ops",
                "messages": [
                    {"role": "system", "content": "Environment:\nOS: Linux\nShell: bash\nCWD: /home"},
                    {"role": "user", "content": "list all files including hidden ones"},
                    {"role": "assistant", "content": json.dumps({"command": "ls -la", "targets": ["."], "clarifying_question": None})},
                ],
            },
            {
                "family": "file_ops",
                "messages": [
                    {"role": "system", "content": "Environment:\nOS: macOS\nShell: zsh\nCWD: /repo"},
                    {"role": "user", "content": "search for text todo across all files in src folder"},
                    {"role": "assistant", "content": json.dumps({"command": "grep -rn 'TODO' src/", "targets": ["src/"], "clarifying_question": None})},
                ],
            },
            {
                "family": "clarification",
                "messages": [
                    {"role": "system", "content": "Environment:\nOS: Windows\nShell: powershell\nCWD: C:\\Users"},
                    {"role": "user", "content": "delete the database file"},
                    {"role": "assistant", "content": json.dumps({"command": None, "targets": [], "clarifying_question": "Which database file?"})},
                ],
            },
            {
                "family": "process_control",
                "messages": [
                    {"role": "system", "content": "Environment:\nOS: Linux\nShell: bash\nCWD: /var"},
                    {"role": "user", "content": "kill process 1234"},
                    {"role": "assistant", "content": json.dumps({"command": "kill -9 1234", "targets": [], "clarifying_question": None})},
                ],
            },
        ]

        val_rows = [
            # Exact duplicate of row 0 with different shell
            {
                "family": "file_ops",
                "messages": [
                    {"role": "system", "content": "Environment:\nOS: Windows\nShell: powershell\nCWD: C:\\test"},
                    {"role": "user", "content": "list all files including hidden ones"},
                    {"role": "assistant", "content": json.dumps({"command": "Get-ChildItem -Force", "targets": ["."], "clarifying_question": None})},
                ],
            },
            # Near-duplicate of row 1 (Jaccard >= 0.7)
            {
                "family": "git_vcs",
                "messages": [
                    {"role": "system", "content": "Environment:\nOS: macOS\nShell: zsh\nCWD: /repo"},
                    {"role": "user", "content": "search for text todo across all files in src directory"},
                    {"role": "assistant", "content": json.dumps({"command": "grep -rn 'TODO' src/", "targets": ["src/"], "clarifying_question": None})},
                ],
            },
            # Completely unique unseen request
            {
                "family": "networking",
                "messages": [
                    {"role": "system", "content": "Environment:\nOS: Linux\nShell: bash\nCWD: /root"},
                    {"role": "user", "content": "display active listening ports on the system"},
                    {"role": "assistant", "content": json.dumps({"command": "ss -tuln", "targets": [], "clarifying_question": None})},
                ],
            },
        ]

        train_path = os.path.join(self.temp_dir, "synthetic_train.jsonl")
        val_path = os.path.join(self.temp_dir, "synthetic_val.jsonl")

        with open(train_path, "w", encoding="utf-8") as f:
            for r in train_rows:
                f.write(json.dumps(r) + "\n")

        with open(val_path, "w", encoding="utf-8") as f:
            for r in val_rows:
                f.write(json.dumps(r) + "\n")

        return train_path, val_path

    def test_audit_datasets_synthetic(self):
        train_path, val_path = self._create_synthetic_datasets()
        out_md = os.path.join(self.temp_dir, "synthetic_audit.md")

        results = audit_datasets(train_path, val_path, jaccard_threshold=0.6)

        t_stat = results["train_stats"]
        v_stat = results["val_stats"]
        ov = results["overlap"]

        # Instance counts
        self.assertEqual(t_stat["total"], 4)
        self.assertEqual(v_stat["total"], 3)
        self.assertEqual(t_stat["unique_user_requests"], 4)
        self.assertEqual(v_stat["unique_user_requests"], 3)

        # Shell distribution
        self.assertEqual(t_stat["shell_counts"]["bash"], 2)
        self.assertEqual(t_stat["shell_counts"]["zsh"], 1)
        self.assertEqual(t_stat["shell_counts"]["powershell"], 1)
        self.assertEqual(v_stat["shell_counts"]["powershell"], 1)

        # Clarification counts
        self.assertEqual(t_stat["clarification_counts"][True], 1)
        self.assertEqual(t_stat["clarification_counts"][False], 3)
        self.assertEqual(v_stat["clarification_counts"][False], 3)

        # Exact match
        self.assertEqual(ov["exact_shared_queries"], 1)

        # Near duplicate count
        self.assertGreaterEqual(ov["val_queries_with_near_dup"], 2)

        # Distinct commands
        self.assertEqual(t_stat["unique_commands"], 3)
        self.assertEqual(v_stat["unique_commands"], 3)

        # Report generation
        report = generate_markdown_report(results, out_md)
        self.assertTrue(os.path.exists(out_md))
        self.assertIn("# Fine-Tuning Dataset Audit", report)
        self.assertIn("Executive Summary & Critical Leakage Findings", report)
        self.assertIn("Template-Family Distribution", report)
        self.assertIn("Environment & Shell Distribution", report)

    def test_audit_dataset_cli(self):
        train_path, val_path = self._create_synthetic_datasets()
        out_md = os.path.join(self.temp_dir, "cli_audit.md")

        cmd = [
            sys.executable,
            "scripts/audit_dataset.py",
            "--train", train_path,
            "--val", val_path,
            "--output", out_md,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=root_dir)
        self.assertEqual(res.returncode, 0)
        self.assertIn("DATASET AUDIT SUMMARY", res.stdout)
        self.assertTrue(os.path.exists(out_md))


if __name__ == "__main__":
    unittest.main()
