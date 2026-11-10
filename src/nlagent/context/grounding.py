"""
src/nlagent/context/grounding.py - Bounded Read-Only Context Grounding Engine.

Responsibilities:
1. Bounded File Search: searches by name, extension, and modified-time range.
2. Strict Confinement: strictly restricted to approved folders listed in config or UserProfile.
3. Hard Resource Caps: bounds recursion depth, visited entry count, execution timeout,
   and returns at most K candidates.
4. Read-Only Safety: zero side effects, skips external symlinks, gracefully handles permissions.
5. Prompt Formatting: leverages sanitize.py to format safe, delimited FILES blocks.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from nlagent.context.profile import UserProfile
from nlagent.context.sanitize import format_files_block

logger = logging.getLogger("nlagent.context.grounding")


@dataclass(frozen=True)
class GroundedFile:
    """Represents a verified, existing local filesystem entry discovered during grounding."""
    path: Path
    rel_path: str
    name: str
    size_bytes: int
    mtime: float
    is_dir: bool = False

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def modified_iso(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.mtime))

    @property
    def size_human(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "rel_path": self.rel_path,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "modified_iso": self.modified_iso,
            "is_dir": self.is_dir,
            "extension": self.extension,
        }


DEFAULT_EXCLUDED_PATTERNS = [
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".eggs",
    "*.egg-info",
]


@dataclass
class GroundingConfig:
    """Configuration and guard limits for bounded filesystem grounding."""
    allowed_folders: list[Path] = field(default_factory=list)
    max_candidates: int = 10
    max_depth: int = 3
    max_visited_entries: int = 1000
    timeout_seconds: float = 0.5
    excluded_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_PATTERNS))


def is_path_under_root(path: Path, root: Path) -> bool:
    """Check if path is located strictly inside root directory."""
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
        return resolved_path == resolved_root or resolved_root in resolved_path.parents
    except Exception:
        return False


def _is_excluded(name: str, excluded_patterns: list[str]) -> bool:
    """Check if directory or filename matches any exclusion pattern."""
    for pattern in excluded_patterns:
        if pattern.startswith("*."):
            if name.endswith(pattern[1:]):
                return True
        elif name.lower() == pattern.lower():
            return True
    return False


def search_files(
    query: str | None = None,
    extensions: Sequence[str] | None = None,
    min_mtime: float | None = None,
    max_mtime: float | None = None,
    modified_within_days: float | None = None,
    allowed_folders: Sequence[str | Path] | None = None,
    profile: UserProfile | None = None,
    cwd: str | Path | None = None,
    config: GroundingConfig | None = None,
    k: int = 10,
) -> list[GroundedFile]:
    """
    Execute a bounded read-only search across allowed filesystem directories.

    Guarantees:
    - Never scans paths outside configured allowed_folders.
    - Caps recursion depth and visited entries.
    - Limits runtime to timeout_seconds.
    - Returns at most k candidates ranked by relevance and modification time.
    """
    start_time = time.time()
    cfg = config or GroundingConfig()

    # 1. Determine candidate search roots
    candidate_roots: list[Path] = []

    # From explicit parameter
    if allowed_folders:
        for f in allowed_folders:
            p = Path(f).expanduser().resolve()
            if p.is_dir() and p not in candidate_roots:
                candidate_roots.append(p)

    # From profile
    if profile:
        profile_roots = profile.get_allowed_roots(current_cwd=cwd)
        for p in profile_roots:
            if p.is_dir() and p not in candidate_roots:
                candidate_roots.append(p)

    # From config allowed_folders
    if cfg.allowed_folders:
        for f in cfg.allowed_folders:
            p = Path(f).expanduser().resolve()
            if p.is_dir() and p not in candidate_roots:
                candidate_roots.append(p)

    # Fallback to cwd if no roots configured
    if not candidate_roots and cwd:
        p = Path(cwd).resolve()
        if p.is_dir():
            candidate_roots.append(p)

    # Enforce strict policy confinement: if cfg.allowed_folders is configured,
    # all search roots MUST reside strictly under one of cfg.allowed_folders.
    approved_roots: list[Path] = []
    if cfg.allowed_folders:
        policy_allowed = [Path(f).expanduser().resolve() for f in cfg.allowed_folders]
        for cand in candidate_roots:
            if any(is_path_under_root(cand, pol) for pol in policy_allowed):
                if cand not in approved_roots:
                    approved_roots.append(cand)
    else:
        approved_roots = candidate_roots

    if not approved_roots:
        return []

    # 2. Setup query and filter criteria
    norm_query = query.strip().lower() if query else ""
    norm_extensions = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions] if extensions else None

    # Alias expansion check (if under allowed policy)
    if profile and norm_query:
        alias_target = profile.resolve_alias(norm_query)
        if alias_target and alias_target.is_dir():
            if not cfg.allowed_folders or any(is_path_under_root(alias_target, pol) for pol in [Path(f).expanduser().resolve() for f in cfg.allowed_folders]):
                if alias_target not in approved_roots:
                    approved_roots.insert(0, alias_target)

    # Time bounds
    now = time.time()
    if modified_within_days is not None and modified_within_days > 0:
        min_mtime = max(min_mtime or 0.0, now - (modified_within_days * 86400.0))

    candidates: list[tuple[int, float, GroundedFile]] = []
    visited_count = 0
    max_k = min(k, cfg.max_candidates)

    # 3. Traverse allowed roots with strict bounds
    for root in approved_roots:
        if visited_count >= cfg.max_visited_entries or (time.time() - start_time) > cfg.timeout_seconds:
            break

        queue: list[tuple[Path, int]] = [(root, 0)]

        while queue:
            if visited_count >= cfg.max_visited_entries or (time.time() - start_time) > cfg.timeout_seconds:
                break

            current_dir, depth = queue.pop(0)

            try:
                with os.scandir(str(current_dir)) as it:
                    for entry in it:
                        visited_count += 1
                        if visited_count >= cfg.max_visited_entries or (time.time() - start_time) > cfg.timeout_seconds:
                            break

                        entry_name = entry.name
                        if _is_excluded(entry_name, cfg.excluded_patterns):
                            continue

                        entry_path = Path(entry.path)

                        # Symlink safety check: must remain within root
                        if entry.is_symlink():
                            try:
                                target = entry_path.resolve()
                                if not is_path_under_root(target, root):
                                    continue
                            except Exception:
                                continue

                        is_dir = entry.is_dir(follow_symlinks=False)

                        if is_dir:
                            if depth + 1 <= cfg.max_depth:
                                queue.append((entry_path, depth + 1))
                            # Directories can also be grounded if query matches
                            if not norm_query or norm_query in entry_name.lower():
                                score = 2 if norm_query and entry_name.lower() == norm_query else 1 if norm_query else 0
                                try:
                                    stat = entry.stat(follow_symlinks=False)
                                    rel_p = str(entry_path.relative_to(root)).replace("\\", "/")
                                    gf = GroundedFile(
                                        path=entry_path,
                                        rel_path=rel_p,
                                        name=entry_name,
                                        size_bytes=0,
                                        mtime=stat.st_mtime,
                                        is_dir=True,
                                    )
                                    candidates.append((score, stat.st_mtime, gf))
                                except Exception:
                                    pass
                            continue

                        # Regular file evaluation
                        ext = entry_path.suffix.lower()
                        if norm_extensions and ext not in norm_extensions:
                            continue

                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except Exception:
                            continue

                        mtime = stat.st_mtime
                        if min_mtime is not None and mtime < min_mtime:
                            continue
                        if max_mtime is not None and mtime > max_mtime:
                            continue

                        # Query match scoring
                        score = 0
                        if norm_query:
                            low_name = entry_name.lower()
                            if low_name == norm_query:
                                score = 10
                            elif low_name.startswith(norm_query):
                                score = 7
                            elif norm_query in low_name:
                                score = 5
                            elif any(tok in low_name for tok in re.split(r"[ _\-.]+", norm_query) if len(tok) >= 3):
                                score = 3
                            else:
                                # Did not match query
                                continue

                        rel_p = str(entry_path.relative_to(root)).replace("\\", "/")
                        gf = GroundedFile(
                            path=entry_path,
                            rel_path=rel_p,
                            name=entry_name,
                            size_bytes=stat.st_size,
                            mtime=mtime,
                            is_dir=False,
                        )
                        candidates.append((score, mtime, gf))

            except (PermissionError, OSError, FileNotFoundError):
                continue

    # Sort candidates by relevance score descending, then mtime descending
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # Deduplicate by absolute path
    seen_paths: set[str] = set()
    deduped: list[GroundedFile] = []
    for _, _, gf in candidates:
        sp = str(gf.path)
        if sp not in seen_paths:
            seen_paths.add(sp)
            deduped.append(gf)
            if len(deduped) >= max_k:
                break

    return deduped


def ground_request(
    request: str,
    cwd: str | Path,
    profile: UserProfile | None = None,
    k: int = 5,
    config: GroundingConfig | None = None,
) -> str:
    """
    Inspect user natural language request, discover candidate matching files,
    and format them into a sanitized FILES context block.
    """
    clean_req = request.lower()

    # Common semantic type hints in natural language requests
    ext_hints: list[str] = []
    if "python" in clean_req or ".py" in clean_req:
        ext_hints.append(".py")
    if "shell" in clean_req or "bash" in clean_req or ".sh" in clean_req:
        ext_hints.extend([".sh", ".bash"])
    if "powershell" in clean_req or ".ps1" in clean_req:
        ext_hints.append(".ps1")
    if "pdf" in clean_req:
        ext_hints.append(".pdf")
    if "json" in clean_req:
        ext_hints.append(".json")
    if "text" in clean_req or "txt" in clean_req:
        ext_hints.extend([".txt", ".md"])
    if "csv" in clean_req:
        ext_hints.append(".csv")

    # Extract word tokens from request
    tokens = [t.strip("\"'()[]") for t in request.split() if len(t.strip("\"'()[]")) >= 3]

    # 1. Look for explicit path/filename in request
    explicit_file = None
    for tok in tokens:
        if "." in tok or "/" in tok or "\\" in tok:
            explicit_file = Path(tok).name
            break

    files: list[GroundedFile] = []

    if explicit_file:
        files = search_files(
            query=explicit_file,
            allowed_folders=[cwd],
            profile=profile,
            cwd=cwd,
            config=config,
            k=k,
        )

    # 2. If no explicit file match, try semantic extension hints
    if not files and ext_hints:
        files = search_files(
            extensions=ext_hints,
            allowed_folders=[cwd],
            profile=profile,
            cwd=cwd,
            config=config,
            k=k,
        )

    # 3. If still empty, search for content word tokens
    if not files:
        stopwords = {"run", "the", "file", "files", "all", "show", "list", "and", "for", "from", "with", "script"}
        for tok in tokens:
            if tok.lower() in stopwords:
                continue
            files = search_files(
                query=tok,
                allowed_folders=[cwd],
                profile=profile,
                cwd=cwd,
                config=config,
                k=k,
            )
            if files:
                break

    # 4. If still empty, return top k recent files in cwd
    if not files:
        files = search_files(
            allowed_folders=[cwd],
            profile=profile,
            cwd=cwd,
            config=config,
            k=k,
        )

    if not files:
        return ""

    return format_files_block(files, max_files=k, rel_to=cwd)
