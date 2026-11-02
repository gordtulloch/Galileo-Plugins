# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Staging, session gate, transactional apply, snapshot and report (NCON-190, 200, 220, 230, 320).

Received changes are written to the durable inbox and acknowledged before anything is applied
(ADR-NCON-005). ``ApplyGate`` applies them at once when the node is idle and otherwise when the
session ends. ``Applier`` applies one table at a time in a single transaction, in dependency order:
a table that fails rolls back alone, a row whose parent is missing is deferred and reported, a row
with an unacknowledged local edit is left alone (the Master arbitrates it), and every value replaced
or deleted is snapshotted first so a run can be undone.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from galileo.net import is_loopback
from galileo.replication import tracking
from galileo.replication.registry import RegistryError

from nodeconnect import repl
from nodeconnect.codec import ItemFilter, merge_fields

logger = logging.getLogger(__name__)

# Fields that hold a device or PHD2 address: applied as received, but a loopback value is reported.
_ADDRESS_FIELDS = frozenset({"server", "host", "address", "phd2_host"})
UPSERT, DELETE = "upsert", "delete"


@dataclass(frozen=True)
class Change:
    table: str
    op: str                 # UPSERT or DELETE
    key: list[Any]
    wire: dict[str, Any] | None = None
    rev: int = 0
    force: bool = False         # the Master decided against this node's pending edit: overwrite it
    tomb: int = 0               # a deletion's tombstone id on the Master (its pull cursor)


@dataclass
class RunReport:
    run_id: str
    applied: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    deleted: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped_dirty: list[str] = field(default_factory=list)       # local edit pending: Master arbitrates
    deferred: list[str] = field(default_factory=list)            # parent missing
    loopback: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)         # table -> why it was rolled back
    secrets_changed: list[str] = field(default_factory=list)     # names only, never values

    def summary(self) -> dict[str, Any]:
        return {"applied": dict(self.applied), "deleted": dict(self.deleted),
                "skipped_dirty": self.skipped_dirty, "deferred": self.deferred,
                "loopback": self.loopback, "errors": self.errors, "secrets_changed": self.secrets_changed}

    @property
    def outcome(self) -> str:
        return "partial" if self.errors else "ok"


def _db():
    from galileo.library.models.base import db
    return db


def _columns(table: str) -> set[str]:
    return {c.name for c in _db().get_columns(table)}


def _model_for(table: str):
    """The Peewee model for *table*, or ``None`` (a plugin's table without one)."""
    import importlib
    import pkgutil

    from galileo.library import models
    from galileo.library.models.base import BaseModel
    for m in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"galileo.library.models.{m.name}")
    stack = list(BaseModel.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls._meta.table_name == table:
            return cls
        stack.extend(cls.__subclasses__())
    return None


# the foreign-key column that points at a row of these tables
_CHILD_FK = {"observatories": "observatory_id", "piers": "pier_id"}


def _rows(sql: str, *params: Any) -> list[dict[str, Any]]:
    cur = _db().execute_sql(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def _label(change: Change) -> str:
    return f"{change.table} {change.key}"


class Applier:
    def __init__(self, store, item_filter: ItemFilter | None = None,
                 on_changed: Callable[[list[str]], None] | None = None) -> None:
        self._store = store
        self._filter = item_filter or ItemFilter()
        self._on_changed = on_changed

    def apply(self, changes: list[Change], run_id: str | None = None, peer_id: str = "") -> RunReport:
        run_id = run_id or uuid.uuid4().hex
        report = RunReport(run_id)
        self._store.report_start(run_id, peer_id)
        by_table: dict[str, list[Change]] = defaultdict(list)
        for c in changes:
            by_table[c.table].append(c)
        touched: list[str] = []
        specs = repl.specs()
        # upserts parents-first, then deletions children-first, so a deleted parent cannot cascade
        # away a child that was about to be updated, and the children are snapshotted before it goes
        for ops, ordered in ((UPSERT, specs), (DELETE, list(reversed(specs)))):
            for spec in ordered:
                batch = [c for c in by_table.get(spec.table, ()) if c.op == ops]
                if not batch:
                    continue
                try:
                    with _db().atomic():
                        self._apply_table(spec, batch, report)
                    if spec.table not in touched:
                        touched.append(spec.table)
                except Exception as exc:                           # this table rolls back; the rest go on
                    logger.exception("NodeConnect: applying %s failed", spec.table)
                    report.errors[spec.table] = str(exc) or exc.__class__.__name__
                    self._rollback_counts(report, spec.table)
        known = {s.table for s in specs}
        for table in by_table:                                     # not a replicable table here
            if table not in known:
                report.errors[table] = "not a replicable table on this node"
        self._store.report_finish(run_id, report.outcome, report.summary())
        if touched and self._on_changed:
            try:
                self._on_changed(touched)
            except Exception:
                logger.exception("settings-changed callback raised")
        return report

    @staticmethod
    def _rollback_counts(report: RunReport, table: str) -> None:
        report.applied.pop(table, None)
        report.deleted.pop(table, None)

    # -- one table, inside its own transaction ---------------------------------------------------
    def _apply_table(self, spec, batch: list[Change], report: RunReport) -> None:
        cols = _columns(spec.table)
        with tracking.apply_mode():
            for change in batch:
                local = repl.find_local(spec, change.key)
                if local is not None and local.get("dirty") and not change.force:
                    report.skipped_dirty.append(_label(change))
                    continue
                if change.op == DELETE:
                    if local is not None:
                        self._snapshot_dependants(report, spec.table, local["id"])
                        self._snapshot(report, spec, change, local, "deleted")
                        _db().execute_sql(f"DELETE FROM {spec.table} WHERE id = ?", (local["id"],))
                        report.deleted[spec.table] += 1
                    continue
                try:
                    row = spec.from_wire(change.wire or {})
                except (RegistryError, LookupError) as exc:
                    report.deferred.append(f"{_label(change)}: {exc}")
                    continue
                self._flag_loopback(report, change, row)
                merged = merge_fields(spec, local, row, self._filter)
                merged.update(rev=change.rev or row.get("rev", 0), base_rev=change.rev or row.get("rev", 0), dirty=0)
                if local is None:
                    self._insert(spec.table, cols, merged)
                else:
                    self._snapshot(report, spec, change, local, "replaced")
                    self._update(spec.table, cols, local["id"], merged)
                report.applied[spec.table] += 1

    @staticmethod
    def _insert(table: str, cols: set[str], row: dict[str, Any]) -> None:
        data = {k: v for k, v in row.items() if k in cols and k != "id"}     # unknown columns are never guessed at
        model = _model_for(table)
        by_column = {f.column_name: f for f in model._meta.sorted_fields} if model else {}
        declared = {by_column[k]: v for k, v in data.items() if k in by_column}
        if model is None or not declared:
            names = ", ".join(data)
            _db().execute_sql(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' * len(data))})",
                              tuple(data.values()))
            return
        # through the model, so its Python-side defaults fill any column the sender did not carry
        new_id = model.insert(declared).execute()
        rest = {k: v for k, v in data.items() if k not in by_column}         # tracking columns
        if rest:
            sets = ", ".join(f"{k} = ?" for k in rest)
            _db().execute_sql(f"UPDATE {table} SET {sets} WHERE id = ?", (*rest.values(), new_id))

    @staticmethod
    def _update(table: str, cols: set[str], row_id: int, row: dict[str, Any]) -> None:
        data = {k: v for k, v in row.items() if k in cols and k != "id"}
        sets = ", ".join(f"{k} = ?" for k in data)
        _db().execute_sql(f"UPDATE {table} SET {sets} WHERE id = ?", (*data.values(), row_id))

    def _snapshot(self, report: RunReport, spec, change: Change, local: dict[str, Any], kind: str) -> None:
        old = {k: v for k, v in local.items() if k not in spec.secret_fields}
        if any(k in local for k in spec.secret_fields):
            report.secrets_changed.append(_label(change))
        key = repl.key_of_row(spec, local)
        wire = spec.wire(local)
        self._store.snapshot_save(report.run_id, {"table": spec.table, "key": key, "kind": kind,
                                                  "old": old, "wire": wire})

    def _snapshot_dependants(self, report: RunReport, table: str, row_id: int) -> None:
        """Rows that a delete will remove by cascade are kept too, children before their parent."""
        fk = _CHILD_FK.get(table)
        if fk is None:
            return
        for spec in repl.specs():
            if table not in spec.depends_on:
                continue
            for child in _rows(f"SELECT * FROM {spec.table} WHERE {fk} = ?", row_id):
                self._snapshot_dependants(report, spec.table, child["id"])
                self._snapshot(report, spec, Change(spec.table, DELETE, []), child, "cascaded")

    @staticmethod
    def _flag_loopback(report: RunReport, change: Change, row: dict[str, Any]) -> None:
        for k in _ADDRESS_FIELDS & row.keys():
            v = row[k]
            if isinstance(v, str) and v and is_loopback(v):
                report.loopback.append(f"{_label(change)}: {k}={v}")


def restore(store, run_id: str) -> int:
    """Undo a run by writing its snapshots back as **ordinary local edits** (dirty rows), so the
    restore replicates like any other change (NCON-220). Returns how many rows were restored."""
    n = 0
    for snap in reversed(store.snapshot_get(run_id)):
        d = snap["data"]
        spec = repl.spec_for(d["table"])
        if spec is None:
            continue
        cols = _columns(d["table"])
        old = {k: v for k, v in d["old"].items() if k in cols and k not in ("id", "dirty", "modified", "rev", "base_rev")}
        local = repl.find_local(spec, d["key"])
        if local is None:
            try:                                           # re-resolve foreign keys: ids differ once a parent was re-created
                row = spec.from_wire(d["wire"])
            except (RegistryError, LookupError):
                continue
            row.update({k: v for k, v in old.items() if k in spec.node_local_fields})
            for k in ("modified", "rev", "base_rev"):
                row.pop(k, None)
            Applier._insert(d["table"], cols, row)
        else:
            keep = {k: v for k, v in old.items() if k not in spec.secret_fields}
            Applier._update(d["table"], cols, local["id"], keep)
        n += 1
    return n


class ApplyGate:
    """Stage first, apply when idle (ADR-NCON-005): there is no manual apply and no prompt (NCON-200)."""

    def __init__(self, store, applier: Applier, is_busy: Callable[[], bool],
                 on_idle: Callable[[Callable[[], None]], None]) -> None:
        self._store, self._applier, self._is_busy, self._on_idle = store, applier, is_busy, on_idle
        self._waiting = False

    def submit(self, peer_id: str, changes: list[Change]) -> RunReport | None:
        """Stage *changes* durably (the caller may now acknowledge them), then apply if idle.
        Returns the report, or ``None`` if application was deferred until the session ends."""
        for c in changes:
            self._store.inbox_add(peer_id, c.table, c.key, c.op, c.wire, c.rev)
        return self.drain(peer_id)

    def drain(self, peer_id: str = "") -> RunReport | None:
        if self._is_busy():
            if not self._waiting:
                self._waiting = True
                self._on_idle(lambda: self._resume(peer_id))
            return None
        pending = self._store.inbox_pending()
        if not pending:
            return None
        changes = [Change(r["table_name"], r["op"], r["natural_key"], r["payload"], r["rev"]) for r in pending]
        report = self._applier.apply(changes, peer_id=peer_id)
        # rows of a table that rolled back stay pending and are tried again at the next drain
        self._store.inbox_mark([r["id"] for r in pending if r["table_name"] not in report.errors], "applied")
        return report

    def _resume(self, peer_id: str) -> None:
        self._waiting = False
        try:
            self.drain(peer_id)
        except Exception:
            logger.exception("NodeConnect: deferred apply failed")
