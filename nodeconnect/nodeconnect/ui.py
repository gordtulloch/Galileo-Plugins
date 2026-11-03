# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect's page under Options (NCON-330, 340, 500, 510).

One page, two halves. **This node's Master** is for a spoke: enter the Master's address, ask to pair,
compare the confirmation code with the one shown on the Master, and watch the sync status. **Nodes
paired with this one** is for a Master: approve or reject pairing requests and revoke or remove nodes.
Network calls run on a daemon thread and report back through signals, so the page never blocks the UI.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
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

from nodeconnect.pairing import APPROVED, PENDING, PairingFailed, PendingPairing
from nodeconnect.roles import MASTER, SPOKE, RoleError

logger = logging.getLogger(__name__)

_REFRESH_MS = 3000
_POLL_MS = 2000


class _Task(QObject):
    """Runs ``fn`` on a daemon thread; the result or exception comes back on the UI thread."""

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
        except Exception as exc:  # noqa: BLE001 - reported to the page, not swallowed
            self.failed.emit(exc)
        else:
            self.done.emit(result)


def _describe(result) -> str:
    if result is None:
        return "No sync has run yet."
    parts = [f"{result.state.capitalize()}"]
    if result.message:
        parts.append(result.message)
    parts.append(f"{result.pulled} received, {result.pushed} sent, {result.conflicts} conflict(s)")
    if result.deferred:
        parts.append("changes are waiting for the session to end")
    return " - ".join(parts)


class NodeConnectOptions(QWidget):
    def __init__(self, runtime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rt = runtime
        self._pending: PendingPairing | None = None
        self._busy = False

        layout = QVBoxLayout(self)
        self.role_label = QLabel("")
        self.role_label.setObjectName("StatusHint")
        layout.addWidget(self.role_label)
        layout.addWidget(self._build_spoke_box())
        layout.addWidget(self._build_master_box())
        layout.addStretch(1)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_pairing)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(_REFRESH_MS)
        self.refresh()

    # -- layout --------------------------------------------------------------------------------
    def _build_spoke_box(self) -> QGroupBox:
        box = QGroupBox("This node's Master")
        form = QFormLayout(box)
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("https://observatory.local:8765")
        form.addRow("Master address:", self.address_edit)

        row = QHBoxLayout()
        self.pair_btn = QPushButton("Pair with Master")
        self.pair_btn.setObjectName("AccentButton")
        self.unpair_btn = QPushButton("Unpair")
        self.sync_btn = QPushButton("Sync now")
        for b in (self.pair_btn, self.unpair_btn, self.sync_btn):
            row.addWidget(b)
        row.addStretch(1)
        form.addRow(row)

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(30, 86400)
        self.interval_spin.setDecimals(0)
        self.interval_spin.setSuffix(" s")
        form.addRow("Sync every:", self.interval_spin)

        self.window_check = QCheckBox("Limit image transfer to a time window")
        self.from_spin, self.to_spin = QSpinBox(), QSpinBox()
        for spin in (self.from_spin, self.to_spin):
            spin.setRange(0, 23)
            spin.setSuffix(":00")
        self.to_spin.setValue(6)
        form.addRow(self.window_check)
        form.addRow("Transfer from:", self.from_spin)
        form.addRow("Transfer to:", self.to_spin)
        self.remove_check = QCheckBox("Remove local images once the Master has them")
        form.addRow(self.remove_check)
        st = self._rt.settings
        if st is not None:
            start, end = st.get("transfer_from_hour"), st.get("transfer_to_hour")
            self.window_check.setChecked(start is not None and end is not None)
            self.from_spin.setValue(int(start or 0))
            self.to_spin.setValue(int(end if end is not None else 6))
            self.remove_check.setChecked(bool(st.get("remove_after_receipt", False)))
        for w in (self.window_check, self.remove_check):
            w.toggled.connect(self._save_transfer)
        for spin in (self.from_spin, self.to_spin):
            spin.editingFinished.connect(self._save_transfer)

        self.code_label = QLabel("")
        self.code_label.setStyleSheet("font-size: 18pt; font-weight: bold;")
        form.addRow(self.code_label)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("StatusHint")
        form.addRow(self.status_label)

        self.pair_btn.clicked.connect(self._on_pair)
        self.unpair_btn.clicked.connect(self._on_unpair)
        self.sync_btn.clicked.connect(self._on_sync)
        self.interval_spin.setValue(self._rt.sync_interval())
        self.interval_spin.editingFinished.connect(lambda: self._rt.set_sync_interval(self.interval_spin.value()))
        return box

    def _save_transfer(self) -> None:
        st = self._rt.settings
        if st is None:
            return
        on = self.window_check.isChecked() and self.from_spin.value() != self.to_spin.value()
        st.set("transfer_from_hour", self.from_spin.value() if on else None)
        st.set("transfer_to_hour", self.to_spin.value() if on else None)
        st.set("remove_after_receipt", self.remove_check.isChecked())

    def _build_master_box(self) -> QGroupBox:
        self.master_box = QGroupBox("Nodes paired with this one")
        v = QVBoxLayout(self.master_box)
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

        v.addWidget(QLabel("Paired nodes"))
        self.nodes = QListWidget()
        v.addWidget(self.nodes)
        row2 = QHBoxLayout()
        self.revoke_btn = QPushButton("Revoke")
        self.remove_btn = QPushButton("Remove node")
        row2.addWidget(self.revoke_btn)
        row2.addWidget(self.remove_btn)
        row2.addStretch(1)
        v.addLayout(row2)
        self.master_status = QLabel("")
        self.master_status.setObjectName("StatusHint")
        self.master_status.setWordWrap(True)
        v.addWidget(self.master_status)

        self.approve_btn.clicked.connect(lambda: self._desk(self.requests, "request_id", "approve"))
        self.reject_btn.clicked.connect(lambda: self._desk(self.requests, "request_id", "reject"))
        self.revoke_btn.clicked.connect(lambda: self._desk(self.nodes, "peer_id", "revoke"))
        self.remove_btn.clicked.connect(lambda: self._desk(self.nodes, "peer_id", "delete"))
        return self.master_box

    # -- state ---------------------------------------------------------------------------------
    def refresh(self) -> None:
        role = self._rt.role
        paired = self._rt.paired
        self.role_label.setText({
            SPOKE: "This node is a spoke: it takes its configuration from a Master.",
            MASTER: "This node is a Master: other nodes take their configuration from it.",
        }.get(role, "This node is standalone. Pair it with a Master, or approve nodes that ask to pair with it."))
        waiting = self._pending is not None
        if not self.address_edit.hasFocus() and not waiting:
            self.address_edit.setText(self._rt.master_address or self.address_edit.text())
        self.address_edit.setEnabled(not paired and not waiting and role != MASTER)
        self.pair_btn.setEnabled(not paired and not waiting and not self._busy and role != MASTER)
        self.unpair_btn.setEnabled(bool(self._rt.master_address) or waiting)
        self.sync_btn.setEnabled(paired)
        self.master_box.setEnabled(role != SPOKE)
        if waiting:
            pass                                             # the pairing text is owned by _poll_pairing
        elif paired:
            self.code_label.setText("")
            self.status_label.setText(_describe(self._rt.last_result) + self._queue_text())
        elif self._rt.master_address:
            self.status_label.setText("A Master is chosen but not paired yet. Press Pair with Master.")
        else:
            self.status_label.setText("")
        desk = self._rt.desk
        if desk is None or role == SPOKE:
            return
        self._fill(self.requests, desk.pending(),
                   lambda r: f"{r['code']}  -  {r['name']} ({r['platform']}) from {r['source']}", "request_id")
        self._fill(self.nodes, desk.nodes(), self._node_text, "peer_id")

    def _queue_text(self) -> str:
        try:
            from galileo.replication import SyncStore

            from nodeconnect.transfer import queue_counts
            q = queue_counts(SyncStore())
        except Exception:  # noqa: BLE001 - the status line never breaks the page
            return ""
        err = self._rt.last_error
        waiting = f"Images waiting: {q['items']} ({q['bytes'] / 1e6:.1f} MB)" if q["items"] else "No images waiting."
        return "\n" + waiting + (f"\nLast problem: {err}" if err else "")

    @staticmethod
    def _node_text(n: dict) -> str:
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(n["last_contact"])) if n.get("last_contact") else "never"
        state = "revoked" if n["revoked"] else "paired"
        return f"{n['name']} ({n['platform']}) - {state}, last contact {seen}"

    @staticmethod
    def _fill(widget: QListWidget, rows: list[dict], text: Callable[[dict], str], key: str) -> None:
        current = widget.currentItem().data(0x0100) if widget.currentItem() else None
        widget.clear()
        for row in rows:
            item = QListWidgetItem(text(row))
            item.setData(0x0100, row[key])
            widget.addItem(item)
            if row[key] == current:
                widget.setCurrentItem(item)

    # -- spoke actions ---------------------------------------------------------------------------
    def _on_pair(self) -> None:
        address = self.address_edit.text().strip()
        if address and "://" not in address:
            address = "https://" + address
        client = self._rt.client
        if client is None:
            self.status_label.setText("Pairing is not available in this session.")
            return
        self._busy = True
        self.status_label.setText("Contacting the Master...")
        self.refresh()
        task = _Task(lambda: client.request(address), self)
        task.done.connect(self._on_requested)
        task.failed.connect(self._on_failed)
        task.start()

    def _on_requested(self, pending: PendingPairing) -> None:
        self._busy = False
        self._pending = pending
        self.code_label.setText(pending.code)
        self.status_label.setText("Check this code matches the one on the Master, then approve it there.")
        self._poll.start(_POLL_MS)
        self.refresh()

    def _on_failed(self, exc: Exception) -> None:
        self._busy = False
        if not isinstance(exc, (PairingFailed, RoleError)):
            logger.error("NodeConnect pairing failed: %s", exc)
        self._abandon()
        self.status_label.setText(str(exc))
        self.refresh()

    def _poll_pairing(self) -> None:
        pending, client = self._pending, self._rt.client
        if pending is None or client is None or self._busy:
            return
        self._busy = True
        task = _Task(lambda: client.poll(pending), self)
        task.done.connect(self._on_polled)
        task.failed.connect(self._on_failed)
        task.start()

    def _on_polled(self, state: str) -> None:
        self._busy = False
        if state == PENDING:
            return
        self._abandon()
        if state == APPROVED:
            self._rt.start()                                 # now a spoke with credentials: begin syncing
            self.status_label.setText("Paired. The first sync is starting.")
        else:
            self.status_label.setText(f"The pairing request was {state}.")
        self.refresh()

    def _abandon(self) -> None:
        self._poll.stop()
        self._pending = None
        self.code_label.setText("")

    def _on_unpair(self) -> None:
        self._abandon()
        if self._rt.client is not None:
            self._rt.client.unpair()
        self._rt.start()                                     # back to standalone
        self.address_edit.clear()
        self.status_label.setText("Unpaired. This node no longer takes configuration from a Master.")
        self.refresh()

    def _on_sync(self) -> None:
        self.status_label.setText("Sync requested." if self._rt.sync_now() else "Not paired.")

    # -- master actions ---------------------------------------------------------------------------
    def _desk(self, widget: QListWidget, key: str, action: str) -> None:
        item = widget.currentItem()
        if item is None or self._rt.desk is None:
            self.master_status.setText("Select a row first.")
            return
        before = self._rt.role
        try:
            getattr(self._rt.desk, action)(item.data(0x0100))
            self.master_status.setText("")
        except (PairingFailed, RoleError) as exc:
            self.master_status.setText(str(exc))
        if self._rt.role != before:
            self._rt.start()                                 # a role change; otherwise leave the listener alone
        self.refresh()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._timer.start(_REFRESH_MS)
        super().showEvent(event)
