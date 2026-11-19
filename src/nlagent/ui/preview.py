"""
src/nlagent/ui/preview.py - Desktop Consequence Preview Window & Usability Trial View.

Provides:
1. PreviewWindow: Floating desktop popup presenting synthesized command proposals:
   - Dynamic Risk Tier badge (read_only, state_modifying, destructive, privileged, ambiguous).
   - Deterministic plain-English consequence headline and warnings from AST renderer.
   - Quarantine and reversibility indicator (Rg).
   - Affected filesystem targets list.
   - Safe Defaults: "Cancel" button is default-focused for destructive and ambiguous tiers.
   - Quick Undo panel for one-click rollback of quarantined operations.
2. StudyTrialWidget: Specialized presentation widget for human-factors study trials:
   - Alternates between Plain Consequence Preview and Raw Command string based on participant condition.
   - Comprehension multiple-choice questionnaire.
   - Timing measurement and decision logging.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Sequence

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nlagent.ui.client import MockServiceCoreClient, get_ui_client
from nlagent.ui.study import DisplayCondition, StudySession, StudyTask, StudyTrialRecord
from nlagent.ui.theme import apply_app_theme, set_style_property

TIER_COLORS = {
    "read_only": "#2da44e",       # Green
    "state_modifying": "#0969da",  # Blue
    "ambiguous": "#9a6700",        # Dark Yellow/Amber
    "destructive": "#cf222e",      # Red
    "privileged": "#8250df",       # Purple
}


class ConsequenceBadge(QLabel):
    """Visual pill badge displaying the safety risk tier."""

    def __init__(self, tier: str = "read_only", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.set_tier(tier)

    def set_tier(self, tier: str) -> None:
        clean_tier = tier.lower().replace("-", "_")
        color = TIER_COLORS.get(clean_tier, "#57606a")
        display_text = clean_tier.upper().replace("_", " ")

        self.setText(f"  {display_text}  ")
        self.setStyleSheet(
            f"background-color: {color}; color: white; font-weight: bold; font-size: 11px; "
            f"border-radius: 4px; padding: 3px 6px;"
        )


class PreviewWindow(QDialog):
    """Desktop Floating Preview Dialog for NL-OS-Agent command proposals."""

    approved = Signal(dict)
    cancelled = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        client: Any = None,
        proposal: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        apply_app_theme(self)
        self.setWindowTitle("NL-OS-Agent — Safe Command Preview")
        self.resize(580, 520)
        self.client = client or get_ui_client()
        self.current_proposal: dict[str, Any] = dict(proposal or {})

        self._init_ui()
        if proposal:
            self.set_proposal(proposal)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header: Query + Tier Badge
        header_layout = QHBoxLayout()
        self.title_label = QLabel("Command Proposal Preview")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #e6edf3;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()
        self.badge = ConsequenceBadge("read_only")
        header_layout.addWidget(self.badge)
        layout.addLayout(header_layout)

        # Input query bar
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Enter natural language request (e.g. 'find large log files')...")
        self.query_input.returnPressed.connect(self._on_submit_query)
        layout.addWidget(self.query_input)

        # Consequence Summary Box
        self.consequence_box = QTextEdit()
        self.consequence_box.setReadOnly(True)
        self.consequence_box.setMaximumHeight(110)
        layout.addWidget(self.consequence_box)

        # Proposed Shell Command Box
        cmd_header = QLabel("Synthesized Shell Command:")
        cmd_header.setStyleSheet("font-size: 11px; font-weight: bold; color: #8b949e;")
        layout.addWidget(cmd_header)

        self.cmd_box = QLineEdit()
        self.cmd_box.setReadOnly(True)
        set_style_property(self.cmd_box, "role", "terminal")
        layout.addWidget(self.cmd_box)

        # Reversibility & Target Files
        target_header = QLabel("Affected Targets & Reversibility:")
        target_header.setStyleSheet("font-size: 11px; font-weight: bold; color: #8b949e;")
        layout.addWidget(target_header)

        self.targets_list = QListWidget()
        self.targets_list.setMaximumHeight(90)
        layout.addWidget(self.targets_list)

        self.reversibility_label = QLabel("Reversibility status: Protected by quarantine rollback.")
        self.reversibility_label.setStyleSheet("color: #58a6ff; font-size: 11px;")
        layout.addWidget(self.reversibility_label)

        # Action Buttons
        btn_layout = QHBoxLayout()

        self.btn_undo = QPushButton("Undo Last Action")
        self.btn_undo.setStyleSheet("padding: 6px 12px; color: #57606a;")
        self.btn_undo.clicked.connect(self._on_undo_clicked)
        btn_layout.addWidget(self.btn_undo)

        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet("padding: 6px 16px; font-weight: bold;")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_approve = QPushButton("Approve & Run")
        self.btn_approve.setStyleSheet(
            "background-color: #2da44e; color: white; font-weight: bold; padding: 6px 20px; border-radius: 6px;"
        )
        self.btn_approve.clicked.connect(self._on_approve_clicked)
        btn_layout.addWidget(self.btn_approve)

        layout.addLayout(btn_layout)

    def set_proposal(self, prop: Mapping[str, Any]) -> None:
        """Update window elements with proposal metadata."""
        self.current_proposal = dict(prop)
        tier = prop.get("tier", "read_only")
        cmd = prop.get("command", "")
        preview = prop.get("preview", "No consequence preview generated.")
        targets = prop.get("targets", [])
        reversible = prop.get("reversible", False)

        self.badge.set_tier(tier)
        self.cmd_box.setText(cmd)
        self.consequence_box.setText(preview)

        self.targets_list.clear()
        if targets:
            for t in targets:
                self.targets_list.addItem(QListWidgetItem(str(t)))
        else:
            self.targets_list.addItem(QListWidgetItem("(No persistent files modified)"))

        if reversible:
            self.reversibility_label.setText("✔ Reversibility: Full byte-exact rollback available via 'nlctl undo'.")
            self.reversibility_label.setStyleSheet("color: #2da44e; font-weight: bold; font-size: 11px;")
        elif tier in ("destructive", "privileged"):
            self.reversibility_label.setText("⚠ Warning: Irreversible operation. Changes cannot be undone via quarantine.")
            self.reversibility_label.setStyleSheet("color: #cf222e; font-weight: bold; font-size: 11px;")
        else:
            self.reversibility_label.setText("ℹ State modification without destructive file deletion.")
            self.reversibility_label.setStyleSheet("color: #57606a; font-size: 11px;")

        # Safe defaults: focus Cancel button for destructive/privileged tiers
        if tier in ("destructive", "privileged", "ambiguous"):
            self.btn_cancel.setDefault(True)
            self.btn_cancel.setFocus()
            self.btn_approve.setStyleSheet(
                "background-color: #cf222e; color: white; font-weight: bold; padding: 6px 20px; border-radius: 6px;"
            )
        else:
            self.btn_approve.setDefault(True)
            self.btn_approve.setFocus()
            self.btn_approve.setStyleSheet(
                "background-color: #2da44e; color: white; font-weight: bold; padding: 6px 20px; border-radius: 6px;"
            )

    def _on_submit_query(self) -> None:
        query = self.query_input.text().strip()
        if not query:
            return
        try:
            res = self.client.propose(query, cwd=os.getcwd())
            self.set_proposal(res)
        except Exception as exc:
            self.consequence_box.setText(f"Proposal error: {exc}")

    def _on_approve_clicked(self) -> None:
        cmd = self.current_proposal.get("command", "")
        prop_id = self.current_proposal.get("id")
        tier = self.current_proposal.get("tier", "read_only")
        target = prop_id or cmd
        if target:
            self.client.commit(target, tier=tier)
        self.approved.emit(self.current_proposal)
        self.accept()

    def _on_cancel_clicked(self) -> None:
        self.cancelled.emit()
        self.reject()

    def _on_undo_clicked(self) -> None:
        res = self.client.undo()
        restored = res.get("restored_count")
        if restored is None:
            restored = len(res.get("restored_paths", []))
        status = res.get("status") or ("ok" if res.get("success") else "failed")
        QMessageBox.information(
            self,
            "Undo Completed",
            f"Rollback status: {status}\nRestored: {restored} file(s).",
        )


# -----------------------------------------------------------------------------
# Study Mode Trial Presentation Dialog
# -----------------------------------------------------------------------------

class StudyTrialDialog(QDialog):
    """Participant trial window evaluating comprehension and approval decisions."""

    trial_recorded = Signal(StudyTrialRecord)

    def __init__(self, session: StudySession, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        apply_app_theme(self)
        self.session = session
        self.setWindowTitle(f"Usability Study — Participant: {session.participant.participant_id}")
        self.resize(640, 580)

        self._init_ui()
        self._load_next_task()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, len(self.session.tasks))
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        # Task Header Frame
        header_frame = QFrame()
        header_frame.setObjectName("CardFrame")
        h_layout = QVBoxLayout(header_frame)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(6)

        self.lbl_task_title = QLabel("Task Title")
        self.lbl_task_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #e6edf3;")
        h_layout.addWidget(self.lbl_task_title)

        self.lbl_user_request = QLabel("Natural Language Request...")
        self.lbl_user_request.setStyleSheet("font-size: 13px; color: #e6edf3;")
        h_layout.addWidget(self.lbl_user_request)
        layout.addWidget(header_frame)

        # Display Section (Condition Dependent)
        disp_header = QLabel("Proposed Agent Action Preview:")
        disp_header.setStyleSheet("font-size: 12px; font-weight: bold; color: #8b949e;")
        layout.addWidget(disp_header)

        self.preview_display = QTextEdit()
        self.preview_display.setReadOnly(True)
        self.preview_display.setMaximumHeight(110)
        layout.addWidget(self.preview_display)

        # Comprehension Question Box
        q_frame = QFrame()
        q_frame.setObjectName("CardFrame")
        q_layout = QVBoxLayout(q_frame)
        q_layout.setContentsMargins(12, 12, 12, 12)
        q_layout.setSpacing(8)

        self.lbl_question = QLabel("What will this command do?")
        self.lbl_question.setStyleSheet("font-weight: bold; font-size: 12px; color: #e6edf3;")
        q_layout.addWidget(self.lbl_question)

        self.radio_group = QButtonGroup(self)
        self.radio_options: list[QRadioButton] = []
        for i in range(4):
            rb = QRadioButton(f"Option {i + 1}")
            rb.setStyleSheet("font-size: 12px; padding: 2px 0;")
            self.radio_group.addButton(rb, i)
            self.radio_options.append(rb)
            q_layout.addWidget(rb)

        layout.addWidget(q_frame)

        # Decision Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel / Reject")
        set_style_property(self.btn_cancel, "variant", "danger")
        self.btn_cancel.clicked.connect(lambda: self._on_decision_clicked("cancel"))
        btn_layout.addWidget(self.btn_cancel)

        self.btn_approve = QPushButton("Approve & Execute")
        set_style_property(self.btn_approve, "variant", "success")
        self.btn_approve.clicked.connect(lambda: self._on_decision_clicked("approve"))
        btn_layout.addWidget(self.btn_approve)

        layout.addLayout(btn_layout)

    def _load_next_task(self) -> None:
        """Load and display the next task in the study battery."""
        if self.session.is_completed():
            self._show_completion_summary()
            self.accept()
            return

        task = self.session.current_task()
        if not task:
            return

        idx = self.session.current_task_idx
        self.progress_bar.setValue(idx)
        self.progress_bar.setFormat(f"Task {idx + 1} of {len(self.session.tasks)}")

        self.lbl_task_title.setText(f"Scenario {idx + 1}: {task.title}")
        self.lbl_user_request.setText(f"User Goal: \"{task.request}\"")

        # Condition rendering
        if self.session.participant.condition == DisplayCondition.PLAIN_PREVIEW:
            self.preview_display.setPlainText(task.plain_preview)
            self.preview_display.setStyleSheet(
                "background-color: #f0fdf4; border: 2px solid #2da44e; border-radius: 6px; padding: 10px; font-size: 13px; color: #166534;"
            )
        else:
            self.preview_display.setPlainText(f"$ {task.command}")
            self.preview_display.setStyleSheet(
                "background-color: #1e1e1e; border: 2px solid #57606a; border-radius: 6px; padding: 10px; font-family: Consolas; font-size: 13px; color: #4ade80;"
            )

        # Options
        self.lbl_question.setText(task.comprehension_question)
        for rb, opt_text in zip(self.radio_options, task.options):
            rb.setText(opt_text)
            rb.setChecked(False)

        # Reset selection & start timers
        self.radio_options[0].setChecked(True)
        self.session.start_trial()

    def _on_decision_clicked(self, decision: str) -> None:
        """Handle participant approval or cancel decision."""
        selected_id = self.radio_group.checkedId()
        if selected_id < 0:
            QMessageBox.warning(self, "Answer Required", "Please select a comprehension answer before making your decision.")
            return

        rec = self.session.record_decision(chosen_option_idx=selected_id, approval_decision=decision)
        self.trial_recorded.emit(rec)
        self._load_next_task()

    def _show_completion_summary(self) -> None:
        """Display summary statistics upon battery completion."""
        stats = self.session.summary_stats()
        cond_label = "Plain Consequence Preview" if stats["condition"] == "plain_preview" else "Raw Shell Command"
        msg = (
            f"Study Completed for Participant {self.session.participant.participant_id}\n\n"
            f"Assigned Condition: {cond_label}\n"
            f"Comprehension Accuracy: {stats['comprehension_accuracy']}%\n"
            f"Safe Decision Rate: {stats['safe_decision_rate']}%\n"
            f"Accidental Approval Rate: {stats['accidental_approval_rate']}%\n"
            f"Mean Total Response Time: {stats['mean_total_ms'] / 1000.0:.2f} s\n\n"
            f"All records safely logged to local JSONL (Zero Network)."
        )
        QMessageBox.information(self, "Study Complete", msg)

