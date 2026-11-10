"""
src/nlagent/ui/client.py - Desktop UI Client & Service Connector.

Connects the desktop GUI (preview popup, tray, wizard, study mode) to either:
1. The local Core Service daemon running on 127.0.0.1 with an ephemeral bearer token.
2. In-process direct fallback execution when the daemon is not active.
3. MockServiceCoreClient for isolated unit testing and offscreen verification.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

def get_ui_client(mock: bool = False, state_dir: str | Path | None = None) -> Any:
    """Factory creating an active ServiceClient with automatic fallback or mock."""
    if mock:
        return MockServiceCoreClient()
    from nlagent.service.core import ServiceClient
    return ServiceClient(state_dir=state_dir)


class MockServiceCoreClient:
    """Mock core client providing deterministic responses for unit testing."""

    def __init__(self, running: bool = True) -> None:
        self.running = running
        self.history: list[dict[str, Any]] = []
        self.committed: list[str] = []
        self.undone_count = 0

    def is_running(self) -> bool:
        return self.running

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.running else "down",
            "backend": "mock-core",
            "uptime_seconds": 120.0,
        }

    def get_models(self) -> dict[str, Any]:
        return {
            "active_model": "NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf",
            "models": ["NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf"],
            "backend": "llama-server",
        }

    def propose(self, request: str, cwd: str = "") -> dict[str, Any]:
        req_clean = request.lower()
        if "delete" in req_clean or "clean" in req_clean or "wipe" in req_clean:
            tier = "destructive"
            cmd = "del /f /s /q *.log"
            preview = "Will delete files matching '*.log' across workspace. Reversible via quarantine."
            targets = ["temp.log", "cache.log"]
            reversible = True
        elif "create" in req_clean or "make" in req_clean or "mkdir" in req_clean:
            tier = "state_modifying"
            cmd = "mkdir release_dir"
            preview = "Will create new directory 'release_dir' in current workspace."
            targets = ["release_dir"]
            reversible = False
        else:
            tier = "read_only"
            cmd = "Get-ChildItem -Path . -Recurse"
            preview = "Read-only inspection. Searches files without modifying disk state."
            targets = []
            reversible = False

        res = {
            "command": cmd,
            "tier": tier,
            "preview": preview,
            "targets": targets,
            "reversible": reversible,
            "cached": False,
            "clarifying_question": None,
        }
        self.history.append({"request": request, "response": res})
        return res

    def commit(self, command: str, tier: str = "read_only", execution_id: str = "") -> dict[str, Any]:
        self.committed.append(command)
        return {
            "status": "success",
            "returncode": 0,
            "command": command,
            "stdout": "Command executed successfully.\n",
            "stderr": "",
        }

    def undo(self) -> dict[str, Any]:
        self.undone_count += 1
        return {
            "status": "success",
            "restored_count": 1,
            "restored_paths": ["restored_file.txt"],
        }


def get_ui_client(mock: bool = False, state_dir: str | Path | None = None) -> Any:
    """Factory creating an active ServiceClient with automatic fallback or mock."""
    if mock:
        return MockServiceCoreClient()
    return ServiceClient(state_dir=state_dir)
