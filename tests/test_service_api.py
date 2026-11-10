"""
tests/test_service_api.py - Comprehensive Integration Tests for Core Service Daemon & API.

Verifies:
1. ServiceInfo serialization and state directory persistence.
2. ServiceDaemon initialization, ephemeral token generation, and lifecycle.
3. Bearer token authentication (401 on missing/malformed, 403 on invalid, 200 on valid).
4. All service endpoints: /health, /propose, /commit, /undo, /models, /shutdown.
5. In-process fallback execution when daemon is offline or unreachable.
6. CLI service subcommand interactions.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from nlagent.cli.db import SQLiteStateStore
from nlagent.service.api import ServiceAPI
from nlagent.service.core import (
    ServiceClient,
    ServiceDaemon,
    ServiceInfo,
    get_service_file,
    get_state_dir,
    handle_service_command,
    load_service_info,
    remove_service_file,
    save_service_info,
)


@pytest.fixture
def tmp_state_dir(tmp_path: Path):
    """Provide an isolated temporary state directory."""
    state_dir = tmp_path / "nlagent_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


@pytest.fixture
def test_daemon(tmp_state_dir: Path):
    """Provide an active ServiceDaemon configured with in-process test dispatch."""
    db_path = tmp_state_dir / "test_state.db"
    daemon = ServiceDaemon(
        port=59999,
        token="test-secret-bearer-token-12345",
        state_dir=tmp_state_dir,
        db_path=db_path,
    )
    daemon.start()
    yield daemon
    daemon.stop()


# -----------------------------------------------------------------------------
# 1. ServiceInfo & Registry File Persistence
# -----------------------------------------------------------------------------

def test_service_info_persistence(tmp_state_dir: Path):
    info = ServiceInfo(
        port=8088,
        token="test_tok_abcdef",
        pid=12345,
        url="http://127.0.0.1:8088",
        started_at="2026-11-09T12:00:00Z",
    )
    saved_path = save_service_info(info, state_dir=tmp_state_dir)
    assert saved_path.is_file()

    loaded = load_service_info(state_dir=tmp_state_dir)
    assert loaded is not None
    assert loaded.port == 8088
    assert loaded.token == "test_tok_abcdef"
    assert loaded.pid == 12345
    assert loaded.url == "http://127.0.0.1:8088"

    remove_service_file(state_dir=tmp_state_dir)
    assert load_service_info(state_dir=tmp_state_dir) is None


# -----------------------------------------------------------------------------
# 2. Bearer Token Authentication
# -----------------------------------------------------------------------------

def test_auth_verification(test_daemon: ServiceDaemon):
    api = test_daemon.api

    # Missing authorization header
    ok, code, msg = api.verify_auth({})
    assert not ok
    assert code == 401
    assert "Missing" in msg

    # Malformed authorization header
    ok, code, msg = api.verify_auth({"Authorization": "Basic dXNlcjpwYXNz"})
    assert not ok
    assert code == 401
    assert "Expected 'Bearer <token>'" in msg

    # Invalid token
    ok, code, msg = api.verify_auth({"Authorization": "Bearer wrong-token"})
    assert not ok
    assert code == 403
    assert "Invalid bearer token" in msg

    # Valid token
    ok, code, msg = api.verify_auth({"Authorization": "Bearer test-secret-bearer-token-12345"})
    assert ok
    assert code == 200


# -----------------------------------------------------------------------------
# 3. GET /health Endpoint
# -----------------------------------------------------------------------------

def test_health_endpoint(test_daemon: ServiceDaemon):
    opener = test_daemon.get_test_opener()
    client = ServiceClient(state_dir=test_daemon.state_dir, opener=opener)

    assert client.is_service_running()

    code, data = client._call("GET", "/health")
    assert code == 200
    assert data["status"] == "ok"
    assert "pid" in data
    assert "uptime_seconds" in data
    assert data["version"] == "0.1.0"


# -----------------------------------------------------------------------------
# 4. POST /propose Endpoint
# -----------------------------------------------------------------------------

def test_propose_endpoint(test_daemon: ServiceDaemon, tmp_path: Path):
    opener = test_daemon.get_test_opener()
    client = ServiceClient(state_dir=test_daemon.state_dir, opener=opener)

    # 1. Missing request field -> 400
    code, err_data = client._call("POST", "/propose", {"cwd": str(tmp_path)})
    assert code == 400
    assert "Missing required field 'request'" in err_data.get("error", "")

    # 2. Propose with mock command
    res = client.propose(
        request="list files",
        cwd=str(tmp_path),
        mock="ls -la",
    )
    assert res is not None
    assert res.get("command") == "ls -la"
    assert res.get("tier") == "read_only"
    assert "id" in res

    # Verify persisted in test SQLite state store
    db = SQLiteStateStore(db_path=test_daemon.db_path)
    rec = db.get_proposal(res["id"])
    assert rec is not None
    assert rec["command"] == "ls -la"
    assert rec["status"] == "proposed"


# -----------------------------------------------------------------------------
# 5. POST /commit Endpoint
# -----------------------------------------------------------------------------

def test_commit_endpoint(test_daemon: ServiceDaemon, tmp_path: Path):
    opener = test_daemon.get_test_opener()
    client = ServiceClient(state_dir=test_daemon.state_dir, opener=opener)

    # Commit non-existent proposal -> 404
    code, res = client._call("POST", "/commit", {"id": "non_existent_proposal_999"})
    assert code == 404
    assert "not found" in res.get("error", "")

    # Create a proposal first
    prop = client.propose(
        request="show directory contents",
        cwd=str(tmp_path),
        mock="echo service_test_output",
    )
    prop_id = prop["id"]

    # Commit with emit-shell mode
    exit_code, commit_res = client.commit(proposal_id=prop_id, emit_shell=True)
    assert exit_code == 0
    assert commit_res["status"] == "committed"
    assert commit_res["command"] == "echo service_test_output"

    # Verify status in database
    db = SQLiteStateStore(db_path=test_daemon.db_path)
    updated = db.get_proposal(prop_id)
    assert updated is not None
    assert updated["status"] == "committed"


# -----------------------------------------------------------------------------
# 6. GET /models Endpoint
# -----------------------------------------------------------------------------

def test_models_endpoint(test_daemon: ServiceDaemon):
    opener = test_daemon.get_test_opener()
    client = ServiceClient(state_dir=test_daemon.state_dir, opener=opener)

    models_list = client.models()
    assert isinstance(models_list, list)
    assert len(models_list) >= 2
    names = [m["name"] for m in models_list]
    assert "nemotron-3-nano-4b" in names
    assert "nemotron-3-nano-4b-dense" in names


# -----------------------------------------------------------------------------
# 7. POST /undo Endpoint
# -----------------------------------------------------------------------------

def test_undo_endpoint(test_daemon: ServiceDaemon):
    opener = test_daemon.get_test_opener()
    client = ServiceClient(state_dir=test_daemon.state_dir, opener=opener)

    # Undo when no committed proposals exist
    undo_res = client.undo(target_id="p_none_existing")
    assert not undo_res.get("success")


# -----------------------------------------------------------------------------
# 8. POST /shutdown Endpoint
# -----------------------------------------------------------------------------

def test_shutdown_endpoint(test_daemon: ServiceDaemon):
    opener = test_daemon.get_test_opener()
    client = ServiceClient(state_dir=test_daemon.state_dir, opener=opener)

    shutdown_called = False

    def on_shutdown():
        nonlocal shutdown_called
        shutdown_called = True

    test_daemon.api.shutdown_callback = on_shutdown

    code, res = client._call("POST", "/shutdown")
    assert code == 200
    assert res.get("status") == "shutting_down"
    assert shutdown_called


# -----------------------------------------------------------------------------
# 9. In-Process Fallback When Service is Offline
# -----------------------------------------------------------------------------

def test_in_process_fallback_when_offline(tmp_path: Path):
    """
    Ensure ServiceClient seamlessly executes proposals, commits, and models
    in-process when the daemon is not running.
    """
    empty_state_dir = tmp_path / "empty_state"
    empty_state_dir.mkdir(parents=True, exist_ok=True)

    client = ServiceClient(state_dir=empty_state_dir)
    assert not client.is_service_running()

    # 1. Propose fallback
    prop = client.propose(
        request="show directory contents",
        cwd=str(tmp_path),
        mock="echo fallback_output",
    )
    assert prop is not None
    assert prop.get("command") == "echo fallback_output"
    assert "targets" in prop

    # 2. Models fallback
    models = client.models()
    assert isinstance(models, list)
    assert len(models) >= 2

    # 3. Undo fallback with invalid target
    undo_res = client.undo(target_id="non_existent_proposal")
    assert not undo_res.get("success")


# -----------------------------------------------------------------------------
# 10. CLI Command Handlers
# -----------------------------------------------------------------------------

def test_service_cli_status_and_stop_offline(tmp_state_dir: Path, capsys: pytest.CaptureFixture[str]):
    # Status when offline
    exit_code = handle_service_command("status", state_dir=tmp_state_dir)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[OFFLINE]" in captured.out

    # Stop when offline
    exit_code = handle_service_command("stop", state_dir=tmp_state_dir)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "already stopped" in captured.out
