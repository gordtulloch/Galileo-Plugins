# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The plugin's Options page: approve apps, set their roles, revoke them. The only place a role can change."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mobileconnect.clients import OPERATOR, VIEWER, ClientError

_REFRESH_MS = 2000
_ID = Qt.ItemDataRole.UserRole


class MobileConnectOptions(QWidget):
    def __init__(self, runtime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rt = runtime
        layout = QVBoxLayout(self)
        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusHint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        waiting = QGroupBox("Waiting for approval")
        box = QVBoxLayout(waiting)
        self.pending_list = QListWidget()
        box.addWidget(self.pending_list)
        row = QHBoxLayout()
        self.approve_viewer = QPushButton("Approve as Viewer")
        self.approve_operator = QPushButton("Approve as Operator")
        self.refuse = QPushButton("Refuse")
        for button, action in ((self.approve_viewer, lambda i: self._rt.clients.approve(i, VIEWER)),
                               (self.approve_operator, lambda i: self._rt.clients.approve(i, OPERATOR)),
                               (self.refuse, lambda i: self._rt.clients.reject(i))):
            button.clicked.connect(lambda _=False, a=action: self._act(self.pending_list, a))
            row.addWidget(button)
        box.addLayout(row)
        layout.addWidget(waiting)

        paired = QGroupBox("Paired apps")
        box = QVBoxLayout(paired)
        self.client_list = QListWidget()
        box.addWidget(self.client_list)
        row = QHBoxLayout()
        self.make_viewer = QPushButton("Make Viewer")
        self.make_operator = QPushButton("Make Operator")
        self.revoke = QPushButton("Revoke")
        for button, action in ((self.make_viewer, lambda i: self._rt.clients.set_role(i, VIEWER)),
                               (self.make_operator, lambda i: self._rt.clients.set_role(i, OPERATOR)),
                               (self.revoke, lambda i: self._rt.clients.revoke(i))):
            button.clicked.connect(lambda _=False, a=action: self._act(self.client_list, a))
            row.addWidget(button)
        box.addLayout(row)
        layout.addWidget(paired)
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(_REFRESH_MS)
        self.refresh()

    def _act(self, widget: QListWidget, action) -> None:
        item = widget.currentItem()
        if item is None:
            return
        try:
            action(item.data(_ID))
        except ClientError as exc:
            QMessageBox.information(self, "MobileConnect", str(exc))
        self.refresh()

    @staticmethod
    def _fill(widget: QListWidget, rows: list[tuple[str, str]]) -> None:
        selected = widget.currentItem().data(_ID) if widget.currentItem() is not None else None
        widget.clear()
        for key, text in rows:
            item = QListWidgetItem(text)
            item.setData(_ID, key)
            widget.addItem(item)
            if key == selected:
                widget.setCurrentItem(item)

    def refresh(self) -> None:
        self.status_label.setText(
            "Running. Apps on this network can find this computer." if self._rt.running
            else "Off. This computer is a NodeConnect spoke, or the plugin is disabled.")
        self._fill(self.pending_list, [
            (r["request_id"], f"{r['code']}   {r['name']} ({r['platform'] or 'unknown'}), from {r['source']}")
            for r in self._rt.clients.pending()])
        self._fill(self.client_list, [
            (c["peer_id"], f"{c['name']}: {'revoked' if c['revoked'] else c['role']}")
            for c in self._rt.clients.clients()])


__all__ = ["MobileConnectOptions"]
