#!/usr/bin/env python3
"""
scripts/profile_aiperf_vs_e5.py - Benchmark NVIDIA-Nemotron-3-Nano-4B via NVIDIA AIPerf.

Measures:
- Time-to-First-Token (TTFT) distribution (mean, p50, p90, p99)
- Inter-Token Latency (ITL)
- Output Token Throughput (tokens/sec)
- Total Request Latency

Compares side-by-side with our verified e5_matrix.jsonl ground truth benchmark.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LLAMA_SERVER = REPO_ROOT / "bin" / "llama-server.exe"
MODEL_PATH = REPO_ROOT / "models" / "NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf"
AIPERF_EXE = REPO_ROOT / ".venv" / "Scripts" / "aiperf.exe"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(port: int, timeout_s: float = 45.0) -> bool:
    import urllib.request
    url = f"http://127.0.0.1:{port}/health"
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def main():
    print("=" * 80)
    print(" NVIDIA AIPerf vs. E5 Benchmark Hardware Profiling Comparison")
    print("=" * 80)

    if not LLAMA_SERVER.exists():
        print(f"[ERROR] llama-server not found at: {LLAMA_SERVER}")
        sys.exit(1)
    if not MODEL_PATH.exists():
        print(f"[ERROR] Model file not found at: {MODEL_PATH}")
        sys.exit(1)
    if not AIPERF_EXE.exists():
        print(f"[ERROR] aiperf executable not found at: {AIPERF_EXE}")
        sys.exit(1)

    port = find_free_port()
    print(f"[*] Starting llama-server on port {port} (NGL=33, Model={MODEL_PATH.name})...")

    server_cmd = [
        str(LLAMA_SERVER),
        "-m", str(MODEL_PATH),
        "-ngl", "33",
        "-c", "2048",
        "--port", str(port),
        "--log-disable",
    ]

    server_proc = subprocess.Popen(server_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        print("[*] Waiting for llama-server to become ready...")
        if not wait_for_server(port):
            print("[ERROR] llama-server failed to start within timeout.")
            sys.exit(1)
        print("[+] llama-server is healthy and ready for inference on GPU!")

        artifact_dir = REPO_ROOT / "results" / "aiperf_profile"
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        print("\n[*] Launching NVIDIA AIPerf profile run...")
        print("    Configuration:")
        print("    - Model: NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf")
        print("    - Mode: chat (streaming=True)")
        print("    - Concurrency: 1 (single-user desktop assistant scenario)")
        print("    - Requests: 10")
        print("    - Prompt Input Tokens: ~118 (matching our short prompt)")
        print("    - Output Tokens: ~50 (matching our command JSON length)")

        aiperf_cmd = [
            str(AIPERF_EXE),
            "profile",
            MODEL_PATH.name,
            "--url", f"http://127.0.0.1:{port}",
            "--endpoint-type", "chat",
            "--streaming",
            "--concurrency", "1",
            "--request-count", "10",
            "--prompt-input-tokens-mean", "118",
            "--prompt-input-tokens-stddev", "10",
            "--output-tokens-mean", "50",
            "--tokenizer", "gpt2",
            "--use-server-token-count",
            "--artifact-dir", str(artifact_dir),
            "--no-gpu-telemetry",
        ]

        print(f"[*] Running command: {' '.join(aiperf_cmd[:4])} ...")
        t_start = time.time()
        res = subprocess.run(aiperf_cmd, capture_output=True, text=True)
        elapsed = time.time() - t_start

        print(f"[+] AIPerf completed in {elapsed:.1f}s (Exit code: {res.returncode})")
        if res.stdout:
            print("\n--- AIPerf STDOUT ---")
            print(res.stdout[-3000:])  # Print last 3000 characters
        if res.stderr and res.returncode != 0:
            print("\n--- AIPerf STDERR ---")
            print(res.stderr[-2000:])

    finally:
        print("\n[*] Stopping llama-server cleanly...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        print("[+] llama-server stopped.")


if __name__ == "__main__":
    main()
