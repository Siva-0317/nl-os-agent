"""
tests/test_runtime_manager.py - Unit tests for Standalone Runtime Manager & Doctor.

Tests verify:
1. Hardware detection (RAM, CPU cores, GPU parsing via nvidia-smi and list-devices).
2. Runtime configuration selection (CUDA, Vulkan, CPU, and layer offload calculation).
3. Process lifecycle with a fake llama-server executable (hidden launch, port, API key).
4. HTTP health check and crash auto-restart recovery.
5. GPU load test failure triggering automatic fallback to CPU mode (n_gpu_layers=0).
6. Idle timeout detection and memory unload.
7. nlagent doctor diagnostic report generation and sensitive data redaction.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
import pytest

from nlagent.runtime.manager import (
    CPUInfo,
    DoctorReport,
    GPUInfo,
    HardwareProfile,
    LlamaServerProcess,
    RAMInfo,
    RuntimeConfig,
    RuntimeManager,
    detect_cpu,
    detect_gpus_via_llama_server,
    detect_gpus_via_nvidia_smi,
    detect_hardware,
    detect_ram,
    find_free_port,
    generate_api_key,
    run_doctor,
    select_runtime_config,
)


class TestHardwareDetection:
    """Verify system RAM, CPU, and GPU detection."""

    def test_detect_ram_values(self):
        ram = detect_ram()
        assert ram.total_bytes > 0
        assert ram.total_gb > 0.0
        assert ram.available_bytes >= 0

    def test_detect_cpu_values(self):
        cpu = detect_cpu()
        assert cpu.cores_logical >= 1
        assert cpu.cores_physical >= 1
        assert len(cpu.architecture) > 0

    def test_detect_gpus_via_list_devices_parsing(self, tmp_path):
        # Create a fake binary that prints device list and exits
        fake_bin = tmp_path / "fake_llama_devices.bat" if sys.platform == "win32" else tmp_path / "fake_llama_devices.sh"
        if sys.platform == "win32":
            fake_bin.write_text(
                "@echo off\n"
                "echo Available devices:\n"
                "echo   CUDA0: NVIDIA GeForce RTX 2050 (4096 MiB, 3800 MiB free)\n"
                "echo   Vulkan0: Intel Iris Xe Graphics (1600 MiB)\n",
                encoding="utf-8",
            )
        else:
            fake_bin.write_text(
                "#!/bin/sh\n"
                "echo 'Available devices:'\n"
                "echo '  CUDA0: NVIDIA GeForce RTX 2050 (4096 MiB, 3800 MiB free)'\n",
                encoding="utf-8",
            )
            fake_bin.chmod(0o755)

        gpus = detect_gpus_via_llama_server(str(fake_bin))
        assert len(gpus) >= 1
        assert "RTX 2050" in gpus[0].name
        assert gpus[0].backend == "cuda"
        assert gpus[0].vram_total_mb == 4096


class TestRuntimeConfiguration:
    """Verify runtime config and layer offload calculation."""

    def test_select_config_cuda_high_vram(self):
        profile = HardwareProfile(
            ram=RAMInfo(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
            cpu=CPUInfo(cores_logical=8, cores_physical=4),
            gpus=[GPUInfo(index=0, name="RTX 4090", vram_total_mb=24000, vram_free_mb=20000, backend="cuda")],
            preferred_backend="cuda",
            recommended_gpu_layers=33,
        )
        cfg = select_runtime_config(profile)
        assert cfg.backend == "cuda"
        assert cfg.n_gpu_layers == 33
        assert cfg.port > 0
        assert len(cfg.api_key) == 32

    def test_select_config_cpu_fallback_low_vram(self):
        profile = HardwareProfile(
            ram=RAMInfo(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
            cpu=CPUInfo(cores_logical=4, cores_physical=2),
            gpus=[],
            preferred_backend="cpu",
            recommended_gpu_layers=0,
        )
        cfg = select_runtime_config(profile)
        assert cfg.backend == "cpu"
        assert cfg.n_gpu_layers == 0
        assert cfg.threads >= 1


class TestFakeServerProcessLifecycle:
    """Test hidden startup, health check, crash recovery, and CPU fallback using a fake executable."""

    @pytest.fixture
    def fake_server_bin(self, tmp_path):
        """
        Create a fake llama-server executable script.
        Writes status file with PID, port, ngl, and simulated GPU crash state.
        Supports --list-devices and handles simulated GPU crash on ngl > 0.
        """
        fake_py = tmp_path / "fake_llama_server.py"
        script_code = """
import sys
import argparse
import json
import os
import time
import tempfile

if "--list-devices" in sys.argv:
    print("Available devices:")
    print("  CUDA0: Simulated NVIDIA GPU (4096 MiB, 3900 MiB free)")
    sys.exit(0)

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8080)
parser.add_argument("--api-key", default="")
parser.add_argument("-ngl", type=int, default=0)
parser.add_argument("-c", type=int, default=2048)
parser.add_argument("-t", type=int, default=4)
parser.add_argument("-m", default="")
args, _ = parser.parse_known_args()

status_file = os.path.join(tempfile.gettempdir(), f"fake_llama_{args.port}.json")
is_gpu_crash = (args.ngl > 0 and os.environ.get("SIMULATE_GPU_CRASH") == "1")

data = {
    "pid": os.getpid(),
    "port": args.port,
    "ngl": args.ngl,
    "status": "ok",
    "gpu_crash": is_gpu_crash,
}

with open(status_file, "w", encoding="utf-8") as f:
    json.dump(data, f)

try:
    while True:
        time.sleep(0.1)
finally:
    if os.path.exists(status_file):
        try:
            os.remove(status_file)
        except Exception:
            pass
"""
        fake_py.write_text(script_code, encoding="utf-8")
        return str(fake_py)

    def test_start_health_check_and_stop(self, fake_server_bin):
        port = find_free_port()
        cfg = RuntimeConfig(
            backend="cpu",
            n_gpu_layers=0,
            port=port,
            host="127.0.0.1",
            api_key="test_api_key_1234",
        )
        proc = LlamaServerProcess(binary_path=fake_server_bin, config=cfg)

        started = proc.start(wait_ready=True, timeout=5.0)
        assert started is True
        assert proc.is_running is True
        assert proc.health_check() is True

        # Stop server
        proc.stop(timeout=2.0)
        assert proc.is_running is False

    def test_crash_detection_and_auto_restart(self, fake_server_bin):
        port = find_free_port()
        cfg = RuntimeConfig(backend="cpu", port=port, api_key="test_key")
        proc = LlamaServerProcess(binary_path=fake_server_bin, config=cfg, auto_restart=True)

        proc.start(wait_ready=True, timeout=5.0)
        assert proc.is_running is True

        # Force kill process to simulate unexpected crash
        if proc.process:
            proc.process.kill()
            proc.process.wait()

        assert proc.is_running is False

        # ensure_running detects crash and restarts
        recovered = proc.ensure_running()
        assert recovered is True
        assert proc.is_running is True
        assert proc.restart_count == 1

        proc.stop()

    def test_gpu_load_test_failure_falls_back_to_cpu(self, fake_server_bin, monkeypatch):
        monkeypatch.setenv("SIMULATE_GPU_CRASH", "1")
        port = find_free_port()
        # Start in GPU mode with 33 layers
        cfg = RuntimeConfig(backend="cuda", n_gpu_layers=33, port=port, api_key="gpu_key")
        proc = LlamaServerProcess(binary_path=fake_server_bin, config=cfg)

        proc.start(wait_ready=True, timeout=5.0)
        assert proc.is_running is True
        assert proc.config.backend == "cuda"
        assert proc.config.n_gpu_layers == 33

        # Enforce GPU fallback on failure
        ok = proc.check_and_enforce_gpu_fallback()
        assert ok is True
        assert proc.fallback_occurred is True
        # Verify it automatically fell back to CPU mode
        assert proc.config.backend == "cpu"
        assert proc.config.n_gpu_layers == 0
        assert proc.is_running is True

        proc.stop()

    def test_idle_unload_behavior(self, fake_server_bin):
        port = find_free_port()
        cfg = RuntimeConfig(backend="cpu", port=port)
        proc = LlamaServerProcess(binary_path=fake_server_bin, config=cfg)

        proc.start(wait_ready=True, timeout=5.0)
        assert proc.is_running is True

        # Simulate idle for 10 seconds
        proc.last_active_time = time.time() - 10.0
        unloaded = proc.check_idle_unload(max_idle_seconds=2.0)
        assert unloaded is True
        assert proc.is_running is False


class TestDoctorDiagnostics:
    """Verify nlagent doctor report generation and data redaction."""

    def test_run_doctor_clean_execution(self):
        report = run_doctor()
        assert isinstance(report, DoctorReport)
        assert report.all_passed is True
        assert "NLAGENT SYSTEM & RUNTIME DOCTOR" in report.diagnostics_text
        assert len(report.git_sha) > 0
        assert report.raw_data["ram_total_gb"] > 0.0

    def test_doctor_redaction_masks_sensitive_data(self):
        report = DoctorReport(
            timestamp="2026-11-09T00:00:00Z",
            git_sha="abcdef123456",
            os_info="Windows 10",
            cpu_info="12 cores",
            ram_info="16 GB",
            gpu_info="RTX 2050",
            model_status="models/test.gguf",
            runtime_status="online",
            storage_status="writable",
            latency_status="12ms",
            all_passed=True,
            diagnostics_text=(
                "Storage: C:\\Users\\secret_admin_user\\AppData\\Local\\NLAgent\n"
                "Auth: Bearer 0123456789abcdef0123456789abcdef\n"
            ),
        )
        # Mock username in environment
        os.environ["USERNAME"] = "secret_admin_user"
        redacted = report.get_redacted_text()

        assert "secret_admin_user" not in redacted
        assert "<REDACTED_USER>" in redacted
        assert "0123456789abcdef0123456789abcdef" not in redacted
        assert "<REDACTED_KEY>" in redacted

