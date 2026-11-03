# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The status view (NCON-330): everything a person needs to know about where this node stands, as data and as lines.

Collected from the runtime and the shared database, with no Qt, so the Options page and the tests read the same thing.
Every part is gathered separately and a part that cannot be read leaves the others intact: the view is for looking at a
problem and must not be one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from nodeconnect.roles import MASTER, SPOKE

logger = logging.getLogger(__name__)

CONNECTED = "connected"
RETRYING = "retrying"
WAITING = "waiting for a session to end"
NOT_PAIRED = "not paired"
NO_MASTER = "no Master chosen"
IDLE = "not syncing"


@dataclass
class NodeStatus:
    role: str = ""
    master: str = ""                          # the designated Master's address (a spoke)
    paired: bool = False
    connection: str = IDLE
    last_success: float | None = None         # when a sync last completed
    revision: int = 0                         # the highest Master revision this node has applied
    pending_changes: int = 0                  # local configuration rows not yet sent to the Master
    staged_changes: int = 0                   # received changes waiting to be applied
    queue_items: int = 0                      # frames waiting to go to the Master
    queue_bytes: int = 0
    last_sync: str = ""                       # what the last pass did, in words
    last_error: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)      # a Master's paired nodes


def _safe(default, fn, label: str):
    try:
        return fn()
    except Exception:
        logger.exception("NodeConnect status: could not read %s", label)
        return default


def collect(runtime: Any) -> NodeStatus:
    from nodeconnect.sync import BACKOFF, BLOCKED

    status = NodeStatus(role=runtime.role, master=runtime.master_address or "", paired=bool(runtime.paired))
    result = runtime.last_result
    if status.role == SPOKE:
        if not status.master:
            status.connection = NO_MASTER
        elif not status.paired:
            status.connection = NOT_PAIRED
        elif result is None:
            status.connection = IDLE
        elif result.state == BACKOFF:
            status.connection = RETRYING
        elif result.state == BLOCKED or getattr(result, "deferred", False):
            status.connection = WAITING
        else:
            status.connection = CONNECTED
    status.last_success = getattr(runtime, "last_success", None)
    status.last_error = runtime.last_error or ""
    if result is not None:
        status.last_sync = _describe(result)

    store = getattr(runtime, "store", None)
    if store is not None:
        status.staged_changes = _safe(0, lambda: len(store.inbox_pending()), "staged changes")
        creds = _safe(None, runtime._creds.load, "pairing")
        if creds is not None:
            revs = _safe({}, lambda: store.peer_get(creds.master_id, "last_rev", {}) or {}, "revision")
            status.revision = max((int(v) for k, v in revs.items() if not str(k).endswith("#t")), default=0)
    if status.role == SPOKE or status.paired:
        status.pending_changes = _safe(0, _pending_local_changes, "pending changes")
    queue = _safe({"items": 0, "bytes": 0}, _queue, "the transfer queue")
    status.queue_items, status.queue_bytes = queue["items"], queue["bytes"]
    if status.role != SPOKE and runtime.desk is not None:
        status.nodes = _safe([], runtime.desk.nodes, "paired nodes")
    return status


def _pending_local_changes() -> int:
    from nodeconnect.push import dirty_signature
    return sum(n for _table, n in dirty_signature())  # type: ignore[union-attr]


def _queue() -> dict[str, int]:
    from galileo.replication import SyncStore

    from nodeconnect.transfer import queue_counts
    return queue_counts(SyncStore())


def _describe(result: Any) -> str:
    parts = [result.state.capitalize()]
    if result.message:
        parts.append(result.message)
    parts.append(f"{result.pulled} received, {result.pushed} sent, {result.conflicts} conflict(s)")
    if result.deferred:
        parts.append("changes are waiting for the session to end")
    return " - ".join(parts)


def _when(stamp: float | None) -> str:
    return "never" if not stamp else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))


def lines(status: NodeStatus) -> list[str]:
    """The status as plain lines, in the order a person scans them."""
    out = []
    if status.role == SPOKE:
        out.append(f"Master: {status.master or 'not chosen'} ({'paired' if status.paired else 'not paired'})")
        out.append(f"Connection: {status.connection}")
        out.append(f"Last successful sync: {_when(status.last_success)}   Revision: {status.revision}")
        if status.last_sync:
            out.append(f"Last pass: {status.last_sync}")
        out.append(f"Configuration waiting to send: {status.pending_changes}   "
                   f"received and waiting to apply: {status.staged_changes}")
    elif status.role == MASTER:
        out.append(f"This node is a Master with {len(status.nodes)} paired node(s).")
    else:
        out.append("This node is standalone.")
    out.append(f"Images waiting: {status.queue_items} ({status.queue_bytes / 1e6:.1f} MB)"
               if status.queue_items else "No images waiting.")
    if status.last_error:
        out.append(f"Last problem: {status.last_error}")
    return out


__all__ = ["CONNECTED", "NOT_PAIRED", "NO_MASTER", "RETRYING", "WAITING", "NodeStatus", "collect", "lines"]
