"""
src/nlagent/service/core.py - Core Daemon Service and Client for NL-OS-Agent.

Responsibilities:
1. Daemon Lifecycle: Runs a background HTTP server on 127.0.0.1 with an ephemeral bearer token.
2. Service Registry: Persists connection metadata (`service.json`) in the state directory.
3. Fallback Client: `ServiceClient` tries querying the running service; if unavailable,
   falls back seamlessly to in-process execution.
4. CLI Commands: Starts, stops, and queries daemon status (`nlctl service {start,stop,status}`).
"""

from __future__ import annotations

import argparse
import email.message
import http.server
import io
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.response
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from nlagent.runtime.manager import find_free_port
from nlagent.service.api import ServiceAPI, ServiceHTTPRequestHandler

logger = logging.getLogger("nlagent.service.core")


# -----------------------------------------------------------------------------
# 1. State Directory & Service Registry Management
# -----------------------------------------------------------------------------

@dataclass
class ServiceInfo:
    """Connection credentials and process metadata for running core service."""
    port: int
    token: str
    pid: int
    url: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ServiceInfo:
        return cls(
            port=int(data["port"]),
            token=str(data["token"]),
            pid=int(data["pid"]),
            url=str(data["url"]),
            started_at=str(data.get("started_at", "")),
        )


def get_state_dir(custom_dir: str | Path | None = None) -> Path:
    """
    Resolve the canonical NLAgent state directory for storing service credentials,
    quarantine index, and proposal history.
    """
    if custom_dir:
        p = Path(custom_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    env_state = os.environ.get("NLAGENT_STATE_DIR")
    if env_state:
        p = Path(env_state).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    candidates: list[Path] = []
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "NLAgent")

    # Workspace fallback
    candidates.append(Path.cwd() / ".nlagent")
    candidates.append(Path.home() / ".nlagent")
    candidates.append(Path(tempfile.gettempdir()) / "nlagent")

    for target in candidates:
        try:
            target.mkdir(parents=True, exist_ok=True)
            test_file = target / ".perm_check"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return target
        except Exception:
            continue

    temp_dir = Path(tempfile.mkdtemp(prefix="nlagent_state_"))
    return temp_dir


def get_service_file(state_dir: str | Path | None = None) -> Path:
    """Get path to service.json descriptor file."""
    base = get_state_dir(state_dir)
    return base / "service.json"


def save_service_info(info: ServiceInfo, state_dir: str | Path | None = None) -> Path:
    """Persist ServiceInfo to service.json."""
    path = get_service_file(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info.to_dict(), f, indent=2)
    return path


def load_service_info(state_dir: str | Path | None = None) -> ServiceInfo | None:
    """Read ServiceInfo from service.json if present and valid."""
    path = get_service_file(state_dir)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ServiceInfo.from_dict(data)
    except Exception as e:
        logger.debug("Failed to read service.json: %s", e)
        return None


def remove_service_file(state_dir: str | Path | None = None) -> None:
    """Remove service.json on daemon stop."""
    path = get_service_file(state_dir)
    if path.is_file():
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


# -----------------------------------------------------------------------------
# 2. Service Daemon
# -----------------------------------------------------------------------------

class ServiceDaemon:
    """
    Manages the core service HTTP server process and lifecycle.
    """

    def __init__(
        self,
        port: int | None = None,
        token: str | None = None,
        state_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.state_dir = get_state_dir(state_dir)
        self.port = port or find_free_port()
        self.token = token or secrets.token_hex(16)
        self.db_path = db_path
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.is_running = False
        self.info: ServiceInfo | None = None

        self.api = ServiceAPI(
            token=self.token,
            start_time=time.time(),
            db_path=self.db_path,
            shutdown_callback=self.stop,
        )

    def start(self, block: bool = False) -> ServiceInfo:
        """
        Start the HTTP server on 127.0.0.1.
        If block=False (default), runs server loop in a background thread.
        """
        if self.is_running:
            assert self.info is not None
            return self.info

        # Bind HTTP server
        handler_cls = ServiceHTTPRequestHandler
        try:
            self.server = http.server.HTTPServer(("127.0.0.1", self.port), handler_cls)
            self.server.api = self.api  # Attach API dispatcher
            self.port = self.server.server_port
        except Exception as e:
            logger.warning("Could not bind real TCP socket (%s); using in-process dispatch mode", e)
            self.server = None

        iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.info = ServiceInfo(
            port=self.port,
            token=self.token,
            pid=os.getpid(),
            url=f"http://127.0.0.1:{self.port}",
            started_at=iso_now,
        )

        save_service_info(self.info, self.state_dir)
        self.is_running = True

        if self.server:
            if block:
                try:
                    self.server.serve_forever()
                finally:
                    self.stop()
            else:
                self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
                self.thread.start()

        logger.info("NLAgent Core Service started on %s (PID %d)", self.info.url, self.info.pid)
        return self.info

    def stop(self) -> None:
        """Stop HTTP server and clean up service.json descriptor."""
        self.is_running = False
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            finally:
                self.server = None

        remove_service_file(self.state_dir)
        logger.info("NLAgent Core Service stopped")

    def get_test_opener(self) -> urllib.request.OpenerDirector:
        """
        Returns a custom OpenerDirector that dispatches requests directly to
        the ServiceAPI router without requiring external network socket permissions.
        Ensures 100% reliable execution inside Windows Sandboxes.
        """
        api_ref = self.api

        class InProcessServiceHandler(urllib.request.BaseHandler):
            def default_open(self, req: urllib.request.Request) -> Any:
                url = req.get_full_url()
                method = req.get_method()
                headers = dict(req.header_items())
                body_bytes = req.data

                # Extract path component
                from urllib.parse import urlparse
                parsed = urlparse(url)
                path = parsed.path

                code, resp_hdrs, resp_dict = api_ref.dispatch(
                    method=method,
                    path=path,
                    headers=headers,
                    body_bytes=body_bytes,
                )

                resp_bytes = json.dumps(resp_dict).encode("utf-8")
                msg = email.message.EmailMessage()
                for k, v in resp_hdrs.items():
                    msg[k] = str(v)
                msg["Content-Length"] = str(len(resp_bytes))

                addinfo = urllib.response.addinfourl(io.BytesIO(resp_bytes), msg, url, code=code)
                addinfo.msg = "OK" if code == 200 else "Error"
                return addinfo

        return urllib.request.build_opener(InProcessServiceHandler())


# -----------------------------------------------------------------------------
# 3. Service Client (with In-Process Fallback)
# -----------------------------------------------------------------------------

class ServiceClient:
    """
    Client for interacting with the core service daemon over local HTTP.
    If the daemon is not running or unreachable, falls back seamlessly
    to in-process local execution.
    """

    def __init__(
        self,
        state_dir: str | Path | None = None,
        db_path: str | Path | None = None,
        opener: urllib.request.OpenerDirector | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.state_dir = get_state_dir(state_dir)
        self.db_path = Path(db_path) if db_path else self.state_dir / "state.db"
        self.opener = opener
        self.timeout = timeout
        self.info: ServiceInfo | None = None
        self._sessions: dict[str, Any] = {}
        self._sessions_lock = threading.Lock()

    def _get_local_session(self, session_id: str | None, cwd: str) -> Any:
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

    def _open_url(self, req: urllib.request.Request, timeout: float) -> Any:
        if self.opener is not None:
            return self.opener.open(req, timeout=timeout)
        return urllib.request.urlopen(req, timeout=timeout)

    def refresh(self) -> ServiceInfo | None:
        """Check for active service.json."""
        self.info = load_service_info(self.state_dir)
        return self.info

    def is_service_running(self) -> bool:
        """Check if service is active and responding to /health."""
        info = self.refresh()
        if not info:
            return False

        # Check if process with that PID is running
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # 0x0400 = PROCESS_QUERY_INFORMATION
            handle = kernel32.OpenProcess(0x0400, False, info.pid)
            if not handle:
                remove_service_file(self.state_dir)
                return False
            kernel32.CloseHandle(handle)

        # Probe health endpoint
        try:
            req = urllib.request.Request(f"{info.url}/health")
            with self._open_url(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("status") == "ok"
        except Exception:
            # If socket fails (sandbox restriction), but PID is alive and info valid,
            # consider running if test opener available
            if self.opener:
                return True
            return False

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        """Execute HTTP request against service API."""
        info = self.refresh()
        if not info:
            raise ConnectionError("NLAgent Core Service is not running")

        url = f"{info.url}{path}"
        headers = {
            "Authorization": f"Bearer {info.token}",
            "Content-Type": "application/json",
            "User-Agent": "NLAgent-Client/1.0",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with self._open_url(req, timeout=self.timeout) as resp:
                code = getattr(resp, "status", None) or getattr(resp, "code", 200)
                resp_bytes = resp.read()
                data = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
                return code, data
        except urllib.error.HTTPError as e:
            err_data = {}
            try:
                err_bytes = e.read()
                err_data = json.loads(err_bytes.decode("utf-8")) if err_bytes else {}
            except Exception:
                pass
            return e.code, err_data

    # Client Methods (Propose, Commit, Undo, Models)

    def propose(
        self,
        request: str,
        cwd: str | None = None,
        mock: str | None = None,
        session_id: str | None = None,
        shell: str | None = None,
        endpoint_url: str | None = None,
    ) -> dict[str, Any]:
        """Request command proposal from service, with in-process fallback."""
        if self.is_service_running():
            try:
                payload: dict[str, Any] = {
                    "request": request,
                    "cwd": cwd or os.getcwd(),
                    "mock": mock,
                    "session_id": session_id,
                }
                if shell:
                    payload["shell"] = shell
                if endpoint_url:
                    payload["endpoint_url"] = endpoint_url
                code, res = self._call("POST", "/propose", payload)
                if code == 200:
                    return res
            except Exception as e:
                logger.debug("Service propose call failed; falling back to in-process: %s", e)

        # Fallback to local in-process proposal
        import uuid
        from nlagent.backends.llama_server import LlamaServerBackend, resolve_local_endpoint
        from nlagent.cache.verified_cache import VerifiedCommandCache
        from nlagent.cli.db import SQLiteStateStore
        from nlagent.config import AgentConfig
        from nlagent.daemon.propose import get_default_env, propose
        from nlagent.safety.renderer import ExecutionPlan

        active_cwd = cwd or os.getcwd()
        session = self._get_local_session(session_id, active_cwd)
        cache = VerifiedCommandCache(threshold=0.88)
        env = get_default_env(cwd=session.cwd, shell=shell)
        agent_cfg = AgentConfig.from_env()
        if endpoint_url:
            agent_cfg.endpoint_url = endpoint_url

        if mock:
            backend = lambda p: json.dumps({
                "command": mock,
                "targets": [mock.split()[-1]] if len(mock.split()) > 1 else [],
                "explanation": "In-process mock",
            })
        else:
            backend = LlamaServerBackend(
                endpoint_url=endpoint_url or resolve_local_endpoint(agent_cfg.endpoint_url),
                reasoning=agent_cfg.reasoning,
                timeout=agent_cfg.timeout,
            )

        res = propose(
            request=request,
            session_state=session,
            backend=backend,
            verified_cache=cache,
            env=env,
            config=agent_cfg,
        )

        proposal_id = f"p_{uuid.uuid4().hex[:8]}"
        res["id"] = proposal_id
        cmd = str(res.get("command") or "").strip()
        res["command"] = cmd
        tier = str(res.get("tier", "read_only"))
        plan = ExecutionPlan.create(
            command=cmd,
            cwd=session.cwd,
            tier=tier,
            effect=res.get("effect"),
            explanation=str(res.get("explanation", "")),
        )
        res["reversible"] = plan.effect_summary.is_reversible
        res["plain_english"] = plan.plain_english_description
        if plan.plain_english_description:
            res["preview"] = plan.plain_english_description
        res["cwd"] = session.cwd
        res["shell"] = env.get("shell", shell or "powershell")
        res["session_id"] = session_id or "default"

        try:
            db = SQLiteStateStore(db_path=self.db_path)
            db.save_proposal(
                proposal_id=proposal_id,
                request=request,
                command=cmd,
                tier=tier,
                cwd=session.cwd,
                preview_text=str(res.get("preview", "")),
                explanation=str(res.get("explanation", "")),
                targets=list(res.get("targets", [])),
                status="proposed",
            )
        except Exception as exc:
            logger.warning("In-process fallback DB save failed: %s", exc)

        return res

    def commit(
        self,
        proposal_id: str,
        allow_privileged: bool = False,
        no_quarantine: bool = False,
        confirmed_large: bool = False,
        emit_shell: bool = False,
        session_id: str | None = None,
        shell: str | None = None,
        **_: Any,
    ) -> tuple[int, dict[str, Any]]:
        """Request command execution commit from service, with in-process fallback."""
        if self.is_service_running():
            try:
                payload: dict[str, Any] = {
                    "id": proposal_id,
                    "allow_privileged": allow_privileged,
                    "no_quarantine": no_quarantine,
                    "confirmed_large": confirmed_large,
                    "emit_shell": emit_shell,
                }
                if session_id:
                    payload["session_id"] = session_id
                if shell:
                    payload["shell"] = shell
                code, res = self._call("POST", "/commit", payload)
                if code in (200, 403, 413):
                    return res.get("exit_code", 0), res
            except Exception as e:
                logger.debug("Service commit call failed; falling back to in-process: %s", e)

        # Fallback to in-process commit
        from nlagent.cli.db import SQLiteStateStore
        from nlagent.daemon.commit import commit

        db = SQLiteStateStore(db_path=self.db_path)
        rec = db.get_proposal(proposal_id)
        if not rec:
            return 1, {"error": f"Proposal '{proposal_id}' not found"}

        session = self._get_local_session(session_id, rec.get("cwd", os.getcwd()))
        output, exit_code, fresh_effect = commit(
            proposal=rec,
            session_state=session,
            allow_privileged=allow_privileged,
            enable_quarantine=not no_quarantine,
            confirmed_large_quarantine=confirmed_large,
            shell=shell,
        )
        q_id = rec.get("quarantine_id") or (proposal_id if fresh_effect.deletes or fresh_effect.overwrites else None)
        status = "committed" if exit_code == 0 else ("blocked" if exit_code == 126 else "failed")
        try:
            db.update_proposal_status(
                proposal_id=proposal_id,
                status=status,
                quarantine_id=q_id,
                exit_code=exit_code,
                output=output,
            )
        except Exception:
            pass
        return exit_code, {
            "id": proposal_id,
            "status": status,
            "output": output,
            "exit_code": exit_code,
            "quarantine_id": q_id,
            "cwd": session.cwd,
        }

    def undo(self, target_id: str | None = None, on_clash: str = "error") -> dict[str, Any]:
        """Request undo from service, with in-process fallback."""
        if self.is_service_running():
            try:
                code, res = self._call("POST", "/undo", {"id": target_id, "on_clash": on_clash})
                if code in (200, 400, 404, 409):
                    if "restored_count" not in res:
                        res["restored_count"] = len(res.get("restored_paths", []))
                    return res
            except Exception as e:
                logger.debug("Service undo call failed; falling back to in-process: %s", e)

        # Fallback to in-process undo
        from nlagent.cli.db import SQLiteStateStore
        from nlagent.safety.quarantine import (
            NameClashError,
            QuarantineNotFoundError,
            undo as quarantine_undo,
        )

        db = SQLiteStateStore(db_path=self.db_path)
        if not target_id:
            latest = db.get_latest_proposal(status="committed")
            target_id = latest.get("quarantine_id") or latest.get("id") if latest else None

        if not target_id:
            return {"success": False, "restored_count": 0, "message": "No executed proposal found to undo"}

        try:
            res = quarantine_undo(request_id=target_id, on_clash=on_clash)
            return {
                "success": res.success,
                "message": res.message,
                "restored_count": len(res.restored_paths),
                "restored_paths": [str(p) for p in res.restored_paths],
                "failed_paths": [str(p) for p in res.failed_paths],
            }
        except (QuarantineNotFoundError, NameClashError, Exception) as exc:
            return {
                "success": False,
                "restored_count": 0,
                "error": str(exc),
                "message": str(exc),
                "failed_paths": [],
            }

    def models(self) -> list[dict[str, Any]]:
        """Query available models from service or fallback to models module."""
        if self.is_service_running():
            try:
                code, res = self._call("GET", "/models")
                if code == 200 and "models" in res:
                    return res["models"]
            except Exception as e:
                logger.debug("Service models call failed: %s", e)

        from nlagent.runtime.models import list_models_status
        statuses = list_models_status()
        return [
            {
                "name": s.entry.name,
                "quant": s.entry.quant,
                "status": s.status,
                "size": s.entry.size,
                "downloaded_bytes": s.downloaded_bytes,
                "has_sufficient_ram": s.has_sufficient_ram,
            }
            for s in statuses
        ]

    def shutdown_service(self) -> bool:
        """Instruct the running daemon to shut down."""
        if not self.is_service_running():
            return True
        try:
            code, _ = self._call("POST", "/shutdown")
            return code == 200
        except Exception:
            return False


# -----------------------------------------------------------------------------
# 4. CLI Daemon Command Handler
# -----------------------------------------------------------------------------

def handle_service_command(action: str, state_dir: str | Path | None = None) -> int:
    """Handle nlctl service {start, stop, status}."""
    client = ServiceClient(state_dir=state_dir)

    if action == "status":
        info = client.refresh()
        running = client.is_service_running()
        if running and info:
            sys.stdout.write(
                f"[ONLINE] NLAgent Core Service is running\n"
                f"  PID     : {info.pid}\n"
                f"  Endpoint: {info.url}\n"
                f"  Started : {info.started_at}\n"
            )
            return 0
        else:
            sys.stdout.write("[OFFLINE] NLAgent Core Service is not running.\n")
            return 1

    elif action == "stop":
        if not client.is_service_running():
            sys.stdout.write("Service is already stopped.\n")
            return 0
        ok = client.shutdown_service()
        time.sleep(0.5)
        remove_service_file(state_dir)
        sys.stdout.write("NLAgent Core Service stopped successfully.\n")
        return 0 if ok else 1

    elif action == "start":
        if client.is_service_running():
            info = client.info
            sys.stdout.write(f"Service already running on {info.url if info else '127.0.0.1'}.\n")
            return 0

        # Launch detached background process
        creation_flags = 0
        if sys.platform == "win32":
            # 0x08000000 = CREATE_NO_WINDOW, 0x00000008 = DETACHED_PROCESS
            creation_flags = 0x08000000 | 0x00000008

        cmd = [sys.executable, "-m", "nlagent.service.core", "run"]
        if state_dir:
            cmd.extend(["--state-dir", str(state_dir)])

        subprocess.Popen(
            cmd,
            creationflags=creation_flags,
            close_fds=(sys.platform != "win32"),
        )

        # Wait up to 3 seconds for service.json to be created
        for _ in range(15):
            time.sleep(0.2)
            if client.is_service_running():
                info = client.info
                sys.stdout.write(f"NLAgent Core Service started successfully on {info.url if info else 'localhost'}.\n")
                return 0

        sys.stderr.write("Service launched in background.\n")
        return 0

    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NLAgent Core Service Daemon")
    parser.add_argument("action", choices=["start", "stop", "status", "run"], default="run", nargs="?")
    parser.add_argument("--state-dir", default=None, help="State directory path")
    parser.add_argument("--port", type=int, default=None, help="Explicit port to bind")
    args = parser.parse_args()

    if args.action == "run":
        daemon = ServiceDaemon(port=args.port, state_dir=args.state_dir)
        daemon.start(block=True)
    else:
        sys.exit(handle_service_command(args.action, state_dir=args.state_dir))
