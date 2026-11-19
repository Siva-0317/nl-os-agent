"""
src/nlagent/ui/app.py - Master Desktop GUI Application Launcher.

CLI Flags:
  --chat                Open main Chat Assistant window (default when no mode flag given)
  --tray-only           Start minimized in system tray only
  --no-wizard           Skip automatic first-run model/runtime readiness check
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

import requests
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from nlagent.runtime.manager import RuntimeManager
from nlagent.runtime.models import resolve_best_model_path
from nlagent.ui.chat_window import ChatMainWindow
from nlagent.ui.client import get_ui_client
from nlagent.ui.consent import show_consent_dialog
from nlagent.ui.preview import PreviewWindow, StudyTrialDialog
from nlagent.ui.study import DisplayCondition, StudySession
from nlagent.ui.theme import apply_app_theme
from nlagent.ui.tray import NLSystemTrayIcon
from nlagent.ui.wizard import show_setup_wizard


def needs_first_run_setup() -> bool:
    """Return True if neither a live local server nor (llama-server + GGUF model) is ready."""
    for url in ("http://127.0.0.1:8080/health", "http://127.0.0.1:1234/v1/models"):
        try:
            r = requests.get(url, timeout=0.25)
            if r.status_code == 200:
                return False
        except Exception:
            continue
    has_binary = RuntimeManager.find_llama_server_binary() is not None
    has_model = resolve_best_model_path() is not None
    return not (has_binary and has_model)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NL-OS-Agent Desktop Application")
    parser.add_argument("--chat", action="store_true", help="Launch main Chat Assistant window")
    parser.add_argument("--tray-only", action="store_true", help="Start minimized in system tray only")
    parser.add_argument("--no-wizard", action="store_true", help="Skip automatic first-run setup check")
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
    apply_app_theme(app)

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

    # 4. Tray-Only Mode
    if args.tray_only:
        tray = NLSystemTrayIcon(client=client)
        tray.show()
        return app.exec()

    # 5. Default Desktop App Flow: First-Run Setup Check -> ChatMainWindow
    if not args.mock_core and not args.no_wizard and needs_first_run_setup():
        accepted = show_setup_wizard()
        if not accepted and needs_first_run_setup():
            return 0

    chat_window = ChatMainWindow(client=client, auto_start_runtime=not args.mock_core)
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = NLSystemTrayIcon(client=client, chat_window=chat_window)
        tray.show()
        chat_window.tray_icon = tray  # keep reference alive
    chat_window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

