"""
src/nlagent/ui/consent.py - Study Mode Consent Screen & Participant Enrollment.

Provides:
1. Information Sheet: Explains study objectives, safety telemetry, and privacy guarantees.
2. Anonymous ID Management: Generates or assigns non-identifying IDs (e.g. P-8B2A1C).
3. Condition Allocation: Supports randomized or manual assignment of display conditions
   (Plain-English Preview vs. Raw Shell Command).
4. Explicit Consent Enforcement: Blocks study commencement until the participant
   explicitly checks the consent acknowledgment.
"""

from __future__ import annotations

import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nlagent.ui.study import DisplayCondition, StudyParticipant


class ConsentDialog(QDialog):
    """Institutional / Ethics compliant participant consent screen."""

    consent_granted = Signal(StudyParticipant)

    def __init__(self, parent: Optional[QWidget] = None, fixed_condition: Optional[DisplayCondition] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("NL-OS-Agent Usability Study — Participant Information & Consent")
        self.resize(620, 560)
        self.setMinimumSize(540, 480)

        self.participant = StudyParticipant.generate(condition=fixed_condition)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title & Subtitle
        title_label = QLabel("Operating System Agent Command Comprehension Study")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1f2328;")
        layout.addWidget(title_label)

        sub_label = QLabel(
            "An empirical human-factors investigation measuring whether plain-English consequence previews "
            "significantly reduce accidental approvals compared to raw shell command inspection."
        )
        sub_label.setWordWrap(True)
        sub_label.setStyleSheet("color: #57606a; font-size: 12px;")
        layout.addWidget(sub_label)

        # Divider
        divider1 = QFrame()
        divider1.setFrameShape(QFrame.Shape.HLine)
        divider1.setStyleSheet("color: #d0d7de;")
        layout.addWidget(divider1)

        # Information Sheet Box
        info_box = QTextEdit()
        info_box.setReadOnly(True)
        info_box.setHtml(
            """
            <h4 style="margin: 0 0 6px 0;">Participant Information & Privacy Notice</h4>
            <p style="font-size: 11px; line-height: 1.4; color: #24292f;">
            <b>1. Purpose:</b> You will be presented with a sequence of common computer management tasks. 
            For each task, an AI agent proposes a shell action. You will be asked a quick comprehension 
            question about what the command does, followed by an approval decision (Approve vs Cancel).
            </p>
            <p style="font-size: 11px; line-height: 1.4; color: #24292f;">
            <b>2. Data Protection:</b> This study operates <b>strictly offline</b>. No files, personal records, 
            or network packets leave this computer. All timing, comprehension answers, and approval decisions 
            are stored strictly in a local JSONL file under your anonymous ID.
            </p>
            <p style="font-size: 11px; line-height: 1.4; color: #24292f;">
            <b>3. Voluntary Participation:</b> Your participation is completely voluntary. You may exit the 
            study at any time without penalty. No individual performance metrics are linked to your identity.
            </p>
            """
        )
        info_box.setStyleSheet("background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 6px;")
        layout.addWidget(info_box)

        # Participant Setup Form
        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #ffffff; border: 1px solid #d0d7de; border-radius: 6px; padding: 10px;")
        form_layout = QFormLayout(form_frame)
        form_layout.setSpacing(10)

        # Anonymous ID Row
        id_layout = QHBoxLayout()
        self.id_input = QLineEdit(self.participant.participant_id)
        self.id_input.setReadOnly(True)
        self.id_input.setStyleSheet("font-weight: bold; background-color: #f6f8fa; padding: 4px;")
        id_layout.addWidget(self.id_input)

        self.btn_regen_id = QPushButton("Generate New ID")
        self.btn_regen_id.setStyleSheet("padding: 4px 10px;")
        self.btn_regen_id.clicked.connect(self._on_regenerate_id)
        id_layout.addWidget(self.btn_regen_id)

        form_layout.addRow("Anonymous ID:", id_layout)

        # Condition Allocation Row
        cond_layout = QHBoxLayout()
        self.btn_group = QButtonGroup(self)

        self.radio_plain = QRadioButton("Plain Consequence Preview")
        self.radio_raw = QRadioButton("Raw Shell Command")
        self.btn_group.addButton(self.radio_plain, 0)
        self.btn_group.addButton(self.radio_raw, 1)

        if self.participant.condition == DisplayCondition.PLAIN_PREVIEW:
            self.radio_plain.setChecked(True)
        else:
            self.radio_raw.setChecked(True)

        self.btn_group.idToggled.connect(self._on_condition_toggled)

        cond_layout.addWidget(self.radio_plain)
        cond_layout.addWidget(self.radio_raw)
        form_layout.addRow("Evaluation Condition:", cond_layout)

        layout.addWidget(form_frame)

        # Consent Checkbox
        self.chk_consent = QCheckBox(
            "I have read and understood the participant information above and consent to participate in this study."
        )
        self.chk_consent.setStyleSheet("font-weight: bold; color: #1f2328; margin-top: 6px;")
        self.chk_consent.toggled.connect(self._on_consent_toggled)
        layout.addWidget(self.chk_consent)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel / Exit")
        self.btn_cancel.setStyleSheet("padding: 6px 14px;")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_start = QPushButton("Begin Usability Study")
        self.btn_start.setEnabled(False)
        self.btn_start.setStyleSheet(
            "QPushButton { background-color: #2da44e; color: white; font-weight: bold; padding: 6px 18px; border-radius: 6px; } "
            "QPushButton:disabled { background-color: #94d3a2; color: #ffffff; }"
        )
        self.btn_start.clicked.connect(self._on_start_study)
        btn_layout.addWidget(self.btn_start)

        layout.addLayout(btn_layout)

    def _on_regenerate_id(self) -> None:
        self.participant = StudyParticipant.generate(condition=self.participant.condition)
        self.id_input.setText(self.participant.participant_id)

    def _on_condition_toggled(self, btn_id: int, checked: bool) -> None:
        if checked:
            cond = DisplayCondition.PLAIN_PREVIEW if btn_id == 0 else DisplayCondition.RAW_COMMAND
            self.participant.condition = cond

    def _on_consent_toggled(self, checked: bool) -> None:
        self.btn_start.setEnabled(checked)
        self.participant.consented = checked
        if checked:
            self.participant.consented_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        else:
            self.participant.consented_at = ""

    def _on_start_study(self) -> None:
        if self.participant.consented:
            self.consent_granted.emit(self.participant)
            self.accept()


def show_consent_dialog(parent: Optional[QWidget] = None, fixed_condition: Optional[DisplayCondition] = None) -> Optional[StudyParticipant]:
    """Modal helper launching the consent dialog and returning the enrolled participant."""
    dlg = ConsentDialog(parent=parent, fixed_condition=fixed_condition)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.participant
    return None

