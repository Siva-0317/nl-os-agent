"""
src/nlagent/plan/engine.py - Multi-Step Plan Engine on Virtual Filesystem Overlay.

Implements Task T36:
- Supports multi-step workflows of up to 10 steps (`MAX_PLAN_STEPS = 10`).
- Maintains a copy-on-write metadata overlay (`VirtualFS`) of the filesystem tree.
- Simulates step k's static Effect against the cumulative overlay state of steps 1..k-1.
- Compiles one CombinedPlanPreview (net manifest diff, combined risk tier,
  plain-English consequence summary, reversibility status, Cancel default).
- Executes plans atomically under ONE UndoGroup: captures per-step quarantine records
  and newly created paths, stops immediately on any step failure or safety violation,
  and automatically rolls back all preceding steps in reverse order.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from nlagent.daemon.commit import get_preferred_shell
from nlagent.safety.effects import Effect, analyse, tier_from_effect
from nlagent.safety.manifest import diff as manifest_diff, manifest as build_manifest
from nlagent.safety.overlay import VirtualFS, _split_compound_segments, analyse_on_overlay
from nlagent.safety.quarantine import (
    QuarantineLimitExceededError,
    QuarantineNotFoundError,
    get_quarantine_root,
    quarantine_effect,
    undo as quarantine_undo,
)
from nlagent.safety.renderer import (
    EffectSummary,
    format_bytes_human,
    render_plain_english_consequence,
    render_reversibility_description,
)

MAX_PLAN_STEPS = 10

TIER_ORDER: dict[str, int] = {
    "read_only": 0,
    "state_modifying": 1,
    "ambiguous": 2,
    "destructive": 3,
    "privileged": 4,
}


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------


class PlanError(Exception):
    """Base exception for multi-step plan engine."""


class PlanLimitExceededError(PlanError, ValueError):
    """Raised when a plan exceeds MAX_PLAN_STEPS or has 0 steps."""


class PlanRollbackError(PlanError):
    """Raised when plan rollback fails."""


class UndoGroupNotFoundError(PlanError):
    """Raised when requested undo group manifest cannot be found."""


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------


@dataclass
class PlanStep:
    """Single step within a MultiStepPlan."""

    step_index: int
    command: str
    explanation: str = ""
    effect: Effect = field(default_factory=Effect)
    tier: str = "read_only"
    summary: EffectSummary = field(default_factory=EffectSummary)
    cwd_before: str = ""
    cwd_after: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "command": self.command,
            "explanation": self.explanation,
            "tier": self.tier,
            "cwd_before": self.cwd_before,
            "cwd_after": self.cwd_after,
            "deletes": list(self.effect.deletes),
            "overwrites": list(self.effect.overwrites),
            "creates": list(self.effect.creates),
            "appends": list(self.effect.appends),
            "perm_changes": list(self.effect.perm_changes),
            "bytes_affected": self.effect.bytes_affected,
            "plain_english": render_plain_english_consequence(self.summary),
        }


@dataclass
class CombinedPlanPreview:
    """Aggregated safety & consequence preview for the entire multi-step plan."""

    plan_id: str
    total_steps: int
    step_summaries: list[dict[str, Any]]
    combined_tier: str
    net_manifest_diff: dict[str, list[str]]
    net_effect_summary: EffectSummary
    plain_english_headline: str
    reversibility_description: str
    default_action: str  # "cancel" or "execute"
    confirmation_prompt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "total_steps": self.total_steps,
            "combined_tier": self.combined_tier,
            "net_manifest_diff": self.net_manifest_diff,
            "plain_english_headline": self.plain_english_headline,
            "reversibility_description": self.reversibility_description,
            "default_action": self.default_action,
            "confirmation_prompt": self.confirmation_prompt,
            "bytes_affected": self.net_effect_summary.bytes_affected,
            "bytes_affected_human": format_bytes_human(self.net_effect_summary.bytes_affected),
            "directory_scopes": self.net_effect_summary.directory_scopes,
            "steps": self.step_summaries,
        }

    def render_banner(self) -> str:
        """Render a multi-line terminal banner summarizing the multi-step plan."""
        lines = [
            f"Multi-Step Plan ({self.total_steps} step{'s' if self.total_steps != 1 else ''})",
            f"Combined Risk : [{self.combined_tier.upper()}]",
            f"Net Consequence: {self.plain_english_headline}",
            (
                f"Net Diff      : {len(self.net_manifest_diff['created'])} created, "
                f"{len(self.net_manifest_diff['modified'])} modified, "
                f"{len(self.net_manifest_diff['deleted'])} deleted, "
                f"{len(self.net_manifest_diff['perm'])} perm"
            ),
            f"Reversibility : {self.reversibility_description}",
            f"Default Action: [{self.default_action.upper()}] ({self.confirmation_prompt.strip()})",
            "Steps:",
        ]
        for s in self.step_summaries:
            lines.append(f"  {s['step_index']}. [{s['tier'].upper()}] {s['command']} -> {s['plain_english']}")
        return "\n".join(lines)


@dataclass
class MultiStepPlan:
    """Complete simulated multi-step plan ready for user confirmation and execution."""

    plan_id: str
    initial_cwd: str
    steps: list[PlanStep]
    preview: CombinedPlanPreview
    overlay: VirtualFS
    initial_manifest: dict[str, tuple[int, str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "initial_cwd": self.initial_cwd,
            "steps": [s.to_dict() for s in self.steps],
            "preview": self.preview.to_dict(),
        }


@dataclass
class StepExecutionRecord:
    """Execution and rollback metadata for a single executed step."""

    step_index: int
    command: str
    exit_code: int
    output: str = ""
    quarantine_request_id: str | None = None
    created_paths: list[str] = field(default_factory=list)
    cwd_before: str = ""
    cwd_after: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepExecutionRecord":
        return cls(**data)


@dataclass
class UndoGroup:
    """Atomic undo group spanning all steps in a multi-step plan."""

    group_id: str
    plan_id: str
    timestamp: str
    initial_cwd: str
    status: str = "running"  # "running" | "completed" | "failed_rolled_back" | "undone"
    step_records: list[StepExecutionRecord] = field(default_factory=list)

    @property
    def quarantine_request_ids(self) -> list[str]:
        return [r.quarantine_request_id for r in self.step_records if r.quarantine_request_id]

    @property
    def created_paths(self) -> list[str]:
        out: list[str] = []
        for r in self.step_records:
            out.extend(r.created_paths)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "plan_id": self.plan_id,
            "timestamp": self.timestamp,
            "initial_cwd": self.initial_cwd,
            "status": self.status,
            "step_records": [r.to_dict() for r in self.step_records],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UndoGroup":
        return cls(
            group_id=data["group_id"],
            plan_id=data["plan_id"],
            timestamp=data["timestamp"],
            initial_cwd=data["initial_cwd"],
            status=data.get("status", "completed"),
            step_records=[StepExecutionRecord.from_dict(r) for r in data.get("step_records", [])],
        )


@dataclass
class UndoGroupResult:
    """Result of rolling back an entire UndoGroup."""

    group_id: str
    success: bool
    restored_paths: list[str] = field(default_factory=list)
    removed_created_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class PlanExecutionResult:
    """Execution outcome for a MultiStepPlan."""

    plan_id: str
    group_id: str
    success: bool
    completed_steps: int
    total_steps: int
    failed_step_index: int | None = None
    rolled_back: bool = False
    error: str = ""
    step_results: list[dict[str, Any]] = field(default_factory=list)
    undo_group: UndoGroup | None = None
    rollback_result: UndoGroupResult | None = None


# -----------------------------------------------------------------------------
# Shell Step Execution Helper (Real Disk)
# -----------------------------------------------------------------------------


def _list_paths_under(root: str, max_entries: int = 10000) -> set[str]:
    """Return normalized absolute paths of all entries currently under root."""
    norm_root = os.path.normpath(os.path.abspath(root))
    found: set[str] = set()
    if not os.path.isdir(norm_root):
        return found
    count = 0
    for dp, dn, fn in os.walk(norm_root):
        for name in fn + dn:
            p = os.path.normpath(os.path.join(dp, name))
            found.add(p)
            count += 1
            if count >= max_entries:
                return found
    return found


def _expand_vars_in_env(text: str, env: dict[str, str]) -> str:
    out = re.sub(
        r"\$env:([A-Za-z_][A-Za-z0-9_]*)",
        lambda m: env.get(m.group(1), m.group(0)),
        text,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"%([A-Za-z_][A-Za-z0-9_]*)%",
        lambda m: env.get(m.group(1), m.group(0)),
        out,
    )
    out = re.sub(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
        lambda m: env.get(m.group(1), m.group(0)),
        out,
    )
    out = re.sub(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        lambda m: env.get(m.group(1), m.group(0)),
        out,
    )
    return out


def default_plan_runner(
    command: str,
    cwd: str,
    env: dict[str, str],
) -> tuple[str, int, str, dict[str, str]]:
    """
    Execute a step command on real disk with persistent cwd and environment mutations.
    Returns (output, exit_code, new_cwd, new_env).
    """
    current_cwd = os.path.normpath(os.path.abspath(cwd))
    current_env = dict(env)
    segments = _split_compound_segments(command)
    outputs: list[str] = []

    for seg in segments:
        out, rc, current_cwd, current_env = _run_single_segment(seg, current_cwd, current_env)
        if out:
            outputs.append(out)
        if rc != 0:
            return "\n".join(outputs), rc, current_cwd, current_env

    return "\n".join(outputs), 0, current_cwd, current_env


def _run_single_segment(
    seg: str,
    cwd: str,
    env: dict[str, str],
) -> tuple[str, int, str, dict[str, str]]:
    """Execute one segment cleanly on disk across Windows/POSIX."""
    clean = seg.strip()
    if not clean:
        return "", 0, cwd, env

    lower = clean.lower()

    # Handle env assignments
    m_ps_env = re.match(r'^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["\']?(.*?)["\']?$', clean, re.IGNORECASE)
    if m_ps_env:
        env[m_ps_env.group(1)] = _expand_vars_in_env(m_ps_env.group(2), env)
        return "", 0, cwd, env

    m_sh_env = re.match(r"^(?:export|set)\s+([A-Za-z_][A-Za-z0-9_]*)=(.*?)$", clean, re.IGNORECASE)
    if m_sh_env:
        val = m_sh_env.group(2).strip().strip("'\"")
        env[m_sh_env.group(1)] = _expand_vars_in_env(val, env)
        return "", 0, cwd, env

    # Handle directory change (cd / Set-Location)
    m_cd = re.match(r"^(?:cd|chdir|Set-Location|sl)\s+(.+)$", clean, re.IGNORECASE)
    if m_cd and ">" not in clean:
        raw_dir = m_cd.group(1).strip().strip("'\"")
        if raw_dir.lower().startswith("/d "):
            raw_dir = raw_dir[3:].strip().strip("'\"")
        raw_dir = _expand_vars_in_env(raw_dir, env)
        target = raw_dir if os.path.isabs(raw_dir) else os.path.join(cwd, raw_dir)
        target_norm = os.path.normpath(os.path.abspath(target))
        if os.path.isdir(target_norm):
            return "", 0, target_norm, env
        return f"cd: no such directory: {target_norm}", 1, cwd, env

    expanded_cmd = _expand_vars_in_env(clean, env)

    # Check if bash is usable
    bash_exe = get_preferred_shell()
    if bash_exe:
        try:
            proc = subprocess.run(
                [bash_exe, "-c", expanded_cmd],
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=45,
            )
            out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
            return out, proc.returncode, cwd, env
        except Exception as exc:
            return str(exc), 1, cwd, env

    # Native Python/cross-platform execution fallback on Windows when bash is absent/sandboxed
    if sys.platform == "win32":
        # Handle redirection > and >> with echo directly for fast, deterministic UTF-8 behavior
        m_echo = re.match(r"^(?:echo|Write-Output)\s+(.*?)\s*(>>?)\s*([^\s]+)$", expanded_cmd, re.IGNORECASE)
        if m_echo:
            body = m_echo.group(1).strip().strip("'\"")
            op = m_echo.group(2)
            tgt = m_echo.group(3).strip("'\"")
            tgt_p = Path(cwd) / tgt if not os.path.isabs(tgt) else Path(tgt)
            try:
                tgt_p.parent.mkdir(parents=True, exist_ok=True)
                mode = "a" if op == ">>" else "w"
                with open(tgt_p, mode, encoding="utf-8") as f:
                    f.write(body + "\n")
                return "", 0, cwd, env
            except OSError as exc:
                return str(exc), 1, cwd, env

        try:
            parts = shlex.split(expanded_cmd, posix=True)
        except ValueError:
            parts = expanded_cmd.split()

        if parts:
            verb = parts[0].lower()
            if verb.endswith(".exe"):
                verb = verb[:-4]

            # Fast native handling for standard file verbs (matches overlay & POSIX semantics cleanly)
            if verb in ("mkdir", "md"):
                targets = [p for p in parts[1:] if not p.startswith("-")]
                try:
                    for t in targets:
                        tp = Path(cwd) / t if not os.path.isabs(t) else Path(t)
                        tp.mkdir(parents=True, exist_ok=True)
                    return "", 0, cwd, env
                except OSError as exc:
                    return str(exc), 1, cwd, env

            if verb == "touch":
                targets = [p for p in parts[1:] if not p.startswith("-")]
                try:
                    for t in targets:
                        tp = Path(cwd) / t if not os.path.isabs(t) else Path(t)
                        tp.parent.mkdir(parents=True, exist_ok=True)
                        tp.touch(exist_ok=True)
                    return "", 0, cwd, env
                except OSError as exc:
                    return str(exc), 1, cwd, env

            if verb in ("rm", "del", "erase"):
                is_rec = any(
                    tok.lower() in ("-r", "-rf", "-fr", "/s", "-recurse")
                    for tok in parts[1:]
                )
                targets = [p for p in parts[1:] if not p.startswith("-") and not p.startswith("/")]
                for t in targets:
                    pat = str(Path(cwd) / t if not os.path.isabs(t) else Path(t))
                    matches = glob.glob(pat) if any(ch in pat for ch in "*?[]") else [pat]
                    for m in matches:
                        mp = Path(m)
                        if not mp.exists() and not mp.is_symlink():
                            continue
                        try:
                            if mp.is_dir():
                                if is_rec:
                                    shutil.rmtree(mp)
                                else:
                                    mp.rmdir()
                            else:
                                mp.unlink()
                        except OSError as exc:
                            return str(exc), 1, cwd, env
                return "", 0, cwd, env

            if verb in ("rd", "rmdir"):
                targets = [p for p in parts[1:] if not p.startswith("-") and not p.startswith("/")]
                for t in targets:
                    tp = Path(cwd) / t if not os.path.isabs(t) else Path(t)
                    if tp.exists():
                        try:
                            shutil.rmtree(tp)
                        except OSError as exc:
                            return str(exc), 1, cwd, env
                return "", 0, cwd, env

            if verb in ("cp", "copy"):
                args = [p for p in parts[1:] if not p.startswith("-") and not p.startswith("/")]
                if len(args) >= 2:
                    *srcs, dst = args
                    dst_p = Path(cwd) / dst if not os.path.isabs(dst) else Path(dst)
                    for s in srcs:
                        pat = str(Path(cwd) / s if not os.path.isabs(s) else Path(s))
                        matches = glob.glob(pat) if any(ch in pat for ch in "*?[]") else [pat]
                        for m in matches:
                            sp = Path(m)
                            if not sp.exists():
                                return f"copy: source does not exist: {sp}", 1, cwd, env
                            final = dst_p / sp.name if dst_p.is_dir() else dst_p
                            try:
                                if sp.is_dir():
                                    shutil.copytree(sp, final, dirs_exist_ok=True)
                                else:
                                    final.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(sp, final)
                            except OSError as exc:
                                return str(exc), 1, cwd, env
                    return "", 0, cwd, env

            if verb in ("mv", "move", "ren", "rename"):
                args = [p for p in parts[1:] if not p.startswith("-") and not p.startswith("/")]
                if len(args) >= 2:
                    *srcs, dst = args
                    dst_p = Path(cwd) / dst if not os.path.isabs(dst) else Path(dst)
                    for s in srcs:
                        pat = str(Path(cwd) / s if not os.path.isabs(s) else Path(s))
                        matches = glob.glob(pat) if any(ch in pat for ch in "*?[]") else [pat]
                        for m in matches:
                            sp = Path(m)
                            if not sp.exists():
                                return f"move: source does not exist: {sp}", 1, cwd, env
                            final = dst_p / sp.name if dst_p.is_dir() else dst_p
                            try:
                                final.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(sp), str(final))
                            except OSError as exc:
                                return str(exc), 1, cwd, env
                    return "", 0, cwd, env

        # Fallback to subprocess (PowerShell or cmd.exe)
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", expanded_cmd],
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=45,
            )
            if proc.returncode != 0:
                proc = subprocess.run(
                    expanded_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    env=env,
                    timeout=45,
                )
            out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
            return out, proc.returncode, cwd, env
        except Exception as exc:
            return str(exc), 1, cwd, env

    # Non-Windows POSIX fallback
    try:
        proc = subprocess.run(
            expanded_cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=45,
        )
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return out, proc.returncode, cwd, env
    except Exception as exc:
        return str(exc), 1, cwd, env


# -----------------------------------------------------------------------------
# Undo Group Storage Helpers
# -----------------------------------------------------------------------------


def _get_undo_groups_dir(quarantine_base_dir: str | Path | None = None) -> Path:
    q_root = get_quarantine_root(quarantine_base_dir)
    groups_dir = q_root / "undo_groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    return groups_dir


def _save_undo_group(group: UndoGroup, quarantine_base_dir: str | Path | None = None) -> Path:
    groups_dir = _get_undo_groups_dir(quarantine_base_dir)
    path = groups_dir / f"{group.group_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(group.to_dict(), f, indent=2)
    return path


def _load_undo_group(group_id: str, quarantine_base_dir: str | Path | None = None) -> UndoGroup:
    groups_dir = _get_undo_groups_dir(quarantine_base_dir)
    path = groups_dir / f"{group_id}.json"
    if not path.is_file():
        raise UndoGroupNotFoundError(f"Undo group '{group_id}' not found in {groups_dir}")
    with open(path, "r", encoding="utf-8") as f:
        return UndoGroup.from_dict(json.load(f))


# -----------------------------------------------------------------------------
# Plan Engine
# -----------------------------------------------------------------------------


class PlanEngine:
    """
    Multi-step plan engine backed by an in-memory copy-on-write VirtualFS overlay.
    Supports planning (up to 10 steps), combined consequence previewing,
    atomic multi-step execution under a single UndoGroup, and automatic rollback on failure.
    """

    def __init__(
        self,
        max_steps: int = MAX_PLAN_STEPS,
        quarantine_base_dir: str | Path | None = None,
    ) -> None:
        self.max_steps = max_steps
        self.quarantine_base_dir = quarantine_base_dir

    def create_plan(
        self,
        steps: Sequence[str | dict[str, str]],
        cwd: str = "",
        env: dict[str, str] | None = None,
        enable_quarantine: bool = True,
    ) -> MultiStepPlan:
        """
        Simulate up to `max_steps` commands on an isolated copy-on-write VirtualFS overlay
        and compile a CombinedPlanPreview without modifying the physical disk.
        """
        if not steps:
            raise PlanLimitExceededError("Plan must contain at least 1 step.")
        if len(steps) > self.max_steps:
            raise PlanLimitExceededError(
                f"Plan contains {len(steps)} steps, exceeding the limit of {self.max_steps} steps."
            )

        initial_cwd = os.path.normpath(os.path.abspath(cwd or os.getcwd()))
        vfs = VirtualFS(base_cwd=initial_cwd, env=env)
        initial_manifest = build_manifest(initial_cwd) if os.path.isdir(initial_cwd) else {}

        plan_id = f"plan_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        compiled_steps: list[PlanStep] = []

        highest_step_tier = "read_only"
        agg_signals: list[str] = []
        agg_registry: list[str] = []
        agg_external = False
        agg_opaque = False
        agg_protected = False
        agg_hard_block = False
        step_bytes_deleted = 0
        raw_commands_joined = []

        for idx, raw_step in enumerate(steps, start=1):
            if isinstance(raw_step, dict):
                cmd = str(raw_step.get("command", "")).strip()
                expl = str(raw_step.get("explanation", ""))
            else:
                cmd = str(raw_step).strip()
                expl = ""

            if not cmd:
                raise PlanError(f"Step {idx} has an empty command.")

            raw_commands_joined.append(cmd)
            cwd_before = vfs.cwd
            step_eff = analyse_on_overlay(cmd, vfs=vfs, mutate=True)
            cwd_after = vfs.cwd

            step_tier = tier_from_effect(step_eff)
            if TIER_ORDER.get(step_tier, 0) > TIER_ORDER.get(highest_step_tier, 0):
                highest_step_tier = step_tier

            step_summary = EffectSummary.from_effect(
                step_eff,
                command=cmd,
                cwd=cwd_before,
                enable_quarantine=enable_quarantine,
            )

            compiled_steps.append(
                PlanStep(
                    step_index=idx,
                    command=cmd,
                    explanation=expl,
                    effect=step_eff,
                    tier=step_tier,
                    summary=step_summary,
                    cwd_before=cwd_before,
                    cwd_after=cwd_after,
                )
            )

            agg_signals.extend(step_eff.signals)
            agg_registry.extend(step_eff.registry_writes)
            agg_external = agg_external or step_eff.external
            agg_opaque = agg_opaque or step_eff.opaque
            agg_protected = agg_protected or step_eff.protected_hit
            agg_hard_block = agg_hard_block or step_eff.hard_block
            step_bytes_deleted += step_eff.bytes_affected

        # Compute net manifest diff from initial disk snapshot vs final virtual overlay snapshot
        final_manifest = vfs.snapshot(initial_cwd) if os.path.isdir(initial_cwd) else vfs.snapshot(vfs.cwd)
        net_diff = manifest_diff(initial_manifest, final_manifest)

        # Build net Effect from net manifest diff + cumulative flags
        net_effect = Effect(
            deletes=[os.path.join(initial_cwd, rel) for rel in net_diff["deleted"]],
            overwrites=[os.path.join(initial_cwd, rel) for rel in net_diff["modified"]],
            creates=[os.path.join(initial_cwd, rel) for rel in net_diff["created"]],
            perm_changes=[os.path.join(initial_cwd, rel) for rel in net_diff["perm"]],
            signals=list(dict.fromkeys(agg_signals)),
            registry_writes=list(dict.fromkeys(agg_registry)),
            external=agg_external,
            opaque=agg_opaque,
            protected_hit=agg_protected,
            hard_block=agg_hard_block,
        )

        # Calculate bytes affected accurately from initial manifest for deleted/modified files
        net_bytes = 0
        for rel in net_diff["deleted"] + net_diff["modified"]:
            if rel in initial_manifest:
                net_bytes += initial_manifest[rel][0]
        if net_bytes == 0 and step_bytes_deleted > 0 and net_effect.deletes:
            net_bytes = step_bytes_deleted
        net_effect.bytes_affected = net_bytes

        net_tier = tier_from_effect(net_effect)
        combined_tier = (
            highest_step_tier
            if TIER_ORDER.get(highest_step_tier, 0) >= TIER_ORDER.get(net_tier, 0)
            else net_tier
        )

        net_summary = EffectSummary.from_effect(
            net_effect,
            command=" && ".join(raw_commands_joined),
            cwd=initial_cwd,
            enable_quarantine=enable_quarantine,
        )

        headline = self._render_combined_headline(
            total_steps=len(compiled_steps),
            net_diff=net_diff,
            net_summary=net_summary,
            combined_tier=combined_tier,
        )
        reversibility = (
            render_reversibility_description(net_summary)
            if (net_summary.deletes or net_summary.overwrites)
            else (
                "Reversible via Undo Group rollback (removes created files and restores shadowed backups)."
                if net_diff["created"]
                else "No net filesystem changes to undo."
            )
        )

        is_high_risk = (
            combined_tier in ("destructive", "privileged", "ambiguous")
            or net_summary.is_bulk
            or net_summary.is_drive_root
            or net_summary.hard_block
        )

        if is_high_risk:
            default_action = "cancel"
            confirmation_prompt = "Press Enter to abort plan (Cancel is default), or type 'yes' to execute all steps: "
        else:
            default_action = "execute"
            confirmation_prompt = "Press Enter to execute plan, or Ctrl+C to abort: "

        preview = CombinedPlanPreview(
            plan_id=plan_id,
            total_steps=len(compiled_steps),
            step_summaries=[s.to_dict() for s in compiled_steps],
            combined_tier=combined_tier,
            net_manifest_diff=net_diff,
            net_effect_summary=net_summary,
            plain_english_headline=headline,
            reversibility_description=reversibility,
            default_action=default_action,
            confirmation_prompt=confirmation_prompt,
        )

        return MultiStepPlan(
            plan_id=plan_id,
            initial_cwd=initial_cwd,
            steps=compiled_steps,
            preview=preview,
            overlay=vfs,
            initial_manifest=initial_manifest,
        )

    @staticmethod
    def _render_combined_headline(
        total_steps: int,
        net_diff: dict[str, list[str]],
        net_summary: EffectSummary,
        combined_tier: str,
    ) -> str:
        """Produce clear plain-English net headline across all steps in the plan."""
        if net_summary.hard_block:
            return render_plain_english_consequence(net_summary)
        if net_summary.is_drive_root or net_summary.protected_hit:
            return render_plain_english_consequence(net_summary)

        n_del = len(net_diff["deleted"])
        n_mod = len(net_diff["modified"])
        n_cre = len(net_diff["created"])
        n_prm = len(net_diff["perm"])

        parts: list[str] = []
        if n_del:
            parts.append(f"delete {n_del} item{'s' if n_del != 1 else ''} ({format_bytes_human(net_summary.bytes_affected)})")
        if n_mod:
            parts.append(f"modify {n_mod} existing item{'s' if n_mod != 1 else ''}")
        if n_cre:
            parts.append(f"create {n_cre} new item{'s' if n_cre != 1 else ''}")
        if n_prm:
            parts.append(f"change permissions on {n_prm} item{'s' if n_prm != 1 else ''}")

        if not parts:
            if net_summary.signals or net_summary.registry_writes or net_summary.opaque or net_summary.external:
                return render_plain_english_consequence(net_summary)
            return f"{total_steps}-step plan produces zero net changes on disk."

        joined = ", ".join(parts)
        return f"Across {total_steps} steps, net effect will {joined}."

    def execute_plan(
        self,
        plan: MultiStepPlan,
        runner: Callable[[str, str, dict[str, str]], tuple[str, int, str, dict[str, str]]] | None = None,
        allow_privileged: bool = False,
        allow_tier_escalation: bool = False,
        enable_quarantine: bool = True,
        confirmed_large_quarantine: bool = False,
        env: dict[str, str] | None = None,
    ) -> PlanExecutionResult:
        """
        Execute all steps in `plan` atomically under a single UndoGroup.
        Stops immediately on any failure and rolls back all preceding steps cleanly.
        """
        exec_fn = runner if runner is not None else default_plan_runner
        group_id = f"ugrp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        undo_group = UndoGroup(
            group_id=group_id,
            plan_id=plan.plan_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            initial_cwd=plan.initial_cwd,
            status="running",
        )

        real_cwd = plan.initial_cwd
        real_env = dict(env) if env is not None else dict(os.environ)
        step_results: list[dict[str, Any]] = []

        for step in plan.steps:
            # 1. Fresh TOCTOU / Safety check against real current disk
            fresh_eff = analyse(step.command, cwd=real_cwd)
            fresh_tier = tier_from_effect(fresh_eff)

            if fresh_tier == "privileged" and not allow_privileged:
                err_msg = f"Blocked at step {step.step_index}: Targets protected system paths ({step.command})."
                undo_group.status = "failed_rolled_back"
                rb_res = self._rollback_undo_group_in_memory(undo_group)
                _save_undo_group(undo_group, quarantine_base_dir=self.quarantine_base_dir)
                return PlanExecutionResult(
                    plan_id=plan.plan_id,
                    group_id=group_id,
                    success=False,
                    completed_steps=step.step_index - 1,
                    total_steps=len(plan.steps),
                    failed_step_index=step.step_index,
                    rolled_back=True,
                    error=err_msg,
                    step_results=step_results,
                    undo_group=undo_group,
                    rollback_result=rb_res,
                )

            if (
                not allow_tier_escalation
                and TIER_ORDER.get(fresh_tier, 0) > TIER_ORDER.get(step.tier, 0)
            ):
                err_msg = (
                    f"Blocked at step {step.step_index}: TOCTOU tier escalation "
                    f"('{step.tier}' -> '{fresh_tier}') on command '{step.command}'."
                )
                undo_group.status = "failed_rolled_back"
                rb_res = self._rollback_undo_group_in_memory(undo_group)
                _save_undo_group(undo_group, quarantine_base_dir=self.quarantine_base_dir)
                return PlanExecutionResult(
                    plan_id=plan.plan_id,
                    group_id=group_id,
                    success=False,
                    completed_steps=step.step_index - 1,
                    total_steps=len(plan.steps),
                    failed_step_index=step.step_index,
                    rolled_back=True,
                    error=err_msg,
                    step_results=step_results,
                    undo_group=undo_group,
                    rollback_result=rb_res,
                )

            # 2. Pre-step Quarantine
            q_req_id: str | None = None
            if enable_quarantine and (fresh_eff.deletes or fresh_eff.overwrites or fresh_eff.appends):
                candidate_id = f"{group_id}_step_{step.step_index}"
                try:
                    q_manifest = quarantine_effect(
                        effect=fresh_eff,
                        request_id=candidate_id,
                        command=step.command,
                        base_dir=self.quarantine_base_dir,
                        confirmed_large=confirmed_large_quarantine,
                    )
                    if q_manifest is not None:
                        q_req_id = candidate_id
                except QuarantineLimitExceededError as qe:
                    err_msg = f"Blocked at step {step.step_index}: {qe}"
                    undo_group.status = "failed_rolled_back"
                    rb_res = self._rollback_undo_group_in_memory(undo_group)
                    _save_undo_group(undo_group, quarantine_base_dir=self.quarantine_base_dir)
                    return PlanExecutionResult(
                        plan_id=plan.plan_id,
                        group_id=group_id,
                        success=False,
                        completed_steps=step.step_index - 1,
                        total_steps=len(plan.steps),
                        failed_step_index=step.step_index,
                        rolled_back=True,
                        error=err_msg,
                        step_results=step_results,
                        undo_group=undo_group,
                        rollback_result=rb_res,
                    )

            # 3. Capture disk paths before running step to track newly created items cleanly
            before_paths = _list_paths_under(plan.initial_cwd)

            # 4. Run step
            cwd_before_run = real_cwd
            try:
                out, exit_code, next_cwd, next_env = exec_fn(step.command, real_cwd, real_env)
            except Exception as exc:
                out = f"Execution exception: {exc}"
                exit_code = 1
                next_cwd = real_cwd
                next_env = real_env

            after_paths = _list_paths_under(plan.initial_cwd)
            newly_created = sorted(after_paths - before_paths)

            step_rec = StepExecutionRecord(
                step_index=step.step_index,
                command=step.command,
                exit_code=exit_code,
                output=out,
                quarantine_request_id=q_req_id,
                created_paths=newly_created,
                cwd_before=cwd_before_run,
                cwd_after=next_cwd,
            )
            undo_group.step_records.append(step_rec)

            step_results.append(
                {
                    "step_index": step.step_index,
                    "command": step.command,
                    "exit_code": exit_code,
                    "output": out,
                }
            )

            # 5. Stop & Rollback on failure
            if exit_code != 0:
                err_msg = f"Step {step.step_index} failed with exit code {exit_code}: {out}"
                undo_group.status = "failed_rolled_back"
                rb_res = self._rollback_undo_group_in_memory(undo_group)
                _save_undo_group(undo_group, quarantine_base_dir=self.quarantine_base_dir)
                return PlanExecutionResult(
                    plan_id=plan.plan_id,
                    group_id=group_id,
                    success=False,
                    completed_steps=step.step_index - 1,
                    total_steps=len(plan.steps),
                    failed_step_index=step.step_index,
                    rolled_back=True,
                    error=err_msg,
                    step_results=step_results,
                    undo_group=undo_group,
                    rollback_result=rb_res,
                )

            real_cwd = next_cwd
            real_env = next_env

        undo_group.status = "completed"
        _save_undo_group(undo_group, quarantine_base_dir=self.quarantine_base_dir)
        return PlanExecutionResult(
            plan_id=plan.plan_id,
            group_id=group_id,
            success=True,
            completed_steps=len(plan.steps),
            total_steps=len(plan.steps),
            failed_step_index=None,
            rolled_back=False,
            error="",
            step_results=step_results,
            undo_group=undo_group,
            rollback_result=None,
        )

    def _rollback_undo_group_in_memory(self, group: UndoGroup) -> UndoGroupResult:
        """Roll back all recorded steps in reverse chronological order."""
        restored: list[str] = []
        removed: list[str] = []
        failed: list[str] = []

        for rec in reversed(group.step_records):
            # 1. Remove files and directories created during this step (deepest paths first)
            created_sorted = sorted(
                rec.created_paths,
                key=lambda p: len(Path(p).parts),
                reverse=True,
            )
            for cp in created_sorted:
                p_obj = Path(cp)
                if not p_obj.exists() and not p_obj.is_symlink():
                    continue
                try:
                    if p_obj.is_dir() and not p_obj.is_symlink():
                        try:
                            p_obj.rmdir()
                            removed.append(cp)
                        except OSError:
                            shutil.rmtree(p_obj, ignore_errors=True)
                            removed.append(cp)
                    else:
                        p_obj.unlink(missing_ok=True)
                        removed.append(cp)
                except OSError:
                    failed.append(cp)

            # 2. Restore quarantined deleted / overwritten items for this step
            if rec.quarantine_request_id:
                try:
                    ures = quarantine_undo(
                        request_id=rec.quarantine_request_id,
                        base_dir=self.quarantine_base_dir,
                        on_clash="overwrite",
                    )
                    restored.extend(ures.restored_paths)
                    failed.extend(ures.failed_paths)
                except QuarantineNotFoundError:
                    pass
                except Exception:
                    failed.append(rec.quarantine_request_id)

        success = len(failed) == 0
        msg = (
            f"UndoGroup '{group.group_id}' rolled back cleanly: "
            f"{len(restored)} restored, {len(removed)} created paths removed."
            if success
            else f"UndoGroup '{group.group_id}' rollback encountered {len(failed)} failure(s)."
        )
        return UndoGroupResult(
            group_id=group.group_id,
            success=success,
            restored_paths=restored,
            removed_created_paths=removed,
            failed_paths=failed,
            message=msg,
        )

    def rollback_group(self, group_id: str) -> UndoGroupResult:
        """Load persisted UndoGroup by ID and execute atomic rollback across all steps."""
        group = _load_undo_group(group_id, quarantine_base_dir=self.quarantine_base_dir)
        res = self._rollback_undo_group_in_memory(group)
        if res.success:
            group.status = "undone"
            _save_undo_group(group, quarantine_base_dir=self.quarantine_base_dir)
        return res
