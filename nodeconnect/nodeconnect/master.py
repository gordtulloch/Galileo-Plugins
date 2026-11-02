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

from galileo.replication import tracking

from nodeconnect import conflict, repl
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


TOMB = "#t"            # a table's deletion cursor travels beside its revision: "<table>#t"


class MasterService:
    def __init__(self, store, issuer, applier: Applier | None = None, item_filter: ItemFilter | None = None) -> None:
        """*issuer* is core's ``RevisionIssuer``: only the Master may issue revisions."""
        self._store = store
        self._issuer = issuer
        self._applier = applier or Applier(store)
        self._filter = item_filter or ItemFilter()

    # -- committing the Master's own edits ---------------------------------------------------
    def commit_local(self) -> int:
        """Give every local edit on the Master its revision (the Master never waits for arbitration:
        its writes are committed at once). Deletions need none: a spoke finds them by the tombstone
        cursor. Returns how many rows were committed."""
        n = 0
        for spec in repl.specs():
            for row in tracking.dirty_rows(spec.table):
                if not spec.replicates(row):                   # node-local: stays here, never replicated
                    tracking.mark_clean(spec.table, "id = ?", (row["id"],), row["rev"])
                    continue
                tracking.mark_clean(spec.table, "id = ?", (row["id"],), self._issuer.commit_rev(spec.table))
                n += 1
        return n

    # -- pull -----------------------------------------------------------------------------------
    def changes_since(self, since: dict[str, int], tables: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Per table (dependency order): the changes above the spoke's revision and tombstone cursor,
        and the table's current revision and newest tombstone, which the spoke records once it has
        applied them."""
        self.commit_local()
        allowed = set(tables) if tables is not None else None
        out = []
        for spec in repl.specs():
            if allowed is not None and spec.table not in allowed:
                continue
            last, last_tomb = int(since.get(spec.table, 0)), int(since.get(spec.table + TOMB, 0))
            changes: list[Change] = []
            for row in repl.rows(f"SELECT * FROM {spec.table} WHERE rev > ? AND dirty = 0 ORDER BY rev", last):
                if self._filter.allow_row(spec, row):
                    changes.append(Change(spec.table, UPSERT, repl.key_of_row(spec, row),
                                          self._filter.wire_of(spec, row), row["rev"]))
            changes.sort(key=lambda c: c.rev)
            tombs = tracking.tombstones(spec.table, last_tomb)
            changes.extend(Change(spec.table, DELETE, t["key"], None, t["rev"], tomb=t["id"])
                           for t in tombs if repl.tombstone_replicates(spec, t["key"]))
            out.append({"table": spec.table, "rev": self._issuer.current(spec.table),
                        "tomb": tombs[-1]["id"] if tombs else last_tomb, "changes": changes})
        return out

    # -- push -----------------------------------------------------------------------------------
    def receive_push(self, node_id: str, pushed: list[Pushed], offset: conflict.ClockOffset) -> list[PushResult]:
        self.commit_local()
        results: list[PushResult] = []
        order = {s.table: i for i, s in enumerate(repl.specs())}
        # parents first for edits, children first for deletions, as on the spoke
        for p in sorted((p for p in pushed if p.op == UPSERT), key=lambda p: order.get(p.table, 999)):
            results.append(self._one(p, offset))
        for p in sorted((p for p in pushed if p.op == DELETE), key=lambda p: -order.get(p.table, 999)):
            results.append(self._one(p, offset))
        return results

    def _one(self, p: Pushed, offset: conflict.ClockOffset) -> PushResult:
        spec = repl.spec_for(p.table)
        if spec is None:
            raise ValueError(f"{p.table} is not a replicable table here")
        local = repl.find_local(spec, p.key)
        tomb = None
        if local is None:
            tomb = next((t for t in tracking.tombstones(p.table) if t["key"] == p.key), None)
        # a deletion is itself a change after the revision the row had, so an edit based on that
        # revision is stale and the later of the two wins, as for any other conflict
        cur_rev = local["rev"] if local else (tomb["rev"] + 1 if tomb else 0)
        cur_time = local["modified"] if local else (tomb["deleted_at"] if tomb else 0.0)
        if local is None and tomb is None and p.op == DELETE:
            return PushResult(p.table, p.key, "committed", conflict.NODE, 0, DELETE)          # already gone
        decision = conflict.arbitrate(p.base_rev, cur_rev, p.modified, cur_time, offset)
        if decision.winner == conflict.NODE:
            rev = self._issuer.commit_rev(p.table)
            if p.op == DELETE:
                self._applier.apply([Change(p.table, DELETE, p.key, None, rev)], peer_id="push")
                repl.record_tombstone(p.table, p.key, rev)
                return PushResult(p.table, p.key, decision.kind, conflict.NODE, rev, DELETE)
            self._applier.apply([Change(p.table, UPSERT, p.key, p.wire, rev)], peer_id="push")
            return PushResult(p.table, p.key, decision.kind, conflict.NODE, rev)
        # the Master's value stands: tell the spoke to adopt it (the spoke snapshots its own loser)
        if local is None:
            return PushResult(p.table, p.key, "conflict", conflict.MASTER, cur_rev, DELETE)
        return PushResult(p.table, p.key, "conflict", conflict.MASTER, cur_rev, UPSERT,
                          self._filter.wire_of(spec, local))

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
        for spec in repl.specs():
            acks = {n: int((self._store.peer_get(n, "ack", {}) or {}).get(spec.table + TOMB, 0)) for n in nodes}
            upto = conflict.releasable_up_to(acks)
            if upto > 0:
                released += tracking.release_tombstones(spec.table, upto)
        return released


