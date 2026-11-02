# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The spoke's half of sync (NCON-120, 130, 140, 160, 300, 305).

One pass is: handshake and compatibility, pull what changed since the revisions last applied, stage
and apply it through the session gate, push this node's own edits and deletions, apply the Master's
answers, then acknowledge. Each page of a pull is applied before ``last_rev`` advances, so a failure
anywhere resumes from the last point that really committed. A node that has never synced
(no ``last_rev``) pulls everything.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from galileo.replication import tracking

from nodeconnect import compat, conflict, repl
from nodeconnect.apply import DELETE, UPSERT, Applier, ApplyGate, Change
from nodeconnect.codec import ItemFilter
from nodeconnect.master import TOMB, Pushed, PushResult

logger = logging.getLogger(__name__)

IDLE, BLOCKED, BACKOFF = "idle", "blocked", "backoff"


class MasterLinkPort(Protocol):
    """What the spoke needs from the Master over Connect Link."""

    def handshake(self, info: dict[str, Any], fingerprints: dict[str, str]) -> dict[str, Any]: ...
    def pull(self, since: dict[str, int], tables: list[str]) -> list[dict[str, Any]]: ...
    def push(self, pushed: list[Pushed], offset: conflict.ClockOffset) -> list[PushResult]: ...
    def ack(self, revs: dict[str, int]) -> None: ...


@dataclass
class SyncResult:
    state: str
    message: str = ""
    first_sync: bool = False
    pulled: int = 0
    pushed: int = 0
    conflicts: int = 0
    deferred: bool = False                     # received changes are staged, waiting for a session to end
    skipped_tables: dict[str, str] = field(default_factory=dict)
    run_ids: list[str] = field(default_factory=list)


def column_fingerprints() -> dict[str, str]:
    from galileo.library.models.base import db
    return {s.table: compat.column_fingerprint(c.name for c in db.get_columns(s.table)) for s in repl.specs()}


class SpokeSync:
    def __init__(
        self, store, applier: Applier, gate: ApplyGate, master_id: str, local_info: Callable[[], dict[str, Any]],
        item_filter: ItemFilter | None = None, clock: Callable[[], float] = time.time,
    ) -> None:
        self._store, self._applier, self._gate = store, applier, gate
        self._master, self._local_info = master_id, local_info
        self._filter = item_filter or ItemFilter()
        self._clock = clock
        self.failures = 0

    # -- state kept per Master ----------------------------------------------------------------
    def last_rev(self) -> dict[str, int]:
        return dict(self._store.peer_get(self._master, "last_rev", {}) or {})

    def _advance(self, table: str, rev: int, tomb: int = 0) -> None:
        revs = self.last_rev()
        revs[table] = max(revs.get(table, 0), rev)
        revs[table + TOMB] = max(revs.get(table + TOMB, 0), tomb)
        self._store.peer_set(self._master, "last_rev", revs)

    @staticmethod
    def backoff_delay(failures: int, rand: Callable[[], float] = random.random) -> float:
        """Exponential from 5 s to 15 min, with jitter (so nodes do not reconnect in step)."""
        base = min(900.0, 5.0 * (2 ** max(0, failures - 1)))
        return base * (0.75 + 0.5 * rand())

    # -- one pass ------------------------------------------------------------------------------
    def run_once(self, link: MasterLinkPort) -> SyncResult:
        try:
            result = self._run(link)
        except Exception as exc:                          # fault isolation: never reaches the session or the bus
            self.failures += 1
            logger.exception("NodeConnect sync failed")
            return SyncResult(BACKOFF, f"{exc.__class__.__name__}: {exc}")
        self.failures = 0 if result.state == IDLE else self.failures
        return result

    def _run(self, link: MasterLinkPort) -> SyncResult:
        local = self._local_info()
        t0 = self._clock()
        remote = link.handshake(local, column_fingerprints())
        t3 = self._clock()
        policy = compat.decide(local, remote["info"], column_fingerprints(), remote["fingerprints"])
        if policy.mode == compat.REFUSE:
            return SyncResult(BLOCKED, policy.message)
        offset = conflict.ClockOffset.measure(t0, remote["received"], remote["sent"], t3)
        since = self.last_rev()
        result = SyncResult(IDLE, policy.message, first_sync=not since, skipped_tables=dict(policy.skipped))

        # 1. pull, stage and apply (NCON-120/130)
        tables = list(policy.tables)
        for page in link.pull(since, tables):
            changes: list[Change] = page["changes"]
            if changes:
                report = self._gate.submit(self._master, changes)
                result.pulled += len(changes)
                if report is None:
                    result.deferred = True              # staged; last_rev stays so nothing is skipped
                    continue
                result.run_ids.append(report.run_id)
                if page["table"] in report.errors:
                    result.message = f"{page['table']}: {report.errors[page['table']]}"
                    continue
            self._advance(page["table"], page["rev"], page.get("tomb", 0))

        # 2. push this node's own edits and deletions (NCON-140)
        pushed = self._collect(tables)
        if pushed:
            results = link.push(pushed, offset)
            result.pushed = len(pushed)
            result.conflicts = sum(1 for r in results if r.status == "conflict")
            self._apply_answers(results, result)

        # 3. acknowledge what has been applied
        if not result.deferred:
            link.ack(self.last_rev())
        return result

    # -- local changes ---------------------------------------------------------------------------------
    def _collect(self, tables: list[str]) -> list[Pushed]:
        out: list[Pushed] = []
        for spec in repl.specs():
            if spec.table not in tables:
                continue
            for row in tracking.dirty_rows(spec.table):
                if not self._filter.allow_row(spec, row):
                    continue
                out.append(Pushed(spec.table, UPSERT, repl.key_of_row(spec, row),
                                  self._filter.wire_of(spec, row), row["base_rev"], row["modified"]))
            for t in tracking.tombstones(spec.table):
                if repl.tombstone_replicates(spec, t["key"]):
                    out.append(Pushed(spec.table, DELETE, t["key"], None, t["rev"], t["deleted_at"]))
        return out

    def _apply_answers(self, results: list[PushResult], result: SyncResult) -> None:
        """Clear what the Master accepted; adopt (and snapshot) what it decided against us."""
        overrides: list[Change] = []
        answered: set[tuple[str, str]] = set()
        for r in results:
            spec = repl.spec_for(r.table)
            if spec is None:
                continue
            answered.add((r.table, json.dumps(r.key)))
            if r.winner == conflict.NODE:                     # accepted: clean, at the Master's revision
                if r.op != DELETE:
                    local = repl.find_local(spec, r.key)
                    if local is not None:
                        tracking.mark_clean(r.table, "id = ?", (local["id"],), r.rev)
            else:                                             # the Master's value stands over ours
                overrides.append(Change(r.table, r.op, r.key, r.wire, r.rev, force=True))
        for table in {t for t, _ in answered}:                # a decided deletion is no longer pending
            for t in tracking.tombstones(table):
                if (table, json.dumps(t["key"])) in answered:
                    repl.drop_tombstone(t["id"])
        if overrides:
            report = self._applier.apply(overrides, peer_id=self._master)
            result.run_ids.append(report.run_id)
