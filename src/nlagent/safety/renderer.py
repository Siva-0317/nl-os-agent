"""
src/nlagent/safety/renderer.py - Deterministic Plain-English Consequence Generator.

Responsibilities:
1. Converts raw AST Effect models into structured EffectSummary and ExecutionPlan instances.
2. Translates technical effects into clear, non-technical plain-English descriptions:
   - Target counts, total bytes affected, human-readable directory scopes.
   - Specific warnings for overwrite of existing files and wildcard expansion.
   - Explicit alerts for drive root deletions and bulk operations.
   - Clear reversibility and quarantine rollback status.
3. Enforces safety defaults: Cancel MUST be the default action for destructive/privileged tiers.
4. Deterministic symbolic code guarantees the LLM cannot soften or manipulate safety warnings.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from nlagent.safety.effects import Effect, analyse, tier_from_effect


def format_bytes_human(b: int) -> str:
    """Format byte integer into a clean, human-readable string (B, KB, MB, GB)."""
    if b <= 0:
        return "0 B"
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


def is_root_or_drive_path(path_str: str) -> bool:
    """Check if path represents a drive root (e.g. /, C:\\, C:/, D:\\, D:/)."""
    clean = path_str.strip().replace("\\", "/").rstrip("/")
    if clean in ("", "/", "c:", "d:", "e:", "f:"):
        return True
    if re.match(r"^[a-zA-Z]:/?$", clean):
        return True
    if clean in ("/root", "c:/windows", "c:/program files"):
        return True
    return False


def summarize_directory_scopes(paths: Sequence[str], cwd: str = "") -> list[str]:
    """
    Extract human-readable directory scopes from a collection of affected paths.
    Collapses long lists of files into unique directory names relative to cwd or root.
    """
    if not paths:
        return []

    norm_cwd = os.path.normpath(cwd).replace("\\", "/") if cwd else ""
    scopes: list[str] = []

    for p in paths:
        if not p or p.startswith("registry:") or p.startswith("pid:"):
            continue

        clean_p = p.replace("\\", "/").strip()
        if is_root_or_drive_path(clean_p):
            root_display = clean_p if clean_p.endswith("/") else clean_p + "/"
            if root_display not in scopes:
                scopes.append(root_display)
            continue

        # Extract parent directory
        parent = os.path.dirname(clean_p)
        if not parent or parent == ".":
            display = os.path.basename(norm_cwd) or "current folder"
        elif norm_cwd and parent.startswith(norm_cwd):
            rel = parent[len(norm_cwd):].lstrip("/")
            display = rel or os.path.basename(norm_cwd) or "current folder"
        else:
            # Home directory or absolute path collapse
            home = os.path.expanduser("~").replace("\\", "/")
            if parent.startswith(home):
                display = "~" + parent[len(home):]
            else:
                display = parent

        display = display.rstrip("/") + "/"
        if display not in scopes:
            scopes.append(display)

    if not scopes:
        return ["current folder"]

    return sorted(scopes, key=lambda s: (len(s), s))


@dataclass
class EffectSummary:
    """
    Structured summary of static filesystem and system consequences.
    Decoupled from raw AST internals for deterministic reporting.
    """
    deletes: list[str] = field(default_factory=list)
    overwrites: list[str] = field(default_factory=list)
    creates: list[str] = field(default_factory=list)
    appends: list[str] = field(default_factory=list)
    perm_changes: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    registry_writes: list[str] = field(default_factory=list)
    bytes_affected: int = 0
    directory_scopes: list[str] = field(default_factory=list)
    is_reversible: bool = False
    quarantine_eligible: bool = False
    is_drive_root: bool = False
    has_wildcards: bool = False
    is_bulk: bool = False
    protected_hit: bool = False
    hard_block: bool = False
    opaque: bool = False
    external: bool = False
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_effect(
        cls,
        effect: Effect,
        command: str = "",
        cwd: str = "",
        enable_quarantine: bool = True,
    ) -> EffectSummary:
        """Construct an EffectSummary from an AST Effect and command string."""
        all_destructive = effect.deletes + effect.overwrites

        # Wildcard detection in command or paths
        has_wildcards = any(c in command for c in ("*", "?", "[")) or any(
            any(c in p for c in ("*", "?", "[")) for p in all_destructive
        )

        # Drive root check
        is_drive_root = effect.hard_block or any(is_root_or_drive_path(p) for p in all_destructive)

        # Bulk operation check (>= 25 items or >= 50 MB)
        count_affected = len(effect.deletes) + len(effect.overwrites)
        is_bulk = count_affected >= 25 or effect.bytes_affected >= (50 * 1024 * 1024)

        # Directory scopes
        all_paths = effect.deletes + effect.overwrites + effect.creates + effect.appends + effect.perm_changes
        scopes = summarize_directory_scopes(all_paths, cwd=cwd)

        # Quarantine eligibility & reversibility
        quarantine_eligible = bool(enable_quarantine and (effect.deletes or effect.overwrites))
        # Payload size cap: > 500 MB exceeds automatic quarantine limit
        is_reversible = bool(
            quarantine_eligible
            and not is_drive_root
            and not effect.hard_block
            and effect.bytes_affected < (500 * 1024 * 1024)
        )

        return cls(
            deletes=list(effect.deletes),
            overwrites=list(effect.overwrites),
            creates=list(effect.creates),
            appends=list(effect.appends),
            perm_changes=list(effect.perm_changes),
            signals=list(effect.signals),
            registry_writes=list(effect.registry_writes),
            bytes_affected=effect.bytes_affected,
            directory_scopes=scopes,
            is_reversible=is_reversible,
            quarantine_eligible=quarantine_eligible,
            is_drive_root=is_drive_root,
            has_wildcards=has_wildcards,
            is_bulk=is_bulk,
            protected_hit=effect.protected_hit,
            hard_block=effect.hard_block,
            opaque=effect.opaque,
            external=effect.external,
            notes=list(effect.notes),
        )


def render_plain_english_consequence(summary: EffectSummary) -> str:
    """
    Generate a clear, deterministic non-technical description of the command's consequence.
    """
    # 1. Critical Hard Block & Drive Root Alerts
    if summary.hard_block:
        return "BLOCKED: Command attempts raw disk or partition formatting. Direct device operations are forbidden."

    if summary.is_drive_root:
        root_name = summary.directory_scopes[0] if summary.directory_scopes else "system root"
        return f"CRITICAL: Will attempt to delete the entire filesystem root ({root_name}). This will cause unrecoverable system destruction."

    if summary.protected_hit:
        scopes_str = ", ".join(summary.directory_scopes[:3])
        return f"PRIVILEGED: Targets protected operating system directories ({scopes_str}). Requires administrative approval."

    # 2. Deletions
    if summary.deletes:
        cnt = len(summary.deletes)
        b_str = format_bytes_human(summary.bytes_affected)
        scope_str = summary.directory_scopes[0] if summary.directory_scopes else "current folder"

        if summary.is_bulk:
            dir_count = len(summary.directory_scopes)
            return f"BULK DELETION: Will delete {cnt} items across {dir_count} directories ({b_str})."

        if summary.has_wildcards:
            return f"Will delete {cnt} file{'s' if cnt != 1 else ''} matching wildcard pattern in '{scope_str}' ({b_str})."

        if cnt == 1:
            target_name = os.path.basename(summary.deletes[0]) or summary.deletes[0]
            return f"Will delete '{target_name}' in '{scope_str}' ({b_str})."

        return f"Will delete {cnt} files in '{scope_str}' ({b_str})."

    # 3. Overwrites
    if summary.overwrites:
        cnt = len(summary.overwrites)
        scope_str = summary.directory_scopes[0] if summary.directory_scopes else "current folder"
        if cnt == 1:
            target_name = os.path.basename(summary.overwrites[0]) or summary.overwrites[0]
            return f"Will overwrite '{target_name}' in '{scope_str}' (existing file will be replaced)."
        return f"Will overwrite {cnt} existing files in '{scope_str}' (existing files will be replaced)."

    # 4. State Modifications (Creates, Appends, Perms)
    if summary.creates and summary.appends:
        cnt_c = len(summary.creates)
        cnt_a = len(summary.appends)
        scope_str = summary.directory_scopes[0] if summary.directory_scopes else "current folder"
        return f"Will create {cnt_c} new item{'s' if cnt_c != 1 else ''} and append data to {cnt_a} file{'s' if cnt_a != 1 else ''} in '{scope_str}'."

    if summary.creates:
        cnt = len(summary.creates)
        scope_str = summary.directory_scopes[0] if summary.directory_scopes else "current folder"
        if cnt == 1:
            target_name = os.path.basename(summary.creates[0]) or summary.creates[0]
            return f"Will create '{target_name}' in '{scope_str}'."
        return f"Will create {cnt} new file{'s' if cnt != 1 else ''} in '{scope_str}'."

    if summary.appends:
        cnt = len(summary.appends)
        scope_str = summary.directory_scopes[0] if summary.directory_scopes else "current folder"
        if cnt == 1:
            target_name = os.path.basename(summary.appends[0]) or summary.appends[0]
            return f"Will append data to '{target_name}' in '{scope_str}'."
        return f"Will append data to {cnt} file{'s' if cnt != 1 else ''} in '{scope_str}'."

    if summary.perm_changes:
        cnt = len(summary.perm_changes)
        scope_str = summary.directory_scopes[0] if summary.directory_scopes else "current folder"
        return f"Will modify file access permissions on {cnt} item{'s' if cnt != 1 else ''} in '{scope_str}'."

    # 5. Process signals (kills)
    if summary.signals:
        sig_str = ", ".join(summary.signals[:3])
        return f"Will terminate running system processes ({sig_str})."

    # 6. Windows Registry writes
    if summary.registry_writes:
        cnt = len(summary.registry_writes)
        return f"Will modify {cnt} Windows Registry setting{'s' if cnt != 1 else ''}."

    # 7. Ambiguous / Opaque / External
    if summary.opaque:
        return "Ambiguous consequence: Command contains nested shells or dynamic interpreter script code that cannot be statically inspected."

    if summary.external:
        return "External side effects: Accesses network resources or package repositories. Local filesystem consequences cannot be fully verified in advance."

    # 8. Read-only
    return "Read-only command. Inspects system state without modifying any files or settings."


def render_reversibility_description(summary: EffectSummary) -> str:
    """Describe whether this operation can be safely rolled back."""
    if summary.is_reversible:
        return "Protected by shadow quarantine backup. Can be rolled back at any time with 'nlctl undo'."

    if summary.deletes or summary.overwrites:
        if summary.bytes_affected >= (500 * 1024 * 1024):
            return "IRREVERSIBLE: Payload exceeds the 500 MB quarantine backup limit. Changes cannot be rolled back."
        if summary.is_drive_root or summary.hard_block:
            return "IRREVERSIBLE: Drive-level operations cannot be quarantined or undone."
        return "IRREVERSIBLE: Quarantine shadow backup is disabled. Changes cannot be rolled back."

    if summary.creates or summary.appends or summary.perm_changes:
        return "State modification: New files or permission changes are not quarantined."

    return "No filesystem changes to undo."


@dataclass
class ExecutionPlan:
    """
    Complete safety execution plan presenting consequences and default actions to the user.
    """
    command: str
    tier: str
    effect_summary: EffectSummary
    plain_english_description: str
    reversibility_description: str
    default_action: str  # "cancel" or "execute"
    confirmation_prompt: str
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "tier": self.tier,
            "plain_english_description": self.plain_english_description,
            "reversibility_description": self.reversibility_description,
            "default_action": self.default_action,
            "confirmation_prompt": self.confirmation_prompt,
            "directory_scopes": self.effect_summary.directory_scopes,
            "bytes_affected": self.effect_summary.bytes_affected,
            "bytes_affected_human": format_bytes_human(self.effect_summary.bytes_affected),
            "explanation": self.explanation,
        }

    @classmethod
    def create(
        cls,
        command: str,
        cwd: str = "",
        tier: str | None = None,
        effect: Effect | None = None,
        explanation: str = "",
        enable_quarantine: bool = True,
    ) -> ExecutionPlan:
        """
        Build an ExecutionPlan from command string and environment context.
        """
        eff = effect if effect is not None else analyse(command, cwd=cwd)
        resolved_tier = tier or tier_from_effect(eff)
        summary = EffectSummary.from_effect(eff, command=command, cwd=cwd, enable_quarantine=enable_quarantine)

        consequence_text = render_plain_english_consequence(summary)
        reversibility_text = render_reversibility_description(summary)

        # Safety Rule: Cancel MUST be designated as the default action for destructive tiers
        is_high_risk = (
            resolved_tier in ("destructive", "privileged", "ambiguous")
            or summary.is_bulk
            or summary.is_drive_root
            or summary.hard_block
        )

        if is_high_risk:
            default_action = "cancel"
            confirmation_prompt = "Press Enter to abort (Cancel is default), or type 'yes' to proceed: "
        else:
            default_action = "execute"
            confirmation_prompt = "Press Enter to run, or Ctrl+C to abort: "

        return cls(
            command=command,
            tier=resolved_tier,
            effect_summary=summary,
            plain_english_description=consequence_text,
            reversibility_description=reversibility_text,
            default_action=default_action,
            confirmation_prompt=confirmation_prompt,
            explanation=explanation,
        )


def render_full_preview(plan: ExecutionPlan) -> str:
    """
    Format a multi-line, plain-English preview banner for terminal display.
    """
    tier_upper = plan.tier.upper()
    lines = [
        f"Risk Level    : [{tier_upper}]",
        f"Consequence   : {plan.plain_english_description}",
    ]

    if plan.effect_summary.directory_scopes and plan.effect_summary.directory_scopes != ["current folder"]:
        lines.append(f"Directory Scope: {', '.join(plan.effect_summary.directory_scopes)}")

    if plan.effect_summary.deletes or plan.effect_summary.overwrites:
        lines.append(f"Reversibility : {plan.reversibility_description}")

    if plan.explanation:
        lines.append(f"Rationale     : {plan.explanation}")

    lines.append(f"Default Action: [{plan.default_action.upper()}] ({plan.confirmation_prompt.strip()})")
    return "\n".join(lines)

