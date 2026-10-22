# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""SwitchBot controls for Galileo's Equipment > Switches page.

A "Scan for SwitchBots" button finds Bots in Bluetooth range; each one gets a
box with On / Off / Press buttons and its battery level. "Add by address…"
covers a Bot the scan misses. Unlike a LAN device, a Bot is not polled in the
background — every contact wakes it and drains its coin cell — so its status
is read once when the box appears and again on **Check**. All Bluetooth I/O
runs off the UI thread, and one unreachable Bot only greys out its own box
(mirrors core's per-device fault isolation, ARCH-060).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
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

from switchbot_bot import protocol
from switchbot_bot.adapter import SwitchBotAdapter
from switchbot_bot.settings import SwitchBotSettings

logger = logging.getLogger(__name__)


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


class AddBotDialog(QDialog):
    """Prompts for the Bluetooth address of a Bot to add by hand."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add SwitchBot")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("e.g. C1:2B:3D:4E:5F:60 (macOS: the UUID from a scan)")
        form.addRow("Address:", self.address_edit)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("e.g. Seestar power")
        form.addRow("Label:", self.label_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Only if you set one in the SwitchBot app")
        form.addRow("Password:", self.password_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.address_edit.text().strip():
            QMessageBox.warning(self, "Address required", "Enter the Bot's Bluetooth address.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        address = self.address_edit.text().strip()
        label = self.label_edit.text().strip() or address
        return address, label, self.password_edit.text()


class _BotBox(QGroupBox):
    """One Bot: status line plus Check / On / Off / Press / Remove buttons."""

    removed = Signal(str)

    def __init__(self, address: str, label: str, password: str, parent=None) -> None:
        super().__init__(label, parent)
        self.address = address
        self.label = label
        self.adapter = SwitchBotAdapter(address=address, name=label, password=password)
        self._busy = False

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Checking…")
        self.status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for text, tip, fn in (
            ("On", "Extend the arm (Bot in dual-state mode); a press in press mode.",
             lambda: self.adapter.set_switch(self.adapter.switches[0].name, True)),
            ("Off", "Retract the arm (Bot in dual-state mode); a press in press mode.",
             lambda: self.adapter.set_switch(self.adapter.switches[0].name, False)),
            ("Press", "Press once, whatever the Bot's mode.", self.adapter.press),
            ("Check", "Read the Bot's battery level and mode.", self.adapter.refresh),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c, f=fn: self._run(f))
            row.addWidget(btn)
            self.buttons[text] = btn
        row.addStretch(1)
        remove_btn = QPushButton("Remove")
        remove_btn.setToolTip("Forget this Bot (it can be found again with a scan).")
        remove_btn.clicked.connect(lambda: self.removed.emit(self.address))
        row.addWidget(remove_btn)
        layout.addLayout(row)

        self._run(self.adapter.refresh)

    def _run(self, coro_fn: Callable[[], object]) -> None:
        if self._busy:
            return
        self._busy = True
        self.status_label.setText("Talking to the Bot…")
        self.status_label.setStyleSheet("color: gray;")
        for btn in self.buttons.values():
            btn.setEnabled(False)
        task = _Task(lambda: asyncio.run(coro_fn()), self)
        task.done.connect(lambda _r: self._finish(None))
        task.failed.connect(self._finish)
        task.start()

    def _finish(self, error: Exception | None) -> None:
        self._busy = False
        for btn in self.buttons.values():
            btn.setEnabled(True)
        if error is not None:
            logger.error("SwitchBot %r at %s: %s", self.label, self.address, error)
            self.status_label.setText(f"Unavailable: {error}")
            self.status_label.setStyleSheet("color: #c0392b;")
            return
        mode = "press mode" if self.adapter.press_mode else "dual-state mode"
        last = "" if self.adapter.press_mode else (
            f" · last command: {'On' if self.adapter.switches[0].state else 'Off'}")
        self.status_label.setText(
            f"Connected · battery {self.adapter.battery}% · {mode}{last}"
        )
        self.status_label.setStyleSheet("color: #2e7d32;")


def build_switchbot_page() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)

    heading = QLabel("SwitchBot")
    heading.setObjectName("CriteriaHeading")
    layout.addWidget(heading)

    button_row = QHBoxLayout()
    scan_btn = QPushButton("Scan for SwitchBots")
    scan_btn.setObjectName("AccentButton")
    add_btn = QPushButton("Add by address…")
    add_btn.setToolTip("Add a Bot the scan can't find.")
    status = QLabel("")
    status.setObjectName("StatusHint")
    button_row.addWidget(scan_btn)
    button_row.addWidget(add_btn)
    button_row.addWidget(status, 1)
    layout.addLayout(button_row)

    # No scroll area of its own: every Bot box shows at full height, and the
    # host page scrolls the whole switch area.
    boxes_layout = QVBoxLayout()
    boxes_layout.setContentsMargins(0, 0, 0, 0)
    layout.addLayout(boxes_layout)

    settings = SwitchBotSettings()
    boxes: dict[str, _BotBox] = {}

    def _add_box(address: str, label: str, password: str) -> None:
        if address in boxes:
            return
        box = _BotBox(address, label, password)
        box.removed.connect(_remove_box)
        boxes[address] = box
        boxes_layout.addWidget(box)

    def _remove_box(address: str) -> None:
        box = boxes.pop(address, None)
        if box is not None:
            box.deleteLater()
        settings.remove_device(address)

    def _on_scan_done(found: list) -> None:
        scan_btn.setEnabled(True)
        new = 0
        for address, name, _rssi in found:
            if address in boxes:
                continue
            settings.add_device(address, name)
            _add_box(address, name, "")
            new += 1
        status.setText(
            f"Found {len(found)} SwitchBot(s), {new} new. Rename or set a password by "
            "removing one and using Add by address…" if found
            else "No SwitchBot Bots answered. Wake the Bot (press its button) and move closer."
        )

    def _on_scan_failed(exc: Exception) -> None:
        scan_btn.setEnabled(True)
        logger.error("SwitchBot scan failed: %s", exc)
        status.setText(f"Scan failed: {exc}")

    def _on_scan() -> None:
        scan_btn.setEnabled(False)
        status.setText("Scanning…")
        task = _Task(lambda: asyncio.run(protocol.scan()), page)
        task.done.connect(_on_scan_done)
        task.failed.connect(_on_scan_failed)
        task.start()

    def _on_add() -> None:
        dialog = AddBotDialog(page)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        address, label, password = dialog.values()
        if address not in boxes:
            settings.add_device(address, label, password)
            _add_box(address, label, password)

    scan_btn.clicked.connect(_on_scan)
    add_btn.clicked.connect(_on_add)

    for device in settings.devices:
        address = device.get("address", "")
        if address:
            _add_box(address, device.get("label") or address, device.get("password", ""))

    return page
