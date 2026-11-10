"""
Baseline safety guards ported from open-source agent CLIs and published benchmarks.

This package provides standardized safety evaluators for:
- AutoGPT (`autogpt.py`)
- Open-Interpreter (`open_interpreter.py`)
- LangChain ShellTool (`langchain_shell.py`)
- Aider (`aider.py`)
- DestructiveBench G0-simple (`g0_simple.py`)
- DestructiveBench G0-published (`g0_published.py`)

Each baseline implements the exact logic or heuristics from its upstream reference,
reimplemented for license compatibility and cross-platform (POSIX, Windows CMD, PowerShell) evaluation.
"""

from __future__ import annotations

from typing import Callable, Mapping

from nlagent.safety.baselines.aider import evaluate_aider_guard
from nlagent.safety.baselines.autogpt import BaselineDecision, evaluate_autogpt_guard
from nlagent.safety.baselines.g0_published import evaluate_g0_published
from nlagent.safety.baselines.g0_simple import evaluate_g0_simple
from nlagent.safety.baselines.langchain_shell import evaluate_langchain_guard
from nlagent.safety.baselines.open_interpreter import evaluate_open_interpreter_guard

BASELINE_EVALUATORS: Mapping[str, Callable[[str], BaselineDecision]] = {
    "autogpt": evaluate_autogpt_guard,
    "open_interpreter": evaluate_open_interpreter_guard,
    "langchain": evaluate_langchain_guard,
    "aider": evaluate_aider_guard,
    "g0_simple": evaluate_g0_simple,
    "g0_published": evaluate_g0_published,
}

AVAILABLE_BASELINES: list[str] = list(BASELINE_EVALUATORS.keys())


def evaluate_baseline(baseline_id: str, command: str) -> BaselineDecision:
    """
    Evaluate a command against a specified baseline guard.

    Args:
        baseline_id: Name of baseline ("autogpt", "open_interpreter", "langchain",
                     "aider", "g0_simple", "g0_published").
        command: The raw shell command string.

    Returns:
        BaselineDecision dataclass with baseline_id, action, tier, reason, and latency_ms.
    """
    evaluator = BASELINE_EVALUATORS.get(baseline_id)
    if not evaluator:
        raise ValueError(
            f"Unknown baseline '{baseline_id}'. Available baselines: {AVAILABLE_BASELINES}"
        )
    return evaluator(command)


__all__ = [
    "BaselineDecision",
    "BASELINE_EVALUATORS",
    "AVAILABLE_BASELINES",
    "evaluate_baseline",
    "evaluate_autogpt_guard",
    "evaluate_open_interpreter_guard",
    "evaluate_langchain_guard",
    "evaluate_aider_guard",
    "evaluate_g0_simple",
    "evaluate_g0_published",
]

