"""
exec_eval.py - Execution-Based Evaluator for Natural Language Terminal Commands.

Given test cases with a fixture specification, natural language request, and reference command:
1. Materializes identical fixture copies into separate temporary directories.
2. Executes reference and candidate commands in isolation.
3. Compares final system states:
   - For state-modifying / destructive commands: compares manifest diffs (deleted, created,
     modified, permission changes, and exact file content hashes via manifest.py).
   - For read-only inspection commands: compares normalised stdout (and asserts zero state changes).
4. Returns comprehensive EvalResult objects for benchmarking and verification.

All execution occurs strictly inside temporary directories (tempfile).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from nlagent.safety.manifest import diff, manifest, tier_from_diff


@dataclass
class TestCase:
    """Specification of an execution evaluation test case."""

    case_id: str | int
    request: str
    reference_command: str
    fixture_spec: dict[str, str | bytes] | str | Path | Callable[[Path], None]
    is_read_only: bool = False
    shell: str = "auto"
    timeout: float = 10.0
    description: str = ""


@dataclass
class EvalResult:
    """Detailed execution result for a single test case evaluation."""

    case_id: str | int
    request: str
    reference_command: str
    candidate_command: str
    success: bool
    is_read_only: bool
    diff_match: bool
    stdout_match: bool
    ref_diff: dict[str, list[str]]
    cand_diff: dict[str, list[str]]
    ref_stdout: str
    cand_stdout: str
    ref_stderr: str
    cand_stderr: str
    ref_returncode: int
    cand_returncode: int
    reason: str
    error: str | None = None
    wall_time_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_rel_path(path: str) -> str:
    """Normalize path keys to forward slashes for cross-platform consistency."""
    return path.replace("\\", "/").strip("./")


def normalize_stdout(stdout: str) -> str:
    """Normalize command stdout for robust whitespace/newline comparison.
    
    1. Normalizes line endings (\\r\\n -> \\n, \\r -> \\n).
    2. Strips leading/trailing whitespace on each line.
    3. Collapses consecutive horizontal whitespace to single space.
    4. Omits empty blank lines.
    """
    if not stdout:
        return ""
    normalized_lines: list[str] = []
    raw_lines = stdout.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in raw_lines:
        cleaned = re.sub(r"[ \t]+", " ", line.strip())
        if cleaned:
            normalized_lines.append(cleaned)
    return "\n".join(normalized_lines)


def compare_stdout(cand_stdout: str, ref_stdout: str) -> bool:
    """Compare stdout between candidate and reference executions.
    
    Returns True if:
    1. Direct normalized strings match identically, OR
    2. The multiline set of outputs matches (for unordered outputs like find or ls).
    """
    norm_cand = normalize_stdout(cand_stdout)
    norm_ref = normalize_stdout(ref_stdout)

    if norm_cand == norm_ref:
        return True

    # Check order-insensitive matching for unordered outputs
    cand_lines = sorted(norm_cand.split("\n"))
    ref_lines = sorted(norm_ref.split("\n"))
    return cand_lines == ref_lines


def compare_manifests(
    cand_diff: dict[str, list[str]],
    ref_diff: dict[str, list[str]],
    cand_after: dict[str, Any],
    ref_after: dict[str, Any],
) -> tuple[bool, str]:
    """Compare manifest diffs and final file hashes between candidate and reference runs.
    
    Returns (matches: bool, reason: str).
    """
    # 1. Compare list of modified, created, deleted, and permission-changed paths
    for key in ("deleted", "created", "modified"):
        cand_items = sorted(normalize_rel_path(k) for k in cand_diff.get(key, []))
        ref_items = sorted(normalize_rel_path(k) for k in ref_diff.get(key, []))
        if cand_items != ref_items:
            return False, f"Mismatch in {key} items: candidate={cand_items} vs reference={ref_items}"

    # 2. Verify content hashes for all created or modified files
    for k in ref_diff.get("created", []) + ref_diff.get("modified", []):
        norm_k = normalize_rel_path(k)
        cand_key = next((ck for ck in cand_after if normalize_rel_path(ck) == norm_k), None)
        ref_key = next((rk for rk in ref_after if normalize_rel_path(rk) == norm_k), None)

        if not cand_key or not ref_key:
            return False, f"Path '{norm_k}' missing from final directory state"

        cand_hash = cand_after[cand_key][1]
        ref_hash = ref_after[ref_key][1]

        if cand_hash != ref_hash:
            return (
                False,
                f"Content hash mismatch for '{norm_k}': candidate sha1={cand_hash} vs reference sha1={ref_hash}",
            )

    return True, "Manifest diff and file contents match reference exactly"


def materialize_fixture(
    fixture_spec: dict[str, str | bytes] | str | Path | Callable[[Path], None],
    target_dir: Path,
) -> None:
    """Populate a directory with files according to fixture specification."""
    if isinstance(fixture_spec, dict):
        for rel_path, content in fixture_spec.items():
            full_path = target_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                full_path.mkdir(parents=True, exist_ok=True)
            elif isinstance(content, bytes):
                full_path.write_bytes(content)
            else:
                full_path.write_text(str(content), encoding="utf-8")
    elif isinstance(fixture_spec, (str, Path)):
        src = Path(fixture_spec)
        if src.is_dir():
            shutil.copytree(src, target_dir, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, target_dir)
        else:
            raise FileNotFoundError(f"Fixture source path does not exist: {fixture_spec}")
    elif callable(fixture_spec):
        fixture_spec(target_dir)
    else:
        raise TypeError(f"Unsupported fixture specification type: {type(fixture_spec)}")


def run_command(
    cmd: str,
    cwd: Path,
    timeout: float = 10.0,
    shell: str = "auto",
) -> tuple[int, str, str]:
    """Execute a shell command inside a specified working directory.
    
    Returns (returncode, stdout, stderr).
    """
    if not cmd or not cmd.strip():
        return 0, "", ""

    cmd_str = cmd.strip()
    is_windows = platform.system() == "Windows"

    # Select shell executable
    if shell == "auto":
        if is_windows:
            # Check for PowerShell specific cmdlets or variables
            pwsh_indicators = ("get-", "set-", "remove-", "new-item", "stop-process", "select-string", "$env:")
            if any(ind in cmd_str.lower() for ind in pwsh_indicators):
                cmd_args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_str]
            else:
                # Default to powershell on Windows, or bash if explicitly posix
                cmd_args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_str]
        else:
            cmd_args = ["/bin/sh", "-c", cmd_str]
    elif shell == "powershell":
        cmd_args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_str]
    elif shell in ("bash", "sh"):
        cmd_args = [shell, "-c", cmd_str]
    else:
        cmd_args = [shell, "-c", cmd_str]

    try:
        proc = subprocess.run(
            cmd_args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return -1, "", f"Execution error: {e}"


def evaluate_case(
    case: TestCase,
    candidate_command: str,
    shell: str | None = None,
) -> EvalResult:
    """Evaluate candidate command against reference command in isolated temporary directories."""
    t_start = time.perf_counter()
    active_shell = shell or case.shell

    with tempfile.TemporaryDirectory() as td_ref_raw, tempfile.TemporaryDirectory() as td_cand_raw:
        ref_dir = Path(td_ref_raw)
        cand_dir = Path(td_cand_raw)

        # 1. Materialize identical fixture copies
        materialize_fixture(case.fixture_spec, ref_dir)
        materialize_fixture(case.fixture_spec, cand_dir)

        # 2. Record initial manifests
        before_ref = manifest(str(ref_dir))
        before_cand = manifest(str(cand_dir))

        # 3. Execute reference command
        ref_rc, ref_stdout, ref_stderr = run_command(
            cmd=case.reference_command,
            cwd=ref_dir,
            timeout=case.timeout,
            shell=active_shell,
        )

        # 4. Execute candidate command
        cand_rc, cand_stdout, cand_stderr = run_command(
            cmd=candidate_command,
            cwd=cand_dir,
            timeout=case.timeout,
            shell=active_shell,
        )

        # 5. Record post-execution manifests
        after_ref = manifest(str(ref_dir))
        after_cand = manifest(str(cand_dir))

        diff_ref = diff(before_ref, after_ref)
        diff_cand = diff(before_cand, after_cand)

        # Automatically determine if reference command is read-only
        has_ref_state_changes = any(bool(diff_ref[k]) for k in ("deleted", "created", "modified", "perm"))
        is_read_only = case.is_read_only or (not has_ref_state_changes)

        diff_match, diff_reason = compare_manifests(diff_cand, diff_ref, after_cand, after_ref)
        stdout_match = compare_stdout(cand_stdout, ref_stdout)

        # Evaluation verdict
        success = False
        error_msg = None

        if cand_rc != 0 and ref_rc == 0:
            success = False
            reason = f"Candidate command failed with exit code {cand_rc}: {cand_stderr.strip()}"
            error_msg = cand_stderr.strip()
        elif is_read_only:
            # Read-only commands: must make 0 changes AND match expected stdout
            has_cand_state_changes = any(bool(diff_cand[k]) for k in ("deleted", "created", "modified", "perm"))
            if has_cand_state_changes:
                success = False
                reason = "Read-only command erroneously mutated filesystem state"
            elif stdout_match:
                success = True
                reason = "Read-only command output matches reference and made 0 state mutations"
            else:
                success = False
                reason = f"Stdout mismatch: cand='{cand_stdout.strip()}' vs ref='{ref_stdout.strip()}'"
        else:
            # State-modifying / destructive: manifest diff and file contents must match
            if diff_match:
                success = True
                reason = diff_reason
            else:
                success = False
                reason = diff_reason

        wall_time = time.perf_counter() - t_start

        return EvalResult(
            case_id=case.case_id,
            request=case.request,
            reference_command=case.reference_command,
            candidate_command=candidate_command,
            success=success,
            is_read_only=is_read_only,
            diff_match=diff_match,
            stdout_match=stdout_match,
            ref_diff=diff_ref,
            cand_diff=diff_cand,
            ref_stdout=ref_stdout,
            cand_stdout=cand_stdout,
            ref_stderr=ref_stderr,
            cand_stderr=cand_stderr,
            ref_returncode=ref_rc,
            cand_returncode=cand_rc,
            reason=reason,
            error=error_msg,
            wall_time_s=wall_time,
        )


def evaluate_batch(
    cases: Sequence[TestCase],
    candidate_commands: Sequence[str] | Mapping[str | int, str] | Callable[[TestCase], str],
    shell: str | None = None,
) -> list[EvalResult]:
    """Evaluate a collection of test cases against model-generated candidate commands."""
    results: list[EvalResult] = []

    for idx, case in enumerate(cases):
        if callable(candidate_commands):
            cand_cmd = candidate_commands(case)
        elif isinstance(candidate_commands, Mapping):
            cand_cmd = candidate_commands.get(case.case_id, "")
        elif isinstance(candidate_commands, Sequence):
            cand_cmd = candidate_commands[idx] if idx < len(candidate_commands) else ""
        else:
            raise TypeError(f"Invalid candidate_commands type: {type(candidate_commands)}")

        res = evaluate_case(case, cand_cmd, shell=shell)
        results.append(res)

    return results


def summarize_results(results: Sequence[EvalResult]) -> dict[str, Any]:
    """Compute aggregate evaluation metrics across all evaluated test cases."""
    total = len(results)
    if total == 0:
        return {"total": 0, "passed": 0, "failed": 0, "accuracy": 0.0}

    passed = sum(1 for r in results if r.success)
    failed = total - passed
    accuracy = (passed / total) * 100.0

    read_only_cases = [r for r in results if r.is_read_only]
    state_mod_cases = [r for r in results if not r.is_read_only]

    ro_passed = sum(1 for r in read_only_cases if r.success)
    sm_passed = sum(1 for r in state_mod_cases if r.success)

    ro_acc = (ro_passed / len(read_only_cases) * 100.0) if read_only_cases else 0.0
    sm_acc = (sm_passed / len(state_mod_cases) * 100.0) if state_mod_cases else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "accuracy": round(accuracy, 2),
        "read_only": {
            "total": len(read_only_cases),
            "passed": ro_passed,
            "accuracy": round(ro_acc, 2),
        },
        "state_modifying": {
            "total": len(state_mod_cases),
            "passed": sm_passed,
            "accuracy": round(sm_acc, 2),
        },
    }

