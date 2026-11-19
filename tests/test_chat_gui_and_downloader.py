"""
tests/test_chat_gui_and_downloader.py - Unit & Offscreen Qt Tests for Sprint 3 Desktop App.

Verifies:
1. Download cancellation + partial resume in `nlagent.runtime.models`.
2. Priority resolution (`resolve_best_model_path`) preferring recommended/fine-tuned GGUFs.
3. Safe runtime zip extraction (`install_runtime_zip`) + Zip-Slip rejection.
4. Multi-turn SessionState persistence, shell override, and clarification handling in `ServiceAPI`/`ServiceClient`.
5. Offscreen Qt tests for `ChatMainWindow`, `ProposalCard` safe defaults, `ModelManagerWidget`, and `needs_first_run_setup`.
"""

from __future__ import annotations

import hashlib
import os
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from nlagent.daemon.propose import get_default_env
from nlagent.runtime.models import (
    DownloadCancelledError,
    ModelEntry,
    download_model,
    install_runtime_zip,
    resolve_best_model_path,
    save_manifest,
)
from nlagent.service.api import ServiceAPI
from nlagent.ui.app import needs_first_run_setup
from nlagent.ui.chat_window import ChatMainWindow, ProposalCard
from nlagent.ui.client import MockServiceCoreClient, normalize_commit_result
from nlagent.ui.model_manager import ModelManagerWidget
from nlagent.ui.wizard import ModelPage
from tests.test_models import MockRangeServer


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _process_until(qapp_inst, predicate, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp_inst.processEvents()
        if predicate():
            return
        time.sleep(0.01)


# -----------------------------------------------------------------------------
# 1. Runtime & Model Store Extensions
# -----------------------------------------------------------------------------


def test_download_cancel_and_resume(tmp_path: Path):
    payload = b"ABCD" * 40000  # 160 KB
    sha = hashlib.sha256(payload).hexdigest()
    entry = ModelEntry(
        name="nemotron-test",
        quant="Q4_K_M",
        size=len(payload),
        sha256=sha,
        min_ram_gb=4.0,
        url="https://example.invalid/test.gguf",
        filename="test.gguf",
    )
    server = MockRangeServer(payload=payload)

    chunks_seen = 0

    def cancel_after_one() -> bool:
        return chunks_seen >= 1

    def on_prog(_dl: int, _tot: int | None) -> None:
        nonlocal chunks_seen
        chunks_seen += 1

    with pytest.raises(DownloadCancelledError):
        download_model(
            entry=entry,
            dest_dir=tmp_path,
            chunk_size=32768,
            progress_callback=on_prog,
            opener=server.opener,
            cancel_check=cancel_after_one,
        )

    part_file = tmp_path / "test.gguf.part"
    assert part_file.is_file()
    assert 0 < part_file.stat().st_size < len(payload)

    final = download_model(entry=entry, dest_dir=tmp_path, chunk_size=32768, opener=server.opener)
    assert final.is_file()
    assert final.read_bytes() == payload
    assert not part_file.exists()


def test_resolve_best_model_path_prefers_recommended(tmp_path: Path):
    base_file = tmp_path / "base.gguf"
    tuned_file = tmp_path / "tuned.gguf"
    base_file.write_bytes(b"base")
    tuned_file.write_bytes(b"tuned")

    manifest_path = tmp_path / "manifest.json"
    entries = [
        ModelEntry(
            name="base",
            quant="Q4_K_M",
            size=4,
            sha256=hashlib.sha256(b"base").hexdigest(),
            min_ram_gb=4.0,
            filename="base.gguf",
            recommended=False,
            is_finetuned=False,
        ),
        ModelEntry(
            name="tuned",
            quant="Q4_K_M",
            size=5,
            sha256=hashlib.sha256(b"tuned").hexdigest(),
            min_ram_gb=4.0,
            filename="tuned.gguf",
            recommended=True,
            is_finetuned=True,
        ),
    ]
    save_manifest(entries, manifest_path)

    best = resolve_best_model_path(manifest_path=manifest_path, models_dir=tmp_path)
    assert best == tuned_file


def test_install_runtime_zip_and_block_zip_slip(tmp_path: Path):
    zpath = tmp_path / "runtime.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("nested/bin/llama-server.exe", b"MZ-FAKE-BINARY")
        zf.writestr("nested/bin/ggml.dll", b"DLL")

    dest = tmp_path / "installed_bin"
    exe = install_runtime_zip(zpath, dest_dir=dest)
    assert exe.name == "llama-server.exe"
    assert (dest / "llama-server.exe").read_bytes() == b"MZ-FAKE-BINARY"
    assert (dest / "ggml.dll").read_bytes() == b"DLL"

    bad_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../../evil.dll", b"BAD")
    with pytest.raises(ValueError, match="Unsafe zip member path"):
        install_runtime_zip(bad_zip, dest_dir=dest)


# -----------------------------------------------------------------------------
# 2. Shell Override, Session Persistence & Payload Normalization
# -----------------------------------------------------------------------------


def test_get_default_env_honors_cmd_and_powershell():
    if os.name == "nt":
        env_cmd = get_default_env(shell="cmd")
        assert env_cmd["shell"] == "cmd"
        assert env_cmd["path_style"] == "windows"
        env_ps = get_default_env(shell="powershell")
        assert env_ps["shell"] == "powershell"


def test_service_api_retains_session_and_handles_clarification(tmp_path: Path, monkeypatch):
    api = ServiceAPI(token="tok", db_path=tmp_path / "state.db")

    s1 = api.get_session("s_chat", str(tmp_path))
    s2 = api.get_session("s_chat", str(tmp_path))
    assert s1 is s2

    monkeypatch.setenv(
        "NLAGENT_MOCK_PROPOSAL",
        '{"command": null, "targets": [], "clarifying_question": "Which folder?", "explanation": "ambig"}',
    )
    code, res = api.handle_propose(
        {"request": "clean up everything", "cwd": str(tmp_path), "session_id": "s_chat"}
    )
    assert code == 200
    assert res["command"] == ""
    assert res["clarifying_question"] == "Which folder?"
    assert res["tier"] == "ambiguous"


def test_normalize_commit_result_variants():
    t = normalize_commit_result((0, {"status": "committed", "output": "ok", "quarantine_id": "q1"}))
    assert t["exit_code"] == 0
    assert t["quarantine_id"] == "q1"

    m = normalize_commit_result({"status": "success", "returncode": 0, "stdout": "hi", "stderr": ""})
    assert m["exit_code"] == 0
    assert m["output"] == "hi"


# -----------------------------------------------------------------------------
# 3. Offscreen Desktop Chat Window & Model Manager Tests
# -----------------------------------------------------------------------------


def test_chat_window_read_only_and_destructive_safe_defaults(qapp, tmp_path: Path):
    client = MockServiceCoreClient()
    win = ChatMainWindow(client=client, auto_start_runtime=False)
    win.current_cwd = str(tmp_path)
    win.cwd_input.setText(str(tmp_path))

    # 1. Read-only proposal
    win.prompt_input.setText("list files in workspace")
    win.submit_prompt()
    _process_until(qapp, lambda: len(win._cards) == 1)
    card1: ProposalCard = win._cards[0]
    assert card1.badge.text().strip() == "READ ONLY"
    assert card1.btn_approve.isDefault()

    # Execute read-only proposal
    card1._on_approve()
    _process_until(qapp, lambda: card1._executed)
    assert card1.output_box.isVisibleTo(card1)
    assert "Command executed successfully." in card1.output_box.toPlainText()
    assert len(client.committed) == 1

    # 2. Destructive proposal -> Cancel must be default focus!
    win.prompt_input.setText("delete all temp log files")
    win.submit_prompt()
    _process_until(qapp, lambda: len(win._cards) == 2)
    card2: ProposalCard = win._cards[1]
    assert card2.badge.text().strip() == "DESTRUCTIVE"
    assert card2.btn_cancel.isDefault(), "Cancel must be default for destructive actions"

    card2._on_approve()
    _process_until(qapp, lambda: card2._executed)
    assert card2.btn_undo_card.isVisibleTo(card2)

    with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
        card2._on_undo()
        assert client.undone_count == 1
        assert mock_info.called

    win.close()


def test_chat_window_clarification_card(qapp, tmp_path: Path):
    client = MockServiceCoreClient()
    win = ChatMainWindow(client=client, auto_start_runtime=False)
    win.current_cwd = str(tmp_path)

    win.prompt_input.setText("clean up")
    win.submit_prompt()
    _process_until(qapp, lambda: len(win._cards) == 1)
    card = win._cards[0]
    assert card.clarify_banner.isVisibleTo(card)
    assert not card.btn_approve.isVisibleTo(card)
    assert card.badge.text().strip() == "AMBIGUOUS"
    win.close()


def test_model_manager_widget_and_wizard_model_page(qapp, tmp_path: Path):
    store = tmp_path / "models"
    store.mkdir()
    (store / "NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf.part").write_bytes(b"123456")

    widget = ModelManagerWidget(models_dir=store)
    assert widget.models_list.count() >= 2
    first_item = widget.models_list.item(0).text()
    assert "PARTIAL" in first_item

    page = ModelPage()
    page.initializePage()
    assert page.lbl_model_name.text() != ""


def test_needs_first_run_setup_detects_missing_binary_or_model():
    with (
        patch("nlagent.ui.app.requests.get", side_effect=OSError("down")),
        patch("nlagent.ui.app.RuntimeManager.find_llama_server_binary", return_value=None),
        patch("nlagent.ui.app.resolve_best_model_path", return_value=None),
    ):
        assert needs_first_run_setup() is True

    with (
        patch("nlagent.ui.app.requests.get", side_effect=OSError("down")),
        patch("nlagent.ui.app.RuntimeManager.find_llama_server_binary", return_value="C:/bin/llama-server.exe"),
        patch("nlagent.ui.app.resolve_best_model_path", return_value=Path("models/model.gguf")),
    ):
        assert needs_first_run_setup() is False

