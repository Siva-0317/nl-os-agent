"""
src/nlagent/ui/tray.py - System Tray Icon and Desktop Shell Integration.

Provides:
1. NLSystemTrayIcon: Persistent taskbar notification icon.
2. Context Menu: Instant access to Quick Propose, Usability Study Mode, First-Run Wizard,
   Service Status toggle, and One-Click Undo.
3. System Notifications: Native desktop toasts for proposal readiness and rollback confirmations.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from nlagent.ui.client import get_ui_client
from nlagent.ui.consent import show_consent_dialog
from nlagent.ui.preview import PreviewWindow, StudyTrialDialog
from nlagent.ui.study import StudySession
from nlagent.ui.wizard import show_setup_wizard

logger = logging.getLogger("nlagent.ui.tray")


def create_default_tray_pixmap() -> QPixmap:
    """Generate a clean vector-style icon pixmap for the system tray."""
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Outer shield / badge circle
    painter.setBrush(QColor("#0969da"))  # Accent blue
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 28, 28, 8, 8)

    # Inner terminal prompt '>'
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(18)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, ">_")
    painter.end()

    return pix


class NLSystemTrayIcon(QSystemTrayIcon):
    """System tray manager for NL-OS-Agent desktop application."""

    def __init__(self, parent: Optional[QWidget] = None, client: Any = None) -> None:
        super().__init__(parent)
        self.client = client or get_ui_client()
        self.preview_window: Optional[PreviewWindow] = None
        self.study_dialog: Optional[StudyTrialDialog] = None

        self.setIcon(QIcon(create_default_tray_pixmap()))
        self.setToolTip("NL-OS-Agent — Safety-Verified Shell Assistant")

        self._init_menu()
        self.activated.connect(self._on_tray_activated)

    def _init_menu(self) -> None:
        menu = QMenu()

        # Quick propose
        self.act_propose = QAction("Quick Propose... (Ctrl+Alt+Space)", self)
        self.act_propose.triggered.connect(self.show_preview_window)
        menu.addAction(self.act_propose)

        menu.addSeparator()

        # Usability Study Mode (Task N7)
        self.act_study = QAction("Usability Study Mode (Task N7)...", self)
        self.act_study.triggered.connect(self.launch_study_mode)
        menu.addAction(self.act_study)

        # First-Run Setup Wizard (Task T34)
        self.act_wizard = QAction("Run Setup Wizard...", self)
        self.act_wizard.triggered.connect(self.launch_wizard)
        menu.addAction(self.act_wizard)

        menu.addSeparator()

        # Core service toggle
        self.act_service_status = QAction("Core Service: Running", self)
        self.act_service_status.setEnabled(False)
        menu.addAction(self.act_service_status)

        # One-Click Undo
        self.act_undo = QAction("Undo Last Command (nlctl undo)", self)
        self.act_undo.triggered.connect(self._on_undo_clicked)
        menu.addAction(self.act_undo)

        menu.addSeparator()

        # Exit
        self.act_quit = QAction("Exit NL-OS-Agent", self)
        self.act_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(self.act_quit)

        self.setContextMenu(menu)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_preview_window()

    def show_preview_window(self) -> None:
        """Display the floating command proposal window."""
        if not self.preview_window:
            self.preview_window = PreviewWindow(client=self.client)
        self.preview_window.show()
        self.preview_window.raise_()
        self.preview_window.activateWindow()

    def launch_study_mode(self) -> None:
        """Launch the usability study participant workflow."""
        participant = show_consent_dialog()
        if not participant:
            return

        session = StudySession(participant=participant)
        self.study_dialog = StudyTrialDialog(session=session)
        self.study_dialog.exec()

    def launch_wizard(self) -> None:
        """Launch the setup wizard."""
        show_setup_wizard()

    def _on_undo_clicked(self) -> None:
        res = self.client.undo()
        restored = res.get("restored_count", 0)
        self.showMessage(
            "NL-OS-Agent Rollback",
            f"Undo completed: {restored} file(s) restored from quarantine.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

