r"""
tests/test_study_mode.py - Test Suite for Task T33-T35 & N7 (Desktop UI, Wizard & Study Mode).

Verifies:
1. Anonymous participant ID generation and privacy guarantees.
2. Participant consent dialog logic and condition allocation.
3. Standardized task battery balance (benign vs. destructive vs. evasive).
4. Usability study trial execution, comprehension testing, and timing telemetry.
5. Air-gapped local JSONL logging with schema validation and zero-network isolation.
6. Condition rendering differences (Plain-English consequence preview vs. Raw command string).
7. Accidental approval detection ($P(\text{Approve} \mid \text{Harmful})$).
8. Desktop Preview Window safe defaults (Cancel focused on destructive tiers).
9. First-Run Setup Wizard pages and hardware detection integration.
10. Inno Setup installer script syntax and structure.
"""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure Qt runs in headless offscreen mode for test execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from nlagent.ui.client import MockServiceCoreClient
from nlagent.ui.consent import ConsentDialog
from nlagent.ui.preview import ConsequenceBadge, PreviewWindow, StudyTrialDialog
from nlagent.ui.study import (
    DEFAULT_STUDY_TASKS,
    DisplayCondition,
    StudyLogger,
    StudyParticipant,
    StudySession,
    StudyTask,
    StudyTrialRecord,
)
from nlagent.ui.wizard import (
    FinishPage,
    HardwarePage,
    IntroPage,
    ModelPage,
    ProfilePage,
    SetupWizard,
)

# Initialize single QApplication instance for Qt test suite
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# -----------------------------------------------------------------------------
# 1. Study Participant & Consent Tests
# -----------------------------------------------------------------------------

def test_anonymous_participant_generation():
    """Verify anonymous ID format and randomness."""
    p1 = StudyParticipant.generate()
    p2 = StudyParticipant.generate()

    assert p1.participant_id.startswith("P-"), "Participant ID must begin with P-"
    assert len(p1.participant_id) == 8, "Expected format P-XXXXXX (8 chars)"
    assert p1.participant_id != p2.participant_id, "Participant IDs must be unique"
    assert p1.condition in (DisplayCondition.PLAIN_PREVIEW, DisplayCondition.RAW_COMMAND)
    assert not p1.consented, "New participant must not have consented by default"


def test_consent_dialog_enforces_agreement(qapp):
    """Verify that consent checkbox strictly controls the begin study action."""
    dlg = ConsentDialog(fixed_condition=DisplayCondition.PLAIN_PREVIEW)
    assert not dlg.btn_start.isEnabled(), "Begin Study button must be disabled initially"

    # Check consent
    dlg.chk_consent.setChecked(True)
    assert dlg.btn_start.isEnabled(), "Begin Study button must be enabled once consented"
    assert dlg.participant.consented
    assert dlg.participant.consented_at != ""

    # Uncheck consent
    dlg.chk_consent.setChecked(False)
    assert not dlg.btn_start.isEnabled()
    assert not dlg.participant.consented


def test_consent_dialog_condition_toggling(qapp):
    """Verify that condition radio buttons dynamically update participant condition."""
    dlg = ConsentDialog(fixed_condition=DisplayCondition.PLAIN_PREVIEW)
    assert dlg.participant.condition == DisplayCondition.PLAIN_PREVIEW

    dlg.radio_raw.setChecked(True)
    assert dlg.participant.condition == DisplayCondition.RAW_COMMAND

    dlg.radio_plain.setChecked(True)
    assert dlg.participant.condition == DisplayCondition.PLAIN_PREVIEW


# -----------------------------------------------------------------------------
# 2. Task Battery & Session Flow Tests
# -----------------------------------------------------------------------------

def test_default_study_tasks_battery():
    """Verify pre-registered study battery properties."""
    tasks = DEFAULT_STUDY_TASKS
    assert len(tasks) >= 6, "Expected at least 6 pre-registered study tasks"

    harmful_tasks = [t for t in tasks if t.is_harmful]
    benign_tasks = [t for t in tasks if not t.is_harmful]

    assert len(harmful_tasks) >= 3, "Expected balanced harmful tasks in battery"
    assert len(benign_tasks) >= 3, "Expected balanced benign tasks in battery"

    for t in tasks:
        assert len(t.options) == 4, f"Task {t.task_id} must have exactly 4 comprehension options"
        assert 0 <= t.correct_option_idx < 4, f"Task {t.task_id} has invalid correct option index"
        assert t.request, f"Task {t.task_id} missing user request text"
        assert t.command, f"Task {t.task_id} missing command"
        assert t.plain_preview, f"Task {t.task_id} missing plain-English preview text"


def test_study_session_trial_flow_plain_preview(tmp_path: Path):
    """Verify trial execution under PLAIN_PREVIEW condition."""
    log_file = tmp_path / "study_trials.jsonl"
    logger = StudyLogger(output_path=log_file)
    participant = StudyParticipant(participant_id="P-TEST01", condition=DisplayCondition.PLAIN_PREVIEW, consented=True)

    session = StudySession(participant=participant, tasks=DEFAULT_STUDY_TASKS[:2], logger_instance=logger)
    assert not session.is_completed()

    # Trial 1: Benign search task
    t1 = session.current_task()
    assert t1.task_id == "task_01_benign_search"
    session.start_trial()
    session.mark_preview_inspected()

    # User answers correctly and approves benign task
    rec1 = session.record_decision(chosen_option_idx=t1.correct_option_idx, approval_decision="approve")

    assert rec1.is_comprehension_correct is True
    assert rec1.is_safe_decision is True
    assert rec1.is_accidental_approval is False
    assert rec1.condition == "plain_preview"
    assert rec1.preview_displayed == t1.plain_preview
    assert rec1.inspection_duration_ms > 0
    assert rec1.decision_duration_ms > 0

    # Trial 2: Destructive documents wipe
    t2 = session.current_task()
    assert t2.task_id == "task_02_destructive_documents_wipe"
    session.start_trial()
    session.mark_preview_inspected()

    # User answers correctly and cancels harmful task
    rec2 = session.record_decision(chosen_option_idx=t2.correct_option_idx, approval_decision="cancel")

    assert rec2.is_comprehension_correct is True
    assert rec2.is_safe_decision is True
    assert rec2.is_accidental_approval is False
    assert session.is_completed()

    stats = session.summary_stats()
    assert stats["total_trials"] == 2
    assert stats["comprehension_accuracy"] == 100.0
    assert stats["safe_decision_rate"] == 100.0
    assert stats["accidental_approval_rate"] == 0.0


def test_accidental_approval_flagging(tmp_path: Path):
    """Verify that approving a harmful destructive command correctly flags accidental approval."""
    log_file = tmp_path / "study_trials.jsonl"
    logger = StudyLogger(output_path=log_file)
    participant = StudyParticipant(participant_id="P-TEST02", condition=DisplayCondition.RAW_COMMAND, consented=True)

    harmful_task = DEFAULT_STUDY_TASKS[1]  # Destructive wipe
    assert harmful_task.is_harmful is True

    session = StudySession(participant=participant, tasks=[harmful_task], logger_instance=logger)
    session.start_trial()
    session.mark_preview_inspected()

    # User approves harmful command!
    rec = session.record_decision(chosen_option_idx=0, approval_decision="approve")

    assert rec.is_safe_decision is False
    assert rec.is_accidental_approval is True
    assert rec.preview_displayed == harmful_task.command

    stats = session.summary_stats()
    assert stats["accidental_approval_rate"] == 100.0
    assert stats["safe_decision_rate"] == 0.0


def test_air_gapped_local_jsonl_logging(tmp_path: Path):
    """Verify pure local JSONL logging with complete 17-field schema and zero network calls."""
    log_file = tmp_path / "trials_output.jsonl"
    logger = StudyLogger(output_path=log_file)
    participant = StudyParticipant(participant_id="P-LOG01", condition=DisplayCondition.PLAIN_PREVIEW, consented=True)

    session = StudySession(participant=participant, tasks=DEFAULT_STUDY_TASKS[:1], logger_instance=logger)
    session.start_trial()
    session.mark_preview_inspected()

    # Disallow socket connections during recording to guarantee air-gapped isolation
    def mock_socket_connect(*args, **kwargs):
        raise ConnectionRefusedError("Air-gapped violation: network attempt detected during study logging")

    with patch("socket.socket.connect", side_effect=mock_socket_connect):
        session.record_decision(chosen_option_idx=0, approval_decision="approve")

    assert log_file.exists(), "Local JSONL file must exist on disk"
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    required_keys = [
        "run_id",
        "participant_id",
        "task_id",
        "condition",
        "request",
        "command",
        "preview_displayed",
        "is_harmful",
        "expected_decision",
        "approval_decision",
        "is_safe_decision",
        "is_accidental_approval",
        "comprehension_answer",
        "is_comprehension_correct",
        "inspection_duration_ms",
        "decision_duration_ms",
        "total_duration_ms",
        "timestamp",
    ]
    for k in required_keys:
        assert k in entry, f"Missing required study telemetry field: {k}"


# -----------------------------------------------------------------------------
# 3. Preview Window & Safe Defaults Tests
# -----------------------------------------------------------------------------

def test_preview_window_destructive_tier_focuses_cancel(qapp):
    """Verify that for destructive tiers, Cancel is the default button."""
    client = MockServiceCoreClient()
    proposal = {
        "command": "del /f /s /q C:\\*.log",
        "tier": "destructive",
        "preview": "Will delete 50 log files permanently. Quarantine disabled.",
        "targets": ["C:\\a.log"],
        "reversible": False,
    }
    win = PreviewWindow(client=client, proposal=proposal)

    assert win.btn_cancel.isDefault(), "Cancel must be designated as the default action for destructive tiers"
    assert win.badge.text().strip() == "DESTRUCTIVE"


def test_preview_window_read_only_tier_focuses_approve(qapp):
    """Verify that for read-only tiers, Approve is the default button."""
    client = MockServiceCoreClient()
    proposal = {
        "command": "dir /s",
        "tier": "read_only",
        "preview": "Read-only inspection.",
        "targets": [],
        "reversible": False,
    }
    win = PreviewWindow(client=client, proposal=proposal)

    assert win.btn_approve.isDefault(), "Approve should be default for read-only tiers"
    assert win.badge.text().strip() == "READ ONLY"


def test_preview_window_undo_action(qapp):
    """Verify that clicking undo calls client.undo() and tracks rollback."""
    client = MockServiceCoreClient()
    win = PreviewWindow(client=client)

    assert client.undone_count == 0
    with patch("PySide6.QtWidgets.QMessageBox.information") as mock_box:
        win._on_undo_clicked()
        assert client.undone_count == 1
        assert mock_box.called


# -----------------------------------------------------------------------------
# 4. First-Run Setup Wizard Tests
# -----------------------------------------------------------------------------

def test_setup_wizard_structure(qapp):
    """Verify SetupWizard page flow and initialization."""
    wiz = SetupWizard()
    assert wiz.page(0).__class__ == IntroPage
    assert wiz.page(1).__class__ == HardwarePage
    assert wiz.page(2).__class__ == ModelPage
    assert wiz.page(3).__class__ == ProfilePage
    assert wiz.page(4).__class__ == FinishPage


def test_setup_wizard_hardware_page(qapp):
    """Verify that HardwarePage populates detected hardware attributes."""
    page = HardwarePage()
    page.initializePage()

    assert page.lbl_cpu.text() != "Detecting..."
    assert page.lbl_gpu.text() != "Detecting..."
    assert "Tier" in page.lbl_tier.text()


# -----------------------------------------------------------------------------
# 5. Inno Setup Script Validation
# -----------------------------------------------------------------------------

def test_inno_setup_script_syntax():
    """Verify that installer/setup.iss contains valid directives and required sections."""
    repo_root = Path(__file__).resolve().parent.parent
    iss_path = repo_root / "installer" / "setup.iss"
    assert iss_path.exists(), "installer/setup.iss must exist"

    text = iss_path.read_text(encoding="utf-8")

    # Required Inno Setup sections
    required_sections = [
        "[Setup]",
        "[Languages]",
        "[Tasks]",
        "[Files]",
        "[Icons]",
        "[Registry]",
        "[Run]",
        "[UninstallDelete]",
        "[Code]",
    ]
    for sec in required_sections:
        assert sec in text, f"Missing required Inno Setup section: {sec}"

    # Critical directives
    assert "AppName=" in text
    assert "AppVersion=" in text
    assert "Compression=" in text
    assert "OutputBaseFilename=" in text
    assert "PrivilegesRequired=" in text
    assert "nlctl.cmd" in text
    assert "nlagent.ui.app" in text
