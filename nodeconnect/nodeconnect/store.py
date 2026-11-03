# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""``NodeStore``: core's ``SyncStore`` with the shapes NodeConnect works in.

Core stores a natural key as text and snapshots as one row per before-image; the sync code here deals
in key lists and whole snapshot records, and keeps a run's start time until the run finishes. This
class converts between the two and adds nothing else.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from galileo.replication import SyncStore

from nodeconnect.repl import rows

logger = logging.getLogger(__name__)


class NodeStore:
    def __init__(self, store: SyncStore | None = None, retention: Callable[[], int] | None = None) -> None:
        """*retention* says how many run reports to keep (NCON-320); it is asked after each report is written."""
        self._s = store or SyncStore()
        self._started: dict[str, tuple[str, float]] = {}
        self._retention = retention

    # -- inbox ---------------------------------------------------------------------------------
    def inbox_add(self, peer_id: str, table: str, key: list[Any], op: str, payload: Any, rev: int = 0) -> int:
        return self._s.inbox_add(peer_id, table, json.dumps(key), payload, rev, op)

    def inbox_pending(self, peer_id: str = "", limit: int = 1000) -> list[dict[str, Any]]:
        if peer_id:
            out = self._s.inbox_pending(peer_id, limit)
        else:
            out = rows("SELECT * FROM sync_inbox WHERE status = 'pending' ORDER BY id LIMIT ?", limit)
            for r in out:
                r["payload"] = json.loads(r["payload"])
        for r in out:
            r["natural_key"] = json.loads(r["natural_key"])
        return out

    def inbox_mark(self, ids: list[int], status: str) -> None:
        self._s.inbox_mark(ids, status)

    # -- snapshots -----------------------------------------------------------------------------
    def snapshot_save(self, run_id: str, data: dict[str, Any]) -> None:
        self._s.snapshot_save(run_id, data["table"], json.dumps(data["key"]), data)

    def snapshot_get(self, run_id: str) -> list[dict[str, Any]]:
        """Oldest first, each as ``{"data": record}``."""
        return [{"data": r["before"]} for r in reversed(self._s.snapshot_rows(run_id))]

    # -- reports -------------------------------------------------------------------------------
    def report_start(self, run_id: str, peer_id: str = "") -> None:
        self._started[run_id] = (peer_id, time.time())

    def report_finish(self, run_id: str, outcome: str, summary: Any) -> None:
        peer_id, started = self._started.pop(run_id, ("", time.time()))
        self._s.report_append(run_id, peer_id, outcome, summary, started)
        if self._retention is not None:
            try:
                self.prune(self._retention())
            except Exception:
                logger.exception("NodeConnect: could not prune old run reports")

    def reports(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._s.reports(limit)

    def has_snapshots(self, run_id: str) -> bool:
        from galileo.library.models.base import db
        return db.execute_sql("SELECT 1 FROM sync_snapshots WHERE run_id = ? LIMIT 1", (run_id,)).fetchone() is not None

    def prune(self, keep: int) -> int:
        """Keep the newest *keep* run reports and the snapshots of those runs; delete the rest (and any snapshots no
        remaining report refers to). Returns how many reports were deleted."""
        from galileo.library.models.base import db
        old = [r[0] for r in db.execute_sql(
            "SELECT id FROM sync_run_reports ORDER BY id DESC LIMIT -1 OFFSET ?", (max(0, int(keep)),)).fetchall()]
        if old:
            marks = ",".join("?" * len(old))
            db.execute_sql(f"DELETE FROM sync_run_reports WHERE id IN ({marks})", tuple(old))
        db.execute_sql("DELETE FROM sync_snapshots WHERE run_id NOT IN (SELECT run_id FROM sync_run_reports)")
        return len(old)

    # -- per-peer state ------------------------------------------------------------------------
    def peer_get(self, peer_id: str, key: str, default: Any = None) -> Any:
        return self._s.peer_state_get(peer_id, key, default)

    def peer_set(self, peer_id: str, key: str, value: Any) -> None:
        self._s.peer_state_set(peer_id, key, value)

    def peer_forget(self, peer_id: str) -> None:
        from galileo.library.models.base import db
        db.execute_sql("DELETE FROM sync_peer_state WHERE peer_id = ?", (peer_id,))
