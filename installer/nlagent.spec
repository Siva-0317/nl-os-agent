# -*- mode: python ; coding: utf-8 -*-
"""
installer/nlagent.spec - PyInstaller Specification for NL-OS-Agent (Sprint 4).

Packages the application into a standalone onedir distribution (dist/nlagent/)
containing:
  - nlagent-gui.exe : Windowed PySide6 Desktop GUI application
  - nlctl.exe       : Console CLI utility for shell interactions
"""

import os
import sys

block_cipher = None

# Base repository root directory
base_dir = os.path.abspath(os.path.join(SPECPATH, ".."))
src_dir = os.path.join(base_dir, "src")

# Required hidden imports for nlagent, PySide6, and standard libs
hidden_imports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "requests",
    "rich",
    "dotenv",
    "sqlite3",
    "urllib.request",
    "nlagent",
    "nlagent.cli",
    "nlagent.cli.main",
    "nlagent.ui",
    "nlagent.ui.app",
    "nlagent.ui.theme",
    "nlagent.ui.chat_window",
    "nlagent.ui.model_manager",
    "nlagent.ui.wizard",
    "nlagent.ui.tray",
    "nlagent.ui.client",
    "nlagent.daemon",
    "nlagent.daemon.propose",
    "nlagent.safety",
    "nlagent.safety.effects",
    "nlagent.runtime",
    "nlagent.runtime.manager",
    "nlagent.runtime.models",
    "nlagent.backends",
    "nlagent.backends.llama_server",
]

# Excluded bulky scientific / training packages to keep bundle lightweight (<120MB)
excludes = [
    "torch",
    "torchvision",
    "torchaudio",
    "torchdata",
    "torchtext",
    "transformers",
    "unsloth",
    "accelerate",
    "scipy",
    "pandas",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
    "tensorboard",
    "cv2",
    "PIL",
    "albumentations",
    "ultralytics",
    "dill",
    "datasets",
    "sklearn",
    "triton",
    "tkinter",
]

# Extra assets and metadata
datas = []
manifest_file = os.path.join(base_dir, "models", "manifest.json")
if os.path.exists(manifest_file):
    datas.append((manifest_file, "models"))
notice_file = os.path.join(base_dir, "models", "NOTICE")
if os.path.exists(notice_file):
    datas.append((notice_file, "models"))

# -----------------------------------------------------------------------------
# 1. Desktop GUI Binary (nlagent-gui.exe)
# -----------------------------------------------------------------------------
a_gui = Analysis(
    [os.path.join(src_dir, "nlagent", "ui", "app.py")],
    pathex=[src_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz_gui = PYZ(a_gui.pure, a_gui.zipped_data, cipher=block_cipher)

exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="nlagent-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Windowed GUI application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# -----------------------------------------------------------------------------
# 2. Console CLI Binary (nlctl.exe)
# -----------------------------------------------------------------------------
a_cli = Analysis(
    [os.path.join(src_dir, "nlagent", "cli", "main.py")],
    pathex=[src_dir],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data, cipher=block_cipher)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="nlctl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Console application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# -----------------------------------------------------------------------------
# 3. Combined Onedir Collection (dist/nlagent/)
# -----------------------------------------------------------------------------
coll = COLLECT(
    exe_gui,
    a_gui.binaries,
    a_gui.zipfiles,
    a_gui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.zipfiles,
    a_cli.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="nlagent",
)

