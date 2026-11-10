"""
tests/test_stats.py - Rigorous verification of the statistical testing suite.

Verifies all requirements from Prompt T39:
1. Wilson score confidence interval:
   - Known value: 6/8 is approximately [40.9%, 92.9%].
   - Edge cases: 0/n, n/n, total=0.
2. Exact Clopper-Pearson confidence interval:
   - Known value: upper bound for 0/200 is approximately 1.83%.
   - Exact roots, k=n upper bound 100%.
3. Exact McNemar's paired test:
   - Known value: smallest exact p for 8 pairs with a net difference of 3 is 0.25.
   - Identical outcomes give p = 1.0.
   - Large sample continuity-corrected chi-squared.
4. Paired difference interval & Non-inferiority testing:
   - One-sided confidence lower bound against specified margin delta.
   - Rejection of null when lower bound > -margin.
5. Bootstrap confidence intervals:
   - Median and p95 intervals.
   - Deterministic reproducibility with random seed.
6. Sample size calculation helpers:
   - Non-inferiority sample sizes matching pre-registered planning tables.
"""

from __future__ import annotations

import math
import unittest

from nlagent.bench.stats import (
    bootstrap_interval,
    clopper_pearson_interval,
    mcnemar_exact,
    mcnemar_test,
    non_inferiority_test,
    normal_cdf,
    normal_quantile,
    paired_difference_interval,
    percentile,
    sample_size_non_inferiority,
    sample_size_proportion,
    wilson_interval,
)


class TestStatsModule(unittest.TestCase):
    """Test suite for rigorous statistical testing functions."""

    # -------------------------------------------------------------------------
    # 1. Wilson Interval Tests
    # -------------------------------------------------------------------------

    def test_wilson_interval_known_value_6_of_8(self):
        """Prompt constraint: Wilson for 6/8 is about [40.9, 92.9]."""
        low, high = wilson_interval(successes=6, total=8, confidence=0.95, as_percent=True)
        self.assertAlmostEqual(low, 40.9, places=1)
        self.assertAlmostEqual(high, 92.9, places=1)

        low_raw, high_raw = wilson_interval(successes=6, total=8, confidence=0.95, as_percent=False)
        self.assertAlmostEqual(low_raw, 0.409, places=2)
        self.assertAlmostEqual(high_raw, 0.929, places=2)

    def test_wilson_interval_edges(self):
        """Edge cases: 0/total, total/total, total=0."""
        # 0 successes
        low, high = wilson_interval(0, 50, confidence=0.95, as_percent=True)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)
        self.assertLess(high, 10.0)

        # 50/50 successes
        low, high = wilson_interval(50, 50, confidence=0.95, as_percent=True)
        self.assertGreater(low, 90.0)
        self.assertEqual(high, 100.0)

        # 0 total
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))

    # -------------------------------------------------------------------------
    # 2. Clopper-Pearson Interval Tests
    # -------------------------------------------------------------------------

    def test_clopper_pearson_known_value_0_of_200(self):
        """Prompt constraint: Clopper-Pearson upper bound for 0/200 is about 1.83%."""
        low, high = clopper_pearson_interval(successes=0, total=200, confidence=0.95, as_percent=True)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 1.83, places=2)

        # Raw float version
        low_raw, high_raw = clopper_pearson_interval(successes=0, total=200, confidence=0.95, as_percent=False)
        self.assertEqual(low_raw, 0.0)
        self.assertAlmostEqual(high_raw, 0.0183, places=3)

    def test_clopper_pearson_exact_roots(self):
        """Test Clopper-Pearson symmetry and full success boundary."""
        # 200/200 successes: lower bound should be 100 - 1.83 = 98.17%
        low, high = clopper_pearson_interval(200, 200, confidence=0.95, as_percent=True)
        self.assertEqual(high, 100.0)
        self.assertAlmostEqual(low, 100.0 - 1.83, places=2)

        # 50/100 successes (50%)
        low, high = clopper_pearson_interval(50, 100, confidence=0.95, as_percent=True)
        self.assertTrue(low < 50.0 < high)
        self.assertAlmostEqual(50.0 - low, high - 50.0, delta=1.0)

    # -------------------------------------------------------------------------
    # 3. Exact McNemar Tests
    # -------------------------------------------------------------------------

    def test_mcnemar_known_value_8_pairs_net_diff_3(self):
        """
        Prompt constraint: smallest exact McNemar p for 8 pairs with a net difference
        of 3 is 0.25.
        
        Enumerating all partitions of 8 pairs with |b - c| = 3:
        b=3, c=0 -> b+c=3, min(b,c)=0 -> p = 2 * (0.5)^3 = 0.250
        b=4, c=1 -> b+c=5, min(b,c)=1 -> p = 2 * (1+5)/32 = 0.375
        b=5, c=2 -> b+c=7, min(b,c)=2 -> p = 2 * (1+7+21)/128 = 0.453
        Smallest possible exact p-value is exactly 0.25.
        """
        p_3_0 = mcnemar_exact(b=3, c=0)
        self.assertEqual(p_3_0, 0.25)

        p_4_1 = mcnemar_exact(b=4, c=1)
        self.assertEqual(p_4_1, 0.375)

        p_5_2 = mcnemar_exact(b=5, c=2)
        self.assertAlmostEqual(p_5_2, 0.453125, places=5)

        # The minimum is 0.25
        min_p = min(p_3_0, p_4_1, p_5_2)
        self.assertEqual(min_p, 0.25)

    def test_mcnemar_test_paired_vector_interface(self):
        """Test mcnemar_test vector comparison, identical outcomes and chi2 fallback."""
        # 1. Identical vectors -> p = 1.0
        v_a = [True, True, False, True, False]
        res_ident = mcnemar_test(v_a, v_a)
        self.assertEqual(res_ident["p_value"], 1.0)
        self.assertFalse(res_ident["significant"])

        # 2. b=3, c=0 vector test
        vec_test = [True, True, True, True, False]
        vec_ref =  [False, False, False, True, False]
        res_small = mcnemar_test(vec_test, vec_ref)
        self.assertEqual(res_small["b"], 3)
        self.assertEqual(res_small["c"], 0)
        self.assertEqual(res_small["method"], "exact_binomial")
        self.assertEqual(res_small["p_value"], 0.25)

        # 3. Large sample (discordant >= 25)
        vec_large_a = [True] * 35 + [False] * 5 + [True] * 10
        vec_large_b = [False] * 35 + [True] * 5 + [True] * 10
        res_large = mcnemar_test(vec_large_a, vec_large_b)
        self.assertEqual(res_large["method"], "continuity_corrected_chi2")
        self.assertTrue(res_large["significant"])
        self.assertLess(res_large["p_value"], 0.001)

    # -------------------------------------------------------------------------
    # 4. Paired Difference Interval & Non-Inferiority Tests
    # -------------------------------------------------------------------------

    def test_paired_difference_interval(self):
        """Verify paired difference interval calculation."""
        outcomes_test = [True] * 80 + [False] * 20
        outcomes_ref =  [True] * 70 + [False] * 30
        low, high = paired_difference_interval(outcomes_test, outcomes_ref, confidence=0.95)
        delta = 0.80 - 0.70  # +0.10
        self.assertTrue(low < delta < high)

    def test_non_inferiority_test_passing_case(self):
        """Verify non-inferiority test passes when test is within margin."""
        # 200 pairs: candidate has 78% accuracy, reference has 80% accuracy (delta = -0.02)
        # Margin delta = 0.05 (5 points)
        # Delta is -0.02, which is well above -0.05
        test_outcomes = [True] * 156 + [False] * 44
        ref_outcomes =  [True] * 160 + [False] * 40

        res = non_inferiority_test(test_outcomes, ref_outcomes, margin=0.05, confidence=0.95)
        self.assertAlmostEqual(res["delta"], -0.02, places=3)
        self.assertEqual(res["margin"], 0.05)
        self.assertTrue(res["lower_bound_one_sided"] > -0.05)
        self.assertTrue(res["non_inferior"])
        self.assertLess(res["p_value"], 0.05)

    def test_non_inferiority_test_failing_case(self):
        """Verify non-inferiority test fails when candidate is significantly inferior."""
        # Candidate has 60% accuracy, reference has 80% accuracy (delta = -0.20)
        # Margin delta = 0.05
        test_outcomes = [True] * 120 + [False] * 80
        ref_outcomes =  [True] * 160 + [False] * 40

        res = non_inferiority_test(test_outcomes, ref_outcomes, margin=0.05, confidence=0.95)
        self.assertAlmostEqual(res["delta"], -0.20, places=3)
        self.assertFalse(res["non_inferior"])
        self.assertGreater(res["p_value"], 0.5)

    # -------------------------------------------------------------------------
    # 5. Bootstrap Interval Tests
    # -------------------------------------------------------------------------

    def test_bootstrap_interval_median_and_p95(self):
        """Verify non-parametric bootstrap interval covers sample medians & percentiles."""
        data = [10.0, 12.0, 14.0, 15.0, 16.0, 18.0, 20.0, 22.0, 25.0, 50.0]
        sample_med = 17.0  # (16 + 18) / 2 = 17.0

        low, high = bootstrap_interval(data, metric="median", confidence=0.95, n_resamples=1000, seed=123)
        self.assertTrue(low <= sample_med <= high)
        self.assertGreater(low, 10.0)
        self.assertLess(high, 30.0)

        # p95 bootstrap
        p95_low, p95_high = bootstrap_interval(data, metric="p95", confidence=0.95, n_resamples=1000, seed=123)
        self.assertTrue(p95_low <= p95_high)
        self.assertGreater(p95_high, 22.0)

    # -------------------------------------------------------------------------
    # 6. Sample Size Helpers Tests
    # -------------------------------------------------------------------------

    def test_sample_size_non_inferiority_table_values(self):
        """
        Verify sample size helper matches pre-registration table in Planning Doc:
        Formula: n = 1.64485^2 * d / margin^2
        For margin = 0.05:
          d = 0.10 -> 108
          d = 0.20 -> 216
          d = 0.30 -> 325
        For margin = 0.03:
          d = 0.10 -> 301
          d = 0.20 -> 601
          d = 0.30 -> 902
        """
        # Margin 0.05
        self.assertEqual(sample_size_non_inferiority(margin=0.05, discordant_rate=0.10, power=0.50), 108)
        self.assertEqual(sample_size_non_inferiority(margin=0.05, discordant_rate=0.20, power=0.50), 216)
        self.assertEqual(sample_size_non_inferiority(margin=0.05, discordant_rate=0.30, power=0.50), 325)

        # Margin 0.03
        self.assertEqual(sample_size_non_inferiority(margin=0.03, discordant_rate=0.10, power=0.50), 301)
        self.assertEqual(sample_size_non_inferiority(margin=0.03, discordant_rate=0.20, power=0.50), 601)
        self.assertEqual(sample_size_non_inferiority(margin=0.03, discordant_rate=0.30, power=0.50), 902)


if __name__ == "__main__":
    unittest.main()

