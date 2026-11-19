"""
src/nlagent/ui/chat_window.py - Multi-Turn Desktop Chat GUI & Safety Card Stream.

Provides:
- ChatMainWindow(QMainWindow): conversational natural-language shell assistant with:
  - Background RuntimeManager (`LlamaServerProcess` hidden via CREATE_NO_WINDOW)
    and embedded `ServiceDaemon` lifecycle management.
  - Non-blocking QThread workers for proposal synthesis, command execution, and startup.
  - Per-turn safety cards with ConsequenceBadge, plain-English AST consequence summary,
    Cancel-focused defaults on destructive/privileged/ambiguous commands, inline
    clarifying questions, collapsible execution output, and one-click Quarantine Undo.
  - Integrated Workspace folder picker, shell selector (`powershell`, `cmd`, `bash`),
    and Model & Runtime Manager dialog launcher.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

import requests
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nlagent.backends.llama_server import get_preferred_model_id
from nlagent.runtime.manager import RuntimeManager
from nlagent.runtime.models import resolve_best_model_path
from nlagent.ui.client import (
    MockServiceCoreClient,
    call_propose,
    get_ui_client,
    normalize_commit_result,
)
from nlagent.ui.model_manager import ModelManagerDialog
from nlagent.ui.preview import ConsequenceBadge
from nlagent.ui.theme import apply_app_theme, set_style_property

logger = logging.getLogger("nlagent.ui.chat_window")


# -----------------------------------------------------------------------------
# Background QThread Workers
# -----------------------------------------------------------------------------


class RuntimeStartupWorker(QThread):
    """Detects an active local LLM endpoint or starts hidden LlamaServerProcess + ServiceDaemon."""

    startup_ready = Signal(str, str, str)  # endpoint_url, status_label, model_display
    startup_failed = Signal(str)

    def __init__(
        self,
        preferred_model_path: str | None = None,
        state_dir: Path | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.preferred_model_path = preferred_model_path
        self.state_dir = state_dir
        self.runtime_manager: RuntimeManager | None = None

    def run(self) -> None:
        # 1. Check already-running llama-server (:8080) or LM Studio (:1234)
        for port, label in ((8080, "External llama-server"), (1234, "LM Studio")):
            try:
                health_url = f"http://127.0.0.1:{port}/health" if port == 8080 else f"http://127.0.0.1:{port}/v1/models"
                resp = requests.get(health_url, timeout=0.6)
                if resp.status_code == 200:
                    endpoint = f"http://127.0.0.1:{port}/v1/chat/completions"
                    model_id = get_preferred_model_id(f"http://127.0.0.1:{port}/v1/models") or "Nemotron-3-Nano-4B"
                    self.startup_ready.emit(endpoint, f"{label} Connected (:{port})", model_id)
                    return
            except Exception:
                continue

        # 2. Launch managed LlamaServerProcess via RuntimeManager
        binary = RuntimeManager.find_llama_server_binary()
        if not binary:
            self.startup_failed.emit(
                "llama-server.exe not found. Open 'Manage Models' to import or install the runtime."
            )
            return

        model_path: Path | None = Path(self.preferred_model_path) if self.preferred_model_path else None
        if model_path is None or not model_path.is_file():
            model_path = resolve_best_model_path()
        if model_path is None or not model_path.is_file():
            self.startup_failed.emit(
                "No local .gguf model found. Open 'Manage Models' to download or import a model."
            )
            return

        try:
            rm = RuntimeManager(binary_path=binary, model_path=str(model_path))
            ok = rm.start_service()
            if not ok or not rm.server or not rm.server.is_running:
                err = rm.server.last_error if rm.server and rm.server.last_error else "Failed to initialize server."
                self.startup_failed.emit(f"Runtime failed to start: {err}")
                return

            self.runtime_manager = rm
            cfg = rm.server.config
            endpoint = f"{rm.server.base_url}/v1/chat/completions"
            gpu_tag = cfg.backend.upper()
            if rm.hardware.gpus and cfg.backend != "cpu":
                gpu_tag = f"{cfg.backend.upper()} · {rm.hardware.gpus[0].name}"
            if cfg.api_key:
                os.environ["LLAMA_API_KEY"] = cfg.api_key
            self.startup_ready.emit(endpoint, f"{gpu_tag} Ready (:{cfg.port})", model_path.name)
        except Exception as exc:
            self.startup_failed.emit(f"Runtime startup error: {exc}")


class ProposeWorker(QThread):
    """Runs natural-language -> verified command proposal off the Qt main thread."""

    proposal_ready = Signal(dict)
    proposal_failed = Signal(str)

    def __init__(
        self,
        client: Any,
        request_text: str,
        cwd: str,
        session_id: str,
        shell: str,
        endpoint_url: str | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.request_text = request_text
        self.cwd = cwd
        self.session_id = session_id
        self.shell = shell
        self.endpoint_url = endpoint_url

    def run(self) -> None:
        try:
            res = call_propose(
                self.client,
                self.request_text,
                cwd=self.cwd,
                session_id=self.session_id,
                shell=self.shell,
                endpoint_url=self.endpoint_url,
            )
            self.proposal_ready.emit(res)
        except Exception as exc:
            self.proposal_failed.emit(str(exc))


class CommitWorker(QThread):
    """Executes an approved proposal asynchronously with TOCTOU & quarantine checks."""

    commit_finished = Signal(dict)
    commit_failed = Signal(str)

    def __init__(
        self,
        client: Any,
        proposal_id_or_cmd: str,
        tier: str,
        session_id: str,
        shell: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.proposal_id_or_cmd = proposal_id_or_cmd
        self.tier = tier
        self.session_id = session_id
        self.shell = shell

    def run(self) -> None:
        try:
            try:
                raw = self.client.commit(
                    self.proposal_id_or_cmd,
                    tier=self.tier,
                    session_id=self.session_id,
                    shell=self.shell,
                )
            except TypeError:
                raw = self.client.commit(self.proposal_id_or_cmd, tier=self.tier)
            self.commit_finished.emit(normalize_commit_result(raw))
        except Exception as exc:
            self.commit_failed.emit(str(exc))


# -----------------------------------------------------------------------------
# Chat Message & Safety Card Widgets
# -----------------------------------------------------------------------------


class WelcomeCard(QFrame):
    """Friendly onboarding card shown in the chat area before the first prompt."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        heading = QLabel("Welcome to NL-OS-Agent")
        heading.setStyleSheet("font-size: 16px; font-weight: bold; color: #e6edf3;")
        layout.addWidget(heading)

        intro = QLabel(
            "Describe what you want to do in plain English below, or double-click a Quick Example on the left.\n"
            "Every command is statically analyzed before execution:"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 13px; color: #8b949e;")
        layout.addWidget(intro)

        bullets = QLabel(
            "  •  READ ONLY (Green) — Safe inspection with zero disk writes.\n"
            "  •  STATE MODIFYING (Blue) — Creates or updates files with full path preview.\n"
            "  •  DESTRUCTIVE (Red) — Cancel focused by default + automatic Quarantine Undo backup."
        )
        bullets.setWordWrap(True)
        bullets.setStyleSheet("font-size: 12px; color: #e6edf3; padding-top: 4px;")
        layout.addWidget(bullets)


class UserMessageBubble(QFrame):
    """Displays the user's plain-English request cleanly in the chat feed."""

    def __init__(self, text: str, shell: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("UserBubble")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        meta = QLabel(f"You  ·  {shell}  ·  {cwd}")
        meta.setStyleSheet("font-size: 11px; font-weight: 600; color: #58a6ff;")
        layout.addWidget(meta)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("font-size: 13px; color: #e6edf3;")
        layout.addWidget(body)


class ProposalCard(QFrame):
    """Interactive safety card rendering consequence preview, approval buttons, output, and Undo."""

    approve_requested = Signal(object, dict)  # (self, proposal_dict)
    cancel_requested = Signal(object)
    undo_requested = Signal(object, str)  # (self, quarantine_id)

    def __init__(self, proposal: Mapping[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.proposal = dict(proposal)
        self._executed = False
        self._quarantine_id: str | None = None
        self.setObjectName("ProposalCard")
        self._init_ui()
        self._bind_proposal()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Header row: Tier badge + source pill
        header = QHBoxLayout()
        self.badge = ConsequenceBadge(self.proposal.get("tier", "read_only"))
        header.addWidget(self.badge)

        self.lbl_meta = QLabel("")
        self.lbl_meta.setStyleSheet("font-size: 11px; color: #8b949e;")
        header.addWidget(self.lbl_meta)
        header.addStretch()

        self.lbl_state = QLabel("Awaiting decision")
        self.lbl_state.setStyleSheet("font-size: 11px; font-weight: 600; color: #8b949e;")
        header.addWidget(self.lbl_state)
        layout.addLayout(header)

        # Clarifying question banner (hidden unless clarification returned)
        self.clarify_banner = QLabel("")
        self.clarify_banner.setWordWrap(True)
        self.clarify_banner.setStyleSheet(
            "background-color: #2b2100; border: 1px solid #d4a72c; border-radius: 6px; "
            "padding: 10px; font-size: 12px; color: #f8e3a1;"
        )
        self.clarify_banner.setVisible(False)
        layout.addWidget(self.clarify_banner)

        # Consequence summary
        preview_box = QFrame()
        preview_box.setObjectName("PreviewBox")
        pv_layout = QVBoxLayout(preview_box)
        pv_layout.setContentsMargins(10, 8, 10, 8)
        self.lbl_preview = QLabel("")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_preview.setStyleSheet("font-size: 12px; color: #e6edf3;")
        pv_layout.addWidget(self.lbl_preview)
        layout.addWidget(preview_box)

        # Command box
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setReadOnly(True)
        set_style_property(self.cmd_edit, "role", "terminal")
        layout.addWidget(self.cmd_edit)

        # Targets & reversibility line
        self.lbl_targets = QLabel("")
        self.lbl_targets.setWordWrap(True)
        self.lbl_targets.setStyleSheet("font-size: 11px; color: #8b949e;")
        layout.addWidget(self.lbl_targets)

        self.lbl_reversibility = QLabel("")
        self.lbl_reversibility.setWordWrap(True)
        self.lbl_reversibility.setStyleSheet("font-size: 11px; color: #8b949e;")
        layout.addWidget(self.lbl_reversibility)

        # Action row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_approve = QPushButton("Approve & Execute")
        self.btn_approve.clicked.connect(self._on_approve)
        btn_row.addWidget(self.btn_approve)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel)

        self.btn_copy = QPushButton("Copy Command")
        self.btn_copy.clicked.connect(self._on_copy)
        btn_row.addWidget(self.btn_copy)

        self.btn_undo_card = QPushButton("Undo from Quarantine")
        self.btn_undo_card.setVisible(False)
        set_style_property(self.btn_undo_card, "variant", "warning")
        self.btn_undo_card.clicked.connect(self._on_undo)
        btn_row.addWidget(self.btn_undo_card)

        btn_row.addStretch()

        self.btn_toggle_output = QPushButton("Hide Output")
        self.btn_toggle_output.setVisible(False)
        self.btn_toggle_output.clicked.connect(self._toggle_output)
        btn_row.addWidget(self.btn_toggle_output)

        layout.addLayout(btn_row)

        # Collapsible output pane
        self.output_box = QTextEdit()
        self.output_box.setReadOnly(True)
        self.output_box.setMaximumHeight(175)
        self.output_box.setVisible(False)
        set_style_property(self.output_box, "role", "terminal")
        layout.addWidget(self.output_box)

    def _bind_proposal(self) -> None:
        tier = str(self.proposal.get("tier", "read_only")).lower()
        cmd = str(self.proposal.get("command") or "").strip()
        preview = str(self.proposal.get("plain_english") or self.proposal.get("preview") or "")
        targets = list(self.proposal.get("targets") or [])
        reversible = bool(self.proposal.get("reversible", False))
        clar_q = self.proposal.get("clarifying_question")
        is_cached = bool(self.proposal.get("cached", False))

        self.badge.set_tier(tier)
        self.lbl_meta.setText("Verified Cache Hit" if is_cached else f"Shell: {self.proposal.get('shell', 'powershell')}")
        self.lbl_preview.setText(preview or "No consequence description provided.")

        if clar_q and not cmd:
            self.clarify_banner.setText(f"Clarification needed: {clar_q}\nReply below with more detail.")
            self.clarify_banner.setVisible(True)
            self.cmd_edit.setVisible(False)
            self.btn_approve.setVisible(False)
            self.btn_copy.setVisible(False)
            self.btn_cancel.setText("Dismiss")
            self.lbl_state.setText("Needs your reply")
            self.lbl_targets.setVisible(False)
            self.lbl_reversibility.setVisible(False)
            return

        self.cmd_edit.setText(cmd)
        self.lbl_targets.setText(
            f"Targets: {', '.join(str(t) for t in targets)}" if targets else "Targets: (No persistent disk targets detected)"
        )

        if reversible:
            self.lbl_reversibility.setText("✔ Reversible: Quarantine backup runs automatically before execution.")
            self.lbl_reversibility.setStyleSheet("font-size: 11px; color: #3fb950; font-weight: bold;")
        elif tier in ("destructive", "privileged"):
            self.lbl_reversibility.setText("⚠ Warning: High-risk operation. Review carefully before approving.")
            self.lbl_reversibility.setStyleSheet("font-size: 11px; color: #f85149; font-weight: bold;")
        else:
            self.lbl_reversibility.setText("ℹ Read-only or non-destructive command.")
            self.lbl_reversibility.setStyleSheet("font-size: 11px; color: #8b949e;")

        # Safe Defaults invariant: Cancel focused for destructive / privileged / ambiguous
        if tier in ("destructive", "privileged", "ambiguous"):
            self.btn_cancel.setDefault(True)
            self.btn_cancel.setFocus()
            self.btn_approve.setText("Approve & Execute (Risky)")
            set_style_property(self.btn_approve, "variant", "danger")
        elif tier == "read_only":
            self.btn_approve.setDefault(True)
            self.btn_approve.setText("Run Command")
            set_style_property(self.btn_approve, "variant", "success")
        else:
            self.btn_approve.setDefault(True)
            self.btn_approve.setText("Approve & Execute")
            set_style_property(self.btn_approve, "variant", "primary")

    def _on_approve(self) -> None:
        self.btn_approve.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.lbl_state.setText("Running...")
        self.lbl_state.setStyleSheet("font-size: 11px; font-weight: 600; color: #58a6ff;")
        self.approve_requested.emit(self, self.proposal)

    def _on_cancel(self) -> None:
        self.btn_approve.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.lbl_state.setText("Cancelled")
        self.lbl_state.setStyleSheet("font-size: 11px; font-weight: 600; color: #8b949e;")
        self.cancel_requested.emit(self)

    def _on_copy(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.cmd_edit.text())
        self.lbl_state.setText("Copied")

    def _on_undo(self) -> None:
        if self._quarantine_id:
            self.undo_requested.emit(self, self._quarantine_id)

    def _toggle_output(self) -> None:
        vis = not self.output_box.isVisible()
        self.output_box.setVisible(vis)
        self.btn_toggle_output.setText("Hide Output" if vis else "Show Output")

    def set_execution_result(self, result: Mapping[str, Any]) -> None:
        """Render command output, exit status, and reveal Undo button if quarantined."""
        self._executed = True
        exit_code = int(result.get("exit_code", 0))
        status = str(result.get("status", "committed" if exit_code == 0 else "failed"))
        output = str(result.get("output") or "").strip()
        q_id = result.get("quarantine_id")
        self._quarantine_id = str(q_id) if q_id else None

        if exit_code == 0:
            self.lbl_state.setText("✔ Executed (exit 0)")
            self.lbl_state.setStyleSheet("font-size: 11px; font-weight: 600; color: #3fb950;")
        else:
            self.lbl_state.setText(f"✘ {status.capitalize()} (exit {exit_code})")
            self.lbl_state.setStyleSheet("font-size: 11px; font-weight: 600; color: #f85149;")

        self.output_box.setPlainText(output or "(Command completed with empty output)")
        self.output_box.setVisible(True)
        self.btn_toggle_output.setVisible(True)
        self.btn_toggle_output.setText("Hide Output")

        if self._quarantine_id:
            self.btn_undo_card.setVisible(True)

    def set_execution_error(self, error_text: str) -> None:
        self.btn_approve.setEnabled(True)
        self.btn_cancel.setEnabled(True)
        self.lbl_state.setText("✘ Execution error")
        self.lbl_state.setStyleSheet("font-size: 11px; font-weight: 600; color: #f85149;")
        self.output_box.setPlainText(error_text)
        self.output_box.setVisible(True)
        self.btn_toggle_output.setVisible(True)


# -----------------------------------------------------------------------------
# Main Desktop Chat Window
# -----------------------------------------------------------------------------


class ChatMainWindow(QMainWindow):
    """Primary conversational desktop interface for NL-OS-Agent."""

    def __init__(
        self,
        client: Any = None,
        auto_start_runtime: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NL-OS-Agent — Local AI Shell Assistant")
        self.resize(1040, 700)

        self.client = client or get_ui_client()
        self.session_id = f"chat_{uuid.uuid4().hex[:8]}"
        self.current_cwd = os.getcwd()
        self.active_endpoint: str | None = None

        self._service_daemon: Any = None
        self._runtime_manager: RuntimeManager | None = None
        self._startup_worker: RuntimeStartupWorker | None = None
        self._propose_worker: ProposeWorker | None = None
        self._commit_worker: CommitWorker | None = None
        self._cards: list[ProposalCard] = []
        self.tray_icon: Any = None

        apply_app_theme(self)
        self._init_ui()
        if auto_start_runtime and not isinstance(self.client, MockServiceCoreClient):
            QTimer.singleShot(50, self.start_background_services)
        elif isinstance(self.client, MockServiceCoreClient):
            self._set_server_badge("Mock Core Ready", "#238636")

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(12)

        # Top Status Bar
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 10, 14, 10)
        top_layout.setSpacing(10)

        title = QLabel("NL-OS-Agent")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #e6edf3;")
        top_layout.addWidget(title)

        self.lbl_server_status = QLabel("Starting local runtime...")
        self.lbl_server_status.setStyleSheet(
            "background-color: #9e6a03; color: #ffffff; font-size: 11px; font-weight: 600; "
            "padding: 4px 10px; border-radius: 10px;"
        )
        top_layout.addWidget(self.lbl_server_status)

        self.lbl_active_model = QLabel("Model: auto")
        self.lbl_active_model.setStyleSheet("font-size: 12px; color: #8b949e;")
        top_layout.addWidget(self.lbl_active_model)

        top_layout.addStretch()

        self.btn_restart_server = QPushButton("Restart Server")
        self.btn_restart_server.clicked.connect(lambda: self.start_background_services(force_restart=True))
        top_layout.addWidget(self.btn_restart_server)

        self.btn_manage_models = QPushButton("Manage Models...")
        self.btn_manage_models.clicked.connect(self._open_model_manager)
        top_layout.addWidget(self.btn_manage_models)

        self.btn_undo_last = QPushButton("Undo Last")
        self.btn_undo_last.clicked.connect(lambda: self._execute_undo(None, None))
        top_layout.addWidget(self.btn_undo_last)

        root_layout.addWidget(top_bar)

        # Body Splitter (Left Sidebar + Center Chat Feed)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("SidebarPanel")
        sidebar.setMinimumWidth(265)
        sidebar.setMaximumWidth(330)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(12, 12, 12, 12)
        sb_layout.setSpacing(9)

        lbl_ws = QLabel("Workspace & Shell")
        set_style_property(lbl_ws, "role", "section")
        sb_layout.addWidget(lbl_ws)

        cwd_row = QHBoxLayout()
        cwd_row.setSpacing(6)
        self.cwd_input = QLineEdit(self.current_cwd)
        self.cwd_input.editingFinished.connect(self._on_cwd_edited)
        cwd_row.addWidget(self.cwd_input, 1)
        self.btn_browse_cwd = QPushButton("...")
        self.btn_browse_cwd.setFixedWidth(34)
        self.btn_browse_cwd.setToolTip("Browse workspace directory")
        self.btn_browse_cwd.clicked.connect(self._browse_cwd)
        cwd_row.addWidget(self.btn_browse_cwd)
        sb_layout.addLayout(cwd_row)

        self.shell_combo = QComboBox()
        shells = ["powershell", "cmd"]
        if sys.platform != "win32" or shutil.which("bash"):
            shells.append("bash")
        self.shell_combo.addItems(shells)
        sb_layout.addWidget(self.shell_combo)

        self.chk_auto_run_readonly = QCheckBox("Auto-run Read-Only commands")
        self.chk_auto_run_readonly.setChecked(False)
        sb_layout.addWidget(self.chk_auto_run_readonly)

        lbl_ex = QLabel("Quick Examples (click to fill, double-click to run)")
        lbl_ex.setWordWrap(True)
        set_style_property(lbl_ex, "role", "section")
        sb_layout.addWidget(lbl_ex)

        self.examples_list = QListWidget()
        self.examples_list.setWordWrap(True)
        self.examples_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for ex in (
            "list files in this directory",
            "show all hidden files in C:\\ProgramData",
            "check free disk space",
            "find all python files modified today",
            "delete temp log files",
        ):
            self.examples_list.addItem(QListWidgetItem(ex))
        self.examples_list.itemClicked.connect(self._on_example_clicked)
        self.examples_list.itemDoubleClicked.connect(self._on_example_double_clicked)
        sb_layout.addWidget(self.examples_list)

        lbl_hist = QLabel("Quarantine / Recent Actions")
        set_style_property(lbl_hist, "role", "section")
        sb_layout.addWidget(lbl_hist)

        self.history_list = QListWidget()
        self.history_list.setWordWrap(True)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sb_layout.addWidget(self.history_list, 1)

        splitter.addWidget(sidebar)

        # Center Chat Column
        chat_container = QWidget()
        chat_col = QVBoxLayout(chat_container)
        chat_col.setContentsMargins(6, 0, 0, 0)
        chat_col.setSpacing(8)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("ChatScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.feed_widget = QWidget()
        self.feed_widget.setObjectName("FeedCanvas")
        self.feed_layout = QVBoxLayout(self.feed_widget)
        self.feed_layout.setContentsMargins(16, 16, 16, 16)
        self.feed_layout.setSpacing(12)
        self._welcome_card = WelcomeCard(self.feed_widget)
        self.feed_layout.addWidget(self._welcome_card)
        self.feed_layout.addStretch()
        self.scroll_area.setWidget(self.feed_widget)
        chat_col.addWidget(self.scroll_area, 1)

        # Status / thinking bar
        status_row = QHBoxLayout()
        self.lbl_thinking = QLabel("")
        self.lbl_thinking.setStyleSheet("font-size: 12px; color: #58a6ff;")
        status_row.addWidget(self.lbl_thinking, 1)
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedWidth(150)
        self.busy_bar.setFixedHeight(10)
        self.busy_bar.setVisible(False)
        status_row.addWidget(self.busy_bar)
        chat_col.addLayout(status_row)

        # Bottom Composer
        composer = QHBoxLayout()
        composer.setSpacing(8)
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText(
            "Describe what you want to inspect or run (e.g. 'show hidden files in C:\\ProgramData')..."
        )
        self.prompt_input.setMinimumHeight(38)
        self.prompt_input.returnPressed.connect(self.submit_prompt)
        composer.addWidget(self.prompt_input, 1)

        self.btn_send = QPushButton("Propose")
        self.btn_send.setMinimumHeight(38)
        set_style_property(self.btn_send, "variant", "primary")
        self.btn_send.clicked.connect(self.submit_prompt)
        composer.addWidget(self.btn_send)
        chat_col.addLayout(composer)

        splitter.addWidget(chat_container)
        splitter.setStretchFactor(1, 3)
        root_layout.addWidget(splitter, 1)

    def _set_server_badge(self, text: str, bg_color: str) -> None:
        self.lbl_server_status.setText(text)
        self.lbl_server_status.setStyleSheet(
            f"background-color: {bg_color}; color: white; font-size: 11px; font-weight: bold; "
            f"padding: 3px 9px; border-radius: 10px;"
        )

    def start_background_services(self, force_restart: bool = False, preferred_model: str | None = None) -> None:
        """Start embedded ServiceDaemon + hidden llama-server runtime in background thread."""
        if self._startup_worker and self._startup_worker.isRunning():
            return

        if force_restart and self._runtime_manager:
            self._runtime_manager.stop_service()
            self._runtime_manager = None

        if self._service_daemon is None:
            try:
                from nlagent.service.core import ServiceDaemon
                state_dir = getattr(self.client, "state_dir", None)
                self._service_daemon = ServiceDaemon(state_dir=state_dir)
                self._service_daemon.start(block=False)
            except Exception as exc:
                logger.warning("Embedded ServiceDaemon startup skipped: %s", exc)

        self._set_server_badge("Starting local runtime...", "#9a6700")
        self._startup_worker = RuntimeStartupWorker(preferred_model_path=preferred_model, parent=self)
        self._startup_worker.startup_ready.connect(self._on_runtime_ready)
        self._startup_worker.startup_failed.connect(self._on_runtime_failed)
        self._startup_worker.start()

    def _on_runtime_ready(self, endpoint_url: str, status_label: str, model_display: str) -> None:
        self.active_endpoint = endpoint_url
        if self._startup_worker and self._startup_worker.runtime_manager:
            self._runtime_manager = self._startup_worker.runtime_manager
        self._set_server_badge(status_label, "#2da44e")
        self.lbl_active_model.setText(f"Model: {model_display}")

    def _on_runtime_failed(self, reason: str) -> None:
        self._set_server_badge("Runtime Offline", "#cf222e")
        self.lbl_thinking.setText(reason)

    def _open_model_manager(self) -> None:
        dlg = ModelManagerDialog(parent=self)
        dlg.model_selected.connect(self._on_model_chosen)
        dlg.exec()

    def _on_model_chosen(self, model_path: str) -> None:
        self.lbl_active_model.setText(f"Model: {Path(model_path).name}")
        if not isinstance(self.client, MockServiceCoreClient):
            self.start_background_services(force_restart=True, preferred_model=model_path)

    def _browse_cwd(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Workspace Directory", self.current_cwd)
        if chosen:
            self.current_cwd = chosen
            self.cwd_input.setText(chosen)

    def _on_cwd_edited(self) -> None:
        cand = self.cwd_input.text().strip()
        if cand and os.path.isdir(cand):
            self.current_cwd = cand

    def _on_example_clicked(self, item: QListWidgetItem) -> None:
        self.prompt_input.setText(item.text())
        self.prompt_input.setFocus()

    def _on_example_double_clicked(self, item: QListWidgetItem) -> None:
        self.prompt_input.setText(item.text())
        self.submit_prompt()

    def _append_feed_widget(self, widget: QWidget) -> None:
        if getattr(self, "_welcome_card", None) is not None:
            self._welcome_card.hide()
        idx = max(0, self.feed_layout.count() - 1)
        self.feed_layout.insertWidget(idx, widget)
        QTimer.singleShot(
            20,
            lambda: self.scroll_area.verticalScrollBar().setValue(
                self.scroll_area.verticalScrollBar().maximum()
            ),
        )

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.btn_send.setEnabled(not busy)
        self.prompt_input.setEnabled(not busy)
        self.busy_bar.setVisible(busy)
        self.lbl_thinking.setText(message)

    def submit_prompt(self) -> None:
        """Send user natural-language prompt to background ProposeWorker."""
        text = self.prompt_input.text().strip()
        if not text:
            return
        if self._propose_worker and self._propose_worker.isRunning():
            return

        self._on_cwd_edited()
        shell = self.shell_combo.currentText()
        self.prompt_input.clear()
        self._append_feed_widget(UserMessageBubble(text=text, shell=shell, cwd=self.current_cwd))
        self._set_busy(True, "Synthesizing command & verifying safety...")

        self._propose_worker = ProposeWorker(
            client=self.client,
            request_text=text,
            cwd=self.current_cwd,
            session_id=self.session_id,
            shell=shell,
            endpoint_url=self.active_endpoint,
            parent=self,
        )
        self._propose_worker.proposal_ready.connect(self._on_proposal_ready)
        self._propose_worker.proposal_failed.connect(self._on_proposal_failed)
        self._propose_worker.start()

    def _on_proposal_ready(self, proposal: dict[str, Any]) -> None:
        self._set_busy(False, "")
        card = ProposalCard(proposal=proposal)
        card.approve_requested.connect(self._on_card_approve)
        card.cancel_requested.connect(self._on_card_cancel)
        card.undo_requested.connect(lambda c, qid: self._execute_undo(c, qid))
        self._cards.append(card)
        self._append_feed_widget(card)

        tier = str(proposal.get("tier", "")).lower()
        cmd = str(proposal.get("command") or "").strip()
        if not cmd and proposal.get("clarifying_question"):
            self.prompt_input.setFocus()
            return
        if tier == "read_only" and cmd and self.chk_auto_run_readonly.isChecked():
            card._on_approve()

    def _on_proposal_failed(self, err: str) -> None:
        self._set_busy(False, "")
        err_lbl = QLabel(f"Proposal error: {err}")
        err_lbl.setWordWrap(True)
        err_lbl.setStyleSheet(
            "background-color: #ffebe9; border: 1px solid #ff8182; border-radius: 8px; "
            "padding: 8px; color: #cf222e; font-size: 12px;"
        )
        self._append_feed_widget(err_lbl)

    def _on_card_approve(self, card: ProposalCard, proposal: dict[str, Any]) -> None:
        target = str(proposal.get("id") or proposal.get("command") or "").strip()
        if not target:
            card.set_execution_error("Nothing to execute.")
            return
        shell = self.shell_combo.currentText()
        tier = str(proposal.get("tier", "read_only"))
        self._set_busy(True, "Running verified command...")

        worker = CommitWorker(
            client=self.client,
            proposal_id_or_cmd=target,
            tier=tier,
            session_id=self.session_id,
            shell=shell,
            parent=self,
        )
        worker.commit_finished.connect(lambda res, c=card, p=proposal: self._on_commit_finished(c, p, res))
        worker.commit_failed.connect(lambda err, c=card: self._on_commit_failed(c, err))
        self._commit_worker = worker
        worker.start()

    def _on_commit_finished(self, card: ProposalCard, proposal: dict[str, Any], result: dict[str, Any]) -> None:
        self._set_busy(False, "")
        card.set_execution_result(result)
        new_cwd = result.get("cwd")
        if new_cwd and os.path.isdir(str(new_cwd)):
            self.current_cwd = str(new_cwd)
            self.cwd_input.setText(self.current_cwd)

        cmd = str(proposal.get("command") or "")
        q_id = result.get("quarantine_id")
        prefix = "[Reversible] " if q_id else ""
        self.history_list.insertItem(0, QListWidgetItem(f"{prefix}{cmd}"))

    def _on_commit_failed(self, card: ProposalCard, err: str) -> None:
        self._set_busy(False, "")
        card.set_execution_error(f"Execution failed: {err}")

    def _on_card_cancel(self, _card: ProposalCard) -> None:
        self.lbl_thinking.setText("Proposal cancelled safely.")

    def _execute_undo(self, _card: ProposalCard | None, quarantine_id: str | None) -> None:
        try:
            try:
                res = self.client.undo(target_id=quarantine_id)
            except TypeError:
                res = self.client.undo()
            restored = res.get("restored_count")
            if restored is None:
                restored = len(res.get("restored_paths", []))
            msg = res.get("message") or res.get("error") or f"Restored {restored} file(s)."
            QMessageBox.information(self, "Quarantine Undo", f"{msg}\nRestored files: {restored}")
        except Exception as exc:
            QMessageBox.warning(self, "Undo Failed", str(exc))

    def closeEvent(self, event: Any) -> None:
        for worker in (self._startup_worker, self._propose_worker, self._commit_worker):
            try:
                if worker is not None and worker.isRunning():
                    worker.wait(800)
            except Exception:
                pass
        try:
            if self._runtime_manager:
                self._runtime_manager.stop_service()
                self._runtime_manager = None
        except Exception:
            pass
        try:
            if self._service_daemon:
                self._service_daemon.stop()
                self._service_daemon = None
        except Exception:
            pass
        super().closeEvent(event)
