"""
src/nlagent/bench/provenance.py - Benchmark Provenance & Reproducibility Tracker.

Provides collect() to capture end-to-end execution metadata for every benchmark
and evaluation run: run_id, git_sha, model_file, model_sha256, backend, server_build,
gpu (gpu_name), driver, timestamp, seed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
import subprocess
from typing import Any, Optional
import uuid

REQUIRED_PROVENANCE_FIELDS = (
    "run_id",
    "git_sha",
    "model_file",
    "model_sha256",
    "backend",
    "server_build",
    "gpu",
    "driver",
    "timestamp",
    "seed",
)

_CACHED_GIT_SHA: Optional[str] = None
_CACHED_GPU_INFO: Optional[tuple[str, str]] = None
_CACHED_FILE_HASHES: dict[str, str] = {}


def get_git_sha() -> str:
    """Retrieve current Git commit SHA-1."""
    global _CACHED_GIT_SHA
    if _CACHED_GIT_SHA is not None:
        return _CACHED_GIT_SHA

    # Try environment variable first
    if "GIT_SHA" in os.environ:
        _CACHED_GIT_SHA = os.environ["GIT_SHA"].strip()
        return _CACHED_GIT_SHA

    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2.0,
        )
        _CACHED_GIT_SHA = res.stdout.strip()
    except Exception:
        _CACHED_GIT_SHA = "unknown"
    return _CACHED_GIT_SHA


def get_gpu_and_driver() -> tuple[str, str]:
    """Retrieve GPU device name and driver version via nvidia-smi or torch."""
    global _CACHED_GPU_INFO
    if _CACHED_GPU_INFO is not None:
        return _CACHED_GPU_INFO

    gpu_name = "none"
    driver_version = "unknown"

    # Try nvidia-smi query
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3.0,
        )
        out = res.stdout.strip()
        if out:
            # First line if multi-GPU
            first_line = out.splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 2:
                gpu_name, driver_version = parts[0], parts[1]
            elif len(parts) == 1:
                gpu_name = parts[0]
    except Exception:
        # Fallback to PyTorch CUDA check if available
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
        except Exception:
            pass

    _CACHED_GPU_INFO = (gpu_name, driver_version)
    return _CACHED_GPU_INFO


def get_file_sha256(path: str) -> str:
    """Calculate SHA256 hex digest of a file (cached)."""
    if not path or not os.path.exists(path):
        return "none"

    abs_path = os.path.abspath(path)
    if abs_path in _CACHED_FILE_HASHES:
        return _CACHED_FILE_HASHES[abs_path]

    try:
        hasher = hashlib.sha256()
        with open(abs_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        _CACHED_FILE_HASHES[abs_path] = digest
        return digest
    except Exception:
        return "error_reading_file"


def collect(
    model_file: Optional[str] = None,
    backend: str = "llama-server",
    server_build: str = "unknown",
    seed: int = 42,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Collect full provenance metadata for a benchmark record.
    
    Returns a dictionary guaranteed to contain:
    run_id, git_sha, model_file, model_sha256, backend, server_build,
    gpu, gpu_name, driver, timestamp, seed.
    """
    gpu, driver = get_gpu_and_driver()
    git_sha = get_git_sha()

    model_name = model_file or os.environ.get("NL_MODEL_FILE", "NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf")
    model_sha = get_file_sha256(model_name) if os.path.exists(model_name) else "none"

    uid = run_id or uuid.uuid4().hex[:12]

    return {
        "run_id": uid,
        "git_sha": git_sha,
        "model_file": os.path.basename(model_name) if model_name else "none",
        "model_sha256": model_sha,
        "backend": backend,
        "server_build": server_build,
        "gpu": gpu,
        "gpu_name": gpu,
        "driver": driver,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
    }


def validate_provenance(record: dict[str, Any]) -> list[str]:
    """Validate that all required provenance fields are present in a record.
    
    Returns a list of missing field names (empty list if all are present).
    """
    missing: list[str] = []
    for field in REQUIRED_PROVENANCE_FIELDS:
        if field not in record:
            missing.append(field)
    return missing

