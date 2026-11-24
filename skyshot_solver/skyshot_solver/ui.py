# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The plugin's page under Options > Skyshot: where the program is, the star index, and the
download of the index (SKY-030 … SKY-070). Every control has an entry in the plugin's help file."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from skyshot_solver import backend, credits, data, engine
from skyshot_solver.settings import (
    DEADLINE_RANGE_S,
    MODES,
    SkyshotSettings,
    resolve_index,
)

logger = logging.getLogger(__name__)


class _Events(QObject):
    """Signals the worker thread emits; Qt delivers them on the UI thread."""
    planned = Signal(object, str)           # (Release, preset)
    progress = Signal(int, int, str)
    finished = Signal(str, bool)            # (message, ok)


def _gb(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


class SettingsPage(QWidget):
    """Options > Skyshot."""

    def __init__(self, settings: SkyshotSettings) -> None:
        super().__init__()
        self._settings = settings
        self._events = _Events(self)
        self._events.planned.connect(self._confirm_download)
        self._events.progress.connect(self._on_progress)
        self._events.finished.connect(self._on_finished)
        self._cancel = threading.Event()
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QLabel("Skyshot")
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        intro = QLabel("Skyshot is a fast blind plate solver: it needs no position hint. It needs its program "
                       "and a star index. Choose it as the solver under Options > Solve.")
        intro.setWordWrap(True)
        intro.setObjectName("StatusHint")
        layout.addWidget(intro)
        self.status = QLabel("")
        self.status.setObjectName("StatusHint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        form = QFormLayout()
        layout.addLayout(form)
        self.exe_edit = QLineEdit(settings.executable)
        self.exe_edit.setPlaceholderText("Found automatically if on PATH or in ~/.cargo/bin")
        form.addRow("Skyshot program", self._with_browse(self.exe_edit, "Skyshot program", folder=False))
        self.root_edit = QLineEdit(settings.data_root_text)
        self.root_edit.setPlaceholderText(str(settings.data_root))
        form.addRow("Index download folder", self._with_browse(self.root_edit, "Index download folder", folder=True))
        self.index_edit = QLineEdit(settings.index_override)
        self.index_edit.setPlaceholderText("The downloaded index if left blank")
        form.addRow("Index folder", self._with_browse(self.index_edit, "Index folder", folder=True))
        self.mode_combo = QComboBox()
        for mode in MODES:
            self.mode_combo.addItem(mode.capitalize(), mode)
        self.mode_combo.setCurrentIndex(MODES.index(settings.mode))
        form.addRow("Solve mode", self.mode_combo)
        self.deadline_spin = QSpinBox()
        self.deadline_spin.setRange(*DEADLINE_RANGE_S)
        self.deadline_spin.setSuffix(" s")
        self.deadline_spin.setValue(settings.deadline_s)
        form.addRow("Solve time limit", self.deadline_spin)

        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("AccentButton")
        self.save_btn.clicked.connect(self.save)
        layout.addWidget(self.save_btn)

        row = QHBoxLayout()
        self.engine_btn = QPushButton("Download Skyshot program")
        self.engine_btn.clicked.connect(self._start_engine)
        build = engine.build_for(engine.current_target())
        if build is None:
            self.engine_btn.setEnabled(False)
            self.engine_btn.setToolTip("No prebuilt Skyshot program is published for this computer yet. "
                                       "Build it from the Skyshot repository and set its path above.")
        self.full_btn = QPushButton("Download Full index")
        self.full_btn.clicked.connect(lambda: self._start_plan(data.PRESET_FULL))
        self.deep_btn = QPushButton("Download Deep index")
        self.deep_btn.clicked.connect(lambda: self._start_plan(data.PRESET_DEEP))
        self.cancel_btn = QPushButton("Cancel download")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel.set)
        self.check_btn = QPushButton("Check index")
        self.check_btn.clicked.connect(self.check_index)
        for btn in (self.engine_btn, self.full_btn, self.deep_btn, self.cancel_btn, self.check_btn):
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        self.detail = QLabel("")
        self.detail.setObjectName("StatusHint")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        layout.addStretch(1)
        self.credit = QLabel(credits.PAGE_CREDIT_HTML)
        self.credit.setObjectName("StatusHint")
        self.credit.setWordWrap(True)
        self.credit.setOpenExternalLinks(True)
        layout.addWidget(self.credit)
        self.refresh_status()

    # -- helpers -----------------------------------------------------------------------------

    def _with_browse(self, edit: QLineEdit, title: str, folder: bool) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("Browse…")

        def browse() -> None:
            path = (QFileDialog.getExistingDirectory(self, title) if folder
                    else QFileDialog.getOpenFileName(self, title)[0])
            if path:
                edit.setText(path)

        button.clicked.connect(browse)
        row.addWidget(edit, 1)
        row.addWidget(button)
        return box

    def _live_index(self) -> Path | None:
        """The index as the fields show it now, saved or not."""
        root = self.root_edit.text().strip()
        return resolve_index(self.index_edit.text(), Path(root) if root else self._settings.data_root)

    def refresh_status(self) -> None:
        exe = backend.find_executable(self.exe_edit.text().strip(), self._settings.data_root)
        index = self._live_index()
        slots = data.installed_slots(self._settings.data_root)
        parts = [f"Program: {exe or 'not found'}",
                 f"Index: {index if index is not None else 'not installed'}"]
        if slots:
            parts.append("Downloaded: " + ", ".join(slots))
        ready = bool(exe) and index is not None
        self.status.setText(("Ready. " if ready else "Not ready. ") + "   ".join(parts))

    def save(self) -> None:
        self._settings.executable = self.exe_edit.text()
        self._settings.data_root_text = self.root_edit.text()
        self._settings.index_override = self.index_edit.text()
        self._settings.mode = self.mode_combo.currentData()
        self._settings.deadline_s = self.deadline_spin.value()
        self.refresh_status()
        self.detail.setText("Saved.")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in (self.full_btn, self.deep_btn, self.check_btn, self.save_btn):
            btn.setEnabled(not busy)
        self.engine_btn.setEnabled(not busy and engine.build_for(engine.current_target()) is not None)
        self.cancel_btn.setEnabled(busy)
        self.bar.setVisible(busy)

    # -- downloading -------------------------------------------------------------------------

    def _start_engine(self) -> None:
        """Download the prebuilt Skyshot program for this computer, after asking."""
        if self._busy:
            return
        target = engine.current_target()
        build = engine.build_for(target)
        if build is None:
            return
        self.save()
        root = self._settings.data_root
        existing = engine.installed_executable(root)
        if existing is not None:
            self.detail.setText(f"Already downloaded: {existing}")
            self.refresh_status()
            return
        ask = QMessageBox.question(
            self, "Download Skyshot program",
            f"Download the Skyshot program for {target} ({_gb(build['bytes'])}) from Galileo's plugin releases?\n\n"
            f"{credits.DOWNLOAD_PROGRAM_NOTICE} It is checked against a checksum built into this plugin, and it "
            "runs on this computer like any program you install. It is not code-signed.\n\n"
            "Continue?")
        if ask != QMessageBox.StandardButton.Yes:
            return
        self._cancel.clear()
        self._set_busy(True)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)

        def work() -> None:
            try:
                exe = engine.download_engine(
                    root, progress=lambda d, t, text: self._events.progress.emit(d, t, text), cancel=self._cancel)
            except data.Cancelled:
                self._events.finished.emit("Download cancelled. Press the button again to resume.", True)
            except data.DataError as exc:
                self._events.finished.emit(str(exc), False)
            except Exception as exc:
                logger.exception("Skyshot program install failed")
                self._events.finished.emit(f"Install failed: {exc}", False)
            else:
                self._events.finished.emit(f"Skyshot program installed: {exe}", True)

        threading.Thread(target=work, name="skyshot-engine", daemon=True).start()

    def _start_plan(self, preset: str) -> None:
        """Fetch the release manifest (off the UI thread), then ask the user to confirm the sizes."""
        if self._busy:
            return
        self.save()
        self._cancel.clear()
        self._set_busy(True)
        self.bar.setRange(0, 0)
        self.detail.setText("Fetching the release list…")

        def work() -> None:
            try:
                release = data.fetch_release()
                release.plan(preset)
            except data.DataError as exc:
                self._events.finished.emit(str(exc), False)
                return
            self._events.planned.emit(release, preset)

        threading.Thread(target=work, name="skyshot-plan", daemon=True).start()

    def _confirm_download(self, release: data.Release, preset: str) -> None:
        root = self._settings.data_root
        todo = [(s, a) for s, a in release.plan(preset)
                if not (data.install_dir(root, release.release_id) / s).exists()]
        if not todo:
            self._on_finished("That index is already installed.", True)
            return
        total = sum(a.size for _, a in todo)
        need = data.disk_needed(release, preset, root, root / "download-cache" / release.release_id)
        ask = QMessageBox.question(
            self, "Download Skyshot index",
            f"Download {_gb(total)} into\n{data.install_dir(root, release.release_id)}\n\n"
            f"About {_gb(need)} of disk space is needed while it unpacks. The download can be cancelled "
            "and resumed, and each file is checked against its published checksum.\n\n"
            f"{credits.DOWNLOAD_INDEX_NOTICE}\n\nContinue?")
        if ask != QMessageBox.StandardButton.Yes:
            self._on_finished("Download cancelled.", True)
            return
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)

        def work() -> None:
            try:
                done = data.install_preset(
                    release, preset, root,
                    progress=lambda d, t, text: self._events.progress.emit(d, t, text), cancel=self._cancel)
            except data.Cancelled:
                self._events.finished.emit("Download cancelled. Press the button again to resume.", True)
            except data.DataError as exc:
                self._events.finished.emit(str(exc), False)
            except Exception as exc:
                logger.exception("Skyshot index install failed")
                self._events.finished.emit(f"Install failed: {exc}", False)
            else:
                self._events.finished.emit(f"Installed: {', '.join(done) or 'nothing new'}.", True)

        threading.Thread(target=work, name="skyshot-install", daemon=True).start()

    def _on_progress(self, done: int, total: int, text: str) -> None:
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(1000 * done / total) if total else 0)
        self.detail.setText(f"{text} ({_gb(done)} of {_gb(total)})" if total else text)

    def _on_finished(self, message: str, ok: bool) -> None:
        self._set_busy(False)
        self.detail.setText(message if ok else f"Failed: {message}")
        self.refresh_status()

    # -- checking ----------------------------------------------------------------------------

    def check_index(self) -> None:
        """Run ``skyshot inspect`` on the index and show what it says."""
        exe = backend.find_executable(self.exe_edit.text().strip(), self._settings.data_root)
        index = self._live_index()
        if not exe or index is None:
            self.detail.setText(
                ("The Skyshot program was not found. " if not exe else "")
                + ("No index was found in the index folder. It should hold a bundle.json, or an 'index' "
                   "folder that does." if index is None else ""))
            return
        self._set_busy(True)
        self.bar.setRange(0, 0)
        self.cancel_btn.setEnabled(False)

        def work() -> None:
            ok, text = asyncio.run(backend.inspect_index(exe, index))
            self._events.finished.emit(("Index OK. " if ok else "Index check failed. ") + text, ok)

        threading.Thread(target=work, name="skyshot-inspect", daemon=True).start()
