# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The Master's half of sync (NCON-120, 140, 150, 160, 170, 180; ADR-NCON-001, -002, -004).

The Master issues every revision. Its own local edits are committed first; a spoke then asks for
what changed since the revision it last applied (per table) and pushes its own dirty rows with the
revision each was based on, which the Master arbitrates exactly. Nothing here compares clocks to
decide *what* changed -- only to order two conflicting edits, and then with the measured offset.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from galileo.replication import registry, tracking

from nodeconnect import conflict
from nodeconnect.apply import DELETE, UPSERT, Applier, Change
from nodeconnect.codec import ItemFilter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pushed:
    """One spoke change awaiting arbitration."""
    table: str
    op: str                     # UPSERT or DELETE
    key: list[Any]
    wire: dict[str, Any] | None
    base_rev: int               # the revision the spoke's edit started from
    modified: float             # the spoke's own timestamp (corrected by the offset)


@dataclass(frozen=True)
class PushResult:
    table: str
    key: list[Any]
    status: str                 # "committed" or "conflict"
    winner: str                 # conflict.NODE or conflict.MASTER
    rev: int                    # the revision now current for the row
    op: str = UPSERT            # what the spoke should do with the row when the Master won
    wire: dict[str, Any] | None = None


class MasterService:
    def __init__(self, store, applier: Applier | None = None, item_filter: ItemFilter | None = None) -> None:
        self._store = store
        self._applier = applier or Applier(store)
        self._filter = item_filter or ItemFilter()

    # -- committing the Master's own edits ---------------------------------------------------
    def commit_local(self) -> int:
        """Give every local edit and deletion on the Master its revision (the Master never waits for
        arbitration: its writes are committed at once). Returns how many rows were committed."""
        n = 0
        for spec in registry.specs():
            for row in tracking.dirty_rows(spec.table):
                if spec.exclude_row(row):                      # node-local: stays here, never replicated
                    tracking.mark_clean(spec.table, {"id": row["id"]}, row["rev"])
                    continue
                tracking.mark_clean(spec.table, {"id": row["id"]}, tracking.commit_rev(spec.table))
                n += 1
            n += tracking.commit_tombstones(spec.table)
        return n

    # -- pull -----------------------------------------------------------------------------------
    def changes_since(self, since: dict[str, int], tables: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Per table (dependency order): the changes above the spoke's revision and the table's
        current revision, which the spoke records once it has applied them."""
        self.commit_local()
        allowed = set(tables) if tables is not None else None
        out = []
        for spec in registry.specs():
            if allowed is not None and spec.table not in allowed:
                continue
            last = int(since.get(spec.table, 0))
            changes: list[Change] = []
            for row in _rows(f"SELECT * FROM {spec.table} WHERE rev > ? AND dirty = 0 ORDER BY rev", last):
                if self._filter.allow_row(spec, row):
                    changes.append(Change(spec.table, UPSERT, spec.wire_key(row) if spec.wire_key else [],
                                          self._filter.scrub(spec, spec.to_wire(row)), row["rev"]))
            for t in tracking.tombstones(spec.table, committed_after_rev=last):
                changes.append(Change(spec.table, DELETE, t["natural_key"], None, t["rev"]))
            changes.sort(key=lambda c: c.rev)
            out.append({"table": spec.table, "rev": tracking.current_rev(spec.table), "changes": changes})
        return out

    # -- push -----------------------------------------------------------------------------------
    def receive_push(self, node_id: str, pushed: list[Pushed], offset: conflict.ClockOffset) -> list[PushResult]:
        self.commit_local()
        results: list[PushResult] = []
        order = {s.table: i for i, s in enumerate(registry.specs())}
        # parents first for edits, children first for deletions, as on the spoke
        for p in sorted((p for p in pushed if p.op == UPSERT), key=lambda p: order.get(p.table, 999)):
            results.append(self._one(p, offset))
        for p in sorted((p for p in pushed if p.op == DELETE), key=lambda p: -order.get(p.table, 999)):
            results.append(self._one(p, offset))
        return results

    def _one(self, p: Pushed, offset: conflict.ClockOffset) -> PushResult:
        spec = registry.get(p.table)
        if spec is None or spec.find_local is None:
            raise ValueError(f"{p.table} is not a replicable table here")
        local = spec.find_local(p.key)
        tomb = None
        if local is None:
            tomb = next((t for t in tracking.tombstones(p.table, committed_after_rev=0) if t["natural_key"] == p.key), None)
        cur_rev = local["rev"] if local else (tomb["rev"] if tomb else 0)
        cur_time = local["modified"] if local else (tomb["deleted_at"] if tomb else 0.0)
        if local is None and tomb is None and p.op == DELETE:
            return PushResult(p.table, p.key, "committed", conflict.NODE, 0, DELETE)          # already gone
        decision = conflict.arbitrate(p.base_rev, cur_rev, p.modified, cur_time, offset)
        if decision.winner == conflict.NODE:
            rev = tracking.commit_rev(p.table)
            if p.op == DELETE:
                self._applier.apply([Change(p.table, DELETE, p.key, None, rev)], peer_id="push")
                tracking.record_tombstone(p.table, p.key, rev)
                return PushResult(p.table, p.key, decision.kind, conflict.NODE, rev, DELETE)
            self._applier.apply([Change(p.table, UPSERT, p.key, p.wire, rev)], peer_id="push")
            return PushResult(p.table, p.key, decision.kind, conflict.NODE, rev)
        # the Master's value stands: tell the spoke to adopt it (the spoke snapshots its own loser)
        if local is None:
            return PushResult(p.table, p.key, "conflict", conflict.MASTER, cur_rev, DELETE)
        return PushResult(p.table, p.key, "conflict", conflict.MASTER, cur_rev, UPSERT,
                          self._filter.scrub(spec, spec.to_wire(local)))

    # -- acknowledgements and tombstone release ------------------------------------------------------
    def record_info(self, node_id: str, info: dict) -> None:
        """Remember what the node reported at handshake (its Galileo and database version, NCON-040)."""
        self._store.peer_set(node_id, "info", {k: info.get(k) for k in ("galileo", "schema", "protocol")})

    def record_ack(self, node_id: str, revs: dict[str, int]) -> None:
        self._store.peer_set(node_id, "ack", revs)

    def prune_tombstones(self, paired_nodes: Iterable[str]) -> int:
        """Release tombstones every paired node has acknowledged (NCON-180). A node not in
        *paired_nodes* (deleted) no longer holds anything back; one that has never acknowledged holds
        everything back."""
        nodes = list(paired_nodes)
        if not nodes:
            return 0
        released = 0
        for spec in registry.specs():
            acks = {n: int((self._store.peer_get(n, "ack", {}) or {}).get(spec.table, 0)) for n in nodes}
            upto = conflict.releasable_up_to(acks)
            if upto > 0:
                released += tracking.release_committed(spec.table, upto)
        return released


def _rows(sql: str, *params: Any) -> list[dict[str, Any]]:
    from galileo.library.models.base import db
    cur = db.execute_sql(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
