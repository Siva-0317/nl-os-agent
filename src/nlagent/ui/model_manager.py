"""
src/nlagent/ui/model_manager.py - Real Model Store & Runtime Manager UI.

Provides asynchronous QThread workers and Qt widgets wired to `nlagent.runtime.models`
for resumable HTTP Range downloads, SHA-256 verification, custom local `.gguf` import,
and `llama-server` runtime pack installation.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nlagent.runtime.autotune import detect_hardware
from nlagent.runtime.manager import RuntimeManager
from nlagent.runtime.models import (
    ChecksumMismatchError,
    DownloadCancelledError,
    ModelEntry,
    ModelStatus,
    download_model,
    get_models_dir,
    get_runtime_bin_dir,
    get_search_model_dirs,
    import_local_model,
    install_runtime_zip,
    list_models_status,
    resolve_best_model_path,
)


from nlagent.ui.theme import apply_app_theme, set_style_property


def _format_gb(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "Unknown size"
    return f"{num_bytes / (1024 ** 3):.2f} GB"


class ModelDownloadWorker(QThread):
    """Background worker executing resumable HTTP Range download with SHA-256 check."""

    progress_updated = Signal(int, int, float)  # downloaded_bytes, total_bytes, mbps
    download_finished = Signal(str)
    download_cancelled = Signal()
    download_failed = Signal(str)

    def __init__(
        self,
        entry: ModelEntry,
        dest_dir: Path | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.dest_dir = dest_dir
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        start_ts = time.monotonic()
        last_emit = 0.0
        last_bytes = 0

        def on_progress(downloaded: int, total: int | None) -> None:
            nonlocal last_emit, last_bytes
            now = time.monotonic()
            total_val = int(total or self.entry.size or 0)
            if now - last_emit >= 0.10 or (total_val > 0 and downloaded >= total_val):
                elapsed = max(0.05, now - start_ts)
                speed_mbps = (downloaded - last_bytes) / max(0.05, now - (last_emit or start_ts)) / (1024 * 1024)
                if last_emit == 0.0:
                    speed_mbps = downloaded / elapsed / (1024 * 1024)
                last_emit = now
                last_bytes = downloaded
                self.progress_updated.emit(int(downloaded), total_val, float(max(0.0, speed_mbps)))

        try:
            final_path = download_model(
                entry=self.entry,
                dest_dir=self.dest_dir,
                progress_callback=on_progress,
                cancel_check=self._cancel_event.is_set,
            )
            self.download_finished.emit(str(final_path))
        except DownloadCancelledError:
            self.download_cancelled.emit()
        except Exception as exc:
            self.download_failed.emit(str(exc))


class ModelImportWorker(QThread):
    """Background worker importing and hashing local GGUF model files."""

    import_finished = Signal(str)
    import_failed = Signal(str, str)  # error_msg, kind ("checksum" | "error")

    def __init__(
        self,
        source_path: Path,
        entry: ModelEntry | None = None,
        dest_dir: Path | None = None,
        verify_sha: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.source_path = source_path
        self.entry = entry
        self.dest_dir = dest_dir
        self.verify_sha = verify_sha

    def run(self) -> None:
        try:
            dest = import_local_model(
                source_path=self.source_path,
                entry=self.entry,
                dest_dir=self.dest_dir,
                verify_sha=self.verify_sha,
                force=True,
            )
            self.import_finished.emit(str(dest))
        except ChecksumMismatchError as exc:
            self.import_failed.emit(str(exc), "checksum")
        except Exception as exc:
            self.import_failed.emit(str(exc), "error")


class ModelManagerWidget(QWidget):
    """Reusable widget for inspecting, downloading, importing, and selecting local GGUFs and runtime."""

    model_ready_changed = Signal(bool)
    active_model_selected = Signal(str)

    def __init__(
        self,
        models_dir: str | Path | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.custom_models_dir = Path(models_dir) if models_dir else None
        self.statuses: list[ModelStatus] = []
        self.extra_ggufs: list[Path] = []
        self._dl_worker: ModelDownloadWorker | None = None
        self._import_worker: ModelImportWorker | None = None
        self._selected_model_path: str | None = None

        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Runtime status card
        runtime_frame = QFrame()
        runtime_frame.setObjectName("CardFrame")
        rt_layout = QVBoxLayout(runtime_frame)
        rt_layout.setContentsMargins(10, 10, 10, 10)
        rt_layout.setSpacing(6)

        rt_top = QHBoxLayout()
        self.lbl_runtime_status = QLabel("Checking inference runtime (llama-server)...")
        self.lbl_runtime_status.setStyleSheet("font-size: 12px; font-weight: bold; color: #e6edf3;")
        self.lbl_runtime_status.setWordWrap(True)
        rt_top.addWidget(self.lbl_runtime_status, 1)

        self.btn_install_runtime_zip = QPushButton("Install Runtime .zip...")
        self.btn_install_runtime_zip.clicked.connect(self._on_install_runtime_zip)
        rt_top.addWidget(self.btn_install_runtime_zip)

        self.btn_locate_runtime = QPushButton("Browse Binary...")
        self.btn_locate_runtime.clicked.connect(self._on_locate_runtime_binary)
        rt_top.addWidget(self.btn_locate_runtime)
        rt_layout.addLayout(rt_top)
        layout.addWidget(runtime_frame)

        # Models list
        self.lbl_store_dir = QLabel("")
        self.lbl_store_dir.setStyleSheet("font-size: 11px; color: #8b949e;")
        layout.addWidget(self.lbl_store_dir)

        self.models_list = QListWidget()
        self.models_list.setMinimumHeight(145)
        self.models_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.models_list)

        # Download / import progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.lbl_transfer_status = QLabel("")
        self.lbl_transfer_status.setWordWrap(True)
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #58a6ff;")
        layout.addWidget(self.lbl_transfer_status)

        # Action buttons
        btn_row = QHBoxLayout()
        self.btn_download = QPushButton("Download / Resume Selected")
        set_style_property(self.btn_download, "variant", "primary")
        self.btn_download.clicked.connect(self._on_download_clicked)
        btn_row.addWidget(self.btn_download)

        self.btn_cancel_download = QPushButton("Pause / Cancel")
        self.btn_cancel_download.setEnabled(False)
        self.btn_cancel_download.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.btn_cancel_download)

        self.btn_import = QPushButton("Import Local .gguf...")
        self.btn_import.clicked.connect(self._on_import_clicked)
        btn_row.addWidget(self.btn_import)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(self.btn_refresh)
        layout.addLayout(btn_row)

    def refresh(self) -> None:
        """Reload runtime detection status and manifest/local GGUF inventory."""
        bin_path = RuntimeManager.find_llama_server_binary()
        try:
            hw = detect_hardware(bin_path)
            gpu_desc = f"{hw.gpus[0].name} ({hw.gpus[0].backend.upper()})" if hw.gpus else "CPU Mode"
        except Exception:
            gpu_desc = "Auto-detected"

        if bin_path:
            self.lbl_runtime_status.setText(f"Inference Engine Ready: {Path(bin_path).name} ({gpu_desc})")
            self.lbl_runtime_status.setStyleSheet("font-size: 12px; font-weight: bold; color: #3fb950;")
            self.lbl_runtime_status.setToolTip(bin_path)
        else:
            self.lbl_runtime_status.setText(
                f"Inference Engine Missing ({gpu_desc}) — import llama-server .zip or locate binary."
            )
            self.lbl_runtime_status.setStyleSheet("font-size: 12px; font-weight: bold; color: #f85149;")

        store_dir = get_models_dir(self.custom_models_dir)
        self.lbl_store_dir.setText(f"Model Store: {store_dir}")

        try:
            self.statuses = list_models_status(models_dir=self.custom_models_dir)
        except Exception as exc:
            self.statuses = []
            self.lbl_transfer_status.setText(f"Manifest read warning: {exc}")

        known_paths = {
            str(s.local_path.resolve()).lower()
            for s in self.statuses
            if s.status == "ready" and s.local_path
        }
        self.extra_ggufs = []
        for d in get_search_model_dirs(self.custom_models_dir):
            if d.is_dir():
                for f in sorted(d.glob("*.gguf")):
                    if str(f.resolve()).lower() not in known_paths:
                        self.extra_ggufs.append(f)
                        known_paths.add(str(f.resolve()).lower())

        self.models_list.clear()
        ready_count = 0
        default_row = 0

        for idx, st in enumerate(self.statuses):
            entry = st.entry
            if st.status == "ready":
                ready_count += 1
                badge = "[READY]"
                detail = _format_gb(st.downloaded_bytes)
            elif st.status == "partial":
                badge = f"[PARTIAL {st.progress_percent:.1f}%]"
                detail = f"{_format_gb(st.downloaded_bytes)} / {_format_gb(st.total_bytes)}"
            else:
                badge = "[MISSING]"
                detail = _format_gb(st.total_bytes)

            tag = " ★ Recommended" if entry.recommended else ""
            ram_warn = "" if st.has_sufficient_ram else f" | Low RAM ({st.system_ram_gb:.1f} GB < {entry.min_ram_gb:.0f} GB)"
            text = f"{badge} {entry.canonical_filename}{tag}\n    {entry.description} ({detail}{ram_warn})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ("manifest", idx))
            if st.status == "ready" and entry.recommended:
                default_row = self.models_list.count()
            self.models_list.addItem(item)

        for idx, path in enumerate(self.extra_ggufs):
            ready_count += 1
            size_str = _format_gb(path.stat().st_size)
            text = f"[READY - LOCAL] {path.name}\n    Custom local GGUF ({size_str}) at {path}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ("extra", idx))
            self.models_list.addItem(item)

        if self.models_list.count() > 0:
            self.models_list.setCurrentRow(default_row)

        best = resolve_best_model_path(models_dir=self.custom_models_dir)
        self._selected_model_path = str(best) if best else self._selected_model_path
        has_ready = ready_count > 0
        self.model_ready_changed.emit(has_ready)
        self._update_buttons_state()

    def selected_manifest_status(self) -> ModelStatus | None:
        item = self.models_list.currentItem()
        if not item:
            return None
        meta = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(meta, tuple) and meta[0] == "manifest":
            idx = int(meta[1])
            if 0 <= idx < len(self.statuses):
                return self.statuses[idx]
        return None

    def selected_model_path(self) -> Path | None:
        item = self.models_list.currentItem()
        if item:
            meta = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(meta, tuple):
                kind, idx = meta
                if kind == "manifest" and 0 <= idx < len(self.statuses):
                    st = self.statuses[idx]
                    if st.status == "ready" and st.local_path:
                        return st.local_path
                elif kind == "extra" and 0 <= idx < len(self.extra_ggufs):
                    return self.extra_ggufs[idx]
        return resolve_best_model_path(models_dir=self.custom_models_dir)

    def _on_selection_changed(self, _row: int) -> None:
        p = self.selected_model_path()
        if p:
            self._selected_model_path = str(p)
            self.active_model_selected.emit(str(p))
        self._update_buttons_state()

    def _update_buttons_state(self) -> None:
        busy = (self._dl_worker is not None and self._dl_worker.isRunning()) or (
            self._import_worker is not None and self._import_worker.isRunning()
        )
        st = self.selected_manifest_status()
        can_download = bool(st and st.entry.url and st.status in ("missing", "partial") and not busy)
        self.btn_download.setEnabled(can_download)
        self.btn_cancel_download.setEnabled(bool(self._dl_worker and self._dl_worker.isRunning()))
        self.btn_import.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)

    def _on_download_clicked(self) -> None:
        st = self.selected_manifest_status()
        if not st:
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(st.progress_percent))
        self.lbl_transfer_status.setText(f"Connecting to download {st.entry.canonical_filename}...")
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #0969da;")

        self._dl_worker = ModelDownloadWorker(entry=st.entry, dest_dir=self.custom_models_dir, parent=self)
        self._dl_worker.progress_updated.connect(self._on_download_progress)
        self._dl_worker.download_finished.connect(self._on_download_finished)
        self._dl_worker.download_cancelled.connect(self._on_download_cancelled)
        self._dl_worker.download_failed.connect(self._on_download_failed)
        self._dl_worker.start()
        self._update_buttons_state()

    def _on_cancel_clicked(self) -> None:
        if self._dl_worker and self._dl_worker.isRunning():
            self.lbl_transfer_status.setText("Pausing download safely (partial file kept for resume)...")
            self._dl_worker.request_cancel()

    def _on_download_progress(self, downloaded: int, total: int, mbps: float) -> None:
        if total > 0:
            pct = min(100, int((downloaded / total) * 100))
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(pct)
            self.lbl_transfer_status.setText(
                f"Downloading {pct}% ({downloaded / (1024**2):.1f} / {total / (1024**2):.1f} MB) — {mbps:.1f} MB/s"
            )
        else:
            self.progress_bar.setRange(0, 0)
            self.lbl_transfer_status.setText(f"Downloaded {downloaded / (1024**2):.1f} MB — {mbps:.1f} MB/s")

    def _on_download_finished(self, path_str: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.lbl_transfer_status.setText(f"✔ Downloaded and SHA-256 verified: {path_str}")
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #1a7f37; font-weight: bold;")
        self.refresh()
        self.active_model_selected.emit(path_str)

    def _on_download_cancelled(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.lbl_transfer_status.setText("Download paused. Click 'Download / Resume Selected' anytime to continue.")
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #9a6700;")
        self.refresh()

    def _on_download_failed(self, err: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.lbl_transfer_status.setText(f"Download error (partial progress saved if started): {err}")
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #cf222e;")
        self.refresh()

    def _on_import_clicked(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Quantized GGUF Model",
            str(Path.home() / "Downloads"),
            "GGUF Models (*.gguf);;All Files (*)",
        )
        if not file_path:
            return
        src = Path(file_path)
        selected_status = self.selected_manifest_status()
        entry = selected_status.entry if (selected_status and src.name == selected_status.entry.canonical_filename) else None
        self._start_import(src, entry=entry, verify_sha=bool(entry))

    def _start_import(self, src: Path, entry: ModelEntry | None, verify_sha: bool) -> None:
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.lbl_transfer_status.setText(f"Importing {src.name} into model store...")
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #0969da;")

        self._import_worker = ModelImportWorker(
            source_path=src,
            entry=entry,
            dest_dir=self.custom_models_dir,
            verify_sha=verify_sha,
            parent=self,
        )
        self._import_worker.import_finished.connect(self._on_import_finished)
        self._import_worker.import_failed.connect(
            lambda msg, kind: self._on_import_failed(src, msg, kind)
        )
        self._import_worker.start()
        self._update_buttons_state()

    def _on_import_finished(self, dest_str: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.lbl_transfer_status.setText(f"✔ Imported model cleanly: {dest_str}")
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #1a7f37; font-weight: bold;")
        self.refresh()
        self.active_model_selected.emit(dest_str)

    def _on_import_failed(self, src: Path, err: str, kind: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        if kind == "checksum":
            ans = QMessageBox.question(
                self,
                "Custom Model Checksum",
                f"{err}\n\nIs this a custom/newly trained GGUF (such as V2)? Import it anyway?",
            )
            if ans == QMessageBox.StandardButton.Yes:
                self._start_import(src, entry=None, verify_sha=False)
                return
        self.lbl_transfer_status.setText(f"Import failed: {err}")
        self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #cf222e;")
        self._update_buttons_state()

    def _on_install_runtime_zip(self) -> None:
        zip_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select llama-server Runtime Pack (.zip)",
            str(Path.home() / "Downloads"),
            "Zip Archives (*.zip);;All Files (*)",
        )
        if not zip_path:
            return
        try:
            exe = install_runtime_zip(zip_path)
            self.lbl_transfer_status.setText(f"✔ Installed inference runtime: {exe}")
            self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #1a7f37; font-weight: bold;")
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Runtime Installation Failed", str(exc))

    def _on_locate_runtime_binary(self) -> None:
        exe_path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate llama-server Executable",
            str(Path.cwd()),
            "Executables (llama-server.exe llama-server);;All Files (*)",
        )
        if not exe_path:
            return
        src = Path(exe_path)
        try:
            dest_dir = get_runtime_bin_dir()
            shutil.copy2(src, dest_dir / src.name)
            for dll in src.parent.glob("*.dll"):
                target_dll = dest_dir / dll.name
                if not target_dll.exists():
                    shutil.copy2(dll, target_dll)
            self.lbl_transfer_status.setText(f"✔ Runtime linked to {dest_dir / src.name}")
            self.lbl_transfer_status.setStyleSheet("font-size: 11px; color: #1a7f37; font-weight: bold;")
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Runtime Copy Failed", str(exc))


class ModelManagerDialog(QDialog):
    """Standalone modal dialog exposing ModelManagerWidget from ChatMainWindow."""

    model_selected = Signal(str)

    def __init__(
        self,
        models_dir: str | Path | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        apply_app_theme(self)
        self.setWindowTitle("NL-OS-Agent — Model & Runtime Manager")
        self.resize(660, 500)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.manager = ModelManagerWidget(models_dir=models_dir, parent=self)
        self.manager.active_model_selected.connect(self.model_selected.emit)
        layout.addWidget(self.manager)

        footer = QHBoxLayout()
        footer.addStretch()
        self.btn_close = QPushButton("Done")
        self.btn_close.setDefault(True)
        self.btn_close.clicked.connect(self.accept)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

