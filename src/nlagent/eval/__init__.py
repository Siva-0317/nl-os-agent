"""
src/nlagent/eval - Execution-based evaluation and benchmark testing package.
"""

from __future__ import annotations

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

__all__ = [
    "TestCase",
    "EvalResult",
    "evaluate_case",
    "evaluate_batch",
    "materialize_fixture",
    "normalize_stdout",
    "compare_stdout",
    "compare_manifests",
    "summarize_results",
]

