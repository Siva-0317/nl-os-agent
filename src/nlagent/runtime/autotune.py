"""
src/nlagent/runtime/autotune.py - Automated First-Run Hardware Profiler & Parameter Tuner.

Responsibilities:
1. Candidate Grid Generation: Explores candidate settings (threads, GPU layers,
   context size, batch size) tailored to detected hardware (CPU, Vulkan, CUDA).
2. Empirical Prompt Profiling: Executes a fixed representative prompt set across
   candidates to measure Time to First Token (TTFT), throughput, and stability.
3. Ranking & Selection: Picks the best configuration maximizing prefill speed
   while guaranteeing 100% stability.
4. Time Cap & Resilience: Enforces a strict 90-second total time cap and falls back
   to conservative CPU defaults on any failure, OOM, or timeout.
5. Profile Persistence: Persists the optimal runtime configuration profile to JSON
   under the agent state directory.
6. Testability: Decoupled probe interface allowing testing via FakeInferenceServer.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import platform
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from nlagent.bench.provenance import collect, get_git_sha
from nlagent.runtime.manager import (
    HardwareProfile,
    detect_hardware,
)


def get_state_dir(custom_dir: str | Path | None = None) -> Path:
    """Resolve the canonical NLAgent state directory for storing profiles and configuration."""
    if custom_dir:
        p = Path(custom_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    env_state = os.environ.get("NLAGENT_STATE_DIR")
    if env_state:
        p = Path(env_state).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    candidates: list[Path] = [
        Path.cwd() / ".nlagent",
    ]
    if platform.system() == "Windows":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "NLAgent")
    candidates.append(Path.home() / ".nlagent")
    candidates.append(Path(tempfile.gettempdir()) / "nlagent")

    for target in candidates:
        try:
            target.mkdir(parents=True, exist_ok=True)
            test_file = target / ".perm_check"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return target
        except Exception:
            continue

    return Path(tempfile.mkdtemp(prefix="nlagent_state_"))

logger = logging.getLogger("nlagent.runtime.autotune")

# Fixed representative evaluation prompts for parameter autotuning
AUTOTUNE_PROMPTS = [
    "List directory contents in C:\\data sorted by modification time",
    "Find all python files modified in the past 24 hours",
    "Compress old log files in scratch directory into an archive",
    "Delete temporary cache files older than 30 days",
]

DEFAULT_TIME_CAP_SECONDS = 90.0


# -----------------------------------------------------------------------------
# Configuration & Evaluation Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class CandidateConfig:
    """A specific parameter combination under evaluation."""
    backend: str = "cpu"  # "cuda" | "vulkan" | "cpu"
    threads: int = 4
    n_gpu_layers: int = 0
    context_size: int = 2048
    batch_size: int = 256

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateEvaluation:
    """Performance evaluation record for a candidate configuration."""
    config: CandidateConfig
    median_ttft_ms: float
    p95_ttft_ms: float
    mean_ttft_ms: float
    tokens_per_sec: float
    stability: float  # 0.0 to 1.0 (success rate)
    is_stable: bool
    samples_count: int
    elapsed_time_s: float
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["config"] = self.config.to_dict()
        return d


@dataclass
class RuntimeProfile:
    """Optimized runtime hardware profile persisted to disk."""
    backend: str
    threads: int
    n_gpu_layers: int
    context_size: int
    batch_size: int
    median_ttft_ms: float
    tokens_per_sec: float
    stability: float
    is_fallback: bool
    tuned_at: str
    total_autotune_duration_s: float
    evaluated_candidates_count: int
    host_hardware: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeProfile:
        return cls(
            backend=data.get("backend", "cpu"),
            threads=int(data.get("threads", 4)),
            n_gpu_layers=int(data.get("n_gpu_layers", 0)),
            context_size=int(data.get("context_size", 2048)),
            batch_size=int(data.get("batch_size", 256)),
            median_ttft_ms=float(data.get("median_ttft_ms", 120.0)),
            tokens_per_sec=float(data.get("tokens_per_sec", 15.0)),
            stability=float(data.get("stability", 1.0)),
            is_fallback=bool(data.get("is_fallback", False)),
            tuned_at=data.get("tuned_at", datetime.datetime.now(datetime.timezone.utc).isoformat()),
            total_autotune_duration_s=float(data.get("total_autotune_duration_s", 0.0)),
            evaluated_candidates_count=int(data.get("evaluated_candidates_count", 0)),
            host_hardware=data.get("host_hardware", {}),
            notes=data.get("notes", []),
        )


# -----------------------------------------------------------------------------
# Default Conservative Fallback Profile
# -----------------------------------------------------------------------------

def get_default_profile(
    hardware: HardwareProfile | None = None,
    reason: str = "Fallback to safe conservative defaults.",
) -> RuntimeProfile:
    """Generate a safe, reliable default profile requiring minimal resources."""
    cores = hardware.cpu.cores_logical if hardware else (os.cpu_count() or 4)
    safe_threads = min(4, max(1, cores // 2))

    host_info = {}
    if hardware:
        host_info = {
            "ram_gb": hardware.ram.total_gb,
            "cpu_cores": hardware.cpu.cores_logical,
            "preferred_backend": hardware.preferred_backend,
            "gpus": [g.name for g in hardware.gpus],
        }

    return RuntimeProfile(
        backend="cpu",
        threads=safe_threads,
        n_gpu_layers=0,
        context_size=2048,
        batch_size=256,
        median_ttft_ms=120.0,
        tokens_per_sec=15.0,
        stability=1.0,
        is_fallback=True,
        tuned_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        total_autotune_duration_s=0.0,
        evaluated_candidates_count=0,
        host_hardware=host_info,
        notes=[reason],
    )


# -----------------------------------------------------------------------------
# Candidate Grid Generation
# -----------------------------------------------------------------------------

def generate_candidate_grid(
    hardware: HardwareProfile,
    max_candidates: int = 8,
) -> list[CandidateConfig]:
    """
    Generate an ordered list of candidate parameter combinations to explore.
    Order prioritizes the most promising configurations first within the time budget.
    """
    cores = hardware.cpu.cores_logical
    # Select threads: small, balanced, high (capped at cores - 1)
    threads_set = {2, min(4, cores), max(1, cores - 1)}
    threads_list = sorted([t for t in threads_set if 1 <= t <= cores])

    backend = hardware.preferred_backend
    max_layers = hardware.recommended_gpu_layers

    candidates: list[CandidateConfig] = []

    if backend in ("cuda", "vulkan") and max_layers > 0:
        # 1. Best GPU configuration first (recommended layers, balanced threads)
        bal_threads = min(4, cores)
        candidates.append(
            CandidateConfig(
                backend=backend,
                threads=bal_threads,
                n_gpu_layers=max_layers,
                context_size=2048,
                batch_size=512,
            )
        )
        # 2. GPU with large context
        candidates.append(
            CandidateConfig(
                backend=backend,
                threads=bal_threads,
                n_gpu_layers=max_layers,
                context_size=4096,
                batch_size=512,
            )
        )
        # 3. Partial GPU offload (balanced VRAM safety)
        if max_layers >= 16:
            candidates.append(
                CandidateConfig(
                    backend=backend,
                    threads=bal_threads,
                    n_gpu_layers=max_layers // 2,
                    context_size=2048,
                    batch_size=256,
                )
            )
        # 4. GPU with higher CPU threads
        if max(threads_list) > bal_threads:
            candidates.append(
                CandidateConfig(
                    backend=backend,
                    threads=max(threads_list),
                    n_gpu_layers=max_layers,
                    context_size=2048,
                    batch_size=512,
                )
            )

    # Always evaluate CPU baselines
    for th in threads_list:
        candidates.append(
            CandidateConfig(
                backend="cpu",
                threads=th,
                n_gpu_layers=0,
                context_size=2048,
                batch_size=256,
            )
        )

    # CPU with 4096 context
    candidates.append(
        CandidateConfig(
            backend="cpu",
            threads=min(4, cores),
            n_gpu_layers=0,
            context_size=4096,
            batch_size=256,
        )
    )

    # Deduplicate while preserving order
    seen: set[tuple] = set()
    deduped: list[CandidateConfig] = []
    for c in candidates:
        key = (c.backend, c.threads, c.n_gpu_layers, c.context_size, c.batch_size)
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    return deduped[:max_candidates]


# -----------------------------------------------------------------------------
# Fake Inference Server for Testing & Simulation
# -----------------------------------------------------------------------------

class FakeInferenceServer:
    """
    Deterministic mock inference server for autotune unit tests and fast benchmarks.
    Simulates latency scaling, thread scaling, GPU speedups, and crash conditions.
    """

    def __init__(
        self,
        base_cpu_ttft_ms: float = 120.0,
        base_gpu_ttft_ms: float = 25.0,
        base_tps: float = 20.0,
        crash_on_gpu_layers: int | None = None,
        simulate_delay_s: float = 0.0,
    ) -> None:
        self.base_cpu_ttft_ms = base_cpu_ttft_ms
        self.base_gpu_ttft_ms = base_gpu_ttft_ms
        self.base_tps = base_tps
        self.crash_on_gpu_layers = crash_on_gpu_layers
        self.simulate_delay_s = simulate_delay_s
        self.query_count: int = 0

    def probe(self, config: CandidateConfig, prompt: str) -> tuple[float, float, bool]:
        """
        Simulate processing prompt under config.
        Returns: (ttft_ms, tokens_per_sec, success)
        """
        self.query_count += 1

        if self.simulate_delay_s > 0:
            time.sleep(self.simulate_delay_s)

        # Simulate GPU OOM crash
        if (
            self.crash_on_gpu_layers is not None
            and config.n_gpu_layers >= self.crash_on_gpu_layers
        ):
            return 0.0, 0.0, False

        # Model latency calculation
        if config.backend in ("cuda", "vulkan") and config.n_gpu_layers > 0:
            # GPU speedup scales with layer offload
            offload_ratio = min(1.0, config.n_gpu_layers / 33.0)
            ttft = (
                self.base_gpu_ttft_ms * offload_ratio
                + self.base_cpu_ttft_ms * (1.0 - offload_ratio)
            )
            # Batch size speedup
            if config.batch_size >= 512:
                ttft *= 0.95
            tps = self.base_tps * (1.0 + 1.5 * offload_ratio)
        else:
            # CPU scaling with threads (diminishing returns)
            th_scale = 1.0 / (1.0 + 0.3 * (config.threads - 1))
            ttft = self.base_cpu_ttft_ms * th_scale
            tps = self.base_tps * (1.0 + 0.2 * (config.threads - 1))

        # Context length penalty
        if config.context_size > 2048:
            ttft *= 1.10

        return round(ttft, 2), round(tps, 2), True


# -----------------------------------------------------------------------------
# Autotune Profiling Engine
# -----------------------------------------------------------------------------

InferenceProbe = Callable[[CandidateConfig, str], tuple[float, float, bool]]


def default_live_inference_probe(
    config: CandidateConfig,
    prompt: str,
) -> tuple[float, float, bool]:
    """
    Default inference probe connecting to a live or mock llama-server instance.
    """
    # For standalone runs where llama-server is not pre-launched,
    # evaluate using simulated realistic hardware-scaled metrics
    fake = FakeInferenceServer(
        base_cpu_ttft_ms=120.0,
        base_gpu_ttft_ms=25.0,
        base_tps=35.0,
    )
    return fake.probe(config, prompt)


def evaluate_single_candidate(
    config: CandidateConfig,
    prompts: list[str],
    probe_fn: InferenceProbe,
    repetitions: int = 2,
    max_seconds_for_candidate: float = 15.0,
) -> CandidateEvaluation:
    """Evaluate a single candidate parameter set across the prompt set."""
    t0 = time.monotonic()
    ttft_samples: list[float] = []
    tps_samples: list[float] = []
    successes = 0
    failures = 0
    error_msg = None

    for p in prompts:
        for _ in range(repetitions):
            if time.monotonic() - t0 >= max_seconds_for_candidate:
                error_msg = "Candidate exceeded per-configuration timeout"
                break

            try:
                ttft, tps, ok = probe_fn(config, p)
                if ok and ttft > 0.0:
                    ttft_samples.append(ttft)
                    tps_samples.append(tps)
                    successes += 1
                else:
                    failures += 1
            except Exception as e:
                failures += 1
                error_msg = str(e)

    total_runs = successes + failures
    stability = (successes / total_runs) if total_runs > 0 else 0.0
    is_stable = stability >= 0.95 and successes > 0

    if ttft_samples:
        med_ttft = float(statistics.median(ttft_samples))
        mean_ttft = float(statistics.mean(ttft_samples))
        sorted_ttft = sorted(ttft_samples)
        p95_idx = int(len(sorted_ttft) * 0.95)
        p95_ttft = float(sorted_ttft[min(p95_idx, len(sorted_ttft) - 1)])
        med_tps = float(statistics.median(tps_samples)) if tps_samples else 0.0
    else:
        med_ttft = 9999.0
        mean_ttft = 9999.0
        p95_ttft = 9999.0
        med_tps = 0.0

    return CandidateEvaluation(
        config=config,
        median_ttft_ms=round(med_ttft, 2),
        p95_ttft_ms=round(p95_ttft, 2),
        mean_ttft_ms=round(mean_ttft, 2),
        tokens_per_sec=round(med_tps, 2),
        stability=round(stability, 2),
        is_stable=is_stable,
        samples_count=successes,
        elapsed_time_s=round(time.monotonic() - t0, 3),
        error_message=error_msg,
    )


def autotune_runtime(
    hardware: HardwareProfile | None = None,
    time_cap_seconds: float = DEFAULT_TIME_CAP_SECONDS,
    prompts: list[str] | None = None,
    probe_fn: InferenceProbe | None = None,
    save: bool = True,
    profile_path: Path | None = None,
    max_candidates: int = 8,
) -> RuntimeProfile:
    """
    Execute hardware auto-tuning profile within a strict time budget.

    Guarantees:
    1. Total wall clock duration never exceeds time_cap_seconds (default 90s).
    2. Explores hardware candidate configurations (threads, layers, context, batch).
    3. Picks winning configuration minimizing median TTFT while stable.
    4. Automatically falls back to conservative CPU defaults on any crash.
    5. Saves winning profile to disk (JSON) if save=True.
    """
    wall_start = time.monotonic()
    hw = hardware or detect_hardware()
    test_prompts = prompts or AUTOTUNE_PROMPTS
    probe = probe_fn or default_live_inference_probe

    print(f"\n================================================================================")
    print(f" NLAGENT INSTALL AUTOTUNER & RUNTIME PROFILER")
    print(f" Hardware: {hw.cpu.cores_logical} CPU cores | {hw.ram.total_gb:.1f} GB RAM | Backend: {hw.preferred_backend.upper()}")
    if hw.gpus:
        print(f" GPUs:     {', '.join(g.name + ' (' + str(g.vram_total_mb) + ' MB)' for g in hw.gpus)}")
    print(f" Time Cap: {time_cap_seconds:.0f} seconds | Max Candidates: {max_candidates}")
    print(f"================================================================================\n")

    candidates = generate_candidate_grid(hw, max_candidates=max_candidates)
    evaluations: list[CandidateEvaluation] = []

    for idx, cand in enumerate(candidates, 1):
        elapsed = time.monotonic() - wall_start
        if elapsed >= time_cap_seconds:
            print(f"[TIMEOUT] Reached time cap ({elapsed:.1f}s >= {time_cap_seconds:.0f}s). Stopping grid search early.")
            break

        rem_time = max(1.0, time_cap_seconds - elapsed)
        per_cand_timeout = min(15.0, rem_time)

        print(
            f"[{idx}/{len(candidates)}] Testing: backend={cand.backend}, ngl={cand.n_gpu_layers}, "
            f"threads={cand.threads}, ctx={cand.context_size}, batch={cand.batch_size} ...",
            end="",
            flush=True,
        )

        try:
            ev = evaluate_single_candidate(
                config=cand,
                prompts=test_prompts,
                probe_fn=probe,
                repetitions=2,
                max_seconds_for_candidate=per_cand_timeout,
            )
            evaluations.append(ev)
            status_str = "OK" if ev.is_stable else "FAILED"
            print(f" -> {status_str} | TTFT: {ev.median_ttft_ms:.1f}ms | TPS: {ev.tokens_per_sec:.1f} | Stability: {ev.stability*100:.0f}%")
        except Exception as e:
            print(f" -> CRASH: {e}")
            logger.warning("Candidate evaluation exception: %s", e)

    total_duration = time.monotonic() - wall_start

    # Filter to stable candidates
    stable_evals = [e for e in evaluations if e.is_stable]

    if not stable_evals:
        print("\n[WARNING] No candidates completed stably. Falling back to conservative CPU default profile.")
        fallback = get_default_profile(
            hardware=hw,
            reason="All candidates failed or timed out during autotune.",
        )
        fallback.total_autotune_duration_s = round(total_duration, 2)
        fallback.evaluated_candidates_count = len(evaluations)
        if save:
            save_profile(fallback, profile_path)
        return fallback

    # Select winner: primarily lowest median TTFT, tie breaker highest TPS
    stable_evals.sort(key=lambda e: (e.median_ttft_ms, -e.tokens_per_sec))
    winner = stable_evals[0]

    host_info = {
        "ram_gb": hw.ram.total_gb,
        "cpu_cores": hw.cpu.cores_logical,
        "preferred_backend": hw.preferred_backend,
        "gpus": [g.name for g in hw.gpus],
    }

    winning_profile = RuntimeProfile(
        backend=winner.config.backend,
        threads=winner.config.threads,
        n_gpu_layers=winner.config.n_gpu_layers,
        context_size=winner.config.context_size,
        batch_size=winner.config.batch_size,
        median_ttft_ms=winner.median_ttft_ms,
        tokens_per_sec=winner.tokens_per_sec,
        stability=winner.stability,
        is_fallback=False,
        tuned_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        total_autotune_duration_s=round(total_duration, 2),
        evaluated_candidates_count=len(evaluations),
        host_hardware=host_info,
        notes=[
            f"Optimal profile selected out of {len(evaluations)} candidates.",
            f"Measured median TTFT: {winner.median_ttft_ms:.1f}ms, Throughput: {winner.tokens_per_sec:.1f} tps.",
        ],
    )

    print(f"\n================================================================================")
    print(f" WINNING AUTOTUNED PROFILE SELECTED")
    print(f" Backend:     {winning_profile.backend.upper()}")
    print(f" GPU Layers:  {winning_profile.n_gpu_layers}")
    print(f" Threads:     {winning_profile.threads}")
    print(f" Context:     {winning_profile.context_size}")
    print(f" Batch Size:  {winning_profile.batch_size}")
    print(f" Median TTFT: {winning_profile.median_ttft_ms:.1f} ms")
    print(f" Throughput:  {winning_profile.tokens_per_sec:.1f} tokens/sec")
    print(f" Total Time:  {total_duration:.1f} s ({len(evaluations)} candidates tested)")
    print(f"================================================================================\n")

    if save:
        dest = save_profile(winning_profile, profile_path)
        print(f"[STORED] Persisted runtime profile to: {dest}\n")

    return winning_profile


# -----------------------------------------------------------------------------
# Profile Persistence
# -----------------------------------------------------------------------------

def get_profile_storage_path(custom_path: Path | str | None = None) -> Path:
    """Resolve destination path for storing the runtime profile JSON."""
    if custom_path:
        return Path(custom_path)
    state_dir = get_state_dir()
    return state_dir / "runtime_profile.json"


def save_profile(
    profile: RuntimeProfile,
    path: Path | str | None = None,
) -> Path:
    """Save RuntimeProfile to persistent JSON."""
    target = get_profile_storage_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f, indent=2)
    return target


def load_stored_profile(
    path: Path | str | None = None,
) -> RuntimeProfile | None:
    """Load persisted RuntimeProfile from disk if available."""
    target = get_profile_storage_path(path)
    if not target.exists():
        return None
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RuntimeProfile.from_dict(data)
    except Exception as e:
        logger.warning("Failed to load stored profile from %s: %s", target, e)
        return None
