# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Kasa switch controls for Galileo's Equipment > Switches page.

A "Scan for KASA switches" button finds Kasa plugs and power strips on the
local network (UDP broadcast); each device found gets a box listing its
outlets with On / Off buttons. "Add by IP…" covers devices the broadcast can't
reach (another subnet). The device list persists across restarts, and each
device is polled in the background so state changed from the Kasa app or a
schedule shows up here. All network I/O runs off the UI thread, and one
unreachable device only greys out its own box (mirrors core's per-device
fault isolation, ARCH-060).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kasa_switch.adapter import KasaSwitchAdapter
from kasa_switch.protocol import discover
from kasa_switch.settings import KasaSettings

logger = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 5000

# The button matching the outlet's current state is filled, so On/Off reads at a glance.
_ON_STYLE = "QPushButton:checked { background-color: #2e7d32; color: white; font-weight: bold; }"
_OFF_STYLE = "QPushButton:checked { background-color: #c0392b; color: white; font-weight: bold; }"


class _Task(QObject):
    """Runs ``fn`` on a daemon thread and delivers its result (or exception)
    back on the UI thread through signals."""

    done = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[], object], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - reported to the UI, not swallowed
            self.failed.emit(exc)
        else:
            self.done.emit(result)


class AddKasaDeviceDialog(QDialog):
    """Prompts for the IP/hostname of a Kasa device to add by hand."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Kasa Device")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g. 192.168.1.50 or kasa-strip.local")
        form.addRow("Host / IP:", self.host_edit)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Optional display name (defaults to the host)")
        form.addRow("Label:", self.label_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "Host required", "Enter the device's IP address or hostname.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        host = self.host_edit.text().strip()
        label = self.label_edit.text().strip() or host
        return host, label


class _DeviceBox(QGroupBox):
    """One device: its outlets, each with On / Off buttons."""

    removed = Signal(str)

    def __init__(self, host: str, label: str, parent=None) -> None:
        super().__init__(label, parent)
        self.host = host
        self.label = label
        self.adapter = KasaSwitchAdapter(host=host, name=label)
        self._busy = False

        self._layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status_label = QLabel("Connecting…")
        self.status_label.setStyleSheet("color: gray;")
        header.addWidget(self.status_label, 1)
        remove_btn = QPushButton("Remove")
        remove_btn.setToolTip("Forget this device (it can be found again with a scan).")
        remove_btn.clicked.connect(lambda: self.removed.emit(self.host))
        header.addWidget(remove_btn)
        self._layout.addLayout(header)

        self._rows = QVBoxLayout()
        self._layout.addLayout(self._rows)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(_POLL_INTERVAL_MS)
        self.refresh()

    def _run(self, coro_fn: Callable[[], object], on_done: Callable[[], None] | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        task = _Task(lambda: asyncio.run(coro_fn()), self)
        task.done.connect(lambda _r: self._finish(None, on_done))
        task.failed.connect(lambda exc: self._finish(exc, on_done))
        task.start()

    def _finish(self, error: Exception | None, on_done: Callable[[], None] | None) -> None:
        self._busy = False
        if error is not None:
            logger.error("Kasa device %r at %s: %s", self.label, self.host, error)
            self.status_label.setText(f"Unreachable: {self.host}")
            self.status_label.setStyleSheet("color: #c0392b;")
            return
        self.status_label.setText(f"Connected: {self.host}")
        self.status_label.setStyleSheet("color: #2e7d32;")
        self._render()
        if on_done:
            on_done()

    def refresh(self) -> None:
        self._run(self.adapter.refresh)

    def _render(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.layout() is not None:
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w is not None:
                        w.deleteLater()
            elif item.widget() is not None:
                item.widget().deleteLater()
        for outlet in self.adapter.switches:
            row = QHBoxLayout()
            row.addWidget(QLabel(outlet.name), 1)
            on_btn = QPushButton("On")
            off_btn = QPushButton("Off")
            for btn, value in ((on_btn, True), (off_btn, False)):
                btn.setCheckable(True)
                btn.setChecked(outlet.state == value)
                btn.setStyleSheet(_ON_STYLE if value else _OFF_STYLE)
                btn.setFixedWidth(60)
                btn.clicked.connect(lambda _c, n=outlet.name, v=value: self._set(n, v))
                row.addWidget(btn)
            self._rows.addLayout(row)

    def _set(self, name: str, value: bool) -> None:
        if self._busy:
            self._render()  # a refresh is in flight; snap the buttons back and let the user retry
            return
        self._run(lambda: self.adapter.set_switch(name, value))


def build_kasa_page() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)

    heading = QLabel("Kasa switches")
    heading.setObjectName("CriteriaHeading")
    layout.addWidget(heading)

    button_row = QHBoxLayout()
    scan_btn = QPushButton("Scan for KASA switches")
    scan_btn.setObjectName("AccentButton")
    add_btn = QPushButton("Add by IP…")
    add_btn.setToolTip("Add a Kasa device the scan can't reach, such as one on another subnet.")
    status = QLabel("")
    status.setObjectName("StatusHint")
    button_row.addWidget(scan_btn)
    button_row.addWidget(add_btn)
    button_row.addWidget(status, 1)
    layout.addLayout(button_row)

    # No scroll area of its own: every device box shows at full height from the
    # top of the page, and the host page scrolls the whole switch area.
    boxes_layout = QVBoxLayout()
    boxes_layout.setContentsMargins(0, 0, 0, 0)
    layout.addLayout(boxes_layout)

    settings = KasaSettings()
    boxes: dict[str, _DeviceBox] = {}

    def _add_box(host: str, label: str) -> None:
        if host in boxes:
            return
        box = _DeviceBox(host, label)
        box.removed.connect(_remove_box)
        boxes[host] = box
        boxes_layout.addWidget(box)

    def _remove_box(host: str) -> None:
        box = boxes.pop(host, None)
        if box is not None:
            box.timer.stop()
            box.deleteLater()
        settings.remove_device(host)

    def _on_scan_done(found: list) -> None:
        scan_btn.setEnabled(True)
        new = 0
        for host, info in found:
            if host in boxes:
                continue
            label = info.get("alias") or host
            settings.add_device(host, label)
            _add_box(host, label)
            new += 1
        status.setText(
            f"Found {len(found)} Kasa device(s), {new} new." if found
            else "No Kasa devices answered. Check they are on this network and that "
            "Third-Party Compatibility is on in the Kasa app, or use Add by IP…"
        )

    def _on_scan_failed(exc: Exception) -> None:
        scan_btn.setEnabled(True)
        logger.error("Kasa scan failed: %s", exc)
        status.setText(f"Scan failed: {exc}")

    def _on_scan() -> None:
        scan_btn.setEnabled(False)
        status.setText("Scanning…")
        task = _Task(discover, page)
        task.done.connect(_on_scan_done)
        task.failed.connect(_on_scan_failed)
        task.start()

    def _on_add() -> None:
        dialog = AddKasaDeviceDialog(page)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        host, label = dialog.values()
        if host not in boxes:
            settings.add_device(host, label)
            _add_box(host, label)

    scan_btn.clicked.connect(_on_scan)
    add_btn.clicked.connect(_on_add)

    for device in settings.devices:
        host = device.get("host", "")
        if host:
            _add_box(host, device.get("label") or host)

    return page
