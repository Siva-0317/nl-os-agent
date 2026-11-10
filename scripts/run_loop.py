#!/usr/bin/env python3
"""
scripts/run_loop.py - Karpathy-Style Autoresearch Autonomous Optimization Loop Runner.

Executes an optimization loop governed by a program.md contract:
1. Confinement Contract:
   - Restricts modifications strictly to the declared mutable file.
   - Any modification to forbidden surfaces (safety rules, datasets, evaluators)
     triggers immediate abort and automatic git/snapshot reversion.
2. Dev / Sealed Partition:
   - Evaluator operates strictly on dev set (datasets/dev_v3.jsonl, N >= 100).
   - Strictly refuses to read or evaluate against the sealed test set.
3. Paired Bootstrap Acceptance Rule:
   - Evaluates candidate modifications against current best baseline.
   - Computes 95% paired bootstrap confidence interval for metric improvement.
   - Keeps change only if lower bound > 0.0 (or non-inferior with token reduction).
4. Git Keep / Revert:
   - If accepted: commits change to git repository, updates current best.
   - If rejected: immediately reverts using `git checkout -- <file>` or snapshot.
5. Persistent Audit Ledger:
   - Appends all trials with provenance and intervals to results.tsv.
   - Tracks total experiment count across restarts.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

# Ensure src and root are in sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_root, "src")
for p in (_root, _src):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.bench.provenance import collect, get_git_sha
from nlagent.bench.stats import bootstrap_interval


# -----------------------------------------------------------------------------
# Governance Exceptions
# -----------------------------------------------------------------------------

class GovernanceViolationError(Exception):
    """Raised when an optimization loop attempts to modify a forbidden surface."""
    pass


class SealedAccessProhibitedError(Exception):
    """Raised when an optimization loop attempts to read or target the sealed test set."""
    pass


# -----------------------------------------------------------------------------
# Contract Specification & Parser
# -----------------------------------------------------------------------------

@dataclass
class LoopContract:
    """Parsed representation of an autoresearch loop contract (program.md)."""

    goal: str
    mutable_file: str
    metric: str = "accuracy_pct"
    guard_metric: str = "mean_prompt_tokens"
    guard_threshold: float = 120.0
    dev_dataset: str = "datasets/dev_v3.jsonl"
    eval_command: str = "python scripts/run_e5.py --split dev"
    forbidden_paths: list[str] = field(default_factory=lambda: [
        "datasets/sealed_test.jsonl",
        "src/nlagent/safety",
        "tests",
        "scripts/run_loop.py",
        "scripts/run_sealed.py",
    ])
    confidence: float = 0.95
    n_resamples: int = 1000
    max_experiments: int = 10
    results_file: str = "results.tsv"
    program_path: str = ""

    def validate(self) -> None:
        """Validate safety invariants of the contract."""
        # 1. Sealed test set access check
        sealed_indicators = ["sealed_test", "sealed", "safety_v3_sealed"]
        dev_lower = self.dev_dataset.lower()
        eval_lower = self.eval_command.lower()

        for ind in sealed_indicators:
            if ind in dev_lower:
                raise SealedAccessProhibitedError(
                    f"Contract violation in {self.program_path}: dev_dataset ('{self.dev_dataset}') "
                    "references sealed data. Autoresearch loops must run strictly on dev splits."
                )
            if f"--split {ind}" in eval_lower or f"--test-file {ind}" in eval_lower:
                raise SealedAccessProhibitedError(
                    f"Contract violation in {self.program_path}: eval_command ('{self.eval_command}') "
                    "targets sealed split. Sealed test set access is strictly forbidden in autoresearch loops."
                )

        # 2. Mutable file safety check
        mut_norm = os.path.normpath(self.mutable_file).replace("\\", "/").lower()
        forbidden_surfaces = [
            "src/nlagent/safety",
            "datasets/",
            "tests/",
            "scripts/run_loop.py",
            "scripts/run_sealed.py",
            "vault/",
        ]
        for f_surf in forbidden_surfaces:
            if mut_norm.startswith(f_surf) or f_surf in mut_norm:
                raise GovernanceViolationError(
                    f"Contract violation in {self.program_path}: declared mutable file '{self.mutable_file}' "
                    f"falls under protected surface '{f_surf}'. Modifying safety rules, datasets, tests, "
                    "or evaluators in autonomous loops is strictly prohibited."
                )


def parse_program_contract(path: str | Path) -> LoopContract:
    """
    Parse a program.md file containing YAML frontmatter and markdown documentation.
    Uses pure-Python extraction without third-party YAML parser dependencies.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Loop contract not found at: {path}")

    text = p.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}

    # Extract YAML frontmatter between leading --- delimiters
    match = re.match(r"^---\s*\n([\s\S]*?)\n---\s*\n", text)
    if match:
        raw_yaml = match.group(1)
        for line in raw_yaml.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if ":" in line_str:
                k, v = line_str.split(":", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if v.isdigit():
                    frontmatter[k] = int(v)
                elif re.match(r"^\d+\.\d+$", v):
                    frontmatter[k] = float(v)
                elif v.lower() in ("true", "false"):
                    frontmatter[k] = v.lower() == "true"
                elif v.startswith("[") and v.endswith("]"):
                    items = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
                    frontmatter[k] = items
                else:
                    frontmatter[k] = v

    contract = LoopContract(
        goal=str(frontmatter.get("goal", "Autonomous optimization loop")),
        mutable_file=str(frontmatter.get("mutable_file", "src/nlagent/context/layout.py")),
        metric=str(frontmatter.get("metric", "accuracy_pct")),
        guard_metric=str(frontmatter.get("guard_metric", "mean_prompt_tokens")),
        guard_threshold=float(frontmatter.get("guard_threshold", 120.0)),
        dev_dataset=str(frontmatter.get("dev_dataset", "datasets/dev_v3.jsonl")),
        eval_command=str(frontmatter.get("eval_command", "python scripts/run_e5.py --split dev")),
        confidence=float(frontmatter.get("confidence", 0.95)),
        n_resamples=int(frontmatter.get("n_resamples", 1000)),
        max_experiments=int(frontmatter.get("max_experiments", 10)),
        results_file=str(frontmatter.get("results_file", str(p.parent / "results.tsv"))),
        program_path=str(p),
    )
    contract.validate()
    return contract


# -----------------------------------------------------------------------------
# Working Tree Confinement & Git Operations
# -----------------------------------------------------------------------------

def get_modified_files(repo_root: Path) -> list[str]:
    """Retrieve list of modified or untracked files relative to repo root using git."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if res.returncode != 0:
            return []
        modified = []
        for line in res.stdout.splitlines():
            line_str = line.strip()
            if len(line_str) >= 3:
                # Format: XY filename or XY orig -> new
                file_part = line_str[2:].strip()
                if " -> " in file_part:
                    file_part = file_part.split(" -> ")[1].strip()
                modified.append(os.path.normpath(file_part).replace("\\", "/"))
        return modified
    except Exception:
        return []


def assert_confinement(
    repo_root: Path,
    declared_mutable_file: str,
    baseline_dirty_files: set[str] | None = None,
) -> None:
    """
    Assert that uncommitted changes exist ONLY in the declared mutable file.
    Raises GovernanceViolationError if changes leak into other files.
    """
    target_norm = os.path.normpath(declared_mutable_file).replace("\\", "/").lower()
    modified = get_modified_files(repo_root)

    ignored_base = {os.path.normpath(f).replace("\\", "/").lower() for f in (baseline_dirty_files or set())}

    unauthorized = []
    for f in modified:
        f_norm = os.path.normpath(f).replace("\\", "/").lower()
        if f_norm in ignored_base:
            continue
        # Allow results.tsv and task logs to be touched by the runner itself
        if f_norm.endswith("results.tsv") or f_norm.endswith(".log") or "experiments/loops" in f_norm:
            continue
        if f_norm != target_norm:
            unauthorized.append(f)

    if unauthorized:
        raise GovernanceViolationError(
            f"Confinement boundary violation: detected modifications in unauthorized files: {unauthorized}. "
            f"The program.md contract permits edits ONLY to '{declared_mutable_file}'."
        )


def revert_file(repo_root: Path, relative_path: str, snapshot_bytes: bytes | None = None) -> None:
    """Revert a file to git HEAD or restore from snapshot bytes."""
    full_path = repo_root / relative_path
    if snapshot_bytes is not None:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(snapshot_bytes)

    try:
        subprocess.run(
            ["git", "checkout", "--", relative_path],
            cwd=repo_root,
            capture_output=True,
            check=False,
            timeout=5.0,
        )
    except Exception:
        pass


def commit_file(repo_root: Path, relative_path: str, commit_message: str) -> str:
    """Stage and commit the mutable file, returning new git commit SHA."""
    try:
        subprocess.run(
            ["git", "add", relative_path],
            cwd=repo_root,
            capture_output=True,
            check=True,
            timeout=5.0,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_root,
            capture_output=True,
            check=True,
            timeout=5.0,
        )
        return get_git_sha()
    except Exception:
        return get_git_sha()


# -----------------------------------------------------------------------------
# Statistical Paired Bootstrap Interval
# -----------------------------------------------------------------------------

def compute_paired_bootstrap(
    candidate_outcomes: Sequence[bool | float],
    baseline_outcomes: Sequence[bool | float],
    confidence: float = 0.95,
    n_resamples: int = 1000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """
    Compute paired bootstrap difference confidence interval:
        Delta_i = cand_i - base_i
    Returns:
        (delta_pct, ci_lower_pct, ci_upper_pct)
    """
    if len(candidate_outcomes) != len(baseline_outcomes):
        raise ValueError(f"Mismatched outcome vector lengths: {len(candidate_outcomes)} vs {len(baseline_outcomes)}")

    n = len(candidate_outcomes)
    if n == 0:
        return 0.0, 0.0, 0.0

    diffs = [
        float(c) - float(b)
        for c, b in zip(candidate_outcomes, baseline_outcomes)
    ]
    point_diff = (sum(diffs) / float(n)) * 100.0

    low, high = bootstrap_interval(
        diffs,
        metric="mean",
        confidence=confidence,
        n_resamples=n_resamples,
        seed=seed,
    )

    return round(point_diff, 2), round(low * 100.0, 2), round(high * 100.0, 2)


# -----------------------------------------------------------------------------
# Results Ledger (results.tsv)
# -----------------------------------------------------------------------------

TSV_HEADER = "experiment_id\ttimestamp\tgit_sha\tmetric_val\tbaseline_val\tdelta_pct\tci_lower\tci_upper\tprompt_tokens\tdecision\tdescription\n"


class ResultsLedger:
    """Manages persistent results.tsv tracking across loop executions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.ensure_initialized()

    def ensure_initialized(self) -> None:
        """Create TSV ledger file with standard header if missing."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(TSV_HEADER)

    def count_experiments(self) -> int:
        """Count total completed experiments recorded in the ledger."""
        if not self.path.exists():
            return 0
        count = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if idx > 0 and line.strip():
                    count += 1
        return count

    def append_record(
        self,
        experiment_id: str,
        timestamp: str,
        git_sha: str,
        metric_val: float,
        baseline_val: float,
        delta_pct: float,
        ci_lower: float,
        ci_upper: float,
        prompt_tokens: float,
        decision: str,
        description: str,
    ) -> None:
        """Append an evaluation trial record to results.tsv."""
        self.ensure_initialized()
        clean_desc = description.replace("\t", " ").replace("\n", " ").strip()
        row = (
            f"{experiment_id}\t{timestamp}\t{git_sha}\t{metric_val:.2f}\t{baseline_val:.2f}\t"
            f"{delta_pct:+.2f}\t{ci_lower:+.2f}\t{ci_upper:+.2f}\t{prompt_tokens:.1f}\t"
            f"{decision}\t{clean_desc}\n"
        )
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(row)


# -----------------------------------------------------------------------------
# Autonomous Loop Execution Engine
# -----------------------------------------------------------------------------

@dataclass
class EvaluationResult:
    """Result returned by a fixed evaluator execution on dev split."""
    metric_val: float
    prompt_tokens: float
    latency_ms: float
    success_vector: list[bool]
    metadata: dict[str, Any] = field(default_factory=dict)


def default_dev_evaluator(
    contract: LoopContract,
    allow_mock: bool = True,
) -> EvaluationResult:
    """
    Fixed evaluator execution calling run_e5 on dev split.
    """
    # Strict safety invariant: assert dev split
    if "sealed" in contract.dev_dataset.lower():
        raise SealedAccessProhibitedError("Evaluator attempted to access sealed dataset.")

    from scripts.run_e5 import run_e5_experiment

    summaries, _ = run_e5_experiment(
        test_file=contract.dev_dataset,
        split="dev",
        allow_mock=allow_mock,
        count=100,
    )

    # Use tuned_short as the optimization candidate
    target_summary = summaries.get("tuned_short") or summaries.get("tuned_full")
    if not target_summary:
        target_summary = next(iter(summaries.values()))

    return EvaluationResult(
        metric_val=target_summary.accuracy_pct,
        prompt_tokens=target_summary.mean_prompt_tokens,
        latency_ms=target_summary.mean_prefill_ms,
        success_vector=list(target_summary.case_success_vector),
        metadata={"total_cases": target_summary.total_cases},
    )


def execute_loop_iteration(
    contract: LoopContract,
    baseline_result: EvaluationResult,
    evaluator_fn: Callable[[LoopContract], EvaluationResult],
    ledger: ResultsLedger,
    exp_num: int,
    description: str = "Candidate prompt mutation",
    repo_root: Path | None = None,
    allow_mock: bool = True,
    baseline_snapshot: bytes | None = None,
    baseline_dirty_files: set[str] | None = None,
) -> tuple[str, EvaluationResult, bytes]:
    """
    Execute a single iteration of the autoresearch contract:
    1. Check confinement: assert only mutable_file is modified.
    2. Run fixed evaluator on dev split.
    3. Compute paired bootstrap interval vs baseline.
    4. Apply decision rule (KEEP or REVERT).
    5. Append record to results.tsv.
    """
    root = repo_root or Path(_root)
    mutable_rel = contract.mutable_file
    target_file = root / mutable_rel

    if not target_file.exists():
        raise FileNotFoundError(f"Mutable target file does not exist: {target_file}")

    effective_snapshot = baseline_snapshot if baseline_snapshot is not None else target_file.read_bytes()

    # 1. Assert Confinement Boundary
    try:
        assert_confinement(root, mutable_rel, baseline_dirty_files=baseline_dirty_files)
    except GovernanceViolationError as g_err:
        revert_file(root, mutable_rel, effective_snapshot)
        exp_id = f"exp_{exp_num:03d}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        ledger.append_record(
            experiment_id=exp_id,
            timestamp=now,
            git_sha=get_git_sha(),
            metric_val=0.0,
            baseline_val=baseline_result.metric_val,
            delta_pct=0.0,
            ci_lower=0.0,
            ci_upper=0.0,
            prompt_tokens=0.0,
            decision="REVERTED_VIOLATION",
            description=f"Rejected: {g_err}",
        )
        raise

    # 2. Run Fixed Evaluator on Dev Split
    cand_res = evaluator_fn(contract)

    # 3. Compute Paired Bootstrap Confidence Interval vs Baseline
    delta_pct, ci_low, ci_high = compute_paired_bootstrap(
        cand_res.success_vector,
        baseline_result.success_vector,
        confidence=contract.confidence,
        n_resamples=contract.n_resamples,
    )

    # 4. Decision Rule
    # Acceptance Condition 1: Statistically significant improvement (CI lower bound > 0.0)
    # Acceptance Condition 2: Non-inferior (CI lower >= -1.0%), reduces tokens by >= 5, doesn't breach guard threshold
    token_delta = cand_res.prompt_tokens - baseline_result.prompt_tokens
    guard_ok = cand_res.prompt_tokens <= contract.guard_threshold

    is_significant_improvement = ci_low > 0.0 and guard_ok
    is_pareto_token_reduction = (
        ci_low >= -1.0
        and token_delta <= -5.0
        and cand_res.metric_val >= baseline_result.metric_val
        and guard_ok
    )

    decision = "KEEP" if (is_significant_improvement or is_pareto_token_reduction) else "REVERT"

    # 5. Git Commit or Revert
    exp_id = f"exp_{exp_num:03d}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    current_sha = get_git_sha()

    if decision == "KEEP":
        commit_msg = (
            f"loop({os.path.basename(contract.program_path)}): {exp_id} - "
            f"kept improvement ({cand_res.metric_val:.1f}%, Δ={delta_pct:+.1f}% [{ci_low:+.1f}%, {ci_high:+.1f}%])"
        )
        current_sha = commit_file(root, mutable_rel, commit_msg)
        effective_result = cand_res
        new_snapshot = target_file.read_bytes()
    else:
        revert_file(root, mutable_rel, effective_snapshot)
        effective_result = baseline_result
        new_snapshot = effective_snapshot

    # 6. Append to Ledger
    ledger.append_record(
        experiment_id=exp_id,
        timestamp=now,
        git_sha=current_sha,
        metric_val=cand_res.metric_val,
        baseline_val=baseline_result.metric_val,
        delta_pct=delta_pct,
        ci_lower=ci_low,
        ci_upper=ci_high,
        prompt_tokens=cand_res.prompt_tokens,
        decision=decision,
        description=description,
    )

    print(
        f"[{exp_id}] Cand={cand_res.metric_val:.1f}% vs Base={baseline_result.metric_val:.1f}% | "
        f"Δ={delta_pct:+.1f}% [{ci_low:+.1f}%, {ci_high:+.1f}%] | "
        f"Tokens={cand_res.prompt_tokens:.0f}t -> Decision: {decision}"
    )

    return decision, effective_result, new_snapshot


def run_loop(
    program_path: str | Path,
    evaluator_fn: Callable[[LoopContract], EvaluationResult] | None = None,
    max_experiments: int | None = None,
    allow_mock: bool = True,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """
    Run an autonomous optimization loop against a declared program.md contract.
    """
    root = repo_root or Path(_root)
    contract = parse_program_contract(program_path)
    limit = max_experiments or contract.max_experiments

    ledger = ResultsLedger(root / contract.results_file)
    prior_exps = ledger.count_experiments()

    eval_fn = evaluator_fn or (lambda c: default_dev_evaluator(c, allow_mock=allow_mock))

    print(f"\n================================================================================")
    print(f" AUTORESEARCH OPTIMIZATION LOOP: {contract.goal}")
    print(f" Mutable Surface: {contract.mutable_file}")
    print(f" Dev Dataset:     {contract.dev_dataset} (Sealed test set strictly prohibited)")
    print(f" Ledger:          {contract.results_file} (Prior trials: {prior_exps})")
    print(f" Max Trials:      {limit}")
    print(f"================================================================================\n")

    # Evaluate Initial Baseline
    print("[INIT] Evaluating baseline configuration on dev split...")
    baseline_result = eval_fn(contract)
    print(
        f"[INIT] Baseline established: Metric={baseline_result.metric_val:.1f}% | "
        f"Tokens={baseline_result.prompt_tokens:.0f}t | Latency={baseline_result.latency_ms:.1f}ms\n"
    )

    target_path = root / contract.mutable_file
    current_snapshot = target_path.read_bytes() if target_path.exists() else None
    current_best = baseline_result
    kept_count = 0
    reverted_count = 0
    baseline_dirty = set(get_modified_files(root))

    for idx in range(1, limit + 1):
        exp_num = prior_exps + idx
        print(f"--- Running Experiment {idx}/{limit} (Total #{exp_num}) ---")

        # Execute trial
        try:
            decision, current_best, current_snapshot = execute_loop_iteration(
                contract=contract,
                baseline_result=current_best,
                evaluator_fn=eval_fn,
                ledger=ledger,
                exp_num=exp_num,
                description=f"Autoresearch trial {exp_num}",
                repo_root=root,
                allow_mock=allow_mock,
                baseline_snapshot=current_snapshot,
                baseline_dirty_files=baseline_dirty,
            )
            if decision == "KEEP":
                kept_count += 1
            else:
                reverted_count += 1
        except Exception as exc:
            print(f"[ERROR] Trial failed or violated contract: {exc}")
            reverted_count += 1

    summary = {
        "contract": contract.program_path,
        "prior_experiments": prior_exps,
        "run_experiments": limit,
        "total_experiments": prior_exps + limit,
        "kept_count": kept_count,
        "reverted_count": reverted_count,
        "final_metric": current_best.metric_val,
        "final_tokens": current_best.prompt_tokens,
    }

    print(f"\n[LOOP COMPLETE] Trials={limit} | Kept={kept_count} | Reverted={reverted_count} | Final Best={current_best.metric_val:.1f}%\n")
    return summary


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run Karpathy-Style Autoresearch Loop.")
    parser.add_argument(
        "--program",
        default="experiments/loops/prompt_opt/program.md",
        help="Path to loop contract program.md",
    )
    parser.add_argument(
        "--max-experiments",
        type=int,
        default=None,
        help="Override maximum experiments to run",
    )
    parser.add_argument(
        "--allow-mock",
        action="store_true",
        default=False,
        help="Allow mock backend (for synthetic dev test runs only)",
    )
    args = parser.parse_args()

    run_loop(
        program_path=args.program,
        max_experiments=args.max_experiments,
        allow_mock=args.allow_mock,
    )


if __name__ == "__main__":
    main()
