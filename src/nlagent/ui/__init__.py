"""
src/nlagent/ui/__init__.py - NL-OS-Agent Desktop UI & Usability Study Core.
"""

from __future__ import annotations

from nlagent.ui.client import MockServiceCoreClient, get_ui_client
from nlagent.ui.consent import ConsentDialog, show_consent_dialog
from nlagent.ui.preview import PreviewWindow, StudyTrialDialog
from nlagent.ui.study import (
    DEFAULT_STUDY_TASKS,
    DisplayCondition,
    StudyLogger,
    StudyParticipant,
    StudySession,
    StudyTask,
    StudyTrialRecord,
)
from nlagent.ui.tray import NLSystemTrayIcon
from nlagent.ui.wizard import SetupWizard, show_setup_wizard

__all__ = [
    "DEFAULT_STUDY_TASKS",
    "ConsentDialog",
    "DisplayCondition",
    "MockServiceCoreClient",
    "NLSystemTrayIcon",
    "PreviewWindow",
    "SetupWizard",
    "StudyLogger",
    "StudyParticipant",
    "StudySession",
    "StudyTask",
    "StudyTrialDialog",
    "StudyTrialRecord",
    "get_ui_client",
    "show_consent_dialog",
    "show_setup_wizard",
]

