# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""WebConnect's page under Options (WCON-050, 100, 110, 170, 190, 900).

Where a person decides which browsers may reach this observatory: see the address to open, approve a pairing
request after comparing its code, promote a browser to Operator or demote it, set it to expire, revoke it, and
read what browsers have been doing. Nothing here talks to the network; it asks the Master half's registry.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from webconnect import PLUGIN
from webconnect.browsers import OPERATOR, BrowserError
from webconnect.config import WebConnectConfig
from webconnect.launch import bind_address, open_address

_REFRESH_MS = 3000
_ROLE = 0x0100


def _when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M") if ts else "never"


class WebConnectOptions(QWidget):
    def __init__(self, runtime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rt = runtime
        layout = QVBoxLayout(self)
        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusHint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addWidget(self._build_server_box())
        layout.addWidget(self._build_browsers_box())
        layout.addWidget(self._build_activity_box())
        layout.addStretch(1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(_REFRESH_MS)
        self.load_options()
        self.refresh()

    # -- layout -------------------------------------------------------------------------------------
    def _build_server_box(self) -> QGroupBox:
        box = QGroupBox("Web application")
        form = QFormLayout(box)
        self.address_edit = QLineEdit()
        self.address_edit.setReadOnly(True)
        form.addRow("Address to open:", self.address_edit)

        row = QHBoxLayout()
        self.start_btn = QPushButton("Start web application")
        self.stop_btn = QPushButton("Stop web application")
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        form.addRow(row)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        form.addRow("Port:", self.port_spin)
        self.autostart_check = QCheckBox("Start with Galileo")
        form.addRow(self.autostart_check)
        self.wide_check = QCheckBox("Allow all network interfaces")
        form.addRow(self.wide_check)
        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(1, 1440)
        form.addRow("Sign out after idle (minutes):", self.idle_spin)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("AccentButton")
        form.addRow(self.save_btn)

        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        self.save_btn.clicked.connect(self._on_save)
        return box

    def _build_browsers_box(self) -> QGroupBox:
        box = QGroupBox("Browsers")
        v = QVBoxLayout(box)
        v.addWidget(QLabel("Pairing requests"))
        self.requests = QListWidget()
        v.addWidget(self.requests)
        row = QHBoxLayout()
        self.approve_btn = QPushButton("Approve")
        self.reject_btn = QPushButton("Reject")
        row.addWidget(self.approve_btn)
        row.addWidget(self.reject_btn)
        row.addStretch(1)
        v.addLayout(row)

        v.addWidget(QLabel("Approved browsers"))
        self.approved = QListWidget()
        v.addWidget(self.approved)
        row2 = QHBoxLayout()
        self.promote_btn = QPushButton("Make Operator")
        self.demote_btn = QPushButton("Make Viewer")
        self.revoke_btn = QPushButton("Revoke")
        self.remove_btn = QPushButton("Remove browser")
        for b in (self.promote_btn, self.demote_btn, self.revoke_btn, self.remove_btn):
            row2.addWidget(b)
        row2.addStretch(1)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Expire after (days):"))
        self.expiry_spin = QSpinBox()
        self.expiry_spin.setRange(0, 3650)
        self.expiry_spin.setSpecialValueText("Never")
        self.expire_btn = QPushButton("Set expiry")
        row3.addWidget(self.expiry_spin)
        row3.addWidget(self.expire_btn)
        row3.addStretch(1)
        v.addLayout(row3)

        self.browsers_status = QLabel("")
        self.browsers_status.setObjectName("StatusHint")
        self.browsers_status.setWordWrap(True)
        v.addWidget(self.browsers_status)

        self.approve_btn.clicked.connect(lambda: self._act(self.requests, self._rt.browsers.approve))
        self.reject_btn.clicked.connect(lambda: self._act(self.requests, self._rt.browsers.reject))
        self.promote_btn.clicked.connect(lambda: self._act(self.approved, self._rt.browsers.promote))
        self.demote_btn.clicked.connect(lambda: self._act(self.approved, self._rt.browsers.demote))
        self.revoke_btn.clicked.connect(lambda: self._act(self.approved, self._rt.browsers.revoke))
        self.remove_btn.clicked.connect(lambda: self._act(self.approved, self._rt.browsers.delete))
        self.expire_btn.clicked.connect(self._on_expiry)
        self.requests.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self.approved.currentItemChanged.connect(lambda *_: self._sync_buttons())
        return box

    def _build_activity_box(self) -> QGroupBox:
        box = QGroupBox("Recent activity")
        v = QVBoxLayout(box)
        v.addWidget(QLabel("Requests from browsers"))
        self.activity = QListWidget()
        v.addWidget(self.activity)
        return box

    # -- options -------------------------------------------------------------------------------------
    def load_options(self) -> None:
        c = self._rt.options()
        self.port_spin.setValue(c.port)
        self.autostart_check.setChecked(c.start_with_galileo)
        self.wide_check.setChecked(c.wide_bind)
        self.idle_spin.setValue(max(1, c.session_idle_s // 60))

    def _on_save(self) -> None:
        current = self._rt.options().to_dict()
        current.update(port=self.port_spin.value(), start_with_galileo=self.autostart_check.isChecked(),
                       wide_bind=self.wide_check.isChecked(), session_idle_s=self.idle_spin.value() * 60)
        self._rt.save_options(WebConnectConfig.from_dict(current))
        self.status_label.setText("Saved.")
        self.refresh()

    def _on_start(self) -> None:
        self._rt.start()
        self.refresh()

    def _on_stop(self) -> None:
        self._rt.stop()
        self.refresh()

    # -- browsers --------------------------------------------------------------------------------------
    def _act(self, widget: QListWidget, action) -> None:
        item = widget.currentItem()
        if item is None:
            self.browsers_status.setText("Select a row first.")
            return
        try:
            action(item.data(_ROLE))
            self.browsers_status.setText("")
        except BrowserError as exc:
            self.browsers_status.setText(str(exc))
        self.refresh()

    def _on_expiry(self) -> None:
        item = self.approved.currentItem()
        if item is None:
            self.browsers_status.setText("Select a row first.")
            return
        days = self.expiry_spin.value()
        try:
            self._rt.browsers.set_expiry(item.data(_ROLE), time.time() + days * 86400 if days else None)
            self.browsers_status.setText("")
        except BrowserError as exc:
            self.browsers_status.setText(str(exc))
        self.refresh()

    # -- display ---------------------------------------------------------------------------------------
    @staticmethod
    def _fill(widget: QListWidget, rows: list[tuple[str, str]]) -> None:
        current = widget.currentItem().data(_ROLE) if widget.currentItem() else None
        widget.clear()
        for key, text in rows:
            item = QListWidgetItem(text)
            item.setData(_ROLE, key)
            widget.addItem(item)
            if key == current:
                widget.setCurrentItem(item)

    def refresh(self) -> None:
        rt = self._rt
        sup = rt.supervisor
        up = sup is not None and sup.running
        port = getattr(sup, "port", 0) or rt.options().port
        self.address_edit.setText(f"https://{open_address(bind_address(rt.options()))}:{port}/")
        self.start_btn.setEnabled(not up)
        self.stop_btn.setEnabled(up)
        problems = getattr(sup, "failures", 0)
        if problems:
            reason = getattr(sup, "last_error", "")
            self.status_label.setText(f"The web application has failed to start {problems} time(s) in a row; "
                                      "Galileo keeps trying." + (f" Last error: {reason}" if reason else ""))
        elif not self.status_label.text() or self.status_label.text() == "Saved.":
            self.status_label.setText("Running." if up else "Stopped. Browsers cannot connect.")
        elif up and self.status_label.text().startswith("The web application has failed"):
            self.status_label.setText("Running.")
        if not problems and getattr(sup, "last_error", ""):
            self.status_label.setText(f"{self.status_label.text().split(' Last error:')[0]} Last error: {sup.last_error}")
        link = getattr(getattr(rt, "_host", None), "status", None)
        link = link() if callable(link) else None
        if up and isinstance(link, dict) and not link.get("running"):
            self.status_label.setText("Running, but browsers cannot pair: Connect Link is not listening. "
                                      "Turn it on in Options > Connect Link.")
        elif up and not self._surface_running():
            self.status_label.setText("Running, but there is nothing to operate: the control surface is not running. "
                                      "Turn on Options > Equipment > \"Hold devices in the device service\" "
                                      "and restart Galileo.")
        self._fill(self.requests, [(r["request_id"], f"{r['name']} ({r['platform']}) - code {r['code']}")
                                   for r in rt.browsers.pending()])
        rows = []
        for b in rt.browsers.browsers():
            state = "revoked" if b["revoked"] else ("expired" if not b["active"] else b["role"].capitalize())
            rows.append((b["peer_id"], f"{b['name']} ({b['platform']}) - {state}, last seen {_when(b['last_contact'])}"
                                       + (f", expires {_when(b['expires_at'])}" if b["expires_at"] else "")))
        self._fill(self.approved, rows)
        self._sync_buttons()
        self._fill_activity()

    @staticmethod
    def _surface_running() -> bool:
        try:
            from galileo.control_surface.service import get_control_surface
            return get_control_surface() is not None
        except Exception:
            return True                                   # cannot tell: do not raise a false alarm

    def _sync_buttons(self) -> None:
        """Buttons that act on a selected row are available only while one is selected."""
        has_browser = self.approved.currentItem() is not None
        for btn in (self.promote_btn, self.demote_btn, self.revoke_btn, self.remove_btn, self.expire_btn):
            btn.setEnabled(has_browser)
        for btn in (self.approve_btn, self.reject_btn):
            btn.setEnabled(self.requests.currentItem() is not None)

    def _fill_activity(self) -> None:
        audit = self._rt.audit
        if audit is None:
            return
        names = {b["peer_id"]: b["name"] for b in self._rt.browsers.browsers()}
        rows = []
        for i, e in enumerate(audit.query(limit=200)):
            if e.plugin != PLUGIN:
                continue
            who = names.get(e.actor, e.actor[:8] or "unknown")
            rows.append((str(i), f"{_when(e.ts)}  {who}  {e.route}  {e.outcome}" + (f" ({e.reason})" if e.reason else "")))
            if len(rows) >= 50:
                break
        self._fill(self.activity, rows)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._timer.start(_REFRESH_MS)
        super().showEvent(event)


__all__ = ["OPERATOR", "WebConnectOptions"]
