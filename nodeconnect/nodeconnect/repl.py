# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect's view of core's replication facilities (``galileo.replication``).

Core provides the registry, trigger-maintained change tracking, tombstones with an id cursor, the
Master's per-table revision issuer and the sync tables. What a sync needs on top -- finding a local
row by its natural key as it appears on the wire, naming a row's key, and recording a deletion that
was applied here on someone else's behalf -- is a few lines over those and lives here rather than in
core, which has no consumer of them but this plugin.
"""

from __future__ import annotations

import json
import time
from typing import Any

from galileo.replication import registry as _registry
from galileo.replication.registry import RegistryError


def specs() -> list[Any]:
    """Every replicable table's spec in dependency order."""
    return _registry.specs()


def spec_for(table: str) -> Any | None:
    return _registry.get(table)


def _db():
    from galileo.library.models.base import db
    db.connect(reuse_if_open=True)
    return db


def rows(sql: str, *params: Any) -> list[dict[str, Any]]:
    cur = _db().execute_sql(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def key_of_row(spec, row: dict[str, Any]) -> list[Any]:
    """The natural key of a database row as it is written on the wire (a parent by name)."""
    wire = spec.to_wire({k: v for k, v in row.items() if k != "id"})
    return [wire[c] for c in spec.natural_key]


def find_local(spec, key: list[Any]) -> dict[str, Any] | None:
    """This node's row for a wire natural key, or ``None`` (including when its parent is absent here)."""
    try:
        db_key = spec.from_wire(dict(zip(spec.natural_key, key, strict=True)))
    except RegistryError:
        return None
    except LookupError:                                  # core's UnresolvedParent
        return None
    cols = [k for k in db_key if k in {c.name for c in _db().get_columns(spec.table)}]
    found = rows(f"SELECT * FROM {spec.table} WHERE " + " AND ".join(f"{c} = ?" for c in cols),
                 *[db_key[c] for c in cols])
    return found[0] if found else None


def record_tombstone(table: str, key: list[Any], rev: int) -> int:
    """Remember a deletion that was applied here for another node, so this node's other peers learn
    of it (applying runs with tracking suspended, which writes no tombstone of its own)."""
    cur = _db().execute_sql(
        "INSERT INTO repl_tombstones (table_name, natural_key, rev, deleted_at) VALUES (?, ?, ?, ?)",
        (table, json.dumps(key), rev, time.time()))
    return int(cur.lastrowid)


def drop_tombstone(tombstone_id: int) -> None:
    """Forget one deletion record (this node has delivered it, or lost the argument over it)."""
    _db().execute_sql("DELETE FROM repl_tombstones WHERE id = ?", (tombstone_id,))


def tombstone_replicates(spec, key: list[Any]) -> bool:
    """Whether a deletion record is one that may leave this node: core's triggers also record the
    deletion of a node-local row (for example a Connect Link setting), which must never reach a peer."""
    return bool(spec.replicates(dict(zip(spec.natural_key, key, strict=False))))
