"""
tests/test_autotune.py - Unit & Integration Tests for Runtime Autotuner & Hardware Matrix.

Verifies:
1. Candidate grid generation for CPU and GPU profiles.
2. Single-candidate evaluation with FakeInferenceServer.
3. Candidate ranking and winning selection based on TTFT and stability.
4. Strict enforcement of time cap (early termination).
5. Safe default profile fallback on crash, OOM, or timeout.
6. Profile JSON serialization and deserialization roundtrip.
7. End-to-end execution of run_hardware_matrix and provenance verification.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src and root are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(root_dir, "src")
scripts_dir = os.path.join(root_dir, "scripts")
for p in (root_dir, src_dir, scripts_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.bench.provenance import validate_provenance
from nlagent.runtime.autotune import (
    CandidateConfig,
    CandidateEvaluation,
    FakeInferenceServer,
    RuntimeProfile,
    autotune_runtime,
    evaluate_single_candidate,
    generate_candidate_grid,
    get_default_profile,
    load_stored_profile,
    save_profile,
)
from nlagent.runtime.manager import (
    CPUInfo,
    GPUInfo,
    HardwareProfile,
    RAMInfo,
)
from scripts.run_hardware_matrix import run_hardware_matrix


class TestAutotune(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_candidate_grid_generation_cpu_only(self):
        hw = HardwareProfile(
            ram=RAMInfo(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
            cpu=CPUInfo(cores_logical=8, cores_physical=4, architecture="x86_64"),
            gpus=[],
            preferred_backend="cpu",
            recommended_gpu_layers=0,
        )

        candidates = generate_candidate_grid(hw, max_candidates=10)
        self.assertGreaterEqual(len(candidates), 3)

        # All candidates must have 0 GPU layers
        for c in candidates:
            self.assertEqual(c.backend, "cpu")
            self.assertEqual(c.n_gpu_layers, 0)
            self.assertIn(c.threads, [2, 4, 7, 8])

    def test_candidate_grid_generation_gpu(self):
        gpu = GPUInfo(
            index=0,
            name="NVIDIA GeForce RTX 2050",
            vram_total_mb=4096,
            vram_free_mb=3500,
            backend="cuda",
        )
        hw = HardwareProfile(
            ram=RAMInfo(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
            cpu=CPUInfo(cores_logical=12, cores_physical=6, architecture="x86_64"),
            gpus=[gpu],
            preferred_backend="cuda",
            recommended_gpu_layers=33,
        )

        candidates = generate_candidate_grid(hw, max_candidates=10)
        self.assertGreater(len(candidates), 4)

        backends = {c.backend for c in candidates}
        self.assertIn("cuda", backends)
        self.assertIn("cpu", backends)

        layers = {c.n_gpu_layers for c in candidates}
        self.assertIn(33, layers)
        self.assertIn(0, layers)

    def test_evaluate_single_candidate_mock(self):
        fake = FakeInferenceServer(
            base_cpu_ttft_ms=100.0,
            base_gpu_ttft_ms=25.0,
            base_tps=30.0,
        )
        config = CandidateConfig(
            backend="cuda",
            threads=4,
            n_gpu_layers=33,
            context_size=2048,
            batch_size=512,
        )

        ev = evaluate_single_candidate(
            config=config,
            prompts=["Test prompt 1", "Test prompt 2"],
            probe_fn=fake.probe,
            repetitions=2,
            max_seconds_for_candidate=5.0,
        )

        self.assertTrue(ev.is_stable)
        self.assertEqual(ev.stability, 1.0)
        self.assertEqual(ev.samples_count, 4)
        self.assertLess(ev.median_ttft_ms, 30.0)
        self.assertGreater(ev.tokens_per_sec, 20.0)

    def test_autotune_picks_fastest_stable_candidate(self):
        fake = FakeInferenceServer(
            base_cpu_ttft_ms=120.0,
            base_gpu_ttft_ms=25.0,
            base_tps=40.0,
        )
        gpu = GPUInfo(
            index=0,
            name="Test GPU",
            vram_total_mb=4096,
            vram_free_mb=3500,
            backend="cuda",
        )
        hw = HardwareProfile(
            ram=RAMInfo(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
            cpu=CPUInfo(cores_logical=8, cores_physical=4, architecture="x86_64"),
            gpus=[gpu],
            preferred_backend="cuda",
            recommended_gpu_layers=33,
        )

        profile_file = self.temp_path / "test_profile.json"
        profile = autotune_runtime(
            hardware=hw,
            time_cap_seconds=10.0,
            prompts=["Prompt 1", "Prompt 2"],
            probe_fn=fake.probe,
            save=True,
            profile_path=profile_file,
            max_candidates=4,
        )

        self.assertFalse(profile.is_fallback)
        self.assertEqual(profile.backend, "cuda")
        self.assertEqual(profile.n_gpu_layers, 33)
        self.assertLess(profile.median_ttft_ms, 40.0)
        self.assertTrue(profile_file.exists())

    def test_autotune_strict_time_cap_enforcement(self):
        # Server with delay simulating slow evaluation
        fake = FakeInferenceServer(
            base_cpu_ttft_ms=100.0,
            base_gpu_ttft_ms=30.0,
            simulate_delay_s=0.25,
        )
        hw = HardwareProfile(
            ram=RAMInfo(total_bytes=8 * 1024**3, available_bytes=4 * 1024**3),
            cpu=CPUInfo(cores_logical=4, cores_physical=2, architecture="x86_64"),
            gpus=[],
            preferred_backend="cpu",
            recommended_gpu_layers=0,
        )

        # Time cap of 0.6 seconds should stop after ~1-2 candidates
        profile = autotune_runtime(
            hardware=hw,
            time_cap_seconds=0.6,
            prompts=["P1", "P2"],
            probe_fn=fake.probe,
            save=False,
            max_candidates=8,
        )

        self.assertLessEqual(profile.total_autotune_duration_s, 2.0)
        self.assertLess(profile.evaluated_candidates_count, 8)

    def test_autotune_crash_fallback_to_defaults(self):
        # Server configured to crash on any GPU layer offload
        crashing_fake = FakeInferenceServer(
            crash_on_gpu_layers=1,  # Crashes if ngl >= 1
        )
        gpu = GPUInfo(
            index=0,
            name="Crashing GPU",
            vram_total_mb=4096,
            vram_free_mb=3500,
            backend="cuda",
        )
        hw = HardwareProfile(
            ram=RAMInfo(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
            cpu=CPUInfo(cores_logical=8, cores_physical=4, architecture="x86_64"),
            gpus=[gpu],
            preferred_backend="cuda",
            recommended_gpu_layers=33,
        )

        # Force a failure probe function that always raises
        def always_fail_probe(config, prompt):
            return 0.0, 0.0, False

        profile_file = self.temp_path / "fallback_profile.json"
        profile = autotune_runtime(
            hardware=hw,
            time_cap_seconds=5.0,
            prompts=["P1"],
            probe_fn=always_fail_probe,
            save=True,
            profile_path=profile_file,
            max_candidates=3,
        )

        # Must fall back to safe CPU profile
        self.assertTrue(profile.is_fallback)
        self.assertEqual(profile.backend, "cpu")
        self.assertEqual(profile.n_gpu_layers, 0)
        self.assertEqual(profile.stability, 1.0)
        self.assertTrue(profile_file.exists())

    def test_profile_persistence_save_and_load(self):
        profile = RuntimeProfile(
            backend="cuda",
            threads=6,
            n_gpu_layers=33,
            context_size=4096,
            batch_size=512,
            median_ttft_ms=24.5,
            tokens_per_sec=55.0,
            stability=1.0,
            is_fallback=False,
            tuned_at="2026-11-09T20:00:00Z",
            total_autotune_duration_s=12.4,
            evaluated_candidates_count=4,
            host_hardware={"cpu_cores": 12, "ram_gb": 15.7},
            notes=["High-performance profile"],
        )

        path = self.temp_path / "saved_profile.json"
        saved_path = save_profile(profile, path)
        self.assertEqual(saved_path, path)
        self.assertTrue(path.exists())

        loaded = load_stored_profile(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.backend, "cuda")
        self.assertEqual(loaded.threads, 6)
        self.assertEqual(loaded.n_gpu_layers, 33)
        self.assertEqual(loaded.median_ttft_ms, 24.5)
        self.assertFalse(loaded.is_fallback)

    def test_run_hardware_matrix_execution(self):
        out_jsonl = self.temp_path / "hardware_matrix.jsonl"
        out_md = self.temp_path / "hardware_matrix.md"

        results = run_hardware_matrix(
            output_jsonl=str(out_jsonl),
            output_md=str(out_md),
            runs_per_prompt=2,
            allow_simulation=True,
            seed=42,
        )

        self.assertEqual(len(results), 3)
        self.assertTrue(out_jsonl.exists())
        self.assertTrue(out_md.exists())

        # Verify 3 tiers present
        tier_ids = [r.tier_id for r in results]
        self.assertEqual(tier_ids, ["cpu", "vulkan", "cuda"])

        # Validate line-by-line JSONL structure and provenance
        with open(out_jsonl, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)

        for l in lines:
            data = json.loads(l)
            self.assertIn("tier_id", data)
            self.assertIn("median_ttft_ms", data)
            self.assertIn("relative_speedup", data)
            missing = validate_provenance(data)
            self.assertEqual(missing, [])

        # Validate Markdown report content
        md_text = out_md.read_text(encoding="utf-8")
        self.assertIn("Hardware Tier Performance Comparison Matrix", md_text)
        self.assertIn("CPU Tier (Baseline)", md_text)
        self.assertIn("CUDA Tier (Dedicated GPU)", md_text)


if __name__ == "__main__":
    unittest.main()

