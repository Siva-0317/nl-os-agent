"""
src/nlagent/runtime/models.py - Local Model Store and Manifest Management.

Responsibilities:
1. Manifest Management: Read and validate `models/manifest.json` entries:
   - name, quant, size, sha256, min_ram_gb, notice_file.
2. Storage Location: Defaults to `%LOCALAPPDATA%/NLAgent/models` on Windows,
   honoring `NLAGENT_MODELS_DIR` and `NLAGENT_STATE_DIR` overrides with portable fallbacks.
3. Resumable Downloads: Downloads model weights using HTTP Range requests (`Range: bytes=X-`),
   handling 206 Partial Content (append) vs 200 OK (restart) and 416 Range Not Satisfiable.
4. Checksum Verification: Streams and validates SHA-256 digests against manifest declarations;
   guards against partial or corrupted downloads.
5. Local Import: Imports models directly from existing local paths, validating checksums
   and preserving associated notice/license documents.
6. Inventory & Diagnostics: Checks file readiness, partial download states, and system RAM compatibility.
"""

from __future__ import annotations

import email
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from nlagent.runtime.manager import detect_ram

logger = logging.getLogger("nlagent.runtime.models")


# -----------------------------------------------------------------------------
# 1. Exceptions
# -----------------------------------------------------------------------------

class ModelError(Exception):
    """Base exception for model management errors."""
    pass


class ManifestError(ModelError):
    """Raised when manifest file is missing or invalid."""
    pass


class ChecksumMismatchError(ModelError):
    """Raised when file SHA-256 checksum does not match expected digest."""

    def __init__(self, message: str, path: Path | str, expected: str, actual: str) -> None:
        super().__init__(message)
        self.path = Path(path)
        self.expected = expected
        self.actual = actual


class InsufficientRAMError(ModelError):
    """Raised when system RAM is less than model minimum requirements."""
    pass


# -----------------------------------------------------------------------------
# 2. Data Structures
# -----------------------------------------------------------------------------

@dataclass
class ModelEntry:
    """Specification of an individual model in manifest.json."""
    name: str
    quant: str
    size: int
    sha256: str
    min_ram_gb: float
    notice_file: str = ""
    url: str = ""
    filename: str = ""
    description: str = ""

    @property
    def canonical_filename(self) -> str:
        """Standardized filename on disk (e.g. `nemotron-3-nano-4b.Q4_K_M.gguf`)."""
        if self.filename:
            return self.filename
        if self.quant:
            return f"{self.name}.{self.quant}.gguf"
        return f"{self.name}.gguf"

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "quant": self.quant,
            "size": self.size,
            "sha256": self.sha256.lower(),
            "min_ram_gb": self.min_ram_gb,
            "notice_file": self.notice_file,
            "url": self.url,
            "filename": self.canonical_filename,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelEntry:
        """Create entry from JSON dictionary, validating required fields."""
        if not data.get("name"):
            raise ManifestError("Model entry missing required 'name' field")

        return cls(
            name=str(data["name"]).strip(),
            quant=str(data.get("quant", "")).strip(),
            size=int(data.get("size", 0)),
            sha256=str(data.get("sha256", "")).strip().lower(),
            min_ram_gb=float(data.get("min_ram_gb", 0.0)),
            notice_file=str(data.get("notice_file", "")).strip(),
            url=str(data.get("url", "")).strip(),
            filename=str(data.get("filename", "")).strip(),
            description=str(data.get("description", "")).strip(),
        )


@dataclass
class ModelStatus:
    """Current on-disk readiness and diagnostic state of a model."""
    entry: ModelEntry
    status: str  # "ready", "partial", "missing", "corrupt"
    local_path: Path | None
    downloaded_bytes: int
    total_bytes: int
    has_sufficient_ram: bool
    system_ram_gb: float

    @property
    def progress_percent(self) -> float:
        """Percentage of model file currently on disk."""
        if self.total_bytes <= 0:
            return 100.0 if self.status == "ready" else 0.0
        return min(100.0, (self.downloaded_bytes / self.total_bytes) * 100.0)


# -----------------------------------------------------------------------------
# 3. Model Storage Directory Management
# -----------------------------------------------------------------------------

def get_models_dir(custom_dir: str | Path | None = None) -> Path:
    """
    Resolve the canonical model storage directory.
    Hierarchy:
    1. Explicit `custom_dir` if passed
    2. `NLAGENT_MODELS_DIR` environment variable
    3. `NLAGENT_STATE_DIR/models` environment variable
    4. Windows: `%LOCALAPPDATA%/NLAgent/models`
    5. Fallback: `~/.nlagent/models` or workspace `.nlagent/models`
    """
    if custom_dir:
        p = Path(custom_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Environment overrides
    env_models = os.environ.get("NLAGENT_MODELS_DIR")
    if env_models:
        p = Path(env_models).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    env_state = os.environ.get("NLAGENT_STATE_DIR")
    if env_state:
        p = Path(env_state).resolve() / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Default to %LOCALAPPDATA%/NLAgent/models on Windows
    candidates: list[Path] = []
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "NLAgent" / "models")

    # Portable user directory fallback
    home_app = Path.home() / ".nlagent" / "models"
    candidates.append(home_app)

    # Local workspace fallback
    workspace_fallback = Path.cwd() / ".nlagent" / "models"
    candidates.append(workspace_fallback)

    # Temp fallback
    temp_fallback = Path(tempfile.gettempdir()) / "nlagent" / "models"
    candidates.append(temp_fallback)

    for target in candidates:
        try:
            target.mkdir(parents=True, exist_ok=True)
            # Test write permission
            test_file = target / ".perm_check"
            try:
                test_file.write_text("ok", encoding="utf-8")
                test_file.unlink(missing_ok=True)
                return target
            except Exception:
                continue
        except Exception:
            continue

    # Final guarantee: system tempdir
    temp_dir = Path(tempfile.mkdtemp(prefix="nlagent_models_"))
    return temp_dir


# -----------------------------------------------------------------------------
# 4. Manifest Reading & Writing
# -----------------------------------------------------------------------------

def resolve_manifest_path(path: str | Path | None = None) -> Path:
    """Locate manifest.json file from explicit path or common project locations."""
    if path:
        p = Path(path).resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"Specified manifest not found: {p}")

    candidates = [
        Path.cwd() / "models" / "manifest.json",
        Path.cwd() / "manifest.json",
        Path(__file__).resolve().parent.parent.parent.parent / "models" / "manifest.json",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()

    # Default target location
    return (Path.cwd() / "models" / "manifest.json").resolve()


def read_manifest(manifest_path: str | Path | None = None) -> list[ModelEntry]:
    """
    Read and parse model entries from `models/manifest.json`.
    Supports both list-of-dicts `[...]` and wrapped `{"models": [...]}` structures.
    """
    path = resolve_manifest_path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest file does not exist: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ManifestError(f"Failed to parse JSON manifest at {path}: {e}") from e

    raw_entries: list[Any]
    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict):
        raw_entries = data.get("models") or data.get("entries") or []
        if not isinstance(raw_entries, list):
            raise ManifestError(f"Manifest at {path} has invalid 'models' field (expected list)")
    else:
        raise ManifestError(f"Unexpected manifest JSON root type: {type(data)}")

    entries: list[ModelEntry] = []
    for item in raw_entries:
        if isinstance(item, dict):
            entries.append(ModelEntry.from_dict(item))

    return entries


def save_manifest(entries: Sequence[ModelEntry], manifest_path: str | Path) -> None:
    """Save model entries list into manifest.json."""
    p = Path(manifest_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "models": [e.to_dict() for e in entries],
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def find_model_in_manifest(
    manifest: Sequence[ModelEntry],
    name: str,
    quant: str | None = None,
) -> ModelEntry | None:
    """Find a specific model entry by name and optional quantization."""
    name_clean = name.strip().lower()
    quant_clean = quant.strip().lower() if quant else None

    for entry in manifest:
        if entry.name.lower() == name_clean:
            if quant_clean is None or entry.quant.lower() == quant_clean:
                return entry
    return None


# -----------------------------------------------------------------------------
# 5. SHA-256 Verification Utilities
# -----------------------------------------------------------------------------

def compute_sha256(file_path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute lowercase hexadecimal SHA-256 digest of a file in streaming chunks."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Cannot compute SHA-256 for non-existent file: {p}")

    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_file_sha256(file_path: str | Path, expected_sha256: str) -> bool:
    """Compare file SHA-256 digest with expected string (case-insensitive)."""
    if not expected_sha256:
        return True
    actual = compute_sha256(file_path)
    return actual == expected_sha256.strip().lower()


# -----------------------------------------------------------------------------
# 6. Resumable HTTP Range Downloader
# -----------------------------------------------------------------------------

def download_model(
    entry: ModelEntry | str,
    url: str | None = None,
    dest_dir: str | Path | None = None,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    chunk_size: int = 64 * 1024,
    timeout: float = 30.0,
    progress_callback: Callable[[int, int | None], None] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    force: bool = False,
) -> Path:
    """
    Download model file using resumable HTTP Range requests.

    Algorithm:
    1. Checks if final model file already exists and passes SHA-256 (skips if valid).
    2. Inspects `<filename>.part`: if present, sends `Range: bytes={existing_bytes}-`.
    3. Handles `206 Partial Content` (appends to `.part`), `200 OK` (rewrites from byte 0),
       and `416 Range Not Satisfiable` (revalidates or resets).
    4. On successful completion, verifies SHA-256 and atomically renames `.part` -> final path.
    """
    # 1. Resolve model metadata
    if isinstance(entry, ModelEntry):
        target_name = entry.canonical_filename
        download_url = url or entry.url
        target_sha256 = expected_sha256 or entry.sha256
        target_size = expected_size or entry.size
        notice_name = entry.notice_file
    else:
        target_name = str(entry)
        download_url = url or ""
        target_sha256 = expected_sha256 or ""
        target_size = expected_size or 0
        notice_name = ""

    if not download_url:
        raise ValueError(f"No download URL provided for model '{target_name}'")

    models_dir = get_models_dir(dest_dir)
    final_path = models_dir / target_name
    part_path = models_dir / f"{target_name}.part"

    # 2. Check if already completely downloaded and valid
    if final_path.exists() and not force:
        if target_sha256:
            if verify_file_sha256(final_path, target_sha256):
                logger.info("Model %s already present and verified.", target_name)
                return final_path
            logger.warning("Existing file %s corrupted; re-downloading...", final_path)
            final_path.unlink(missing_ok=True)
        else:
            return final_path

    # 3. Check partial file size for Range request
    existing_bytes = 0
    if part_path.exists():
        existing_bytes = part_path.stat().st_size
        if target_size > 0 and existing_bytes > target_size:
            # Stale partial file larger than expected; discard
            part_path.unlink(missing_ok=True)
            existing_bytes = 0

    # 4. Prepare HTTP request headers
    headers: dict[str, str] = {
        "User-Agent": "NLAgent-ModelDownloader/1.0",
        "Accept": "*/*",
    }
    if existing_bytes > 0:
        headers["Range"] = f"bytes={existing_bytes}-"

    req = urllib.request.Request(download_url, headers=headers)
    active_opener = opener or urllib.request._opener or urllib.request.build_opener()

    try:
        resp = active_opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416:
            # Range not satisfiable: existing bytes might already equal full file
            if existing_bytes > 0 and target_sha256 and verify_file_sha256(part_path, target_sha256):
                part_path.replace(final_path)
                return final_path
            # Otherwise reset partial file and re-download from start
            part_path.unlink(missing_ok=True)
            existing_bytes = 0
            req = urllib.request.Request(download_url, headers={"User-Agent": "NLAgent-ModelDownloader/1.0"})
            resp = active_opener.open(req, timeout=timeout)
        else:
            raise

    # 5. Evaluate response code and determine append vs overwrite mode
    code = getattr(resp, "status", None) or getattr(resp, "code", 200)

    # Determine total expected bytes
    total_bytes: int | None = target_size if target_size > 0 else None
    content_range = resp.headers.get("Content-Range") if hasattr(resp, "headers") else None
    if content_range and "/" in content_range:
        try:
            total_bytes = int(content_range.split("/")[-1])
        except Exception:
            pass

    if total_bytes is None and hasattr(resp, "headers"):
        clen = resp.headers.get("Content-Length")
        if clen:
            try:
                length = int(clen)
                total_bytes = (existing_bytes + length) if code == 206 else length
            except Exception:
                pass

    if code == 206:
        # Server accepted range: append to partial file
        mode = "ab"
        downloaded = existing_bytes
    else:
        # Full content: overwrite from start
        mode = "wb"
        downloaded = 0

    # 6. Stream content to .part file
    with open(part_path, mode) as f:
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            f.flush()
            downloaded += len(chunk)
            if progress_callback:
                progress_callback(downloaded, total_bytes)

    # 7. Checksum verification
    if target_sha256:
        actual_sha256 = compute_sha256(part_path)
        if actual_sha256 != target_sha256.lower():
            raise ChecksumMismatchError(
                f"SHA-256 verification failed for {target_name}: expected {target_sha256}, got {actual_sha256}",
                path=part_path,
                expected=target_sha256,
                actual=actual_sha256,
            )

    # 8. Promote partial file to final path
    part_path.replace(final_path)

    # 9. Handle notice file if specified
    if notice_name:
        notice_target = models_dir / Path(notice_name).name
        if not notice_target.exists():
            notice_target.write_text(
                f"Model: {target_name}\nNotice: Refer to upstream repository for full terms.\n",
                encoding="utf-8",
            )

    logger.info("Successfully downloaded and verified %s -> %s", target_name, final_path)
    return final_path


# -----------------------------------------------------------------------------
# 7. Import from Local File
# -----------------------------------------------------------------------------

def import_local_model(
    source_path: str | Path,
    entry: ModelEntry | None = None,
    dest_dir: str | Path | None = None,
    notice_path: str | Path | None = None,
    verify_sha: bool = True,
    force: bool = False,
) -> Path:
    """
    Import a model from a local file path into the model store.
    Validates SHA-256 against manifest declaration and copies notice file if available.
    """
    src = Path(source_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Source model file does not exist: {src}")

    models_dir = get_models_dir(dest_dir)
    target_name = entry.canonical_filename if entry else src.name
    dest_path = models_dir / target_name

    # Checksum verification against manifest
    if verify_sha and entry and entry.sha256:
        src_sha256 = compute_sha256(src)
        if src_sha256 != entry.sha256.lower():
            raise ChecksumMismatchError(
                f"Local source file {src.name} checksum mismatch: expected {entry.sha256}, got {src_sha256}",
                path=src,
                expected=entry.sha256,
                actual=src_sha256,
            )

    # Copy if destination is different from source
    if src != dest_path:
        if dest_path.exists() and not force:
            if verify_sha and entry and entry.sha256:
                if verify_file_sha256(dest_path, entry.sha256):
                    return dest_path
        shutil.copy2(src, dest_path)

    # Copy or link notice file
    resolved_notice: Path | None = None
    if notice_path and Path(notice_path).exists():
        resolved_notice = Path(notice_path).resolve()
    elif entry and entry.notice_file:
        cand1 = src.parent / entry.notice_file
        cand2 = Path.cwd() / entry.notice_file
        cand3 = Path.cwd() / "models" / entry.notice_file
        for cand in [cand1, cand2, cand3]:
            if cand.exists():
                resolved_notice = cand
                break

    if resolved_notice and resolved_notice.is_file():
        notice_dest = models_dir / resolved_notice.name
        if resolved_notice != notice_dest:
            shutil.copy2(resolved_notice, notice_dest)

    logger.info("Imported model %s into store: %s", target_name, dest_path)
    return dest_path


# -----------------------------------------------------------------------------
# 8. Model Inventory and Status Diagnostics
# -----------------------------------------------------------------------------

def get_model_path(
    name: str,
    quant: str | None = None,
    models_dir: str | Path | None = None,
) -> Path | None:
    """Find installed model file by name and optional quantization in model store."""
    store = get_models_dir(models_dir)
    candidates = [
        f"{name}.{quant}.gguf" if quant else f"{name}.gguf",
        f"{name}.gguf",
        name,
    ]
    for c in candidates:
        target = store / c
        if target.is_file():
            return target

    # Case-insensitive search across directory
    name_lower = name.lower()
    for f in store.iterdir():
        if f.is_file() and name_lower in f.name.lower():
            if quant is None or quant.lower() in f.name.lower():
                return f
    return None


def get_model_status(
    entry: ModelEntry,
    models_dir: str | Path | None = None,
) -> ModelStatus:
    """Inspect model presence, partial state, and RAM compatibility."""
    store = get_models_dir(models_dir)
    final_path = store / entry.canonical_filename
    part_path = store / f"{entry.canonical_filename}.part"

    sys_ram = detect_ram()
    has_ram = sys_ram.total_gb >= entry.min_ram_gb

    if final_path.exists():
        downloaded = final_path.stat().st_size
        return ModelStatus(
            entry=entry,
            status="ready",
            local_path=final_path,
            downloaded_bytes=downloaded,
            total_bytes=entry.size or downloaded,
            has_sufficient_ram=has_ram,
            system_ram_gb=sys_ram.total_gb,
        )

    if part_path.exists():
        downloaded = part_path.stat().st_size
        return ModelStatus(
            entry=entry,
            status="partial",
            local_path=part_path,
            downloaded_bytes=downloaded,
            total_bytes=entry.size,
            has_sufficient_ram=has_ram,
            system_ram_gb=sys_ram.total_gb,
        )

    return ModelStatus(
        entry=entry,
        status="missing",
        local_path=None,
        downloaded_bytes=0,
        total_bytes=entry.size,
        has_sufficient_ram=has_ram,
        system_ram_gb=sys_ram.total_gb,
    )


def list_models_status(
    manifest_path: str | Path | None = None,
    models_dir: str | Path | None = None,
) -> list[ModelStatus]:
    """Inspect status of all models listed in manifest."""
    manifest = read_manifest(manifest_path)
    return [get_model_status(entry, models_dir=models_dir) for entry in manifest]

