"""
tests/test_evidence_ledger.py - Unit tests for build_evidence_ledger.py (Task T38).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Ensure project root & src are on sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_root, "src")
_scripts = os.path.join(_root, "scripts")
for p in (_root, _src, _scripts):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.bench.provenance import collect
from scripts.build_evidence_ledger import audit_jsonl_file, generate_evidence_ledger_markdown


class TestEvidenceLedger(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_audit_flags_mock_backend(self):
        """Verify audit_jsonl_file flags mock or demo backends as INVALID."""
        path = os.path.join(self.temp_dir, "results_mock.jsonl")
        prov = collect(backend="mock")
        record = {"case_id": 1, "latency_ms": 12.0, **prov}
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        audit = audit_jsonl_file(path)
        self.assertEqual(audit.status, "INVALID")
        self.assertIn("MOCK_OR_DEMO_BACKEND", audit.flags)
        self.assertIn("mock", audit.backends)

    def test_audit_flags_missing_provenance(self):
        """Verify audit_jsonl_file flags missing provenance in results as INVALID."""
        path = os.path.join(self.temp_dir, "results_noprov.jsonl")
        # In results/ path to trigger result verification
        res_dir = os.path.join(self.temp_dir, "results")
        os.makedirs(res_dir, exist_ok=True)
        res_path = os.path.join(res_dir, "test.jsonl")

        with open(res_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"command": "dir", "time": 10.0}) + "\n")

        audit = audit_jsonl_file(res_path)
        self.assertEqual(audit.status, "INVALID")
        self.assertIn("MISSING_PROVENANCE", audit.flags)

    def test_audit_accepts_clean_live_records(self):
        """Verify audit_jsonl_file marks genuine live runs with provenance as VALID."""
        res_dir = os.path.join(self.temp_dir, "results")
        os.makedirs(res_dir, exist_ok=True)
        res_path = os.path.join(res_dir, "live_run.jsonl")

        prov = collect(backend="llama-server", model_file="nemotron.gguf")
        record = {"case_id": 1, "match": True, **prov}
        with open(res_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        audit = audit_jsonl_file(res_path)
        self.assertEqual(audit.status, "VALID")
        self.assertTrue(audit.has_provenance)
        self.assertEqual(audit.backends, ["llama-server"])
        self.assertEqual(audit.flags, [])

    def test_audit_detects_suspicious_constant_timings(self):
        """Verify zero-variance constant latencies are flagged."""
        res_dir = os.path.join(self.temp_dir, "results")
        os.makedirs(res_dir, exist_ok=True)
        res_path = os.path.join(res_dir, "constant_latencies.jsonl")

        prov = collect(backend="local_gpu")
        with open(res_path, "w", encoding="utf-8") as f:
            for i in range(15):
                rec = {"case_id": i, "prefill_ms": 120.0, **prov}
                f.write(json.dumps(rec) + "\n")

        audit = audit_jsonl_file(res_path)
        self.assertEqual(audit.status, "INVALID")
        self.assertIn("SUSPICIOUS_CONSTANT_TIMINGS", audit.flags)

    def test_ledger_cli_exits_nonzero_when_figures_dep_is_invalid(self):
        """Verify build_evidence_ledger script exits non-zero if make_figures references invalid file."""
        script_path = os.path.join(_root, "scripts", "build_evidence_ledger.py")
        # Run script with a custom output path
        out_ledger = os.path.join(self.temp_dir, "test_ledger.md")

        res = subprocess.run(
            [sys.executable, script_path, "--output", out_ledger],
            capture_output=True,
            text=True,
        )
        # Because current make_figures.py references safety.jsonl and e0_real.jsonl, both are VALID -> exit code 0
        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.exists(out_ledger))

        # Now test with --fail-on-any-invalid (since e5_matrix.jsonl is mock, this MUST exit 1)
        res_fail = subprocess.run(
            [sys.executable, script_path, "--output", out_ledger, "--fail-on-any-invalid"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_fail.returncode, 1)
        self.assertIn("files are invalid", res_fail.stderr)


if __name__ == "__main__":
    unittest.main()

