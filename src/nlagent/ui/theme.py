"""
src/nlagent/ui/theme.py - Unified Modern Slate Dark Theme for NL-OS-Agent UI.

Sets Fusion style + consistent dark QPalette + QSS stylesheet across the entire
PySide6 application so text, inputs, dropdowns, checkboxes, lists, and cards
remain high-contrast regardless of Windows OS Dark/Light mode overrides.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget

BG_CANVAS = "#0d1117"
BG_PANEL = "#161b22"
BG_ELEVATED = "#21262d"
BG_INPUT = "#0d1117"
BG_TERMINAL = "#010409"
BORDER_SUBTLE = "#30363d"
BORDER_STRONG = "#3d444d"

TEXT_PRIMARY = "#e6edf3"
TEXT_MUTED = "#8b949e"
TEXT_CODE = "#7ee787"

ACCENT_BLUE = "#1f6feb"
ACCENT_BLUE_HOVER = "#388bfd"
ACCENT_GREEN = "#238636"
ACCENT_GREEN_HOVER = "#2ea043"
ACCENT_RED = "#da3633"
ACCENT_RED_HOVER = "#f85149"
ACCENT_AMBER = "#9e6a03"
ACCENT_AMBER_HOVER = "#bb8009"


APP_STYLESHEET = f"""
QMainWindow, QDialog, QWizard, QWizardPage, QMessageBox {{
    background-color: {BG_CANVAS};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 13px;
}}

QWidget {{
    color: {TEXT_PRIMARY};
}}

QLabel {{
    background-color: transparent;
    border: none;
    color: {TEXT_PRIMARY};
}}

QLabel[role="muted"] {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}

QLabel[role="section"] {{
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
    padding-top: 2px;
}}

QFrame#TopBar, QFrame#SidebarPanel, QFrame#CardFrame, QFrame#ProposalCard, QFrame#WelcomeCard {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 10px;
}}

QFrame#UserBubble {{
    background-color: #13233a;
    border: 1px solid {ACCENT_BLUE};
    border-radius: 10px;
}}

QFrame#PreviewBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
}}

QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACCENT_BLUE};
    selection-color: #ffffff;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT_BLUE_HOVER};
}}

QLineEdit[role="terminal"] {{
    background-color: {BG_TERMINAL};
    color: {TEXT_CODE};
    border: 1px solid {BORDER_SUBTLE};
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    padding: 8px 10px;
}}

QTextEdit[role="terminal"] {{
    background-color: {BG_TERMINAL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    padding: 8px;
}}

QComboBox {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 20px;
}}

QComboBox:hover {{
    border-color: #58a6ff;
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_BLUE};
    selection-color: #ffffff;
    outline: 0px;
}}

QListWidget {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    padding: 4px;
    outline: 0px;
}}

QListWidget::item {{
    color: {TEXT_PRIMARY};
    padding: 7px 9px;
    border-radius: 5px;
    margin: 1px 0px;
}}

QListWidget::item:hover {{
    background-color: {BG_ELEVATED};
}}

QListWidget::item:selected {{
    background-color: #163356;
    color: #ffffff;
    border: 1px solid {ACCENT_BLUE};
}}

QPushButton {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: #30363d;
    border-color: #8b949e;
}}

QPushButton:pressed {{
    background-color: #161b22;
}}

QPushButton:disabled {{
    background-color: #161b22;
    color: #6e7681;
    border-color: {BORDER_SUBTLE};
}}

QPushButton[variant="primary"] {{
    background-color: {ACCENT_BLUE};
    color: #ffffff;
    border: 1px solid {ACCENT_BLUE_HOVER};
}}

QPushButton[variant="primary"]:hover {{
    background-color: {ACCENT_BLUE_HOVER};
}}

QPushButton[variant="success"] {{
    background-color: {ACCENT_GREEN};
    color: #ffffff;
    border: 1px solid {ACCENT_GREEN_HOVER};
}}

QPushButton[variant="success"]:hover {{
    background-color: {ACCENT_GREEN_HOVER};
}}

QPushButton[variant="danger"] {{
    background-color: {ACCENT_RED};
    color: #ffffff;
    border: 1px solid {ACCENT_RED_HOVER};
}}

QPushButton[variant="danger"]:hover {{
    background-color: {ACCENT_RED_HOVER};
}}

QPushButton[variant="warning"] {{
    background-color: {ACCENT_AMBER};
    color: #ffffff;
    border: 1px solid {ACCENT_AMBER_HOVER};
}}

QPushButton[variant="warning"]:hover {{
    background-color: {ACCENT_AMBER_HOVER};
}}

QCheckBox, QRadioButton {{
    color: {TEXT_PRIMARY};
    spacing: 8px;
    background-color: transparent;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_STRONG};
    background-color: {BG_INPUT};
}}

QCheckBox::indicator {{
    border-radius: 4px;
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {ACCENT_BLUE};
    border-color: {ACCENT_BLUE_HOVER};
}}

QProgressBar {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    text-align: center;
    height: 16px;
}}

QProgressBar::chunk {{
    background-color: {ACCENT_BLUE};
    border-radius: 5px;
}}

QScrollArea#ChatScrollArea {{
    background-color: {BG_CANVAS};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 10px;
}}

QWidget#FeedCanvas {{
    background-color: {BG_CANVAS};
}}

QSplitter::handle {{
    background-color: {BORDER_SUBTLE};
    width: 2px;
    margin: 4px 2px;
}}

QScrollBar:vertical {{
    background: {BG_CANVAS};
    width: 10px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    min-height: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical:hover {{
    background: #6e7681;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QToolTip {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    padding: 4px 8px;
}}
"""


def build_dark_palette() -> QPalette:
    """Create a dark QPalette matching Slate Dark so native popups and dialogs stay readable."""
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG_CANVAS))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG_INPUT))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Button, QColor(BG_ELEVATED))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT_BLUE))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    return pal


def apply_app_theme(target: QCoreApplication | QWidget) -> None:
    """Apply Fusion style + Slate Dark palette and stylesheet idempotently."""
    inst = QApplication.instance()
    app = inst if isinstance(inst, QApplication) else (target if isinstance(target, QApplication) else None)
    if app is not None and not getattr(app, "_nlagent_theme_applied", False):
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            app.setStyle(fusion)
        app.setPalette(build_dark_palette())
        app.setStyleSheet(APP_STYLESHEET)
        setattr(app, "_nlagent_theme_applied", True)
    elif isinstance(target, QWidget) and app is None:
        target.setPalette(build_dark_palette())
        target.setStyleSheet(APP_STYLESHEET)


def set_style_property(widget: QWidget, name: str, value: str) -> None:
    """Set a Qt dynamic property and repolish so QSS `[prop="val"]` updates immediately."""
    widget.setProperty(name, value)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()
