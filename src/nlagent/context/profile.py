"""
src/nlagent/context/profile.py - User-Editable Profile with Approve-Before-Save Governance.

Manages:
1. Path aliases (e.g. "invoices" -> "~/Documents/Invoices").
2. Favourite/Approved folders (opt-in roots allowed for read-only grounding searches).
3. Pending suggestion queue with strict approve-before-save requirement.
4. "Forget" functionality for privacy compliance.
5. Persistent storage in JSON under NLAgent state directory.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger("nlagent.context.profile")


@dataclass
class Suggestion:
    """A proposed profile addition awaiting explicit user approval."""
    id: str
    kind: str  # "alias" or "folder"
    key: str  # alias name or folder path
    value: str  # target path or description
    reason: str
    suggested_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Suggestion:
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            key=str(data["key"]),
            value=str(data["value"]),
            reason=str(data.get("reason", "")),
            suggested_at=str(data.get("suggested_at", "")),
        )


def get_default_profile_path(custom_state_dir: str | Path | None = None) -> Path:
    """Resolve the default file path for profile.json."""
    from nlagent.service.core import get_state_dir
    base = get_state_dir(custom_state_dir)
    return base / "profile.json"


@dataclass
class UserProfile:
    """
    User-editable profile providing personalized grounding contexts and aliases.
    Follows strict approve-before-save rules for any agent-generated suggestions.
    """
    aliases: dict[str, str] = field(default_factory=dict)
    favourite_folders: list[str] = field(default_factory=list)
    pending_suggestions: dict[str, Suggestion] = field(default_factory=dict)
    default_shell: str = "powershell" if os.name == "nt" else "bash"
    file_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "aliases": dict(self.aliases),
            "favourite_folders": list(self.favourite_folders),
            "pending_suggestions": {k: s.to_dict() for k, s in self.pending_suggestions.items()},
            "default_shell": self.default_shell,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], file_path: Path | None = None) -> UserProfile:
        aliases = dict(data.get("aliases", {}))
        favourite_folders = list(data.get("favourite_folders", []))
        raw_sugg = data.get("pending_suggestions", {})
        pending = {k: Suggestion.from_dict(v) for k, v in raw_sugg.items()}
        default_shell = str(data.get("default_shell", "powershell" if os.name == "nt" else "bash"))
        return cls(
            aliases=aliases,
            favourite_folders=favourite_folders,
            pending_suggestions=pending,
            default_shell=default_shell,
            file_path=file_path,
        )

    # -------------------------------------------------------------------------
    # Direct User Mutations (Immediately Saved)
    # -------------------------------------------------------------------------

    def add_alias(self, alias_name: str, target_path: str | Path) -> None:
        """User directly registers a path alias."""
        norm_key = alias_name.strip().lower()
        norm_val = str(Path(target_path).expanduser().resolve())
        self.aliases[norm_key] = norm_val
        self.save()

    def remove_alias(self, alias_name: str) -> bool:
        """Remove a registered alias."""
        norm_key = alias_name.strip().lower()
        if norm_key in self.aliases:
            del self.aliases[norm_key]
            self.save()
            return True
        return False

    def add_favourite_folder(self, folder_path: str | Path) -> None:
        """User directly adds an approved folder root for grounding."""
        norm_path = str(Path(folder_path).expanduser().resolve())
        if norm_path not in self.favourite_folders:
            self.favourite_folders.append(norm_path)
            self.save()

    def remove_favourite_folder(self, folder_path: str | Path) -> bool:
        """Remove an approved folder from favourites."""
        norm_path = str(Path(folder_path).expanduser().resolve())
        if norm_path in self.favourite_folders:
            self.favourite_folders.remove(norm_path)
            self.save()
            return True
        return False

    def forget_all(self) -> None:
        """Clear all aliases, favorite folders, and pending suggestions ('Forget' button)."""
        self.aliases.clear()
        self.favourite_folders.clear()
        self.pending_suggestions.clear()
        self.save()

    # -------------------------------------------------------------------------
    # Approve-Before-Save Suggestion Pipeline
    # -------------------------------------------------------------------------

    def suggest_alias(self, alias_name: str, target_path: str | Path, reason: str = "") -> str:
        """
        Agent proposes a new alias. Does NOT save to active aliases until user approves.
        Returns the generated suggestion ID.
        """
        sugg_id = f"sugg_alias_{uuid.uuid4().hex[:8]}"
        norm_key = alias_name.strip().lower()
        norm_val = str(Path(target_path).expanduser().resolve())
        iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.pending_suggestions[sugg_id] = Suggestion(
            id=sugg_id,
            kind="alias",
            key=norm_key,
            value=norm_val,
            reason=reason or "Proposed based on command execution context",
            suggested_at=iso_now,
        )
        self.save()
        return sugg_id

    def suggest_folder(self, folder_path: str | Path, reason: str = "") -> str:
        """
        Agent proposes adding a folder root to favourite_folders.
        Does NOT save to active folders until user approves.
        """
        sugg_id = f"sugg_dir_{uuid.uuid4().hex[:8]}"
        norm_val = str(Path(folder_path).expanduser().resolve())
        iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.pending_suggestions[sugg_id] = Suggestion(
            id=sugg_id,
            kind="folder",
            key=norm_val,
            value=norm_val,
            reason=reason or "Frequently accessed working directory",
            suggested_at=iso_now,
        )
        self.save()
        return sugg_id

    def approve_suggestion(self, suggestion_id: str) -> bool:
        """
        User approves a pending suggestion.
        Promotes it to active aliases or favourite_folders and persists.
        """
        sugg = self.pending_suggestions.pop(suggestion_id, None)
        if not sugg:
            return False

        if sugg.kind == "alias":
            self.aliases[sugg.key] = sugg.value
        elif sugg.kind == "folder":
            if sugg.value not in self.favourite_folders:
                self.favourite_folders.append(sugg.value)

        self.save()
        return True

    def reject_suggestion(self, suggestion_id: str) -> bool:
        """User rejects and discards a pending suggestion."""
        if suggestion_id in self.pending_suggestions:
            del self.pending_suggestions[suggestion_id]
            self.save()
            return True
        return False

    def list_pending_suggestions(self) -> list[dict[str, Any]]:
        """Return list of all pending suggestions."""
        return [s.to_dict() for s in self.pending_suggestions.values()]

    # -------------------------------------------------------------------------
    # Alias Resolution & Grounding Lookup
    # -------------------------------------------------------------------------

    def resolve_alias(self, alias_or_term: str) -> Path | None:
        """
        Resolve an alias to an existing filesystem Path if registered.
        """
        clean = alias_or_term.strip().lower()
        if clean in self.aliases:
            p = Path(self.aliases[clean])
            return p

        # Check for matching alias within natural language phrase
        for alias_name, target in self.aliases.items():
            if alias_name in clean:
                return Path(target)

        return None

    def get_allowed_roots(self, current_cwd: str | Path | None = None) -> list[Path]:
        """
        Return the list of approved directory roots for bounded grounding searches.
        Always includes the current working directory, plus all user-approved favourite folders.
        """
        roots: list[Path] = []
        if current_cwd:
            roots.append(Path(current_cwd).resolve())

        for f in self.favourite_folders:
            p = Path(f).expanduser().resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)

        # Include resolved alias directories
        for target in self.aliases.values():
            p = Path(target).expanduser().resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)

        return roots

    # -------------------------------------------------------------------------
    # Persistence (Atomic JSON Save & Load)
    # -------------------------------------------------------------------------

    def save(self, path: str | Path | None = None) -> Path:
        """Atomically persist UserProfile to disk."""
        target_path = Path(path) if path else (self.file_path or get_default_profile_path())
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = self.to_dict()
        tmp_fd, tmp_file = tempfile.mkstemp(prefix="nlagent_profile_", dir=str(target_path.parent))
        try:
            with open(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # Atomic replace
            os.replace(tmp_file, str(target_path))
        except Exception:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
            raise

        self.file_path = target_path
        return target_path

    @classmethod
    def load(cls, path: str | Path | None = None) -> UserProfile:
        """Load UserProfile from disk, or return empty default if missing."""
        target_path = Path(path) if path else get_default_profile_path()
        if not target_path.is_file():
            return cls(file_path=target_path)

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data, file_path=target_path)
        except Exception as e:
            logger.warning("Could not read profile from %s (%s); starting fresh", target_path, e)
            return cls(file_path=target_path)

