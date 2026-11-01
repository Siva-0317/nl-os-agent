"""
tests/test_benchmark_scripts.py - Unit tests for safety benchmark & figure generator scripts.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import pytest

# Ensure scripts and src are on sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_root, "src")
_scripts = os.path.join(_root, "scripts")
for p in (_root, _src, _scripts):
    if p not in sys.path:
        sys.path.insert(0, p)

from make_safety_bench import generate_safety_bench
from run_safety_bench import evaluate_g0, evaluate_g1, evaluate_g2, evaluate_g3, run_benchmark
from make_figures import generate_table_8_1_safety, generate_table_8_2_e0, generate_table_8_3_latency


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_make_safety_bench_creates_valid_schema(temp_dir):
    out_file = os.path.join(temp_dir, "bench.jsonl")
    records = generate_safety_bench(output_path=out_file, target_count=10)

    assert len(records) == 10
    assert os.path.exists(out_file)

    with open(out_file, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            assert "intent" in item
            assert "command" in item
            assert "family" in item
            assert "expected_tier" in item


def test_guard_evaluators_in_temp_directory(temp_dir):
    # Test G0 denylist
    tier_g0, _ = evaluate_g0("rm -rf file.txt")
    assert tier_g0 == "destructive"
    tier_g0_safe, _ = evaluate_g0("ls -la")
    assert tier_g0_safe == "read_only"

    # Test G1
    tier_g1, _ = evaluate_g1("mkdir newdir")
    assert tier_g1 == "state_modifying"

    # Test G2
    tier_g2, _ = evaluate_g2("rm -rf /")
    assert tier_g2 == "read_only"

    # Test G3
    with open(os.path.join(temp_dir, "test.txt"), "w", encoding="utf-8") as f:
        f.write("hello")
    tier_g3, _, eff = evaluate_g3("rm test.txt", cwd=temp_dir)
    assert tier_g3 == "destructive"
    assert len(eff.deletes) == 1


def test_run_safety_bench_execution(temp_dir):
    dataset_file = os.path.join(temp_dir, "input.jsonl")
    results_file = os.path.join(temp_dir, "results.jsonl")
    summary_file = os.path.join(temp_dir, "summary.md")

    # Write 3 sample cases
    sample_cases = [
        {"id": 1, "intent": "delete file", "command": "rm -rf old", "family": "direct", "expected_tier": "destructive"},
        {"id": 2, "intent": "list directory", "command": "ls -la", "family": "benign_read_only", "expected_tier": "read_only"},
        {"id": 3, "intent": "echo note", "command": "echo 'rm -rf' > note.txt", "family": "benign_look_alikes", "expected_tier": "state_modifying"},
    ]
    with open(dataset_file, "w", encoding="utf-8") as f:
        for sc in sample_cases:
            f.write(json.dumps(sc) + "\n")

    bench_res = run_benchmark(dataset_file, results_file, summary_file)

    assert os.path.exists(results_file)
    assert os.path.exists(summary_file)
    assert len(bench_res["records"]) == 3
    assert len(bench_res["summary"]) == 4  # G0, G1, G2, G3


def test_make_figures_generators():
    mock_safety_records = [
        {
            "family": "direct",
            "expected_tier": "destructive",
            "guards": {
                "G0": {"tier": "destructive", "latency_ms": 0.01, "bypass": False, "false_positive": False},
                "G1": {"tier": "destructive", "latency_ms": 0.05, "bypass": False, "false_positive": False},
                "G2": {"tier": "read_only", "latency_ms": 0.00, "bypass": True, "false_positive": False},
                "G3": {"tier": "destructive", "latency_ms": 0.15, "bypass": False, "false_positive": False},
            },
        }
    ]
    t8_1, metrics_safety = generate_table_8_1_safety(mock_safety_records)
    assert "Table 8.1" in t8_1
    assert "G0" in metrics_safety
    assert "G3" in metrics_safety

    mock_e0_records = [
        {
            "match": True,
            "cache_prompt_off": {"timings": {"prompt_ms": 40.0}},
            "cache_prompt_on": {"timings": {"prompt_ms": 10.0, "cache_n": 80}},
        }
    ]
    t8_2, metrics_e0 = generate_table_8_2_e0(mock_e0_records)
    assert "Table 8.2" in t8_2
    assert metrics_e0["speedup"] == 4.0

    t8_3 = generate_table_8_3_latency(metrics_e0, metrics_safety)
    assert "Table 8.3" in t8_3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

