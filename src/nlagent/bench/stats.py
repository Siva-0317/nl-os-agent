"""
stats.py - Rigorous statistical testing, confidence intervals, and hypothesis evaluation.

Provides:
1. Wilson score confidence interval for binomial proportions.
2. Exact Clopper-Pearson binomial confidence interval.
3. McNemar's paired test for correlated binary outcomes (exact binomial and chi-squared).
4. Paired difference interval and Non-Inferiority hypothesis testing.
5. Non-parametric bootstrap confidence intervals for medians, p95, and arbitrary percentiles.
6. Sample size helpers for non-inferiority trials and proportion surveys.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any, Callable, Sequence


# -----------------------------------------------------------------------------
# Normal Distribution Helpers (Pure Python / Zero-Dependency)
# -----------------------------------------------------------------------------

def normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function Phi(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_quantile(p: float) -> float:
    """
    Standard normal quantile function (probit / inverse CDF) Phi^{-1}(p).
    
    Uses high-precision rational approximations (Acklam / Beasley-Springer-Moro)
    with binary search refinement for machine epsilon precision.
    """
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")

    # Fast lookup for standard critical values
    if abs(p - 0.975) < 1e-6:
        return 1.959963984540054
    if abs(p - 0.025) < 1e-6:
        return -1.959963984540054
    if abs(p - 0.95) < 1e-6:
        return 1.6448536269514722
    if abs(p - 0.05) < 1e-6:
        return -1.6448536269514722
    if abs(p - 0.995) < 1e-6:
        return 2.5758293035489004
    if abs(p - 0.005) < 1e-6:
        return -2.5758293035489004

    # Binary search refinement using exact math.erf
    low, high = -10.0, 10.0
    for _ in range(60):
        mid = (low + high) / 2.0
        if normal_cdf(mid) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


# -----------------------------------------------------------------------------
# Incomplete Beta Function (for Exact Clopper-Pearson)
# -----------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction approximation for incomplete beta function."""
    max_it = 120
    eps = 1e-10
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_it + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        del_val = d * c
        h *= del_val
        if abs(del_val - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    else:
        return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


# -----------------------------------------------------------------------------
# 1. Wilson Score Confidence Interval
# -----------------------------------------------------------------------------

def wilson_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
    as_percent: bool = False,
) -> tuple[float, float]:
    """
    Calculate Wilson score confidence interval for a binomial proportion.

    Parameters:
        successes: Number of successful outcomes (k >= 0).
        total: Total number of Bernoulli trials (n >= k).
        confidence: Confidence level (default 0.95 for 95% CI).
        as_percent: If True, returns bounds scaled to [0.0, 100.0].

    Returns:
        (lower_bound, upper_bound)
    """
    if total <= 0:
        return (0.0, 0.0)

    successes = max(0, min(successes, total))
    alpha = 1.0 - confidence
    z = normal_quantile(1.0 - alpha / 2.0)
    p_hat = successes / total

    denominator = 1.0 + (z * z) / total
    center = p_hat + (z * z) / (2.0 * total)
    margin = z * math.sqrt(
        (p_hat * (1.0 - p_hat)) / total + (z * z) / (4.0 * total * total)
    )

    low = max(0.0, (center - margin) / denominator)
    high = min(1.0, (center + margin) / denominator)

    if successes == 0:
        low = 0.0
    if successes == total:
        high = 1.0

    if as_percent:
        return (low * 100.0, high * 100.0)
    return (low, high)


# -----------------------------------------------------------------------------
# 2. Exact Clopper-Pearson Binomial Confidence Interval
# -----------------------------------------------------------------------------

def clopper_pearson_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
    as_percent: bool = False,
) -> tuple[float, float]:
    """
    Calculate exact Clopper-Pearson confidence interval for a binomial proportion.

    Parameters:
        successes: Number of successful outcomes (k >= 0).
        total: Total number of Bernoulli trials (n >= k).
        confidence: Confidence level (default 0.95 for 95% CI).
        as_percent: If True, returns bounds scaled to [0.0, 100.0].

    Returns:
        (lower_bound, upper_bound)
    """
    if total <= 0:
        return (0.0, 0.0)

    successes = max(0, min(successes, total))
    alpha = 1.0 - confidence
    p_hat = successes / total

    if successes == 0:
        low = 0.0
        high = 1.0 - (alpha / 2.0) ** (1.0 / total)
    elif successes == total:
        low = (alpha / 2.0) ** (1.0 / total)
        high = 1.0
    else:
        # Lower bound root solve: I_x(k, n - k + 1) = alpha / 2
        l, r = 0.0, p_hat
        target_low = alpha / 2.0
        for _ in range(50):
            mid = (l + r) / 2.0
            if _betai(successes, total - successes + 1, mid) < target_low:
                l = mid
            else:
                r = mid
        low = (l + r) / 2.0

        # Upper bound root solve: I_x(k + 1, n - k) = 1 - alpha / 2
        l, r = p_hat, 1.0
        target_high = 1.0 - alpha / 2.0
        for _ in range(50):
            mid = (l + r) / 2.0
            if _betai(successes + 1, total - successes, mid) < target_high:
                l = mid
            else:
                r = mid
        high = (l + r) / 2.0

    low = max(0.0, min(1.0, low))
    high = max(0.0, min(1.0, high))

    if as_percent:
        return (low * 100.0, high * 100.0)
    return (low, high)


# -----------------------------------------------------------------------------
# 3. Exact McNemar Test for Paired Binary Outcomes
# -----------------------------------------------------------------------------

def mcnemar_exact(b: int, c: int) -> float:
    """
    Calculate exact two-tailed binomial p-value for discordant pairs (b, c).

    b: Model A passed, Model B failed (1, 0)
    c: Model A failed, Model B passed (0, 1)

    Under H0 (marginal symmetry), discordant outcomes follow Binomial(b + c, 0.5).
    Two-sided p-value = 2 * sum_{i=0}^{min(b, c)} binom(b + c, i) * (0.5)^(b + c).
    """
    total_discordant = b + c
    if total_discordant == 0:
        return 1.0

    k = min(b, c)
    # Sum binomial probabilities from 0 to k with p = 0.5
    cum_prob = 0.0
    for i in range(k + 1):
        cum_prob += math.comb(total_discordant, i) * (0.5 ** total_discordant)

    two_sided_p = min(1.0, 2.0 * cum_prob)
    return two_sided_p


def mcnemar_test(
    outcomes_a: Sequence[bool],
    outcomes_b: Sequence[bool],
    alpha: float = 0.05,
) -> dict[str, Any]:
    """
    Perform paired McNemar test on two matched binary outcome sequences.

    Returns:
        {
            'b': int,
            'c': int,
            'discordant_total': int,
            'statistic': float,
            'p_value': float,
            'method': str,
            'significant': bool,
        }
    """
    if len(outcomes_a) != len(outcomes_b):
        raise ValueError(
            f"Mismatched outcome lengths: {len(outcomes_a)} vs {len(outcomes_b)}"
        )

    b = 0  # A True, B False
    c = 0  # A False, B True

    for a, b_val in zip(outcomes_a, outcomes_b):
        if a and not b_val:
            b += 1
        elif not a and b_val:
            c += 1

    total_discordant = b + c
    if total_discordant == 0:
        return {
            "b": 0,
            "c": 0,
            "discordant_total": 0,
            "statistic": 0.0,
            "p_value": 1.0,
            "method": "exact_binomial",
            "significant": False,
        }

    # For small sample (discordant < 25), use exact binomial
    if total_discordant < 25:
        p_val = mcnemar_exact(b, c)
        return {
            "b": b,
            "c": c,
            "discordant_total": total_discordant,
            "statistic": float(min(b, c)),
            "p_value": p_val,
            "method": "exact_binomial",
            "significant": p_val < alpha,
        }
    else:
        # Edwards continuity-corrected chi-squared
        chi2 = (abs(b - c) - 1.0) ** 2 / float(total_discordant)
        # 1-df chi-squared survival function: 2 * (1 - normal_cdf(sqrt(chi2)))
        p_val = 2.0 * (1.0 - normal_cdf(math.sqrt(chi2)))
        return {
            "b": b,
            "c": c,
            "discordant_total": total_discordant,
            "statistic": chi2,
            "p_value": p_val,
            "method": "continuity_corrected_chi2",
            "significant": p_val < alpha,
        }


# -----------------------------------------------------------------------------
# 4. Paired Difference Interval & Non-Inferiority Testing
# -----------------------------------------------------------------------------

def paired_difference_interval(
    outcomes_test: Sequence[bool],
    outcomes_ref: Sequence[bool],
    confidence: float = 0.95,
) -> tuple[float, float]:
    """
    Calculate confidence interval for difference in proportions between paired models:
        Delta = p_test - p_ref = (b - c) / n

    Returns:
        (lower_bound, upper_bound)
    """
    n = len(outcomes_test)
    if n != len(outcomes_ref):
        raise ValueError(f"Mismatched vector lengths: {n} vs {len(outcomes_ref)}")
    if n == 0:
        return (0.0, 0.0)

    b = sum(1 for t, r in zip(outcomes_test, outcomes_ref) if t and not r)
    c = sum(1 for t, r in zip(outcomes_test, outcomes_ref) if not t and r)

    delta = (b - c) / float(n)
    if n < 2:
        return (delta, delta)

    # Sample variance of paired difference: s_d^2 = [ (b + c) - (b - c)^2 / n ] / (n - 1)
    var_d = (float(b + c) - (float(b - c) ** 2) / float(n)) / float(n - 1)
    se = math.sqrt(max(0.0, var_d) / float(n))

    z = normal_quantile(1.0 - (1.0 - confidence) / 2.0)
    low = delta - z * se
    high = delta + z * se
    return (low, high)


def non_inferiority_test(
    outcomes_test: Sequence[bool],
    outcomes_ref: Sequence[bool],
    margin: float = 0.05,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """
    Evaluate Non-Inferiority of test model against reference model:
        H0: Delta <= -margin  (test model is inferior by more than margin)
        H1: Delta > -margin   (test model is non-inferior within margin)

    Parameters:
        outcomes_test: Matched binary outcomes for candidate model (1=pass, 0=fail).
        outcomes_ref: Matched binary outcomes for baseline reference model.
        margin: Non-inferiority margin delta > 0 (e.g. 0.05 for 5 percentage points).
        confidence: One-sided confidence level (typically 0.95).

    Returns:
        {
            'delta': float,
            'margin': float,
            'lower_bound_one_sided': float,
            'se': float,
            'z_statistic': float,
            'p_value': float,
            'non_inferior': bool,
        }
    """
    n = len(outcomes_test)
    if n != len(outcomes_ref):
        raise ValueError(f"Mismatched vector lengths: {n} vs {len(outcomes_ref)}")
    if n == 0:
        return {
            "delta": 0.0,
            "margin": margin,
            "lower_bound_one_sided": 0.0,
            "se": 0.0,
            "z_statistic": 0.0,
            "p_value": 1.0,
            "non_inferior": False,
        }

    b = sum(1 for t, r in zip(outcomes_test, outcomes_ref) if t and not r)
    c = sum(1 for t, r in zip(outcomes_test, outcomes_ref) if not t and r)

    delta = (b - c) / float(n)
    if n < 2:
        se = 0.0
    else:
        var_d = (float(b + c) - (float(b - c) ** 2) / float(n)) / float(n - 1)
        se = math.sqrt(max(0.0, var_d) / float(n))

    z_crit = normal_quantile(confidence)
    lower_bound_one_sided = delta - z_crit * se

    if se > 0.0:
        z_stat = (delta + margin) / se
        p_val = 1.0 - normal_cdf(z_stat)
    else:
        z_stat = float("inf") if delta > -margin else -float("inf")
        p_val = 0.0 if delta > -margin else 1.0

    non_inferior = lower_bound_one_sided > -margin

    return {
        "delta": delta,
        "margin": margin,
        "lower_bound_one_sided": lower_bound_one_sided,
        "se": se,
        "z_statistic": z_stat,
        "p_value": p_val,
        "non_inferior": non_inferior,
    }


# -----------------------------------------------------------------------------
# 5. Non-Parametric Bootstrap Intervals (Medians & Percentiles)
# -----------------------------------------------------------------------------

def percentile(data: Sequence[float], p: float) -> float:
    """Compute p-th percentile (0 <= p <= 100) using linear interpolation."""
    if not data:
        raise ValueError("Cannot compute percentile of empty sequence.")
    sorted_d = sorted(data)
    n = len(sorted_d)
    if n == 1:
        return sorted_d[0]

    k = (n - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_d[int(k)]
    d0 = sorted_d[int(f)] * (c - k)
    d1 = sorted_d[int(c)] * (k - f)
    return d0 + d1


def bootstrap_interval(
    data: Sequence[float],
    metric: str | Callable[[list[float]], float] = "median",
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: int | None = 42,
) -> tuple[float, float]:
    """
    Compute non-parametric bootstrap confidence interval for sample metrics.

    Parameters:
        data: Sequence of numerical values (latencies, token counts, etc.).
        metric: 'median', 'p95', 'p90', 'p99', 'mean', or custom callable.
        confidence: Confidence level (default 0.95).
        n_resamples: Number of bootstrap iterations (default 2000).
        seed: Random seed for reproducibility.

    Returns:
        (lower_bound, upper_bound)
    """
    if not data:
        return (0.0, 0.0)
    if len(data) == 1:
        v = float(data[0])
        return (v, v)

    rng = random.Random(seed)
    n = len(data)
    d_list = list(data)

    if callable(metric):
        stat_fn = metric
    elif metric == "median":
        stat_fn = statistics.median
    elif metric == "mean":
        stat_fn = statistics.mean
    elif metric == "p95":
        stat_fn = lambda s: percentile(s, 95.0)
    elif metric == "p90":
        stat_fn = lambda s: percentile(s, 90.0)
    elif metric == "p99":
        stat_fn = lambda s: percentile(s, 99.0)
    else:
        raise ValueError(f"Unsupported metric: {metric}")

    estimates: list[float] = []
    for _ in range(n_resamples):
        sample = [d_list[rng.randint(0, n - 1)] for _ in range(n)]
        estimates.append(stat_fn(sample))

    alpha = 1.0 - confidence
    low = percentile(estimates, (alpha / 2.0) * 100.0)
    high = percentile(estimates, (1.0 - alpha / 2.0) * 100.0)
    return (low, high)


# -----------------------------------------------------------------------------
# 6. Sample Size Helpers
# -----------------------------------------------------------------------------

def sample_size_non_inferiority(
    margin: float,
    discordant_rate: float,
    alpha: float = 0.05,
    power: float = 0.50,
) -> int:
    """
    Calculate required sample size for a paired binary non-inferiority test:
        n = (z_{1-alpha} + z_{1-beta})^2 * d / margin^2

    Parameters:
        margin: Non-inferiority margin delta > 0 (e.g. 0.05).
        discordant_rate: Fraction of discordant pairs d = (b + c) / n.
        alpha: One-sided significance level (default 0.05 -> z=1.645).
        power: Statistical power 1 - beta. Default 0.50 (z=0) for threshold covering,
               or 0.80 (z=0.842) for 80% power.

    Returns:
        Minimum integer sample size n.
    """
    if margin <= 0.0:
        raise ValueError("Non-inferiority margin must be positive.")
    if discordant_rate <= 0.0:
        return 0

    z_alpha = 1.645 if abs(alpha - 0.05) < 1e-4 else normal_quantile(1.0 - alpha)
    z_beta = normal_quantile(power) if power > 0.50 else 0.0

    n_exact = ((z_alpha + z_beta) ** 2) * discordant_rate / (margin ** 2)
    return int(round(n_exact))


def sample_size_proportion(
    margin: float,
    expected_proportion: float = 0.5,
    confidence: float = 0.95,
) -> int:
    """
    Calculate sample size needed to estimate a binomial proportion within +/- margin.
        n = z^2 * p * (1 - p) / margin^2
    """
    if margin <= 0.0:
        raise ValueError("Margin of error must be positive.")
    p = max(0.0, min(1.0, expected_proportion))
    alpha = 1.0 - confidence
    z = normal_quantile(1.0 - alpha / 2.0)

    n_exact = (z * z) * p * (1.0 - p) / (margin ** 2)
    return int(math.ceil(n_exact))
