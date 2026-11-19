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


def call_propose(
    client: Any,
    request: str,
    cwd: str = "",
    session_id: str | None = None,
    shell: str | None = None,
    endpoint_url: str | None = None,
) -> dict[str, Any]:
    """Invoke `client.propose` safely regardless of optional kwargs support."""
    kwargs: dict[str, Any] = {"cwd": cwd}
    if isinstance(client, MockServiceCoreClient):
        return client.propose(request, cwd=cwd, session_id=session_id, shell=shell)
    if session_id is not None:
        kwargs["session_id"] = session_id
    if shell is not None:
        kwargs["shell"] = shell
    if endpoint_url is not None:
        kwargs["endpoint_url"] = endpoint_url
    try:
        return dict(client.propose(request, **kwargs))
    except TypeError:
        return dict(client.propose(request, cwd=cwd))


def normalize_commit_result(raw: Any) -> dict[str, Any]:
    """Normalize commit return payloads across ServiceClient `(code, dict)` and MockServiceCoreClient `dict`."""
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], Mapping):
        code, payload = raw
        res = dict(payload)
        res.setdefault("exit_code", int(code))
        return res
    if isinstance(raw, Mapping):
        res = dict(raw)
        if "exit_code" not in res:
            res["exit_code"] = int(res.get("returncode", 0))
        if "output" not in res:
            stdout = str(res.get("stdout", "") or "")
            stderr = str(res.get("stderr", "") or "")
            res["output"] = f"{stdout}\n{stderr}".strip() if stderr else stdout.strip()
        return res
    return {"status": "unknown", "exit_code": 1, "output": str(raw)}


class MockServiceCoreClient:
    """Mock core client providing deterministic responses for unit testing."""

    def __init__(self, running: bool = True) -> None:
        self.running = running
        self.history: list[dict[str, Any]] = []
        self.committed: list[str] = []
        self.undone_count = 0
        self._proposals: dict[str, dict[str, Any]] = {}
        self._seq = 0

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
            "active_model": "NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf",
            "models": ["NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf"],
            "backend": "llama-server",
        }

    def propose(
        self,
        request: str,
        cwd: str = "",
        session_id: str | None = None,
        shell: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        req_clean = request.lower().strip()
        self._seq += 1
        prop_id = f"mock_p_{self._seq}"
        active_shell = (shell or "powershell").lower()

        if req_clean in ("clean up", "delete stuff", "remove files") or "ambiguous" in req_clean:
            res = {
                "id": prop_id,
                "command": "",
                "tier": "ambiguous",
                "preview": "Clarification required: Which specific files or directory should be cleaned up?",
                "targets": [],
                "reversible": False,
                "cached": False,
                "clarifying_question": "Which specific files or directory should be cleaned up?",
                "needs_clarification": True,
                "explanation": "Ambiguous destructive intent requires explicit target clarification.",
                "cwd": cwd,
                "shell": active_shell,
                "session_id": session_id or "default",
            }
            self._proposals[prop_id] = res
            self.history.append({"request": request, "response": res})
            return res

        if "delete" in req_clean or "clean" in req_clean or "wipe" in req_clean:
            tier = "destructive"
            cmd = "del /f /s /q *.log" if active_shell == "cmd" else (
                "rm -f *.log" if active_shell == "bash" else "Remove-Item -Path *.log -Force"
            )
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
            cmd = "dir /a" if active_shell == "cmd" else (
                "ls -la" if active_shell == "bash" else "Get-ChildItem -Path . -Force"
            )
            preview = "Read-only inspection. Searches files without modifying disk state."
            targets = []
            reversible = False

        res = {
            "id": prop_id,
            "command": cmd,
            "tier": tier,
            "preview": preview,
            "targets": targets,
            "reversible": reversible,
            "cached": False,
            "clarifying_question": None,
            "needs_clarification": False,
            "cwd": cwd,
            "shell": active_shell,
            "session_id": session_id or "default",
        }
        self._proposals[prop_id] = res
        self.history.append({"request": request, "response": res})
        return res

    def commit(self, command_or_id: str, tier: str = "read_only", execution_id: str = "", **_: Any) -> dict[str, Any]:
        rec = self._proposals.get(command_or_id)
        cmd = rec["command"] if rec else command_or_id
        effective_tier = rec.get("tier", tier) if rec else tier
        self.committed.append(cmd)
        q_id = command_or_id if effective_tier == "destructive" else None
        return {
            "id": command_or_id,
            "status": "committed",
            "returncode": 0,
            "exit_code": 0,
            "command": cmd,
            "output": "Command executed successfully.",
            "stdout": "Command executed successfully.\n",
            "stderr": "",
            "quarantine_id": q_id,
        }

    def undo(self, target_id: str | None = None, on_clash: str = "error") -> dict[str, Any]:
        self.undone_count += 1
        return {
            "id": target_id or "mock_undo",
            "status": "success",
            "success": True,
            "restored_count": 1,
            "restored_paths": ["restored_file.txt"],
            "message": "Restored 1 file(s) from quarantine.",
        }

