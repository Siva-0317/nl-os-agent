"""
src/nlagent/ui/app.py - Master Desktop GUI Application Launcher.

CLI Flags:
  --study               Directly launch Task N7 Usability Study Mode
  --condition {plain_preview,raw_command}  Preset study evaluation condition
  --wizard              Directly launch Task T34 First-Run Setup Wizard
  --propose "query"     Open command preview dialog with pre-filled natural language request
  --offscreen           Run Qt in headless offscreen mode (for testing/automation)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from PySide6.QtWidgets import QApplication

from nlagent.ui.client import get_ui_client
from nlagent.ui.consent import show_consent_dialog
from nlagent.ui.preview import PreviewWindow, StudyTrialDialog
from nlagent.ui.study import DisplayCondition, StudySession
from nlagent.ui.tray import NLSystemTrayIcon
from nlagent.ui.wizard import show_setup_wizard


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NL-OS-Agent Desktop Application")
    parser.add_argument("--study", action="store_true", help="Launch Human-Factors Usability Study Mode")
    parser.add_argument(
        "--condition",
        choices=["plain_preview", "raw_command"],
        default=None,
        help="Preset display condition for study mode",
    )
    parser.add_argument("--wizard", action="store_true", help="Launch First-Run Setup Wizard")
    parser.add_argument("--propose", type=str, default=None, help="Pre-filled query to propose")
    parser.add_argument("--offscreen", action="store_true", help="Run Qt in headless offscreen mode")
    parser.add_argument("--mock-core", action="store_true", help="Use mock core client without spawning daemon")
    args = parser.parse_args(argv)

    if args.offscreen or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("NL-OS-Agent")
    app.setOrganizationName("NL-OS-Agent Research Team")

    client = get_ui_client(mock=args.mock_core)

    # 1. Wizard Mode
    if args.wizard:
        show_setup_wizard()
        return 0

    # 2. Study Mode
    if args.study:
        cond = DisplayCondition(args.condition) if args.condition else None
        participant = show_consent_dialog(fixed_condition=cond)
        if not participant:
            return 0
        session = StudySession(participant=participant)
        dlg = StudyTrialDialog(session=session)
        dlg.exec()
        return 0

    # 3. Direct Propose Mode
    if args.propose:
        window = PreviewWindow(client=client)
        window.query_input.setText(args.propose)
        window._on_submit_query()
        window.show()
        return app.exec()

    # 4. Standard Desktop Tray Mode
    tray = NLSystemTrayIcon(client=client)
    tray.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

