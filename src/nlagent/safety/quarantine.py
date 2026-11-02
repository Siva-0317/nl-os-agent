"""
quarantine.py - Pre-Execution Quarantine and Rollback Engine.

Before executing any destructive or state-modifying command:
1. Hardlinks (or copies cross-device) every file or directory scheduled to be
   deleted or overwritten into ~/.nl-agent/quarantine/<request_id>/ with a manifest.
2. If predicted payload exceeds 500 MB, requires explicit confirmation as undo is unavailable.
3. Implements undo(request_id) to restore byte-identical files, directories, and permissions.
4. Implements purge(older_than) to manage disk storage.
5. Handles name clashes and partial failure recovery during restore operations.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from nlagent.safety.effects import Effect

# Default size threshold: 500 MB
MAX_QUARANTINE_BYTES = 500 * 1024 * 1024


# -----------------------------------------------------------------------------
# Custom Exceptions
# -----------------------------------------------------------------------------

class QuarantineError(Exception):
    """Base exception for quarantine and undo operations."""
    pass


class QuarantineLimitExceededError(QuarantineError):
    """Raised when quarantine payload exceeds 500 MB without explicit confirmation."""
    pass


class NameClashError(QuarantineError):
    """Raised when undo encounters an existing file at the original path."""
    pass


class QuarantineNotFoundError(QuarantineError):
    """Raised when requested quarantine ID cannot be found."""
    pass


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class QuarantinedItem:
    """Metadata for a single quarantined file or directory."""

    original_path: str
    quarantined_rel_path: str
    item_type: str  # "file" | "dir" | "symlink"
    size_bytes: int
    sha1: str
    mode: int
    action: str  # "delete" | "overwrite"
    symlink_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuarantinedItem":
        return cls(**data)


@dataclass
class QuarantineManifest:
    """Complete restore manifest for a single command execution turn."""

    request_id: str
    timestamp: str
    command: str
    total_bytes: int
    items: list[QuarantinedItem]
    quarantine_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "command": self.command,
            "total_bytes": self.total_bytes,
            "quarantine_dir": self.quarantine_dir,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuarantineManifest":
        items = [QuarantinedItem.from_dict(it) for it in data.get("items", [])]
        return cls(
            request_id=data["request_id"],
            timestamp=data["timestamp"],
            command=data["command"],
            total_bytes=data["total_bytes"],
            items=items,
            quarantine_dir=data["quarantine_dir"],
        )


@dataclass
class UndoResult:
    """Result of an undo rollback execution."""

    request_id: str
    success: bool
    restored_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    clashes: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Path & Hashing Helpers
# -----------------------------------------------------------------------------

def get_quarantine_root(override_dir: str | Path | None = None) -> Path:
    """Determine root directory for quarantine storage."""
    if override_dir:
        root = Path(override_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    if os.environ.get("NLAGENT_QUARANTINE_DIR"):
        root = Path(os.environ["NLAGENT_QUARANTINE_DIR"])
        root.mkdir(parents=True, exist_ok=True)
        return root

    # Try user home directory (~/.nl-agent/quarantine)
    home_candidate = Path.home() / ".nl-agent" / "quarantine"
    try:
        home_candidate.mkdir(parents=True, exist_ok=True)
        return home_candidate
    except (PermissionError, OSError):
        # Fall back to system temp directory when home directory is protected or sandboxed
        temp_candidate = Path(tempfile.gettempdir()) / ".nl-agent" / "quarantine"
        temp_candidate.mkdir(parents=True, exist_ok=True)
        return temp_candidate


def calculate_sha1(path: str | Path) -> str:
    """Calculate SHA-1 hash of a file without leaking open handles."""
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        return ""
    try:
        h = hashlib.sha1()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def get_path_size(path: str | Path) -> int:
    """Compute total size of a path (recursing if directory)."""
    p = Path(path)
    if not p.exists() and not p.is_symlink():
        return 0
    if p.is_file() or p.is_symlink():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for root, _, files in os.walk(p):
            for f in files:
                fp = Path(root) / f
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


# -----------------------------------------------------------------------------
# Quarantine Engine
# -----------------------------------------------------------------------------

def quarantine_effect(
    effect: Effect,
    request_id: str,
    command: str = "",
    base_dir: str | Path | None = None,
    confirmed_large: bool = False,
) -> QuarantineManifest | None:
    """
    Shadow all files or directories affected by the proposed command into quarantine.

    Raises:
        QuarantineLimitExceededError: If affected payload exceeds 500 MB without confirmation.
    """
    # 1. Identify distinct targets to preserve
    targets_to_shadow: list[tuple[str, str]] = []
    seen_paths: set[str] = set()

    for p in effect.deletes:
        norm = os.path.normpath(os.path.abspath(p))
        if norm not in seen_paths and os.path.lexists(norm):
            seen_paths.add(norm)
            targets_to_shadow.append((norm, "delete"))

    for p in effect.overwrites:
        norm = os.path.normpath(os.path.abspath(p))
        if norm not in seen_paths and os.path.lexists(norm):
            seen_paths.add(norm)
            targets_to_shadow.append((norm, "overwrite"))

    if not targets_to_shadow:
        return None

    # 2. Check total size threshold
    total_bytes = sum(get_path_size(p) for p, _ in targets_to_shadow)
    if total_bytes > MAX_QUARANTINE_BYTES and not confirmed_large:
        mb = total_bytes / (1024 * 1024)
        raise QuarantineLimitExceededError(
            f"Quarantine size ({mb:.1f} MB) exceeds safety threshold of 500 MB. "
            f"Undo is unavailable for this operation without explicit confirmation."
        )

    # 3. Create quarantine container
    q_root = get_quarantine_root(base_dir)
    request_dir = q_root / request_id
    data_dir = request_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    items: list[QuarantinedItem] = []

    # 4. Shadow each item (hardlink if possible, copy fallback)
    for idx, (orig_path, action) in enumerate(targets_to_shadow):
        p_obj = Path(orig_path)
        rel_target_name = f"{idx:04d}_{p_obj.name}"
        shadow_dest = data_dir / rel_target_name

        stat_info = p_obj.lstat()
        mode = stat_info.st_mode
        size = get_path_size(p_obj)
        sha1 = ""
        symlink_target = None

        if p_obj.is_symlink():
            item_type = "symlink"
            symlink_target = os.readlink(p_obj)
            try:
                os.symlink(symlink_target, shadow_dest, target_is_directory=p_obj.is_dir())
            except OSError:
                pass
        elif p_obj.is_dir():
            item_type = "dir"
            shutil.copytree(p_obj, shadow_dest, symlinks=True)
        else:
            item_type = "file"
            sha1 = calculate_sha1(p_obj)
            # Try hardlink first (instantaneous, 0 duplicate storage)
            linked = False
            try:
                os.link(p_obj, shadow_dest)
                linked = True
            except OSError:
                linked = False

            if not linked:
                shutil.copy2(p_obj, shadow_dest)

        items.append(
            QuarantinedItem(
                original_path=orig_path,
                quarantined_rel_path=str(Path("data") / rel_target_name).replace("\\", "/"),
                item_type=item_type,
                size_bytes=size,
                sha1=sha1,
                mode=mode,
                action=action,
                symlink_target=symlink_target,
            )
        )

    # 5. Write quarantine manifest
    manifest = QuarantineManifest(
        request_id=request_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        command=command,
        total_bytes=total_bytes,
        items=items,
        quarantine_dir=str(request_dir).replace("\\", "/"),
    )

    manifest_path = request_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2)

    return manifest


# -----------------------------------------------------------------------------
# Undo / Rollback Engine
# -----------------------------------------------------------------------------

def undo(
    request_id: str,
    base_dir: str | Path | None = None,
    on_clash: str = "error",
) -> UndoResult:
    """
    Restore files and directories from a previously recorded quarantine manifest.

    Parameters:
        request_id: Unique execution identifier.
        base_dir: Optional custom quarantine root (for testing isolation).
        on_clash: Clash resolution strategy:
                  - "error": abort and raise NameClashError if target exists.
                  - "backup": rename existing target to .conflict.<ts> and restore.
                  - "overwrite": replace existing target with quarantined version.

    Returns:
        UndoResult describing restored paths, failed paths, and clashes.
    """
    q_root = get_quarantine_root(base_dir)
    request_dir = q_root / request_id
    manifest_path = request_dir / "manifest.json"

    if not manifest_path.is_file():
        raise QuarantineNotFoundError(f"Quarantine record '{request_id}' not found in {q_root}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest = QuarantineManifest.from_dict(data)

    restored_paths: list[str] = []
    failed_paths: list[str] = []
    clashes: list[str] = []

    # 1. Pre-check for name clashes if on_clash == "error"
    if on_clash == "error":
        for item in manifest.items:
            orig = Path(item.original_path)
            if orig.exists() or orig.is_symlink():
                clashes.append(item.original_path)

        if clashes:
            raise NameClashError(
                f"Cannot undo '{request_id}': Target path(s) already exist: {clashes}. "
                f"Use on_clash='backup' or on_clash='overwrite' to resolve."
            )

    # 2. Restore each item
    for item in manifest.items:
        orig = Path(item.original_path)
        quarantined_path = request_dir / Path(item.quarantined_rel_path)

        if not quarantined_path.exists() and not quarantined_path.is_symlink():
            failed_paths.append(item.original_path)
            continue

        try:
            # Handle clash resolution if target currently exists
            if orig.exists() or orig.is_symlink():
                if on_clash == "backup":
                    ts_tag = int(time.time() * 1000)
                    clash_backup = orig.with_name(f"{orig.name}.conflict.{ts_tag}")
                    orig.rename(clash_backup)
                    clashes.append(str(clash_backup))
                elif on_clash == "overwrite":
                    if orig.is_dir() and not orig.is_symlink():
                        shutil.rmtree(orig, ignore_errors=True)
                    else:
                        orig.unlink(missing_ok=True)

            # Ensure parent directories exist
            orig.parent.mkdir(parents=True, exist_ok=True)

            # Restore item
            if item.item_type == "dir":
                if orig.exists():
                    shutil.rmtree(orig, ignore_errors=True)
                shutil.copytree(quarantined_path, orig, symlinks=True)
            elif item.item_type == "symlink" and item.symlink_target:
                if orig.exists() or orig.is_symlink():
                    orig.unlink(missing_ok=True)
                os.symlink(item.symlink_target, orig)
            else:
                shutil.copy2(quarantined_path, orig)

            # Restore file permissions
            try:
                os.chmod(orig, item.mode)
            except OSError:
                pass

            restored_paths.append(item.original_path)

        except Exception:
            failed_paths.append(item.original_path)

    success = len(failed_paths) == 0 and len(restored_paths) > 0
    msg = (
        f"Undo completed: {len(restored_paths)} item(s) restored."
        if success
        else f"Undo completed with partial failures: {len(restored_paths)} restored, {len(failed_paths)} failed."
    )

    return UndoResult(
        request_id=request_id,
        success=success,
        restored_paths=restored_paths,
        failed_paths=failed_paths,
        clashes=clashes,
        message=msg,
    )


# -----------------------------------------------------------------------------
# Retention & Purge Engine
# -----------------------------------------------------------------------------

def purge(
    older_than_seconds: float | None = None,
    older_than_days: float | None = None,
    base_dir: str | Path | None = None,
) -> int:
    """
    Remove quarantine records older than the specified retention window.

    Returns:
        Number of purged quarantine records.
    """
    if older_than_seconds is not None:
        seconds = float(older_than_seconds)
    elif older_than_days is not None:
        seconds = float(older_than_days) * 86400.0
    else:
        seconds = 7.0 * 86400.0
    now = time.time()
    cutoff = now - seconds

    q_root = get_quarantine_root(base_dir)
    purged_count = 0

    if not q_root.exists():
        return 0

    for entry in os.scandir(q_root):
        if entry.is_dir():
            mtime = entry.stat().st_mtime
            manifest_file = Path(entry.path) / "manifest.json"
            if manifest_file.is_file():
                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    ts_str = meta.get("timestamp")
                    if ts_str:
                        dt = datetime.fromisoformat(ts_str)
                        mtime = dt.timestamp()
                except Exception:
                    pass

            if mtime < cutoff:
                try:
                    shutil.rmtree(entry.path, ignore_errors=True)
                    purged_count += 1
                except OSError:
                    pass

    return purged_count
