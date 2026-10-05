# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Internet Relay panel.

One tab per configured relay board, each showing a table of its relays —
identifier and On/Off status — with a click-to-toggle status cell. New
boards are added with the "+" button in the tab bar's corner, which prompts
for the board's IP, how many relays it has, and the URL template used to
toggle one (pre-filled with the common
``http://{ip}/30000/{port}{state}`` scheme). Closing a tab removes that
board from the panel (and from the saved device list) without affecting any
other tab.

Unlike the Kasa panel, there is no polling timer here: a bare HTTP relay
board generally has no readback endpoint, so there is nothing to poll —
the table only reflects the state this adapter last commanded.
"""

from __future__ import annotations

import asyncio
import logging

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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from generic_relay.adapter import DEFAULT_URL_TEMPLATE, GenericRelayAdapter
from generic_relay.settings import GenericRelaySettings

logger = logging.getLogger(__name__)


class AddRelayDeviceDialog(QDialog):
    """Prompts for a relay board's IP, relay count, and toggle-URL template."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Internet Relay Board")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g. 10.0.0.101")
        form.addRow("Host / IP:", self.host_edit)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Optional display name (defaults to the host)")
        form.addRow("Label:", self.label_edit)

        self.port_count_spin = QSpinBox()
        self.port_count_spin.setRange(1, 64)
        self.port_count_spin.setValue(1)
        form.addRow("Number of relays:", self.port_count_spin)

        self.url_template_edit = QLineEdit(DEFAULT_URL_TEMPLATE)
        form.addRow("Toggle URL template:", self.url_template_edit)

        hint = QLabel(
            "Placeholders: {ip} = this board's host/IP, {port} = zero-based "
            "relay index (relay 1 = 0), {state} = 1 (on) or 0 (off).\n"
            "Default reproduces http://<ip>/30000/01 (relay 1 on) / "
            "http://<ip>/30000/00 (relay 1 off)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addLayout(form)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "Host required", "Enter the relay board's IP address or hostname.")
            return
        if not self.url_template_edit.text().strip():
            QMessageBox.warning(self, "URL template required", "Enter a toggle URL template.")
            return
        self.accept()

    def values(self) -> tuple[str, str, int, str]:
        host = self.host_edit.text().strip()
        label = self.label_edit.text().strip() or host
        port_count = self.port_count_spin.value()
        url_template = self.url_template_edit.text().strip()
        return host, label, port_count, url_template


class _DeviceTab(QWidget):
    """One relay board's table, backed by its own ``GenericRelayAdapter``."""

    def __init__(self, host: str, label: str, port_count: int, url_template: str, parent=None) -> None:
        super().__init__(parent)
        self.host = host
        self.label = label
        self.adapter = GenericRelayAdapter(
            host=host, port_count=port_count, url_template=url_template, name=label
        )

        layout = QVBoxLayout(self)

        self.status_label = QLabel(f"{host} — {port_count} relay(s)")
        self.status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Switch", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        asyncio.run(self.adapter.connect())
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
            self.status_label.setText(f"{self.host} — {len(self.adapter.switches)} relay(s)")
            self.status_label.setStyleSheet("color: #2e7d32;")
        except Exception:
            logger.exception("Could not toggle relay %r on %s", name, self.host)
            self.status_label.setText(f"Unreachable: {self.host}")
            self.status_label.setStyleSheet("color: #c0392b;")
            QMessageBox.warning(self, "Switch failed", f"Could not toggle {name!r} — see log.")
        self._render_table()


def build_generic_relay_page() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)

    heading_row = QHBoxLayout()
    heading = QLabel("Internet Relay")
    heading.setStyleSheet("font-weight: bold; font-size: 14px;")
    heading_row.addWidget(heading)
    heading_row.addStretch(1)
    layout.addLayout(heading_row)

    tabs = QTabWidget()
    tabs.setTabsClosable(True)
    layout.addWidget(tabs)

    settings = GenericRelaySettings()

    add_btn = QPushButton("+")
    add_btn.setFixedWidth(28)
    add_btn.setToolTip("Add a relay board")
    tabs.setCornerWidget(add_btn)

    def _add_tab(host: str, label: str, port_count: int, url_template: str) -> None:
        tab = _DeviceTab(host, label, port_count, url_template)
        tabs.addTab(tab, label)
        tabs.setCurrentWidget(tab)

    def _on_add_clicked() -> None:
        dialog = AddRelayDeviceDialog(page)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        host, label, port_count, url_template = dialog.values()
        settings.add_device(host, label, port_count, url_template)
        _add_tab(host, label, port_count, url_template)

    def _on_tab_close(index: int) -> None:
        widget = tabs.widget(index)
        if isinstance(widget, _DeviceTab):
            settings.remove_device(widget.host)
        tabs.removeTab(index)

    add_btn.clicked.connect(_on_add_clicked)
    tabs.tabCloseRequested.connect(_on_tab_close)

    for device in settings.devices:
        host = device.get("host", "")
        if host:
            _add_tab(
                host,
                device.get("label") or host,
                int(device.get("port_count", 1)),
                device.get("url_template") or DEFAULT_URL_TEMPLATE,
            )

    return page
