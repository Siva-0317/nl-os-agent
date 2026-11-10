"""
src/nlagent/safety/overlay.py - In-Memory Copy-on-Write Virtual Filesystem Overlay.

Maintains a metadata-only copy-on-write (CoW) overlay over the physical filesystem
for multi-step command simulation:
- Supports lazy base-disk reads without mutating any physical file.
- Tracks file creations, overwrites, appends, deletions (whiteouts), permission changes,
  and opaque replaced directories (OverlayFS semantics).
- Tracks virtual working directory (cwd), directory stack (pushd/popd), and
  environment variables across steps.
- Resolves wildcards (glob), directory trees (walk/listdir), and static Effects
  for step k conditioned on the cumulative overlay state of steps 1..k-1.
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Iterator

from nlagent.safety.cmd_parser import CmdInvocation, parse_cmd_line
from nlagent.safety.effects import (
    PROTECTED,
    REGISTRY_PROTECTED,
    Effect,
    _is_protected_registry,
    _is_registry_path,
)
from nlagent.safety.ps_parser import ParsedPSCommand, PSParseResult, parse_powershell


def _norm_key(path: str) -> str:
    """Return canonical dict lookup key for a normalized absolute path."""
    norm = os.path.normpath(os.path.abspath(path))
    if os.name == "nt":
        return norm.lower()
    return norm


def _synthetic_sha1(seed: str) -> str:
    """Compute a deterministic synthetic SHA-1 digest for virtual mutations."""
    return hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class VirtualNode:
    """Metadata node stored inside the VirtualFS overlay."""

    path: str  # Preserved display path (absolute, normalized)
    node_type: str  # "file" | "dir" | "symlink"
    size: int = 0
    sha1: str = ""
    mode: int = 0o666 if os.name == "nt" else 0o644
    symlink_target: str | None = None
    mutation_counter: int = 0

    def clone(self) -> "VirtualNode":
        return VirtualNode(
            path=self.path,
            node_type=self.node_type,
            size=self.size,
            sha1=self.sha1,
            mode=self.mode,
            symlink_target=self.symlink_target,
            mutation_counter=self.mutation_counter,
        )


class VirtualFS:
    """
    In-memory copy-on-write virtual filesystem overlay backed by physical disk.

    Never performs destructive or mutating operations on the physical disk.
    """

    def __init__(self, base_cwd: str = "", env: dict[str, str] | None = None) -> None:
        resolved_cwd = os.path.normpath(os.path.abspath(base_cwd or os.getcwd()))
        self.initial_cwd: str = resolved_cwd
        self.cwd: str = resolved_cwd
        self.dir_stack: list[str] = []
        self.env: dict[str, str] = dict(env) if env is not None else dict(os.environ)

        # Overlay state indexed by _norm_key(abs_path)
        self._nodes: dict[str, VirtualNode] = {}
        self._whiteouts: set[str] = set()
        self._opaque_dirs: set[str] = set()
        self._mutation_seq: int = 0

    def fork(self) -> "VirtualFS":
        """Create an independent deep copy of this VirtualFS state."""
        cloned = VirtualFS.__new__(VirtualFS)
        cloned.initial_cwd = self.initial_cwd
        cloned.cwd = self.cwd
        cloned.dir_stack = list(self.dir_stack)
        cloned.env = dict(self.env)
        cloned._nodes = {k: v.clone() for k, v in self._nodes.items()}
        cloned._whiteouts = set(self._whiteouts)
        cloned._opaque_dirs = set(self._opaque_dirs)
        cloned._mutation_seq = self._mutation_seq
        return cloned

    def reset(self) -> None:
        """Reset all overlay mutations, restoring base physical filesystem view."""
        self.cwd = self.initial_cwd
        self.dir_stack.clear()
        self._nodes.clear()
        self._whiteouts.clear()
        self._opaque_dirs.clear()
        self._mutation_seq = 0

    # ------------------------------------------------------------------
    # Path & Variable Resolution
    # ------------------------------------------------------------------

    def expand_vars(self, text: str) -> str:
        """Substitute $VAR, ${VAR}, $env:VAR, and %VAR% using virtual environment."""
        if not text:
            return text

        out = text
        # PowerShell $env:VAR
        out = re.sub(
            r"\$env:([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: self.env.get(m.group(1), m.group(0)),
            out,
            flags=re.IGNORECASE,
        )
        # cmd %VAR%
        out = re.sub(
            r"%([A-Za-z_][A-Za-z0-9_]*)%",
            lambda m: self.env.get(m.group(1), m.group(0)),
            out,
        )
        # POSIX ${VAR}
        out = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda m: self.env.get(m.group(1), m.group(0)),
            out,
        )
        # POSIX $VAR (only match keys present in self.env to avoid clobbering PS vars unnecessarily)
        def _repl_dollar(m: re.Match[str]) -> str:
            var_name = m.group(1)
            return self.env.get(var_name, m.group(0))

        out = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", _repl_dollar, out)
        return out

    def resolve_path(self, raw_path: str, cwd: str | None = None) -> str:
        """Resolve a possibly relative path (with var expansion) to normalized abs path."""
        expanded = self.expand_vars(raw_path.strip().strip("'\""))
        if expanded == "~" or expanded.startswith("~/") or expanded.startswith("~\\"):
            expanded = os.path.expanduser(expanded)
        base = cwd or self.cwd
        joined = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
        return os.path.normpath(os.path.abspath(joined))

    def _ancestors(self, abs_path: str) -> list[str]:
        """Return list of strict ancestor paths from immediate parent up to root."""
        ancestors: list[str] = []
        curr = os.path.dirname(abs_path)
        while curr and curr != abs_path:
            ancestors.append(curr)
            nxt = os.path.dirname(curr)
            if nxt == curr:
                break
            curr = nxt
        return ancestors

    def _is_hidden_by_ancestor(self, abs_path: str) -> tuple[bool, bool]:
        """
        Return (hidden_by_whiteout, blocked_from_base_by_opaque).
        - hidden_by_whiteout: True if any strict ancestor is currently deleted.
        - blocked_from_base_by_opaque: True if any strict ancestor is opaque (replaces deleted dir).
        """
        hidden_whiteout = False
        blocked_base = False
        for anc in self._ancestors(abs_path):
            ak = _norm_key(anc)
            if ak in self._whiteouts:
                hidden_whiteout = True
                blocked_base = True
                break
            if ak in self._opaque_dirs:
                blocked_base = True
        return hidden_whiteout, blocked_base

    def _get_node(self, abs_path: str) -> VirtualNode | None:
        """
        Retrieve VirtualNode for abs_path from overlay, or synthesize from disk if untouched.
        Returns None if deleted or non-existent.
        """
        norm = os.path.normpath(os.path.abspath(abs_path))
        key = _norm_key(norm)

        hidden_whiteout, blocked_base = self._is_hidden_by_ancestor(norm)
        if hidden_whiteout:
            return None

        if key in self._whiteouts:
            return None

        if key in self._nodes:
            return self._nodes[key]

        if blocked_base:
            return None

        # Fall back to base physical disk
        if not os.path.lexists(norm):
            return None

        try:
            st = os.lstat(norm)
            if os.path.islink(norm):
                return VirtualNode(
                    path=norm,
                    node_type="symlink",
                    size=st.st_size,
                    sha1="",
                    mode=st.st_mode & 0o777,
                    symlink_target=os.readlink(norm),
                )
            elif os.path.isdir(norm):
                return VirtualNode(
                    path=norm,
                    node_type="dir",
                    size=0,
                    sha1="",
                    mode=st.st_mode & 0o777,
                )
            else:
                h = ""
                try:
                    with open(norm, "rb") as f:
                        h = hashlib.sha1(f.read()).hexdigest()
                except OSError:
                    pass
                return VirtualNode(
                    path=norm,
                    node_type="file",
                    size=st.st_size,
                    sha1=h,
                    mode=st.st_mode & 0o777,
                )
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Read-Only Filesystem Queries
    # ------------------------------------------------------------------

    def exists(self, path: str, cwd: str | None = None) -> bool:
        norm = self.resolve_path(path, cwd=cwd)
        return self._get_node(norm) is not None

    def lexists(self, path: str, cwd: str | None = None) -> bool:
        return self.exists(path, cwd=cwd)

    def isdir(self, path: str, cwd: str | None = None) -> bool:
        node = self._get_node(self.resolve_path(path, cwd=cwd))
        return node is not None and node.node_type == "dir"

    def isfile(self, path: str, cwd: str | None = None) -> bool:
        node = self._get_node(self.resolve_path(path, cwd=cwd))
        return node is not None and node.node_type == "file"

    def islink(self, path: str, cwd: str | None = None) -> bool:
        node = self._get_node(self.resolve_path(path, cwd=cwd))
        return node is not None and node.node_type == "symlink"

    def getsize(self, path: str, cwd: str | None = None) -> int:
        node = self._get_node(self.resolve_path(path, cwd=cwd))
        if node is None or node.node_type != "file":
            return 0
        return node.size

    def listdir(self, path: str, cwd: str | None = None) -> list[str]:
        """List visible child entry names in a directory."""
        norm = self.resolve_path(path, cwd=cwd)
        node = self._get_node(norm)
        if node is None or node.node_type != "dir":
            return []

        key = _norm_key(norm)
        _, blocked_base = self._is_hidden_by_ancestor(norm)
        is_opaque = blocked_base or (key in self._opaque_dirs)

        entries: dict[str, str] = {}  # lower_name_on_nt -> exact_name

        # 1. Base disk entries unless opaque
        if not is_opaque and os.path.isdir(norm):
            try:
                for name in os.listdir(norm):
                    child_abs = os.path.join(norm, name)
                    child_key = _norm_key(child_abs)
                    if child_key not in self._whiteouts:
                        name_key = name.lower() if os.name == "nt" else name
                        entries[name_key] = name
            except OSError:
                pass

        # 2. Overlay added/updated direct children
        prefix = key.rstrip("\\/") + os.sep
        for nk, nnode in self._nodes.items():
            if nk.startswith(prefix):
                rest = nk[len(prefix):]
                if os.sep not in rest and "/" not in rest:
                    basename = os.path.basename(nnode.path)
                    name_key = basename.lower() if os.name == "nt" else basename
                    entries[name_key] = basename

        return sorted(entries.values())

    def walk(self, top: str, cwd: str | None = None) -> Iterator[tuple[str, list[str], list[str]]]:
        """Virtual os.walk yielding (dirpath, dirnames, filenames)."""
        norm_top = self.resolve_path(top, cwd=cwd)
        node = self._get_node(norm_top)
        if node is None or node.node_type != "dir":
            return

        children = self.listdir(norm_top)
        dirnames: list[str] = []
        filenames: list[str] = []
        for c in children:
            child_path = os.path.join(norm_top, c)
            if self.isdir(child_path):
                dirnames.append(c)
            else:
                filenames.append(c)

        yield norm_top, dirnames, filenames
        for d in dirnames:
            yield from self.walk(os.path.join(norm_top, d))

    def walk_paths(self, path: str, cwd: str | None = None) -> Iterator[str]:
        """Recursively yield all child paths plus path itself (matches effects._walk)."""
        norm = self.resolve_path(path, cwd=cwd)
        if self.isdir(norm) and not self.islink(norm):
            for root, dirs, files in self.walk(norm):
                for n in files + dirs:
                    yield os.path.join(root, n)
        yield norm

    def glob(self, pattern: str, cwd: str | None = None) -> list[str]:
        """Resolve wildcard patterns across the virtual filesystem overlay."""
        expanded = self.expand_vars(pattern.strip().strip("'\""))
        if not expanded:
            return []

        base = cwd or self.cwd
        abs_pat = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
        abs_pat = os.path.normpath(abs_pat)

        if not any(ch in abs_pat for ch in ("*", "?", "[")):
            return [abs_pat] if self.exists(abs_pat) else []

        # Split into drive/root and components
        drive, tail = os.path.splitdrive(abs_pat)
        parts = [p for p in tail.replace("\\", "/").split("/") if p]
        root = (drive + os.sep) if drive else os.sep

        current_matches = [root]
        for part in parts:
            next_matches: list[str] = []
            has_glob = any(ch in part for ch in ("*", "?", "["))
            for cur in current_matches:
                if not self.isdir(cur):
                    continue
                if has_glob:
                    for entry in self.listdir(cur):
                        match_ok = (
                            fnmatch.fnmatch(entry.lower(), part.lower())
                            if os.name == "nt"
                            else fnmatch.fnmatchcase(entry, part)
                        )
                        if match_ok:
                            next_matches.append(os.path.join(cur, entry))
                else:
                    candidate = os.path.join(cur, part)
                    if self.exists(candidate):
                        next_matches.append(candidate)
            current_matches = next_matches

        return sorted(current_matches)

    def expand_arg(self, arg: str, cwd: str | None = None) -> list[str]:
        """Expand argument (wildcards or literal path) against VirtualFS."""
        if not arg:
            return []
        norm = self.resolve_path(arg, cwd=cwd)
        if any(c in arg for c in "*?["):
            return self.glob(arg, cwd=cwd)
        return [norm]

    # ------------------------------------------------------------------
    # Overlay Write / Mutate Primitives
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        self._mutation_seq += 1
        return self._mutation_seq

    def _ensure_parents(self, abs_path: str) -> None:
        """Ensure all strict parent directories exist in the overlay."""
        parent = os.path.dirname(abs_path)
        if not parent or parent == abs_path:
            return
        if not self.isdir(parent):
            self.mkdir(parent, parents=True)

    def mkdir(self, path: str, parents: bool = True, cwd: str | None = None) -> None:
        norm = self.resolve_path(path, cwd=cwd)
        if parents:
            parent = os.path.dirname(norm)
            if parent and parent != norm and not self.isdir(parent):
                self.mkdir(parent, parents=True)

        key = _norm_key(norm)
        was_whiteout = key in self._whiteouts
        _, blocked_base = self._is_hidden_by_ancestor(norm)

        self._whiteouts.discard(key)
        if was_whiteout or blocked_base:
            self._opaque_dirs.add(key)

        existing = self._get_node(norm)
        if existing and existing.node_type == "dir":
            return

        dir_mode = 0o777 if os.name == "nt" else 0o755
        self._nodes[key] = VirtualNode(
            path=norm,
            node_type="dir",
            size=0,
            sha1="",
            mode=dir_mode,
            mutation_counter=self._next_seq(),
        )

    def write_file(
        self,
        path: str,
        content: bytes | str | None = None,
        size: int | None = None,
        sha1: str | None = None,
        mode: int | None = None,
        cwd: str | None = None,
    ) -> None:
        """Create or overwrite a file in the overlay."""
        norm = self.resolve_path(path, cwd=cwd)
        self._ensure_parents(norm)

        key = _norm_key(norm)
        self._whiteouts.discard(key)
        self._opaque_dirs.discard(key)

        existing = self._get_node(norm)
        seq = self._next_seq()

        if content is not None:
            raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
            file_size = len(raw_bytes)
            file_sha1 = hashlib.sha1(raw_bytes).hexdigest()
        else:
            file_size = size if size is not None else (existing.size + 1 if existing else 0)
            file_sha1 = sha1 if sha1 is not None else _synthetic_sha1(f"write:{key}:{seq}")

        file_mode = mode if mode is not None else (existing.mode if existing else (0o666 if os.name == "nt" else 0o644))
        self._nodes[key] = VirtualNode(
            path=norm,
            node_type="file",
            size=file_size,
            sha1=file_sha1,
            mode=file_mode,
            mutation_counter=seq,
        )

    def touch_file(self, path: str, cwd: str | None = None) -> None:
        """Simulate touch / New-Item: creates if missing; preserves contents if exists."""
        norm = self.resolve_path(path, cwd=cwd)
        existing = self._get_node(norm)
        if existing is not None:
            return
        self.write_file(norm, content=b"")

    def append_file(
        self,
        path: str,
        added_content: bytes | str | None = None,
        added_size: int = 16,
        cwd: str | None = None,
    ) -> None:
        """Simulate appending bytes to a file (creates if missing)."""
        norm = self.resolve_path(path, cwd=cwd)
        existing = self._get_node(norm)
        self._ensure_parents(norm)

        key = _norm_key(norm)
        self._whiteouts.discard(key)
        seq = self._next_seq()

        base_size = existing.size if existing else 0
        base_sha = existing.sha1 if existing else ""
        delta = len(added_content.encode("utf-8") if isinstance(added_content, str) else added_content) if added_content is not None else added_size
        new_size = base_size + max(1, delta)
        new_sha = _synthetic_sha1(f"append:{base_sha}:{seq}:{added_content}")

        self._nodes[key] = VirtualNode(
            path=norm,
            node_type="file",
            size=new_size,
            sha1=new_sha,
            mode=existing.mode if existing else (0o666 if os.name == "nt" else 0o644),
            mutation_counter=seq,
        )

    def delete(self, path: str, recursive: bool = True, cwd: str | None = None) -> None:
        """Mark a file or directory (and its subtree if recursive) as whiteout."""
        norm = self.resolve_path(path, cwd=cwd)
        node = self._get_node(norm)
        if node is None:
            return

        key = _norm_key(norm)
        if node.node_type == "dir" and recursive:
            # Remove any existing overlay nodes strictly inside this directory
            prefix = key.rstrip("\\/") + os.sep
            to_del = [nk for nk in self._nodes if nk == key or nk.startswith(prefix)]
            for nk in to_del:
                self._nodes.pop(nk, None)
            # Clear descendant opaque/whiteouts as ancestor whiteout hides entire tree
            self._opaque_dirs = {dk for dk in self._opaque_dirs if dk != key and not dk.startswith(prefix)}
            self._whiteouts = {wk for wk in self._whiteouts if not wk.startswith(prefix)}
            self._whiteouts.add(key)
        else:
            self._nodes.pop(key, None)
            self._opaque_dirs.discard(key)
            self._whiteouts.add(key)

    def copy(self, src: str, dst: str, cwd: str | None = None) -> None:
        """Copy file or directory tree inside the virtual filesystem."""
        src_norm = self.resolve_path(src, cwd=cwd)
        dst_norm = self.resolve_path(dst, cwd=cwd)
        src_node = self._get_node(src_norm)
        if src_node is None:
            return

        final_dst = os.path.join(dst_norm, os.path.basename(src_norm)) if self.isdir(dst_norm) else dst_norm

        if src_node.node_type == "dir":
            self.mkdir(final_dst, parents=True)
            for root, dirs, files in self.walk(src_norm):
                rel = os.path.relpath(root, src_norm)
                target_root = final_dst if rel == "." else os.path.join(final_dst, rel)
                self.mkdir(target_root, parents=True)
                for d in dirs:
                    self.mkdir(os.path.join(target_root, d), parents=True)
                for f in files:
                    sf = os.path.join(root, f)
                    sfn = self._get_node(sf)
                    if sfn:
                        self.write_file(
                            os.path.join(target_root, f),
                            size=sfn.size,
                            sha1=sfn.sha1,
                            mode=sfn.mode,
                        )
        else:
            self.write_file(
                final_dst,
                size=src_node.size,
                sha1=src_node.sha1,
                mode=src_node.mode,
            )

    def move(self, src: str, dst: str, cwd: str | None = None) -> None:
        """Move/rename file or directory tree inside the virtual filesystem."""
        src_norm = self.resolve_path(src, cwd=cwd)
        if not self.exists(src_norm):
            return
        self.copy(src_norm, dst, cwd=cwd)
        self.delete(src_norm, recursive=True, cwd=cwd)

    def chmod(self, path: str, mode_str: str | int, cwd: str | None = None) -> None:
        """Modify permission mode on virtual node."""
        norm = self.resolve_path(path, cwd=cwd)
        node = self._get_node(norm)
        if node is None:
            return
        new_mode = node.mode
        if isinstance(mode_str, int):
            new_mode = mode_str & 0o777
        elif isinstance(mode_str, str) and re.fullmatch(r"[0-7]{3,4}", mode_str.strip()):
            new_mode = int(mode_str.strip(), 8) & 0o777
        cloned = node.clone()
        cloned.mode = new_mode
        cloned.mutation_counter = self._next_seq()
        self._nodes[_norm_key(norm)] = cloned

    def cd(self, path: str) -> bool:
        """Update virtual working directory if target directory exists in overlay."""
        norm = self.resolve_path(path, cwd=self.cwd)
        if self.isdir(norm):
            self.cwd = norm
            return True
        return False

    # ------------------------------------------------------------------
    # Manifest Snapshot & Diff Generation
    # ------------------------------------------------------------------

    def snapshot(self, root: str) -> dict[str, tuple[int, str, str]]:
        """
        Compute virtual directory manifest matching `nlagent.safety.manifest.manifest(root)`.
        Returns dict mapping relpath -> (size, sha1, octal_mode_str).
        """
        norm_root = self.resolve_path(root)
        m: dict[str, tuple[int, str, str]] = {}
        for dp, dn, fn in self.walk(norm_root):
            for name in fn + dn:
                p = os.path.join(dp, name)
                node = self._get_node(p)
                if node is not None:
                    rel = os.path.relpath(p, norm_root)
                    m[rel] = (node.size, node.sha1, oct(node.mode & 0o777))
        return m


# ----------------------------------------------------------------------
# Overlay-Aware Static Effect Analysis & Step Simulation
# ----------------------------------------------------------------------

def _split_compound_segments(command: str) -> list[str]:
    """Split a command string on unquoted &&, ||, ; separators."""
    segments: list[str] = []
    cur: list[str] = []
    in_squote = False
    in_dquote = False
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c == "'" and not in_dquote:
            in_squote = not in_squote
            cur.append(c)
            i += 1
        elif c == '"' and not in_squote:
            in_dquote = not in_dquote
            cur.append(c)
            i += 1
        elif not in_squote and not in_dquote:
            if c in (";", "\n"):
                seg = "".join(cur).strip()
                if seg:
                    segments.append(seg)
                cur.clear()
                i += 1
            elif command[i : i + 2] in ("&&", "||"):
                seg = "".join(cur).strip()
                if seg:
                    segments.append(seg)
                cur.clear()
                i += 2
            else:
                cur.append(c)
                i += 1
        else:
            cur.append(c)
            i += 1
    tail = "".join(cur).strip()
    if tail:
        segments.append(tail)
    return segments


def _extract_echo_payload(segment: str) -> bytes | None:
    """
    If segment is a simple `echo ... > file` or `printf ... > file` command,
    approximate/detect whether literal payload exists so overwrites produce modified state.
    Returns None if unknown.
    """
    m = re.match(r"^(?:echo|Write-Output)\s+(.*?)\s*>>?\s*[^\s]+$", segment.strip(), re.IGNORECASE)
    if m:
        body = m.group(1).strip().strip("'\"")
        return (body + "\n").encode("utf-8")
    return None


def analyse_on_overlay(
    command: str,
    vfs: VirtualFS,
    mutate: bool = True,
    enable_protected_paths: bool = True,
    enable_opaque_escalation: bool = True,
    enable_symlink_resolution: bool = True,
) -> Effect:
    """
    Analyse a command statically against the current VirtualFS overlay state
    (representing steps 1..k-1), and optionally apply the command's predicted mutations
    onto the overlay so subsequent steps k+1..n observe them.

    Physical disk is never touched.
    """
    combined = Effect()
    segments = _split_compound_segments(command)
    if not segments:
        return combined

    for seg in segments:
        step_eff = _analyse_single_segment_on_overlay(
            seg=seg,
            vfs=vfs,
            mutate=mutate,
            enable_protected_paths=enable_protected_paths,
            enable_opaque_escalation=enable_opaque_escalation,
            enable_symlink_resolution=enable_symlink_resolution,
        )
        combined.deletes.extend(step_eff.deletes)
        combined.overwrites.extend(step_eff.overwrites)
        combined.creates.extend(step_eff.creates)
        combined.appends.extend(step_eff.appends)
        combined.perm_changes.extend(step_eff.perm_changes)
        combined.signals.extend(step_eff.signals)
        combined.registry_writes.extend(step_eff.registry_writes)
        combined.external = combined.external or step_eff.external
        combined.opaque = combined.opaque or step_eff.opaque
        combined.protected_hit = combined.protected_hit or step_eff.protected_hit
        combined.hard_block = combined.hard_block or step_eff.hard_block
        combined.bytes_affected += step_eff.bytes_affected
        combined.notes.extend(step_eff.notes)

    combined.deletes = list(dict.fromkeys(combined.deletes))
    combined.overwrites = list(dict.fromkeys(combined.overwrites))
    combined.creates = list(dict.fromkeys(combined.creates))
    combined.appends = list(dict.fromkeys(combined.appends))
    combined.perm_changes = list(dict.fromkeys(combined.perm_changes))
    combined.signals = list(dict.fromkeys(combined.signals))
    combined.registry_writes = list(dict.fromkeys(combined.registry_writes))
    return combined


def _analyse_single_segment_on_overlay(
    seg: str,
    vfs: VirtualFS,
    mutate: bool,
    enable_protected_paths: bool,
    enable_opaque_escalation: bool,
    enable_symlink_resolution: bool,
) -> Effect:
    """Analyse and optionally apply a single command segment on VirtualFS."""
    e = Effect()
    clean_cmd = seg.strip()
    lower_cmd = clean_cmd.lower()

    # 0. Track environment mutations ($env:VAR = "VAL", set VAR=VAL, export VAR=VAL)
    env_ps = re.match(r'^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["\']?(.*?)["\']?$', clean_cmd, re.IGNORECASE)
    if env_ps:
        if mutate:
            vfs.env[env_ps.group(1)] = vfs.expand_vars(env_ps.group(2))
        return e

    env_sh = re.match(r'^(?:export|set)\s+([A-Za-z_][A-Za-z0-9_]*)=(.*?)$', clean_cmd, re.IGNORECASE)
    if env_sh:
        if mutate:
            val = env_sh.group(2).strip().strip("'\"")
            vfs.env[env_sh.group(1)] = vfs.expand_vars(val)
        return e

    # Track directory navigation (cd / Set-Location / pushd / popd)
    cd_m = re.match(r"^(?:cd|chdir|Set-Location|sl|pushd)\s+(.+)$", clean_cmd, re.IGNORECASE)
    if cd_m and ">" not in clean_cmd:
        raw_dir = cd_m.group(1).strip().strip("'\"")
        # Ignore cd /d flag on Windows
        if raw_dir.lower().startswith("/d "):
            raw_dir = raw_dir[3:].strip().strip("'\"")
        if clean_cmd.lower().startswith("pushd") and mutate:
            vfs.dir_stack.append(vfs.cwd)
        if mutate:
            vfs.cd(raw_dir)
        return e

    if lower_cmd == "popd":
        if mutate and vfs.dir_stack:
            vfs.cwd = vfs.dir_stack.pop()
        return e

    # 1. Hard Block Checks
    if re.search(r"\b(format|diskpart|mkfs|fdisk|parted|dd)(\.exe)?\b", lower_cmd):
        e.hard_block = True
        if enable_opaque_escalation:
            e.opaque = True
        if enable_protected_paths:
            e.protected_hit = True
        e.notes.append("hard-blocked raw-disk command (format/diskpart)")
        return e

    # 2. Indirection / Opaque Checks
    if enable_opaque_escalation:
        if any(
            t in clean_cmd
            for t in (
                "$(",
                "`",
                "eval ",
                "base64",
                "| bash",
                "| sh",
                "| powershell",
                "| pwsh",
                "| cmd",
                "bash -c",
                "sh -c",
            )
        ):
            e.opaque = True
            e.notes.append("command uses indirection (eval / substitution / piped shell) - cannot be analysed statically")
            return e
        if any(f in lower_cmd for f in ("-encodedcommand", "-encoded", "-enc ", " -e ")):
            e.opaque = True
            e.notes.append("opaque execution via -EncodedCommand")
            return e
        if re.search(r"\b(powershell|pwsh)(\.exe)?\s+.*(-[a-zA-Z]*c[a-zA-Z]*|-Command)\b", clean_cmd, re.IGNORECASE):
            e.opaque = True
            e.notes.append("opaque execution via nested PowerShell -Command")
            return e
        if re.search(r"&\s*(\$|\(|\"[^\"]+\"\s*\+)", clean_cmd):
            e.opaque = True
            e.notes.append("opaque execution via dynamic PowerShell invocation operator")
            return e
        if re.search(r"\b(invoke-expression|iex)\b", lower_cmd):
            e.opaque = True
            e.notes.append("opaque execution via Invoke-Expression / iex")
            return e

    # 3. Privilege Escalation Checks
    if enable_protected_paths:
        if re.search(r"\bstart-process\b", lower_cmd) and re.search(r"-[vV][eE][rR][bB]\s+[\"']?[rR][uU][nN][aA][sS][\"']?", clean_cmd):
            e.protected_hit = True
            e.notes.append("privilege escalation via Start-Process -Verb RunAs")
        if re.search(r"\brunas(\.exe)?\s+/[uU][sS][eE][rR]:", clean_cmd):
            e.protected_hit = True
            e.notes.append("privilege escalation via runas")

    # 4. Expand environment variables for path analysis
    expanded_seg = vfs.expand_vars(clean_cmd)
    cmd_invocations = parse_cmd_line(expanded_seg)
    ps_result = parse_powershell(expanded_seg)

    if enable_opaque_escalation and ps_result.has_encoded_command:
        e.opaque = True
        e.notes.append("opaque execution via -EncodedCommand")
    if enable_opaque_escalation and ps_result.has_invoke_expression:
        e.opaque = True
        e.notes.append("opaque execution via Invoke-Expression")
    if enable_protected_paths and ps_result.has_runas:
        e.protected_hit = True
        e.notes.append("privilege escalation via Start-Process -Verb RunAs")
    if enable_opaque_escalation and ps_result.has_subexpression:
        e.opaque = True
        e.notes.append("command contains subexpressions")

    echo_payload = _extract_echo_payload(expanded_seg)

    # Check Redirections
    redirs_handled: set[tuple[str, str]] = set()
    for inv in cmd_invocations:
        for op, target in inv.redirections:
            tgt_clean = target.strip("'\"")
            if not tgt_clean or (op, tgt_clean) in redirs_handled:
                continue
            redirs_handled.add((op, tgt_clean))
            for t in vfs.expand_arg(tgt_clean):
                if op == ">>":
                    e.appends.append(t)
                    if mutate:
                        vfs.append_file(t, added_content=echo_payload)
                elif op == ">":
                    (e.overwrites if vfs.exists(t) else e.creates).append(t)
                    if mutate:
                        vfs.write_file(t, content=echo_payload if echo_payload is not None else b"data\n")

    # Redirection fallback
    if not redirs_handled:
        if ">>" in expanded_seg:
            tgt = expanded_seg.split(">>")[-1].strip().split()[0].strip("'\"")
            if tgt:
                for t in vfs.expand_arg(tgt):
                    e.appends.append(t)
                    if mutate:
                        vfs.append_file(t, added_content=echo_payload)
        elif ">" in expanded_seg:
            tgt = expanded_seg.split(">")[-1].strip().split()[0].strip("'\"")
            if tgt:
                for t in vfs.expand_arg(tgt):
                    (e.overwrites if vfs.exists(t) else e.creates).append(t)
                    if mutate:
                        vfs.write_file(t, content=echo_payload if echo_payload is not None else b"data\n")

    # 5. Process PowerShell AST Cmdlets (if any non-cmdlet aliases weren't exclusively cmd.exe)
    ps_handled = False
    for ps_cmd in ps_result.commands:
        v = ps_cmd.normalized_name.lower()
        params = ps_cmd.parameters
        pos = ps_cmd.positional

        ps_targets: list[str] = []
        if "Path" in params:
            val = params["Path"]
            ps_targets.extend(val if isinstance(val, list) else [str(val)])
        if "LiteralPath" in params:
            val = params["LiteralPath"]
            ps_targets.extend(val if isinstance(val, list) else [str(val)])
        ps_targets.extend(pos)

        if v in ("remove-item", "remove-itemproperty"):
            ps_handled = True
            is_recurse = bool(params.get("Recurse", False) or params.get("r", False))
            for t in ps_targets:
                if _is_registry_path(t):
                    e.registry_writes.append(t)
                    if enable_protected_paths and _is_protected_registry(t):
                        e.protected_hit = True
                    e.deletes.append(f"registry:{t}")
                else:
                    for exp in vfs.expand_arg(t):
                        if vfs.isdir(exp) and (is_recurse or v in ("rmdir", "rd")):
                            for w in vfs.walk_paths(exp):
                                e.deletes.append(w)
                                e.bytes_affected += vfs.getsize(w)
                            if mutate:
                                vfs.delete(exp, recursive=True)
                        elif vfs.exists(exp):
                            e.deletes.append(exp)
                            e.bytes_affected += vfs.getsize(exp)
                            if mutate:
                                vfs.delete(exp, recursive=is_recurse)

        elif v == "move-item":
            ps_handled = True
            dst = params.get("Destination", "") or (pos[-1] if len(pos) >= 2 else "")
            srcs = ps_targets[:-1] if (not params.get("Destination") and len(ps_targets) >= 2) else ps_targets
            if dst:
                dst_exp = vfs.expand_arg(str(dst))
                dst_p = dst_exp[0] if dst_exp else vfs.resolve_path(str(dst))
                for s in srcs:
                    for sp in vfs.expand_arg(s):
                        if vfs.exists(sp):
                            e.deletes.append(sp)
                            e.bytes_affected += vfs.getsize(sp)
                            final = os.path.join(dst_p, os.path.basename(sp)) if vfs.isdir(dst_p) else dst_p
                            (e.overwrites if vfs.exists(final) else e.creates).append(final)
                            if mutate:
                                vfs.move(sp, dst_p)

        elif v == "copy-item":
            ps_handled = True
            dst = params.get("Destination", "") or (pos[-1] if len(pos) >= 2 else "")
            srcs = ps_targets[:-1] if (not params.get("Destination") and len(ps_targets) >= 2) else ps_targets
            if dst:
                dst_exp = vfs.expand_arg(str(dst))
                dst_p = dst_exp[0] if dst_exp else vfs.resolve_path(str(dst))
                for s in srcs:
                    for sp in vfs.expand_arg(s):
                        if vfs.exists(sp):
                            final = os.path.join(dst_p, os.path.basename(sp)) if vfs.isdir(dst_p) else dst_p
                            (e.overwrites if vfs.exists(final) else e.creates).append(final)
                            if mutate:
                                vfs.copy(sp, dst_p)

        elif v == "new-item":
            ps_handled = True
            item_type = str(params.get("ItemType", "file")).lower()
            for t in ps_targets:
                resolved = vfs.resolve_path(t)
                if item_type in ("directory", "dir"):
                    if not vfs.exists(resolved):
                        e.creates.append(resolved)
                    if mutate:
                        vfs.mkdir(resolved, parents=True)
                else:
                    (e.overwrites if vfs.exists(resolved) else e.creates).append(resolved)
                    if mutate:
                        val_str = str(params.get("Value", ""))
                        vfs.write_file(resolved, content=val_str.encode("utf-8"))

        elif v == "set-content":
            ps_handled = True
            val_str = str(params.get("Value", "content"))
            for t in ps_targets:
                if _is_registry_path(t):
                    e.registry_writes.append(t)
                    if _is_protected_registry(t):
                        e.protected_hit = True
                    e.creates.append(f"registry:{t}")
                else:
                    for p in vfs.expand_arg(t):
                        (e.overwrites if vfs.exists(p) else e.creates).append(p)
                        if mutate:
                            vfs.write_file(p, content=val_str.encode("utf-8"))

        elif v == "add-content":
            ps_handled = True
            val_str = str(params.get("Value", "appended"))
            for t in ps_targets:
                for p in vfs.expand_arg(t):
                    e.appends.append(p)
                    if mutate:
                        vfs.append_file(p, added_content=val_str.encode("utf-8"))

        elif v == "out-file":
            ps_handled = True
            is_append = bool(params.get("Append", False))
            target_file = params.get("FilePath", "") or (pos[0] if pos else "")
            if target_file:
                for p in vfs.expand_arg(str(target_file)):
                    if is_append:
                        e.appends.append(p)
                        if mutate:
                            vfs.append_file(p)
                    else:
                        (e.overwrites if vfs.exists(p) else e.creates).append(p)
                        if mutate:
                            vfs.write_file(p, content=b"output\n")

        elif v == "stop-process":
            ps_handled = True
            targets = []
            if "Name" in params:
                targets.append(str(params["Name"]))
            if "Id" in params:
                targets.append(str(params["Id"]))
            targets.extend(pos)
            e.signals += targets

    # 6. Process cmd.exe & POSIX verbs if not already handled specifically as PS cmdlet
    if not ps_handled:
        for inv in cmd_invocations:
            v = inv.normalized_verb
            args = [a for a in inv.args if a]

            if v == "del":
                is_recurse = inv.is_recursive
                for a in args:
                    for h in vfs.expand_arg(a):
                        if vfs.isdir(h) and is_recurse:
                            for w in vfs.walk_paths(h):
                                e.deletes.append(w)
                                e.bytes_affected += vfs.getsize(w)
                            if mutate:
                                vfs.delete(h, recursive=True)
                        elif vfs.exists(h):
                            e.deletes.append(h)
                            e.bytes_affected += vfs.getsize(h)
                            if mutate:
                                vfs.delete(h, recursive=False)

            elif v == "rd":
                for a in args:
                    for h in vfs.expand_arg(a):
                        if vfs.exists(h):
                            for w in vfs.walk_paths(h):
                                e.deletes.append(w)
                                e.bytes_affected += vfs.getsize(w)
                            if mutate:
                                vfs.delete(h, recursive=True)

            elif v == "move" and len(args) >= 2:
                *srcs, dst = args
                dst_exp = vfs.expand_arg(dst)
                dst_p = dst_exp[0] if dst_exp else vfs.resolve_path(dst)
                for s in srcs:
                    for sp in vfs.expand_arg(s):
                        if vfs.exists(sp):
                            e.deletes.append(sp)
                            e.bytes_affected += vfs.getsize(sp)
                            final = os.path.join(dst_p, os.path.basename(sp)) if vfs.isdir(dst_p) else dst_p
                            if vfs.isdir(sp):
                                # Subtree files moved
                                for w in vfs.walk_paths(sp):
                                    if w != sp:
                                        e.deletes.append(w)
                            (e.overwrites if vfs.exists(final) else e.creates).append(final)
                            if mutate:
                                vfs.move(sp, dst_p)

            elif v == "copy" and len(args) >= 2:
                *srcs, dst = args
                dst_exp = vfs.expand_arg(dst)
                dst_p = dst_exp[0] if dst_exp else vfs.resolve_path(dst)
                for s in srcs:
                    for sp in vfs.expand_arg(s):
                        if vfs.exists(sp):
                            final = os.path.join(dst_p, os.path.basename(sp)) if vfs.isdir(dst_p) else dst_p
                            (e.overwrites if vfs.exists(final) else e.creates).append(final)
                            if mutate:
                                vfs.copy(sp, dst_p)

            elif v in ("mv",) and len(args) >= 2:
                *srcs, dst = args
                dst_exp = vfs.expand_arg(dst)
                dst_p = dst_exp[0] if dst_exp else vfs.resolve_path(dst)
                for s in srcs:
                    for sp in vfs.expand_arg(s):
                        if vfs.exists(sp):
                            e.deletes.append(sp)
                            e.bytes_affected += vfs.getsize(sp)
                            final = os.path.join(dst_p, os.path.basename(sp)) if vfs.isdir(dst_p) else dst_p
                            (e.overwrites if vfs.exists(final) else e.creates).append(final)
                            if mutate:
                                vfs.move(sp, dst_p)

            elif v in ("cp",) and len(args) >= 2:
                *srcs, dst = args
                dst_exp = vfs.expand_arg(dst)
                dst_p = dst_exp[0] if dst_exp else vfs.resolve_path(dst)
                for s in srcs:
                    for sp in vfs.expand_arg(s):
                        if vfs.exists(sp):
                            final = os.path.join(dst_p, os.path.basename(sp)) if vfs.isdir(dst_p) else dst_p
                            (e.overwrites if vfs.exists(final) else e.creates).append(final)
                            if mutate:
                                vfs.copy(sp, dst_p)

            elif v in ("rm", "rmdir", "unlink"):
                is_rec = any("r" in tok.lower() for tok in inv.raw_tokens if tok.startswith("-"))
                for a in args:
                    for h in vfs.expand_arg(a):
                        if vfs.exists(h):
                            if vfs.isdir(h) and (is_rec or v == "rmdir"):
                                for w in vfs.walk_paths(h):
                                    e.deletes.append(w)
                                    e.bytes_affected += vfs.getsize(w)
                                if mutate:
                                    vfs.delete(h, recursive=True)
                            else:
                                e.deletes.append(h)
                                e.bytes_affected += vfs.getsize(h)
                                if mutate:
                                    vfs.delete(h, recursive=False)

            elif v in ("mkdir", "md"):
                for a in args:
                    resolved = vfs.resolve_path(a)
                    if not vfs.exists(resolved):
                        e.creates.append(resolved)
                    if mutate:
                        vfs.mkdir(resolved, parents=True)

            elif v == "touch":
                for a in args:
                    resolved = vfs.resolve_path(a)
                    if not vfs.exists(resolved):
                        e.creates.append(resolved)
                    if mutate:
                        vfs.touch_file(resolved)

            elif v in ("chmod",):
                if len(args) >= 2:
                    mode_arg = args[0]
                    for a in args[1:]:
                        for h in vfs.expand_arg(a):
                            if vfs.exists(h):
                                e.perm_changes.append(h)
                                if mutate:
                                    vfs.chmod(h, mode_arg)

            elif v in ("taskkill", "kill", "pkill", "killall"):
                targets = list(args)
                if "/im" in inv.switch_args:
                    targets.append(inv.switch_args["/im"])
                if "/pid" in inv.switch_args:
                    targets.append(inv.switch_args["/pid"])
                e.signals += targets

            elif v in ("curl", "wget", "scp", "rsync", "ssh", "git", "pip", "npm", "docker"):
                e.external = True

    # 7. Check Protected System Paths
    if enable_protected_paths:
        allp = e.deletes + e.overwrites + e.perm_changes + e.appends + e.creates
        for p in allp:
            if p.startswith("registry:"):
                continue
            norm_p = os.path.normpath(os.path.abspath(p)).replace("\\", "/")
            if norm_p in ("/", "C:/", "D:/") or (len(norm_p) == 3 and norm_p.endswith(":/")):
                e.protected_hit = True
                break
            for pr in PROTECTED:
                if pr == "/":
                    continue
                norm_pr = os.path.normpath(pr).replace("\\", "/").rstrip("/")
                if norm_p == norm_pr or norm_p.startswith(norm_pr + "/"):
                    e.protected_hit = True
                    break
                if len(norm_p) >= 2 and norm_p[1] == ":":
                    without_drive = norm_p[2:]
                    if without_drive == norm_pr or without_drive.startswith(norm_pr + "/"):
                        e.protected_hit = True
                        break
            if e.protected_hit:
                break

    return e

