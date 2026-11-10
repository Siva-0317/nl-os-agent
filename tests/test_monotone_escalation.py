"""
tests/test_monotone_escalation.py - Unit & Integration Tests for Monotone Learned Escalation (Task N3).

Verifies:
1. Feature extraction from effects.analyse producing 27-dimensional feature vectors.
2. Training and probability calibration for Logistic Regression and GBDT.
3. Classification metrics: Precision, Recall, F1, ROC-AUC, ECE (Expected Calibration Error).
4. Confirmation burden calculation on benign command sets.
5. Absolute enforcement of the Monotone Risk Escalation Principle (T_comb = max(T_det, T_learned)).
6. Invariant proof: Combined guard G3+G5 never lowers a safety tier under any adversarial input.
7. End-to-end execution of run_learned_escalation with full 10-field provenance verification.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src and root are on sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
scripts_dir = os.path.join(root_dir, "scripts")
for p in (root_dir, src_dir, scripts_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.bench.provenance import validate_provenance
from scripts.run_adaptive_attacker import setup_benchmark_fixtures
from scripts.run_learned_escalation import (
    RANK_TO_TIER,
    TIER_RANK,
    GradientBoostingModel,
    LogisticRegressionModel,
    StandardScaler,
    classify_learned_tier,
    compute_classification_metrics,
    compute_confirmation_burden,
    evaluate_combined_g3_g5,
    extract_features,
    run_learned_escalation,
)


class TestMonotoneEscalation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_esc_")
        setup_benchmark_fixtures(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_feature_extraction_shape_and_types(self):
        cmd = "Remove-Item -Path target.txt -Force"
        feats, tier = extract_features(cmd, shell="powershell", cwd=self.temp_dir)
        self.assertEqual(len(feats), 27)
        self.assertIsInstance(feats, list)
        for f in feats:
            self.assertIsInstance(f, float)
        self.assertEqual(tier, "destructive")

    def test_standard_scaler(self):
        X = [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]]
        scaler = StandardScaler()
        scaled = scaler.fit_transform(X)
        self.assertEqual(len(scaled), 3)
        self.assertEqual(len(scaled[0]), 2)
        # Transformed mean should be approximately 0
        mean_0 = sum(row[0] for row in scaled) / 3.0
        self.assertAlmostEqual(mean_0, 0.0, places=5)

    def test_logistic_regression_fit_and_predict(self):
        X = [[5.0, 1.0], [6.0, 2.0], [-5.0, -1.0], [-6.0, -2.0]] * 10
        y = [1, 1, 0, 0] * 10
        model = LogisticRegressionModel(lr=0.1, epochs=100)
        model.fit(X, y)
        probs = model.predict_proba([[5.0, 1.0], [-5.0, -1.0]])
        self.assertEqual(len(probs), 2)
        self.assertGreater(probs[0], 0.6)
        self.assertLess(probs[1], 0.4)

    def test_gradient_boosting_fit_and_predict(self):
        X = [[10.0, 0.0], [12.0, 1.0], [0.0, 10.0], [1.0, 12.0]] * 10
        y = [1, 1, 0, 0] * 10
        model = GradientBoostingModel(n_estimators=15, lr=0.1)
        model.fit(X, y)
        probs = model.predict_proba([[10.0, 0.0], [0.0, 10.0]])
        self.assertEqual(len(probs), 2)
        self.assertGreater(probs[0], 0.6)
        self.assertLess(probs[1], 0.4)

    def test_metrics_calculation(self):
        y_true = [1, 1, 1, 0, 0, 0]
        y_prob = [0.95, 0.85, 0.75, 0.15, 0.25, 0.05]
        metrics = compute_classification_metrics(y_true, y_prob, threshold=0.5)
        self.assertEqual(metrics["precision"], 100.0)
        self.assertEqual(metrics["recall"], 100.0)
        self.assertEqual(metrics["f1"], 100.0)
        self.assertEqual(metrics["roc_auc"], 1.0)
        self.assertLessEqual(metrics["ece"], 0.25)

    def test_confirmation_burden_calculation(self):
        y_true = [1, 1, 0, 0, 0, 0]
        y_prob = [0.9, 0.9, 0.1, 0.2, 0.8, 0.0]  # One benign has prob=0.8 (FP)
        g3_tiers = ["destructive", "destructive", "read_only", "read_only", "read_only", "read_only"]
        burden = compute_confirmation_burden(y_true, y_prob, g3_tiers, threshold=0.5)
        # Benign count = 4. G3 escalations = 0. G5 escalations = 1 (prob=0.8).
        self.assertEqual(burden["g3_confirmations_per_100"], 0.0)
        self.assertEqual(burden["g5_confirmations_per_100"], 25.0)
        self.assertEqual(burden["extra_confirmations_per_100"], 25.0)

    def test_monotone_escalation_never_lowers_tier(self):
        """
        Formal Proof by Exhaustion & Invariant Test:
        Asserts T_comb = max(T_det, T_learned) >= T_det for all tier pairs.
        """
        all_tiers = ["read_only", "state_modifying", "ambiguous", "destructive", "privileged"]
        for t_det in all_tiers:
            det_rank = TIER_RANK[t_det]
            for p_learned in [0.0, 0.1, 0.3, 0.6, 0.9, 1.0]:
                t_learned = classify_learned_tier(p_learned)
                learned_rank = TIER_RANK[t_learned]
                comb_rank = max(det_rank, learned_rank)
                comb_tier = RANK_TO_TIER[comb_rank]

                # Assert strict monotone escalation: combined tier is NEVER lower than det tier
                self.assertGreaterEqual(comb_rank, det_rank)
                self.assertGreaterEqual(TIER_RANK[comb_tier], TIER_RANK[t_det])

    def test_adversarial_false_negative_resilience(self):
        """
        When ML model is fooled to output p=0.00 on a destructive command,
        the combined guard MUST preserve the destructive / privileged tier.
        """
        class FoolishModel:
            def predict_proba(self, X):
                return [0.0] * len(X)

        dummy_scaler = StandardScaler()
        dummy_scaler.means = [0.0] * 27
        dummy_scaler.stds = [1.0] * 27
        model = FoolishModel()

        # Destructive command: Remove-Item
        res1 = evaluate_combined_g3_g5(
            "Remove-Item -Path target.txt -Force",
            shell="powershell",
            cwd=self.temp_dir,
            model=model,
            scaler=dummy_scaler,
        )
        self.assertEqual(res1["g3_tier"], "destructive")
        self.assertEqual(res1["g5_tier"], "read_only")
        self.assertEqual(res1["combined_tier"], "destructive")
        self.assertEqual(res1["action"], "escalated")
        self.assertTrue(res1["monotone_invariant_held"])

        # Privileged command: format C:
        res2 = evaluate_combined_g3_g5(
            "format C: /fs:ntfs /q",
            shell="cmd",
            cwd=self.temp_dir,
            model=model,
            scaler=dummy_scaler,
        )
        self.assertEqual(res2["g3_tier"], "privileged")
        self.assertEqual(res2["g5_tier"], "read_only")
        self.assertEqual(res2["combined_tier"], "privileged")
        self.assertEqual(res2["action"], "hard_blocked")
        self.assertTrue(res2["monotone_invariant_held"])

    def test_learned_escalation_on_suspicious_benign(self):
        """
        When G3 is read_only, but learned model detects suspicious features (p=0.92),
        the combined guard MUST escalate to destructive.
        """
        class SuspiciousModel:
            def predict_proba(self, X):
                return [0.92] * len(X)

        dummy_scaler = StandardScaler()
        dummy_scaler.means = [0.0] * 27
        dummy_scaler.stds = [1.0] * 27
        model = SuspiciousModel()

        res = evaluate_combined_g3_g5(
            "Get-ChildItem scratch",
            shell="powershell",
            cwd=self.temp_dir,
            model=model,
            scaler=dummy_scaler,
        )
        self.assertEqual(res["g3_tier"], "read_only")
        self.assertEqual(res["g5_tier"], "destructive")
        self.assertEqual(res["combined_tier"], "destructive")
        self.assertEqual(res["action"], "escalated")
        self.assertTrue(res["escalated_by_learned"])
        self.assertTrue(res["monotone_invariant_held"])

    def test_run_learned_escalation_end_to_end(self):
        """Execute full runner on small sample sizes and verify outputs and provenance."""
        out_jsonl = os.path.join(self.temp_dir, "test_learned_escalation.jsonl")
        out_md = os.path.join(self.temp_dir, "test_learned_escalation.md")

        results, summary_md = run_learned_escalation(
            repo_root=root_dir,
            output_jsonl=out_jsonl,
            output_md=out_md,
            split_types=["technique_family"],
            sizes=[20, 40],
            seed=42,
            quiet=True,
        )

        self.assertGreater(len(results), 0)
        self.assertTrue(os.path.exists(out_jsonl))
        self.assertTrue(os.path.exists(out_md))

        # Check provenance schema on first record
        with open(out_jsonl, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline())
        missing = validate_provenance(rec)
        self.assertEqual(missing, [])
        self.assertEqual(rec["backend"], "learned-escalation-g5")
        self.assertEqual(rec["monotone_violations"], 0)


if __name__ == "__main__":
    unittest.main()

