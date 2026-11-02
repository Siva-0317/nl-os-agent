"""
tests/test_run_e5.py - Unit tests for 2x2 Factorial Evaluation Matrix (Experiment E5).

Tests:
1. Wilson score interval calculation (proportions, bounds, zero/full edges).
2. McNemar's paired test calculation (exact binomial for small n, continuity corrected chi-squared).
3. Response parsing, tag stripping, and JSON validation.
4. Sealed test dataset generation and schema integrity.
5. End-to-end 2x2 evaluation run with mock backend inside tempfile directories.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import unittest

# Ensure src and root are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
scripts_dir = os.path.join(root_dir, "scripts")
for p in (root_dir, src_dir, scripts_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.bench.provenance import validate_provenance
from scripts.run_e5 import (
    calculate_mcnemar_test,
    calculate_wilson_interval,
    generate_sealed_test_set,
    load_or_create_sealed_test_set,
    parse_model_response,
    run_e5_experiment,
)


class TestE5Runner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_wilson_interval_bounds_and_edges(self):
        # 1. 0% successes
        pt, low, high = calculate_wilson_interval(0, 50)
        self.assertEqual(pt, 0.0)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)
        self.assertLess(high, 10.0)

        # 2. 100% successes
        pt, low, high = calculate_wilson_interval(50, 50)
        self.assertEqual(pt, 100.0)
        self.assertGreater(low, 90.0)
        self.assertEqual(high, 100.0)

        # 3. Known proportion: 45 / 50 = 90%
        pt, low, high = calculate_wilson_interval(45, 50)
        self.assertEqual(pt, 90.0)
        self.assertLess(low, 90.0)
        self.assertGreater(high, 90.0)
        self.assertTrue(0.0 <= low <= high <= 100.0)

        # 4. Zero sample edge case
        pt, low, high = calculate_wilson_interval(0, 0)
        self.assertEqual((pt, low, high), (0.0, 0.0, 0.0))

    def test_mcnemar_test_identical_outcomes(self):
        # When both models have identical outcomes, discordant pairs = 0, p-value = 1.0
        vec_a = [True] * 20 + [False] * 5
        vec_b = [True] * 20 + [False] * 5
        res = calculate_mcnemar_test(vec_a, vec_b)
        self.assertEqual(res["discordant_total"], 0)
        self.assertEqual(res["p_value"], 1.0)
        self.assertFalse(res["significant_at_05"])

    def test_mcnemar_test_small_sample_exact_binomial(self):
        # Discordant count < 25 uses exact two-tailed binomial test
        # 10 cases where A passes and B fails; 1 case where A fails and B passes
        vec_a = [True] * 10 + [False] * 1 + [True] * 5
        vec_b = [False] * 10 + [True] * 1 + [True] * 5
        res = calculate_mcnemar_test(vec_a, vec_b)
        self.assertEqual(res["method"], "exact_binomial")
        self.assertEqual(res["b"], 10)
        self.assertEqual(res["c"], 1)
        self.assertEqual(res["discordant_total"], 11)
        self.assertTrue(res["significant_at_05"])
        self.assertLess(res["p_value"], 0.05)

    def test_mcnemar_test_large_sample_continuity_chi2(self):
        # Discordant count >= 25 uses continuity corrected chi2
        # 30 cases where A passes & B fails, 5 cases where A fails & B passes
        vec_a = [True] * 30 + [False] * 5
        vec_b = [False] * 30 + [True] * 5
        res = calculate_mcnemar_test(vec_a, vec_b)
        self.assertEqual(res["method"], "continuity_corrected_chi2")
        self.assertEqual(res["b"], 30)
        self.assertEqual(res["c"], 5)
        self.assertGreater(res["chi2"], 10.0)
        self.assertTrue(res["significant_at_05"])
        self.assertLess(res["p_value"], 0.001)

    def test_mcnemar_mismatched_length_raises(self):
        with self.assertRaises(ValueError):
            calculate_mcnemar_test([True, False], [True])

    def test_parse_model_response_direct_json(self):
        raw = '{"command": "mkdir -p src", "targets": ["src"], "clarifying_question": null}'
        valid, cmd, tgts, clar, err = parse_model_response(raw)
        self.assertTrue(valid)
        self.assertEqual(cmd, "mkdir -p src")
        self.assertEqual(tgts, ["src"])
        self.assertIsNone(clar)
        self.assertIsNone(err)

    def test_parse_model_response_markdown_and_reasoning(self):
        raw = (
            "<think>Analyzing request to remove log files. Use Remove-Item.</think>\n"
            "Here is the command:\n"
            "```json\n"
            '{"command": "Remove-Item *.log", "targets": ["*.log"], "clarifying_question": null}\n'
            "```"
        )
        valid, cmd, tgts, clar, err = parse_model_response(raw)
        self.assertTrue(valid)
        self.assertEqual(cmd, "Remove-Item *.log")
        self.assertEqual(tgts, ["*.log"])
        self.assertIsNone(clar)

    def test_parse_model_response_clarification(self):
        raw = '{"command": null, "targets": [], "clarifying_question": "Which database would you like to delete?"}'
        valid, cmd, tgts, clar, err = parse_model_response(raw)
        self.assertTrue(valid)
        self.assertIsNone(cmd)
        self.assertEqual(clar, "Which database would you like to delete?")

    def test_parse_model_response_invalid_syntax(self):
        raw = "Sorry I cannot help with that without valid JSON {not json"
        valid, cmd, tgts, clar, err = parse_model_response(raw)
        self.assertFalse(valid)
        self.assertIsNotNone(err)

    def test_sealed_test_set_generation(self):
        test_path = os.path.join(self.temp_dir, "test_cases.jsonl")
        records = generate_sealed_test_set(test_path, count=15)
        self.assertEqual(len(records), 15)
        self.assertTrue(os.path.exists(test_path))

        # Check required fields
        first = records[0]
        self.assertIn("case_id", first)
        self.assertIn("family", first)
        self.assertIn("shell", first)
        self.assertIn("request", first)
        self.assertIn("fixture_spec", first)

        # Loading existing
        loaded = load_or_create_sealed_test_set(test_path, count=15)
        self.assertEqual(len(loaded), 15)
        self.assertEqual(loaded[0]["case_id"], first["case_id"])

    def test_end_to_end_e5_experiment_mock(self):
        test_file = os.path.join(self.temp_dir, "sealed_test.jsonl")
        jsonl_out = os.path.join(self.temp_dir, "e5_matrix.jsonl")
        md_out = os.path.join(self.temp_dir, "e5_matrix.md")

        # Run 8-case evaluation matrix
        summaries, mcnemar_pairs = run_e5_experiment(
            test_file=test_file,
            output_jsonl=jsonl_out,
            output_md=md_out,
            allow_mock=True,
            count=8,
            seed=42,
        )

        # Assert all 4 conditions generated
        for key in ("base_full", "base_short", "tuned_full", "tuned_short"):
            self.assertIn(key, summaries)
            s = summaries[key]
            self.assertEqual(s.total_cases, 8)
            self.assertTrue(0.0 <= s.accuracy_pct <= 100.0)
            self.assertTrue(0.0 <= s.accuracy_interval[0] <= s.accuracy_interval[1] <= 100.0)

        # Assert McNemar paired comparisons generated
        self.assertIn("Tuned_Short_vs_Base_Short", mcnemar_pairs)
        self.assertIn("Tuned_Short_vs_Base_Full", mcnemar_pairs)
        self.assertIn("Tuned_Short_vs_Tuned_Full", mcnemar_pairs)
        self.assertIn("p_value", mcnemar_pairs["Tuned_Short_vs_Base_Short"])

        # Verify output files exist
        self.assertTrue(os.path.exists(jsonl_out))
        self.assertTrue(os.path.exists(md_out))

        # Verify provenance in JSONL
        records = []
        with open(jsonl_out, "r", encoding="utf-8") as f:
            for line in f:
                records.append(json.loads(line))

        # 4 conditions * 8 cases = 32 records
        self.assertEqual(len(records), 32)
        first_rec = records[0]
        missing = validate_provenance(first_rec)
        self.assertEqual(missing, [], f"Missing provenance fields: {missing}")

        # Verify Markdown report content
        with open(md_out, "r", encoding="utf-8") as f:
            md_content = f.read()

        self.assertIn("Table 8.2: 2x2 Factorial Performance Matrix", md_content)
        self.assertIn("Statistical Significance (McNemar's Paired Tests)", md_content)
        self.assertIn("Wilson CI", md_content)
        self.assertIn("Prompt Token Reduction", md_content)


if __name__ == "__main__":
    unittest.main()

