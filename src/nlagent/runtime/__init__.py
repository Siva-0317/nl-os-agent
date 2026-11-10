"""
nlagent.runtime - Standalone runtime and process lifecycle management.

Provides hardware detection, process supervision, hidden window management,
GPU-to-CPU fallback, idle unloading, system health diagnostics (`doctor`),
and local model store management with resumable HTTP range downloads.
"""

from __future__ import annotations

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
from nlagent.runtime.models import (
    ChecksumMismatchError,
    InsufficientRAMError,
    ManifestError,
    ModelEntry,
    ModelError,
    ModelStatus,
    compute_sha256,
    download_model,
    find_model_in_manifest,
    get_model_path,
    get_model_status,
    get_models_dir,
    import_local_model,
    list_models_status,
    read_manifest,
    save_manifest,
    verify_file_sha256,
)
from nlagent.runtime.autotune import (
    CandidateConfig,
    CandidateEvaluation,
    FakeInferenceServer,
    RuntimeProfile,
    autotune_runtime,
    generate_candidate_grid,
    get_default_profile,
    load_stored_profile,
    save_profile,
)

__all__ = [
    # autotune exports
    "CandidateConfig",
    "CandidateEvaluation",
    "FakeInferenceServer",
    "RuntimeProfile",
    "autotune_runtime",
    "generate_candidate_grid",
    "get_default_profile",
    "load_stored_profile",
    "save_profile",
    # manager exports
    # manager exports
    "CPUInfo",
    "DoctorReport",
    "GPUInfo",
    "HardwareProfile",
    "LlamaServerProcess",
    "RAMInfo",
    "RuntimeConfig",
    "RuntimeManager",
    "detect_cpu",
    "detect_gpus_via_llama_server",
    "detect_gpus_via_nvidia_smi",
    "detect_hardware",
    "detect_ram",
    "find_free_port",
    "generate_api_key",
    "run_doctor",
    "select_runtime_config",
    # models exports
    "ChecksumMismatchError",
    "InsufficientRAMError",
    "ManifestError",
    "ModelEntry",
    "ModelError",
    "ModelStatus",
    "compute_sha256",
    "download_model",
    "find_model_in_manifest",
    "get_model_path",
    "get_model_status",
    "get_models_dir",
    "import_local_model",
    "list_models_status",
    "read_manifest",
    "save_manifest",
    "verify_file_sha256",
]
