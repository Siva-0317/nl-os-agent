"""
layout.py - Cache-aware prompt layout for hybrid and recurrent LLMs.

Follows Section 3.1 of Research_Plan_Context_and_Latency.md:
L0 (static, cached forever)      system prompt + JSON schema + safety rules + fixed few-shot
L1 (per session, changes rarely) OS, shell, user, available tools, path style
L2 (per turn, never cached)      compact STATE block + user request

Rules:
1. Deterministic L0 and L1: sorted keys, no timestamps, no volatile identifiers.
2. Exact append-only prefix layout: full_text starts with L0 and L1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json


@dataclass(frozen=True)
class PromptParts:
    """Holds the 3-layer prompt segments and the assembled full text."""
    l0_text: str
    l1_text: str
    l2_text: str
    full_text: str
    reasoning: bool = True

    @property
    def prefix_text(self) -> str:
        """Returns the stable L0+L1 prefix including delimiter."""
        if self.l1_text:
            return f"{self.l0_text}\n\n{self.l1_text}\n\n"
        return f"{self.l0_text}\n\n"


def _format_l1_value(val: Any) -> str:
    """Deterministically format a value in L1."""
    if isinstance(val, (list, tuple, set)):
        return ", ".join(sorted(str(x) for x in val))
    if isinstance(val, dict):
        return json.dumps(val, sort_keys=True)
    return str(val)


def format_l1(l1: dict[str, Any]) -> str:
    """
    Format L1 session/environment metadata deterministically.

    Rules enforced:
    - Keys are sorted alphabetically.
    - Container values are sorted.
    - Volatile fields (e.g. timestamps, datetime objects) are disallowed.
    """
    if not l1:
        return ""

    lines = []
    for k in sorted(l1.keys()):
        val = l1[k]
        if k.lower() in {"timestamp", "time", "date", "datetime", "random_id", "request_id"}:
            raise ValueError(f"Volatile key '{k}' is forbidden in L1 (violates cache prefix stability)")
        if hasattr(val, "isoformat") or hasattr(val, "strftime"):
            raise ValueError(f"Timestamp/date object is forbidden in L1: {k}={val}")

        formatted_val = _format_l1_value(val)
        lines.append(f"{k}: {formatted_val}")

    return "\n".join(lines)


def format_l2(state: str, request: str) -> str:
    """Format L2 volatile turn context: compact state block and user request."""
    parts = []
    if state and state.strip():
        parts.append(state.strip())
    if request and request.strip():
        parts.append(request.strip())
    return "\n".join(parts)


DEFAULT_FULL_L0 = (
    "You are an AI-native operating system shell agent.\n"
    "Convert the user's natural language request into an exact terminal command matching the active environment.\n"
    "Output ONLY a single valid JSON object with keys 'command', 'targets', and 'clarifying_question'.\n"
    "Do not include markdown code fences or conversational text outside the JSON schema."
)

DEFAULT_SHORT_L0 = (
    "Terminal agent. Output JSON: {\"command\": \"...\", \"targets\": [...], \"clarifying_question\": null}."
)


def build_prompt(
    l0: str,
    l1: dict[str, Any],
    state: str,
    request: str,
    reasoning: bool = True,
    mode: str = "full",
) -> PromptParts:
    """
    Build a 3-layer cache-aware prompt.

    Parameters:
        l0: Static system prompt, schema, and safety instructions (L0).
        l1: Session environment dict (L1, deterministic).
        state: Compact session state block (L2).
        request: Natural language user request (L2).
        reasoning: When False, disables chain-of-thought thinking via model card mechanism (<think></think>).
        mode: 'full' (standard complete prompt) or 'short' (condensed prompt under 100 tokens).

    Returns:
        PromptParts with l0_text, l1_text, l2_text, full_text, and reasoning flag.
    """
    if mode == "short" and (not l0 or l0 == DEFAULT_FULL_L0):
        l0_text = DEFAULT_SHORT_L0
    else:
        l0_text = l0.strip()
    l1_text = format_l1(l1)
    l2_text = format_l2(state, request)

    if l1_text:
        prefix = f"{l0_text}\n\n{l1_text}\n\n"
    else:
        prefix = f"{l0_text}\n\n"

    full_text = f"{prefix}{l2_text}" if l2_text else prefix.rstrip()

    # Model card instruction: closing <think></think> prefill disables thinking generation
    if not reasoning and "<think>" not in full_text:
        full_text = f"{full_text}\n<think></think>"

    return PromptParts(
        l0_text=l0_text,
        l1_text=l1_text,
        l2_text=l2_text,
        full_text=full_text,
        reasoning=reasoning,
    )

