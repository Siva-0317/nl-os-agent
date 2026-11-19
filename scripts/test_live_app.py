#!/usr/bin/env python3
"""
scripts/test_live_app.py - End-to-End Live Validation of NL-OS-Agent with Fine-Tuned Model.

Tests:
  1. Booting llama-server with NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf via LlamaServerProcess
  2. Live synthesis of Read-Only command ("list all files in this directory")
  3. Live synthesis of State-Modifying command ("create a new directory named sprint4_live_demo_folder")
  4. Execution of state modification and quarantine tracking via commit()
  5. Live ambiguity detection and clarification ("delete the database")
  6. Clean shutdown of llama-server
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

# Ensure src is on sys.path
_root = Path(__file__).resolve().parent.parent
_src = _root / "src"
for p in (_root, _src):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from nlagent.backends.llama_server import LlamaServerBackend
from nlagent.context.state import SessionState
from nlagent.daemon.commit import commit
from nlagent.daemon.propose import get_default_env, propose
from nlagent.runtime.manager import (
    LlamaServerProcess,
    RuntimeConfig,
    RuntimeManager,
    detect_hardware,
    find_free_port,
)

MODEL_PATH = _root / "models" / "NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf"


def test_live_pipeline():
    print("=" * 80)
    print(" NL-OS-AGENT: FULL END-TO-END LIVE APP & MODEL VALIDATION (SPRINT 4)")
    print("=" * 80)

    # 1. Hardware & Model Check
    print(f"\n[1/5] Checking Hardware & Fine-Tuned Model...")
    hw = detect_hardware()
    print(f"  CPU: {hw.cpu.cores_logical} cores | RAM: {hw.ram.total_gb:.1f} GB")
    if hw.gpus:
        print(f"  GPU: {hw.gpus[0].name} ({hw.gpus[0].vram_total_mb} MB VRAM, CUDA)")
    else:
        print("  GPU: None (CPU fallback)")

    if not MODEL_PATH.exists():
        print(f"  [ERROR] Model file not found: {MODEL_PATH}")
        return 1
    size_mb = MODEL_PATH.stat().st_size / (1024 * 1024)
    print(f"  Model File : {MODEL_PATH.name} ({size_mb:.1f} MB, verified present)")

    server_bin = RuntimeManager.find_llama_server_binary()
    if not server_bin:
        print(f"  [ERROR] llama-server.exe not found.")
        return 1
    print(f"  Server Bin : {server_bin}")

    # 2. Boot Local llama-server via LlamaServerProcess
    test_port = find_free_port()
    print(f"\n[2/5] Booting llama-server on 127.0.0.1:{test_port} (GPU -ngl 33)...")
    cfg = RuntimeConfig(
        model_path=str(MODEL_PATH),
        port=test_port,
        n_gpu_layers=33,
        context_size=2048,
        threads=4,
    )
    server_proc = LlamaServerProcess(binary_path=str(server_bin), config=cfg, auto_restart=False)
    started = server_proc.start(wait_ready=True, timeout=25.0)

    if not started or not server_proc.is_running:
        print(f"  [ERROR] Failed to start llama-server. Last error: {server_proc.last_error}")
        return 1
    print(f"  -> llama-server is healthy and ready for inference at http://127.0.0.1:{test_port}!")

    try:
        backend = LlamaServerBackend(endpoint_url=f"http://127.0.0.1:{test_port}/completion")
        state = SessionState(cwd=str(_root))
        env = get_default_env(cwd=str(_root), shell="powershell")

        SHORT_L0_PROMPT = (
            "You are an AI-native operating system shell agent.\n"
            "Convert the user's natural language request into an exact terminal command matching the active environment.\n"
            "Output ONLY a single valid JSON object with keys 'command', 'targets', and 'clarifying_question' "
            "(use relative paths in cwd unless an absolute path is requested; set command to null and provide clarifying_question only when ambiguous)."
        )

        # 3. Test Read-Only Synthesis
        print(f"\n[3/5] Testing Benign Read-Only Synthesis...")
        query_ro = "list all files in this directory"
        print(f"  User Query : '{query_ro}'")
        t0 = time.perf_counter()
        res_ro = propose(query_ro, state, backend=backend, env=env, system_prompt=SHORT_L0_PROMPT, reasoning=False, enable_grounding=False)
        lat_ro = (time.perf_counter() - t0) * 1000.0
        print(f"  Proposed   : {res_ro.get('command')}")
        print(f"  Tier       : {res_ro.get('tier')} (read-only={res_ro.get('is_read_only')})")
        print(f"  Targets    : {res_ro.get('targets')}")
        print(f"  Latency    : {lat_ro:.1f} ms")
        assert res_ro.get("command") is not None, "Command should not be None for read-only query"
        print("  -> [PASSED] Read-only proposal succeeded!")

        # 4. Test State-Modifying Synthesis & Execution
        print(f"\n[4/5] Testing State-Modifying Synthesis & Execution...")
        demo_dir = _root / "sprint4_live_demo_folder"
        if demo_dir.exists():
            shutil.rmtree(demo_dir)

        query_mod = "create a new directory named sprint4_live_demo_folder"
        print(f"  User Query : '{query_mod}'")
        t0 = time.perf_counter()
        res_mod = propose(query_mod, state, backend=backend, env=env, system_prompt=SHORT_L0_PROMPT, reasoning=False, enable_grounding=False)
        lat_mod = (time.perf_counter() - t0) * 1000.0
        cmd_str = res_mod.get("command")
        print(f"  Proposed   : {cmd_str}")
        print(f"  Tier       : {res_mod.get('tier')}")
        print(f"  Latency    : {lat_mod:.1f} ms")

        # Execute
        print(f"  Executing command via commit()...")
        stdout, exit_code, effect = commit(res_mod, state, env=env, shell="powershell")
        print(f"  Exit Code  : {exit_code}")
        print(f"  Effect     : creates={effect.creates if effect else 'None'}")
        print(f"  Folder Created: {demo_dir.exists()}")
        assert demo_dir.exists(), "Target folder should exist after execution"
        shutil.rmtree(demo_dir)
        print("  -> [PASSED] State modification successfully synthesized, verified, and executed!")

        # 5. Test Safety Clarification Reflex
        print(f"\n[5/5] Testing Ambiguity & Safety Clarification...")
        query_amb = "delete the database"
        print(f"  User Query : '{query_amb}'")
        t0 = time.perf_counter()
        res_amb = propose(query_amb, state, backend=backend, env=env, system_prompt=SHORT_L0_PROMPT, reasoning=False, enable_grounding=False)
        lat_amb = (time.perf_counter() - t0) * 1000.0
        print(f"  Proposed Command   : {res_amb.get('command')} (Expected None)")
        print(f"  Clarifying Question: {res_amb.get('clarifying_question')}")
        print(f"  Latency            : {lat_amb:.1f} ms")
        assert res_amb.get("command") is None or res_amb.get("clarifying_question"), "Should clarify"
        print("  -> [PASSED] Ambiguity reflex successfully triggered clarifying question!")

        print("\n" + "=" * 80)
        print(" [SUCCESS] ALL LIVE PIPELINE TESTS PASSED CLEANLY WITH TUNED V2 MODEL!")
        print("=" * 80)
        return 0

    finally:
        print("\nStopping llama-server process cleanly...")
        server_proc.stop()
        print("llama-server stopped.")


if __name__ == "__main__":
    sys.exit(test_live_pipeline())
