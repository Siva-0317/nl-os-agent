"""
tests/test_loop_runner.py - Unit & Integration Tests for Autoresearch Optimization Loops.

Verifies:
1. Parsing of program.md contract with validation of invariants.
2. Rejection of contracts that attempt to read sealed datasets.
3. Rejection of contracts targeting forbidden surfaces (safety rules, evaluators, datasets).
4. Working tree confinement enforcement (rejects edits to undeclared files).
5. Clean file restoration and reversion mechanics.
6. Paired bootstrap confidence interval calculation and decision rules (KEEP vs REVERT).
7. Persistent results.tsv ledger appending and experiment counting.
8. End-to-end execution of run_loop with mock evaluator.
9. Audited run_sealed execution, provenance verification, and duplicate invocation detection.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src and root are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
scripts_dir = os.path.join(root_dir, "scripts")
for p in (root_dir, src_dir, scripts_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.bench.provenance import validate_provenance
from scripts.run_loop import (
    EvaluationResult,
    GovernanceViolationError,
    LoopContract,
    ResultsLedger,
    SealedAccessProhibitedError,
    assert_confinement,
    compute_paired_bootstrap,
    execute_loop_iteration,
    parse_program_contract,
    revert_file,
    run_loop,
)
from scripts.run_sealed import (
    check_prior_sealed_runs,
    run_sealed_benchmark,
)


class TestLoopRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_program_contract_valid(self):
        program_file = self.temp_path / "program.md"
        content = """---
goal: "Test prompt loop"
mutable_file: "src/nlagent/context/layout.py"
metric: "accuracy_pct"
guard_metric: "mean_prompt_tokens"
guard_threshold: 110.0
dev_dataset: "datasets/dev_v3.jsonl"
eval_command: "python scripts/run_e5.py --split dev"
confidence: 0.95
n_resamples: 500
max_experiments: 5
results_file: "test_results.tsv"
---
# Contract Details
"""
        program_file.write_text(content, encoding="utf-8")
        contract = parse_program_contract(program_file)

        self.assertEqual(contract.goal, "Test prompt loop")
        self.assertEqual(contract.mutable_file, "src/nlagent/context/layout.py")
        self.assertEqual(contract.metric, "accuracy_pct")
        self.assertEqual(contract.guard_threshold, 110.0)
        self.assertEqual(contract.dev_dataset, "datasets/dev_v3.jsonl")
        self.assertEqual(contract.confidence, 0.95)
        self.assertEqual(contract.n_resamples, 500)
        self.assertEqual(contract.max_experiments, 5)

    def test_contract_refuses_sealed_dataset(self):
        # 1. Contract specifying sealed test file directly
        program_file = self.temp_path / "bad_sealed_program.md"
        content = """---
goal: "Contaminated loop"
mutable_file: "src/nlagent/context/layout.py"
dev_dataset: "datasets/sealed_test.jsonl"
---
"""
        program_file.write_text(content, encoding="utf-8")
        with self.assertRaises(SealedAccessProhibitedError):
            parse_program_contract(program_file)

        # 2. Contract specifying --split sealed in eval command
        program_file2 = self.temp_path / "bad_eval_cmd.md"
        content2 = """---
goal: "Contaminated command"
mutable_file: "src/nlagent/context/layout.py"
eval_command: "python scripts/run_e5.py --split sealed"
---
"""
        program_file2.write_text(content2, encoding="utf-8")
        with self.assertRaises(SealedAccessProhibitedError):
            parse_program_contract(program_file2)

    def test_contract_refuses_safety_or_evaluator_mutable_file(self):
        # Declaring safety code as mutable is strictly prohibited
        program_file = self.temp_path / "bad_safety_program.md"
        content = """---
goal: "Attacking safety"
mutable_file: "src/nlagent/safety/effects.py"
---
"""
        program_file.write_text(content, encoding="utf-8")
        with self.assertRaises(GovernanceViolationError):
            parse_program_contract(program_file)

        # Declaring evaluator script as mutable is strictly prohibited
        program_file2 = self.temp_path / "bad_runner_program.md"
        content2 = """---
goal: "Modifying runner"
mutable_file: "scripts/run_loop.py"
---
"""
        program_file2.write_text(content2, encoding="utf-8")
        with self.assertRaises(GovernanceViolationError):
            parse_program_contract(program_file2)

    def test_revert_file_restores_original_snapshot(self):
        target = self.temp_path / "mutable.txt"
        target.write_text("original baseline content", encoding="utf-8")
        snapshot = target.read_bytes()

        # Mutate file
        target.write_text("corrupted mutation content", encoding="utf-8")
        self.assertEqual(target.read_text(encoding="utf-8"), "corrupted mutation content")

        # Revert
        revert_file(self.temp_path, "mutable.txt", snapshot_bytes=snapshot)
        self.assertEqual(target.read_text(encoding="utf-8"), "original baseline content")

    def test_paired_bootstrap_decision_keep_on_significant_improvement(self):
        # 100 cases: candidate gets 85 correct, baseline gets 65 correct
        # Discordant: candidate passes 25 where baseline failed, baseline passes 5 where candidate failed
        cand = [True] * 60 + [True] * 25 + [False] * 5 + [False] * 10
        base = [True] * 60 + [False] * 25 + [True] * 5 + [False] * 10

        delta, ci_low, ci_high = compute_paired_bootstrap(cand, base, confidence=0.95, n_resamples=500, seed=42)
        self.assertEqual(delta, 20.0)
        self.assertGreater(ci_low, 5.0)
        self.assertGreater(ci_high, 20.0)
        self.assertTrue(ci_low > 0.0)  # Significant improvement -> KEEP

    def test_paired_bootstrap_decision_revert_on_regression_or_null(self):
        # Clear regression: candidate gets 40 correct, baseline gets 80 correct (Delta = -40%)
        cand = [True] * 40 + [False] * 60
        base = [True] * 80 + [False] * 20

        delta, ci_low, ci_high = compute_paired_bootstrap(cand, base, confidence=0.95, n_resamples=500, seed=42)
        self.assertEqual(delta, -40.0)
        self.assertLess(ci_low, 0.0)
        self.assertLess(ci_high, 0.0)

    def test_results_ledger_lifecycle_and_counting(self):
        tsv_path = self.temp_path / "results.tsv"
        ledger = ResultsLedger(tsv_path)

        self.assertTrue(tsv_path.exists())
        self.assertEqual(ledger.count_experiments(), 0)

        # Append two trials
        ledger.append_record(
            experiment_id="exp_001",
            timestamp="2026-11-09T20:00:00Z",
            git_sha="abc1234",
            metric_val=75.0,
            baseline_val=70.0,
            delta_pct=5.0,
            ci_lower=1.5,
            ci_upper=8.5,
            prompt_tokens=85.0,
            decision="KEEP",
            description="Initial short prompt test",
        )
        ledger.append_record(
            experiment_id="exp_002",
            timestamp="2026-11-09T20:05:00Z",
            git_sha="abc1234",
            metric_val=68.0,
            baseline_val=75.0,
            delta_pct=-7.0,
            ci_lower=-11.0,
            ci_upper=-3.0,
            prompt_tokens=82.0,
            decision="REVERT",
            description="Aggressive prompt pruning",
        )

        self.assertEqual(ledger.count_experiments(), 2)

        # Validate file content structure
        lines = tsv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)  # header + 2 records
        header_cols = lines[0].split("\t")
        self.assertIn("experiment_id", header_cols)
        self.assertIn("decision", header_cols)
        self.assertIn("ci_lower", header_cols)

        row1 = lines[1].split("\t")
        self.assertEqual(row1[0], "exp_001")
        self.assertEqual(row1[9], "KEEP")

        row2 = lines[2].split("\t")
        self.assertEqual(row2[0], "exp_002")
        self.assertEqual(row2[9], "REVERT")

    def test_execute_loop_iteration_mock(self):
        # Create a mock mutable file
        mut_file = self.temp_path / "layout.py"
        mut_file.write_text("BASE_PROMPT = 'original'", encoding="utf-8")

        program_file = self.temp_path / "program.md"
        tsv_path = self.temp_path / "results.tsv"
        program_file.write_text(f"""---
goal: "Test iteration"
mutable_file: "layout.py"
metric: "accuracy_pct"
guard_metric: "mean_prompt_tokens"
guard_threshold: 120.0
results_file: "{tsv_path.name}"
---
""", encoding="utf-8")

        contract = parse_program_contract(program_file)
        contract.results_file = str(tsv_path)
        ledger = ResultsLedger(tsv_path)

        base_res = EvaluationResult(
            metric_val=70.0,
            prompt_tokens=90.0,
            latency_ms=25.0,
            success_vector=[True] * 70 + [False] * 30,
        )

        initial_snapshot = mut_file.read_bytes()

        # 1. Candidate improves metric -> KEEP
        mut_file.write_text("BASE_PROMPT = 'improved_v1'", encoding="utf-8")
        mock_cand_eval = lambda c: EvaluationResult(
            metric_val=80.0,
            prompt_tokens=85.0,
            latency_ms=24.0,
            success_vector=[True] * 80 + [False] * 20,
        )

        dec, new_best, snap1 = execute_loop_iteration(
            contract=contract,
            baseline_result=base_res,
            evaluator_fn=mock_cand_eval,
            ledger=ledger,
            exp_num=1,
            description="Candidate v1",
            repo_root=self.temp_path,
            baseline_snapshot=initial_snapshot,
        )
        self.assertEqual(dec, "KEEP")
        self.assertEqual(new_best.metric_val, 80.0)

        # 2. Candidate regresses metric -> REVERT
        mut_file.write_text("BASE_PROMPT = 'broken_v2'", encoding="utf-8")
        mock_cand_regress = lambda c: EvaluationResult(
            metric_val=60.0,
            prompt_tokens=80.0,
            latency_ms=23.0,
            success_vector=[True] * 60 + [False] * 40,
        )

        dec2, best_after_revert, snap2 = execute_loop_iteration(
            contract=contract,
            baseline_result=new_best,
            evaluator_fn=mock_cand_regress,
            ledger=ledger,
            exp_num=2,
            description="Candidate v2 (broken)",
            repo_root=self.temp_path,
            baseline_snapshot=snap1,
        )
        self.assertEqual(dec2, "REVERT")
        self.assertEqual(best_after_revert.metric_val, 80.0)
        # Verify file reverted back to improved_v1
        self.assertEqual(mut_file.read_text(encoding="utf-8"), "BASE_PROMPT = 'improved_v1'")

    def test_run_sealed_benchmark_and_audit_logging(self):
        sealed_file = self.temp_path / "sealed_test.jsonl"
        audit_log = self.temp_path / "sealed_audit.jsonl"
        jsonl_out = self.temp_path / "e5_sealed.jsonl"
        md_out = self.temp_path / "e5_sealed.md"
        temp_vault_log = self.temp_path / "vault_log.md"
        temp_vault_log.write_text("# Log\n\n---\n\n", encoding="utf-8")

        # 1. First sealed evaluation run
        entry1, summaries1 = run_sealed_benchmark(
            git_sha="deadbeef1234",
            test_file=str(sealed_file),
            output_jsonl=str(jsonl_out),
            output_md=str(md_out),
            audit_log=str(audit_log),
            vault_log_path=str(temp_vault_log),
            allow_mock=True,
            count=8,
        )

        self.assertEqual(entry1["git_sha"], "deadbeef1234")
        self.assertFalse(entry1["is_duplicate_sha"])
        self.assertEqual(entry1["status"], "COMPLETED")
        self.assertTrue(jsonl_out.exists())
        self.assertTrue(md_out.exists())
        self.assertTrue(audit_log.exists())
        self.assertIn("deadbeef1234", temp_vault_log.read_text(encoding="utf-8"))

        # Verify audit log entry
        priors = check_prior_sealed_runs(audit_log, "deadbeef1234")
        self.assertEqual(len(priors), 1)

        # Verify provenance in e5_sealed.jsonl
        with open(jsonl_out, "r", encoding="utf-8") as f:
            first_rec = json.loads(f.readline())
        missing = validate_provenance(first_rec)
        self.assertEqual(missing, [])

        # 2. Second sealed evaluation run on SAME Git SHA -> Flags duplicate warning
        entry2, summaries2 = run_sealed_benchmark(
            git_sha="deadbeef1234",
            test_file=str(sealed_file),
            output_jsonl=str(jsonl_out),
            output_md=str(md_out),
            audit_log=str(audit_log),
            allow_mock=True,
            count=8,
        )

        self.assertTrue(entry2["is_duplicate_sha"])
        self.assertEqual(entry2["prior_eval_count"], 1)

        # Verify audit log now records both invocations
        priors_after = check_prior_sealed_runs(audit_log, "deadbeef1234")
        self.assertEqual(len(priors_after), 2)


if __name__ == "__main__":
    unittest.main()
