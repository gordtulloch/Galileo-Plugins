# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Kasa Switches panel.

One tab per configured Kasa device (a single plug or a multi-outlet power
strip), each showing a table of that device's switches — identifier and
On/Off status — with a click-to-toggle status cell. New devices are added
with the "+" button in the tab bar's corner; closing a tab removes that
device from the panel (and from the saved device list) without touching any
other tab, so one unreachable strip doesn't take the others down with it
(mirrors core's per-device fault isolation, ARCH-060).
"""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from kasa_switch.adapter import KasaSwitchAdapter
from kasa_switch.settings import KasaSettings

logger = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 5000


class AddKasaDeviceDialog(QDialog):
    """Prompts for the IP/hostname of a Kasa device to add as a new tab."""

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


class _DeviceTab(QWidget):
    """One device's outlet table, backed by its own ``KasaSwitchAdapter``."""

    def __init__(self, host: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.host = host
        self.label = label
        self.adapter = KasaSwitchAdapter(host=host, name=label)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Not connected")
        self.status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Switch", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(_POLL_INTERVAL_MS)

        self.refresh()

    def refresh(self) -> None:
        try:
            asyncio.run(self.adapter.refresh())
        except Exception:
            logger.exception("Could not reach Kasa device %r at %s", self.label, self.host)
            self.status_label.setText(f"Unreachable: {self.host}")
            self.status_label.setStyleSheet("color: #c0392b;")
            return
        self.status_label.setText(f"Connected: {self.host}")
        self.status_label.setStyleSheet("color: #2e7d32;")
        self._render_table()

    def _render_table(self) -> None:
        outlets = self.adapter.switches
        self.table.setRowCount(len(outlets))
        for row, outlet in enumerate(outlets):
            self.table.setItem(row, 0, QTableWidgetItem(outlet.name))
            toggle_btn = QPushButton("On" if outlet.state else "Off")
            toggle_btn.setCheckable(True)
            toggle_btn.setChecked(outlet.state)
            toggle_btn.clicked.connect(lambda _checked, name=outlet.name: self._toggle(name))
            self.table.setCellWidget(row, 1, toggle_btn)

    def _toggle(self, name: str) -> None:
        outlet = next((s for s in self.adapter.switches if s.name == name), None)
        new_value = not outlet.state if outlet else True
        try:
            asyncio.run(self.adapter.set_switch(name, new_value))
        except Exception:
            logger.exception("Could not toggle Kasa outlet %r on %s", name, self.host)
            QMessageBox.warning(self, "Switch failed", f"Could not toggle {name!r} — see log.")
        self._render_table()


def build_kasa_page() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)

    heading_row = QHBoxLayout()
    heading = QLabel("Kasa Switches")
    heading.setStyleSheet("font-weight: bold; font-size: 14px;")
    heading_row.addWidget(heading)
    heading_row.addStretch(1)
    layout.addLayout(heading_row)

    tabs = QTabWidget()
    tabs.setTabsClosable(True)
    layout.addWidget(tabs)

    settings = KasaSettings()

    add_btn = QPushButton("+")
    add_btn.setFixedWidth(28)
    add_btn.setToolTip("Add a Kasa device")
    tabs.setCornerWidget(add_btn)

    def _add_tab(host: str, label: str) -> None:
        tab = _DeviceTab(host, label)
        tabs.addTab(tab, label)
        tabs.setCurrentWidget(tab)

    def _on_add_clicked() -> None:
        dialog = AddKasaDeviceDialog(page)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        host, label = dialog.values()
        settings.add_device(host, label)
        _add_tab(host, label)

    def _on_tab_close(index: int) -> None:
        widget = tabs.widget(index)
        if isinstance(widget, _DeviceTab):
            widget.timer.stop()
            settings.remove_device(widget.host)
        tabs.removeTab(index)

    add_btn.clicked.connect(_on_add_clicked)
    tabs.tabCloseRequested.connect(_on_tab_close)

    for device in settings.devices:
        host = device.get("host", "")
        if host:
            _add_tab(host, device.get("label") or host)

    return page
