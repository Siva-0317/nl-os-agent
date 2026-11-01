"""
state.py - Compact semantic state management for LLM OS Agent.

Follows Section 3.2 of Research_Plan_Context_and_Latency.md:
Instead of replaying full multi-turn command transcripts (O(n) prompt growth),
renders a bounded state block:

STATE cwd=/home/u/proj
ENT f1=./report.pdf(last:t3) d1=./backup(last:t2)
LAST t4: mkdir backup -> created 1; t5: cp *.pdf backup/ -> created 3

Truncates oldest items first to respect max_tokens bound.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class Entity:
    """Tracked system entity (file, directory, process)."""
    name: str
    type: Literal["file", "dir", "process"]
    path: str
    last_turn: int


@dataclass
class EffectRecord:
    """Historical command effect record."""
    turn: int
    command: str
    summary: str


REFERENCE_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"\bthat file\b", re.IGNORECASE), "file"),
    (re.compile(r"\bthe file\b", re.IGNORECASE), "file"),
    (re.compile(r"\bthat script\b", re.IGNORECASE), "file"),
    (re.compile(r"\bthe script\b", re.IGNORECASE), "file"),
    (re.compile(r"\bthat folder\b", re.IGNORECASE), "dir"),
    (re.compile(r"\bthe folder\b", re.IGNORECASE), "dir"),
    (re.compile(r"\bthat directory\b", re.IGNORECASE), "dir"),
    (re.compile(r"\bthe directory\b", re.IGNORECASE), "dir"),
    (re.compile(r"\bthat process\b", re.IGNORECASE), "process"),
    (re.compile(r"\bthe process\b", re.IGNORECASE), "process"),
    (re.compile(r"\bit\b", re.IGNORECASE), None),
]


def count_tokens_approx(text: str) -> int:
    """Whitespace approximation for token count."""
    if not text:
        return 0
    return len(text.split())


class SessionState:
    """
    Maintains session working directory, entity table, and recent command effects.
    Renders a compact, bounded STATE block for the L2 prompt layer.
    """

    def __init__(
        self,
        cwd: str = ".",
        entities: dict[str, Entity] | list[Entity] | None = None,
        effects: list[EffectRecord] | None = None,
    ) -> None:
        self.cwd = cwd.strip() if cwd else "."
        self.entities: dict[str, Entity] = {}
        if entities:
            if isinstance(entities, dict):
                self.entities.update(entities)
            elif isinstance(entities, list):
                for e in entities:
                    self.entities[e.path] = e

        self.effects: list[EffectRecord] = list(effects[-3:]) if effects else []

    def update_cwd(self, new_cwd: str) -> None:
        """Update current working directory."""
        if new_cwd and new_cwd.strip():
            self.cwd = new_cwd.strip()

    def record_entity(
        self,
        name: str,
        entity_type: Literal["file", "dir", "process"],
        path: str,
        turn: int,
    ) -> Entity:
        """Record or update an entity reference."""
        entity = Entity(
            name=name.strip(),
            type=entity_type,
            path=path.strip(),
            last_turn=turn,
        )
        self.entities[entity.path] = entity
        return entity

    def record_effect(self, turn: int, command: str, summary: str) -> None:
        """Record a command execution effect, keeping only the last 3 effects."""
        self.effects.append(
            EffectRecord(
                turn=turn,
                command=command.strip(),
                summary=summary.strip(),
            )
        )
        # Keep strictly the last 3 effects
        if len(self.effects) > 3:
            self.effects = self.effects[-3:]

    def lookup_latest_entity(
        self, entity_type: str | None = None
    ) -> Entity | None:
        """Find the most recently referenced entity matching entity_type."""
        candidates = list(self.entities.values())
        if entity_type:
            candidates = [e for e in candidates if e.type == entity_type]
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.last_turn)

    def resolve_reference(self, text: str) -> str:
        """Replace referring expressions (e.g. 'that file', 'that folder', 'it') with entity paths."""
        resolved = text
        for pattern, entity_type in REFERENCE_PATTERNS:
            if pattern.search(resolved):
                entity = self.lookup_latest_entity(entity_type)
                if entity:
                    resolved = pattern.sub(entity.path, resolved)
        return resolved

    def _build_block(
        self, entities_subset: list[Entity], effects_subset: list[EffectRecord]
    ) -> str:
        """Construct the formatted compact STATE block string."""
        lines = [f"STATE cwd={self.cwd}"]

        if entities_subset:
            type_counters: dict[str, int] = {}
            ent_parts: list[str] = []
            for ent in entities_subset:
                prefix = (
                    "f"
                    if ent.type == "file"
                    else ("d" if ent.type == "dir" else "p")
                )
                type_counters[prefix] = type_counters.get(prefix, 0) + 1
                alias = f"{prefix}{type_counters[prefix]}"
                ent_parts.append(f"{alias}={ent.path}(last:t{ent.last_turn})")
            lines.append("ENT " + " ".join(ent_parts))

        if effects_subset:
            effect_parts = [
                f"t{eff.turn}: {eff.command} -> {eff.summary}"
                for eff in effects_subset
            ]
            lines.append("LAST " + "; ".join(effect_parts))

        return "\n".join(lines)

    def render(self, max_tokens: int = 200) -> str:
        """
        Render compact semantic state block bounded by max_tokens.
        Truncates oldest items first (oldest effects, then oldest entities).
        Token count measured via whitespace approximation.
        """
        # Entities ordered newest first so oldest entities are at the end of the list
        candidate_entities = sorted(
            self.entities.values(), key=lambda e: e.last_turn, reverse=True
        )
        # Effects in chronological order (oldest at index 0)
        candidate_effects = list(self.effects)

        block = self._build_block(candidate_entities, candidate_effects)
        if count_tokens_approx(block) <= max_tokens:
            return block

        # 1. Truncate oldest effects first
        while candidate_effects and count_tokens_approx(block) > max_tokens:
            candidate_effects.pop(0)  # drop oldest effect
            block = self._build_block(candidate_entities, candidate_effects)

        if count_tokens_approx(block) <= max_tokens:
            return block

        # 2. Truncate oldest entities next (from the end of candidate_entities)
        while candidate_entities and count_tokens_approx(block) > max_tokens:
            candidate_entities.pop(-1)  # drop oldest entity
            block = self._build_block(candidate_entities, candidate_effects)

        if count_tokens_approx(block) <= max_tokens:
            return block

        # Fallback to minimal STATE line if budget is extremely tight
        fallback = f"STATE cwd={self.cwd}"
        if count_tokens_approx(fallback) <= max_tokens:
            return fallback

        words = fallback.split()
        return " ".join(words[:max_tokens])

