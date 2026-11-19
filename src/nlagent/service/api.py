"""
src/nlagent/service/api.py - HTTP Request Handlers and Routing for Core Service API.

Endpoints:
  GET  /health   - Service status, uptime, PID (unauthenticated or authenticated)
  POST /propose  - Generate proposed command from natural language request (Bearer auth)
  POST /commit   - Execute proposed command with TOCTOU check and quarantine (Bearer auth)
  POST /undo     - Rollback quarantined file operations (Bearer auth)
  GET  /models   - List local models and manifest installation state (Bearer auth)
  POST /shutdown - Gracefully terminate the service daemon (Bearer auth)
"""

from __future__ import annotations

import email.message
import http.server
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger("nlagent.service.api")


class ServiceAPI:
    """
    Core Service API business logic dispatcher.
    Decoupled from network transports for clean testing and high-performance dispatch.
    """

    def __init__(
        self,
        token: str,
        start_time: float | None = None,
        db_path: str | Path | None = None,
        shutdown_callback: Callable[[], None] | None = None,
    ) -> None:
        self.token = token
        self.start_time = start_time or time.time()
        self.db_path = db_path
        self.shutdown_callback = shutdown_callback
        self.version = "0.1.0"
        self._sessions: dict[str, Any] = {}
        self._sessions_lock = threading.Lock()

    def get_session(self, session_id: str | None, cwd: str) -> Any:
        """Return or initialize a persistent SessionState for multi-turn chat sessions."""
        from nlagent.context.state import SessionState

        sid = session_id or "default"
        with self._sessions_lock:
            sess = self._sessions.get(sid)
            if sess is None:
                sess = SessionState(cwd=cwd)
                self._sessions[sid] = sess
            elif cwd and os.path.isdir(cwd) and sess.cwd != cwd:
                sess.update_cwd(cwd)
            return sess

    def verify_auth(self, headers: Mapping[str, str]) -> tuple[bool, int, str]:
        """Verify Bearer token authorization header."""
        auth_hdr = headers.get("Authorization") or headers.get("authorization")
        if not auth_hdr:
            return False, 401, "Missing Authorization header"

        parts = auth_hdr.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False, 401, "Invalid Authorization header format. Expected 'Bearer <token>'"

        provided_token = parts[1].strip()
        if provided_token != self.token:
            return False, 403, "Invalid bearer token"

        return True, 200, "OK"

    def handle_health(self) -> tuple[int, dict[str, Any]]:
        """GET /health - Service status and heartbeat."""
        uptime = round(time.time() - self.start_time, 2)
        return 200, {
            "status": "ok",
            "pid": os.getpid(),
            "version": self.version,
            "uptime_seconds": uptime,
        }

    def handle_propose(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST /propose - Generate terminal command proposal."""
        req_text = str(body.get("request", "")).strip()
        if not req_text:
            return 400, {"error": "Missing required field 'request'"}

        cwd = body.get("cwd") or os.getcwd()
        mock_cmd = body.get("mock")
        shell = body.get("shell")
        session_id = body.get("session_id")
        endpoint_override = body.get("endpoint_url")

        from nlagent.backends.llama_server import LlamaServerBackend, resolve_local_endpoint
        from nlagent.cache.verified_cache import VerifiedCommandCache
        from nlagent.cli.db import SQLiteStateStore
        from nlagent.daemon.propose import get_default_env, propose
        from nlagent.safety.renderer import ExecutionPlan

        session = self.get_session(session_id, cwd)
        cache = VerifiedCommandCache(threshold=0.88)
        env = get_default_env(cwd=session.cwd, shell=shell)

        from nlagent.config import AgentConfig
        agent_cfg = AgentConfig.from_env()
        if endpoint_override:
            agent_cfg.endpoint_url = str(endpoint_override)

        if mock_cmd:
            backend = lambda prompt: json.dumps({
                "command": mock_cmd,
                "targets": [mock_cmd.split()[-1]] if len(mock_cmd.split()) > 1 else [],
                "explanation": "Mock proposal via service",
            })
        else:
            mock_env = os.environ.get("NLAGENT_MOCK_PROPOSAL")
            if mock_env:
                backend = lambda prompt: mock_env
            else:
                target_endpoint = endpoint_override or resolve_local_endpoint(agent_cfg.endpoint_url)
                backend = LlamaServerBackend(
                    endpoint_url=target_endpoint,
                    reasoning=agent_cfg.reasoning,
                    timeout=agent_cfg.timeout,
                )

        try:
            res = propose(
                request=req_text,
                session_state=session,
                backend=backend,
                verified_cache=cache,
                env=env,
                config=agent_cfg,
            )
        except Exception as e:
            logger.error("Propose failed: %s", e)
            return 500, {"error": f"Propose failed: {e}"}

        import uuid
        proposal_id = body.get("id") or f"p_{uuid.uuid4().hex[:8]}"
        cmd = str(res.get("command") or "").strip()
        tier = res.get("tier", "read_only")
        targets = res.get("targets", [])
        preview_text = res.get("preview", "")
        explanation = res.get("explanation", "")
        clarifying_q = res.get("clarifying_question")
        needs_clar = bool(res.get("needs_clarification"))
        is_cached = bool(res.get("cached"))

        plan = ExecutionPlan.create(
            command=cmd,
            cwd=session.cwd,
            tier=tier,
            effect=res.get("effect"),
            explanation=explanation,
        )
        plain_english = plan.plain_english_description or preview_text
        reversible = plan.effect_summary.is_reversible

        # Persist to state database
        try:
            db = SQLiteStateStore(db_path=self.db_path)
            db.save_proposal(
                proposal_id=proposal_id,
                request=req_text,
                command=cmd,
                tier=tier,
                cwd=session.cwd,
                preview_text=preview_text,
                explanation=explanation,
                targets=targets,
                status="proposed",
            )
        except Exception as e:
            logger.warning("Database persistence failed: %s", e)

        return 200, {
            "id": proposal_id,
            "command": cmd,
            "tier": tier,
            "targets": targets,
            "preview": plain_english,
            "plain_english": plain_english,
            "explanation": explanation,
            "clarifying_question": clarifying_q,
            "needs_clarification": needs_clar,
            "cached": is_cached,
            "reversible": reversible,
            "cwd": session.cwd,
            "shell": env.get("shell", shell or "powershell"),
            "session_id": session_id or "default",
        }

    def handle_commit(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST /commit - Execute proposed command with safety checks."""
        proposal_id = body.get("id")
        proposal_record = body.get("proposal")

        from nlagent.cli.db import SQLiteStateStore
        db = SQLiteStateStore(db_path=self.db_path)

        if not proposal_record and proposal_id:
            proposal_record = db.get_proposal(proposal_id)

        if not proposal_record:
            return 404, {"error": f"Proposal '{proposal_id}' not found"}

        cmd = proposal_record.get("command", "")
        cwd = proposal_record.get("cwd") or os.getcwd()
        pid = proposal_record.get("id", proposal_id or "p_unknown")

        from nlagent.daemon.commit import commit
        from nlagent.safety.effects import analyse, preview, tier_from_effect
        from nlagent.safety.quarantine import QuarantineLimitExceededError

        session_id = body.get("session_id")
        shell = body.get("shell")
        session = self.get_session(session_id, cwd)
        allow_privileged = bool(body.get("allow_privileged", False))
        no_quarantine = bool(body.get("no_quarantine", False))
        confirmed_large = bool(body.get("confirmed_large", False))

        if body.get("emit_shell"):
            eff = analyse(cmd, cwd=cwd)
            tier = tier_from_effect(eff)

            if tier == "privileged" and not allow_privileged:
                db.update_proposal_status(pid, status="blocked", exit_code=126)
                return 403, {"error": "Command targets protected system paths", "status": "blocked", "exit_code": 126}

            quarantine_id = None
            if not no_quarantine and (eff.deletes or eff.overwrites):
                from nlagent.safety.quarantine import quarantine_effect
                try:
                    q_manifest = quarantine_effect(
                        effect=eff,
                        request_id=pid,
                        command=cmd,
                        confirmed_large=confirmed_large,
                    )
                    if q_manifest:
                        quarantine_id = pid
                except QuarantineLimitExceededError as qe:
                    db.update_proposal_status(pid, status="blocked", exit_code=126)
                    return 413, {"error": str(qe), "status": "blocked", "exit_code": 126}

            db.update_proposal_status(
                proposal_id=pid,
                status="committed",
                exit_code=0,
                quarantine_id=quarantine_id,
            )
            return 200, {
                "id": pid,
                "status": "committed",
                "exit_code": 0,
                "command": cmd,
                "quarantine_id": quarantine_id,
            }

        # Normal commit execution
        output, exit_code, fresh_effect = commit(
            proposal=proposal_record,
            session_state=session,
            allow_privileged=allow_privileged,
            enable_quarantine=not no_quarantine,
            confirmed_large_quarantine=confirmed_large,
            shell=shell,
        )

        q_id = proposal_record.get("quarantine_id") or (pid if fresh_effect.deletes or fresh_effect.overwrites else None)
        status = "committed" if exit_code == 0 else "failed" if exit_code != 126 else "blocked"

        db.update_proposal_status(
            proposal_id=pid,
            status=status,
            exit_code=exit_code,
            output=output,
            quarantine_id=q_id,
        )

        return 200, {
            "id": pid,
            "status": status,
            "exit_code": exit_code,
            "output": output,
            "quarantine_id": q_id,
            "cwd": session.cwd,
        }

    def handle_undo(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST /undo - Rollback quarantined filesystem operations."""
        target_id = body.get("id")
        on_clash = body.get("on_clash", "error")

        from nlagent.cli.db import SQLiteStateStore
        db = SQLiteStateStore(db_path=self.db_path)

        if not target_id:
            latest = db.get_latest_proposal(status="committed")
            if latest and latest.get("quarantine_id"):
                target_id = latest["quarantine_id"]
            elif latest:
                target_id = latest["id"]
            else:
                return 404, {"error": "No executed proposal found to undo"}

        from nlagent.safety.quarantine import (
            NameClashError,
            QuarantineNotFoundError,
            undo as quarantine_undo,
        )

        try:
            undo_res = quarantine_undo(request_id=target_id, on_clash=on_clash)
        except NameClashError as ce:
            return 409, {"error": f"Undo Clash Error: {ce}"}
        except QuarantineNotFoundError as qe:
            return 404, {"error": str(qe)}
        except Exception as exc:
            return 500, {"error": f"Undo failed: {exc}"}

        db.update_proposal_status(proposal_id=target_id, status="undone", output=undo_res.message)

        return 200, {
            "id": target_id,
            "success": undo_res.success,
            "message": undo_res.message,
            "restored_paths": [str(p) for p in undo_res.restored_paths],
            "failed_paths": [str(p) for p in undo_res.failed_paths],
        }

    def handle_models(self) -> tuple[int, dict[str, Any]]:
        """GET /models - List available models and manifest status."""
        try:
            from nlagent.runtime.models import list_models_status
            statuses = list_models_status()
            models_data = []
            for s in statuses:
                models_data.append({
                    "name": s.entry.name,
                    "quant": s.entry.quant,
                    "status": s.status,
                    "size": s.entry.size,
                    "downloaded_bytes": s.downloaded_bytes,
                    "progress_percent": s.progress_percent,
                    "has_sufficient_ram": s.has_sufficient_ram,
                    "min_ram_gb": s.entry.min_ram_gb,
                    "local_path": str(s.local_path) if s.local_path else None,
                    "canonical_filename": s.entry.canonical_filename,
                })
            return 200, {"models": models_data}
        except Exception as e:
            return 500, {"error": f"Failed to list models: {e}"}

    def handle_shutdown(self) -> tuple[int, dict[str, Any]]:
        """POST /shutdown - Stop daemon gracefully."""
        if self.shutdown_callback:
            # Trigger callback
            self.shutdown_callback()
        return 200, {"status": "shutting_down"}

    def dispatch(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body_bytes: bytes | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        """
        Main HTTP request router. Returns (status_code, response_headers, response_dict).
        """
        clean_path = path.split("?")[0].rstrip("/")
        if not clean_path:
            clean_path = "/"

        # Parse JSON body if present
        body: dict[str, Any] = {}
        if body_bytes and len(body_bytes.strip()) > 0:
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                return 400, {"Content-Type": "application/json"}, {"error": "Malformed JSON body"}

        # 1. Unauthenticated endpoints
        if clean_path in ("/health", ""):
            status, res = self.handle_health()
            return status, {"Content-Type": "application/json"}, res

        # 2. Authenticated endpoints - check Bearer token
        ok, auth_code, auth_msg = self.verify_auth(headers)
        if not ok:
            return auth_code, {"Content-Type": "application/json"}, {"error": auth_msg}

        # 3. Route matching
        if method == "POST" and clean_path == "/propose":
            status, res = self.handle_propose(body)
        elif method == "POST" and clean_path == "/commit":
            status, res = self.handle_commit(body)
        elif method == "POST" and clean_path == "/undo":
            status, res = self.handle_undo(body)
        elif method == "GET" and clean_path == "/models":
            status, res = self.handle_models()
        elif method == "POST" and clean_path == "/shutdown":
            status, res = self.handle_shutdown()
        else:
            return 404, {"Content-Type": "application/json"}, {"error": f"Path not found: {method} {clean_path}"}

        return status, {"Content-Type": "application/json"}, res


class ServiceHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    """Bridge between Python HTTP server socket and ServiceAPI dispatcher."""

    api: ServiceAPI  # Attached to server or class

    def do_GET(self) -> None:
        self._handle_request("GET")

    def do_POST(self) -> None:
        self._handle_request("POST")

    def _handle_request(self, method: str) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else None

        api_instance = getattr(self.server, "api", None) or getattr(self, "api", None)
        if not api_instance:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": "ServiceAPI not initialized"}')
            return

        status, resp_headers, resp_data = api_instance.dispatch(
            method=method,
            path=self.path,
            headers=dict(self.headers.items()),
            body_bytes=body_bytes,
        )

        resp_bytes = json.dumps(resp_data).encode("utf-8")
        self.send_response(status)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def log_message(self, format: str, *args: Any) -> None:
        pass

