# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Run reports for a person to read, how many are kept, and undoing a run (NCON-320, NCON-220).

Every sync leaves a report row (``NodeStore.report_finish``). This module turns a row into a line and a detail
text, clamps the retention setting, and undoes a run by writing its snapshots back. It has no Qt, so the Options
page and the tests share it. Secret values are never in a report: ``secrets_changed`` holds names only.
"""

from __future__ import annotations

import time
from typing import Any

DEFAULT_RETENTION = 100
MIN_RETENTION = 10
MAX_RETENTION = 1000
SETTING = "report_retention"


def retention(settings: Any) -> int:
    """How many run reports to keep: the stored value clamped to a sane range, the default when unset or unreadable."""
    try:
        value = int(settings.get(SETTING, DEFAULT_RETENTION)) if settings is not None else DEFAULT_RETENTION
    except (TypeError, ValueError):
        return DEFAULT_RETENTION
    return max(MIN_RETENTION, min(MAX_RETENTION, value))


def _total(counts: Any) -> int:
    return sum(int(v) for v in counts.values()) if isinstance(counts, dict) else 0


def _when(stamp: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(stamp)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown time"


def headline(report: dict[str, Any]) -> str:
    """One line for a list: when, how it ended, what changed."""
    summary = report.get("summary") or {}
    parts = [f"{_total(summary.get('applied'))} applied", f"{_total(summary.get('deleted'))} deleted"]
    if summary.get("skipped_dirty"):
        parts.append(f"{len(summary['skipped_dirty'])} left to the Master")
    if summary.get("deferred"):
        parts.append(f"{len(summary['deferred'])} deferred")
    if summary.get("errors"):
        parts.append(f"{len(summary['errors'])} failed")
    return f"{_when(report.get('started_at'))}  {report.get('outcome', '?')}  -  " + ", ".join(parts)


def detail(report: dict[str, Any]) -> str:
    """Everything a run recorded, readable: counts per table, what was skipped or deferred, errors."""
    summary = report.get("summary") or {}
    lines = [headline(report)]
    for title, key in (("Added or changed", "applied"), ("Deleted", "deleted")):
        counts = summary.get(key) or {}
        if counts:
            lines.append(f"{title}: " + ", ".join(f"{table} {n}" for table, n in sorted(counts.items())))
    for title, key in (("Left to the Master (edited here too)", "skipped_dirty"), ("Deferred (parent missing)", "deferred"),
                       ("Loopback addresses", "loopback")):
        if summary.get(key):
            lines.append(f"{title}: " + "; ".join(summary[key]))
    for table, why in sorted((summary.get("errors") or {}).items()):
        lines.append(f"Failed {table}: {why}")
    if summary.get("secrets_changed"):
        lines.append("Secrets changed (names only): " + ", ".join(summary["secrets_changed"]))
    return "\n".join(lines)


def undo(store: Any, run_id: str) -> int:
    """Put back what *run_id* changed, as ordinary local edits that replicate like any other. Returns how many rows
    were restored (0 when the run kept no snapshots, e.g. nothing in it replaced or deleted a row)."""
    from nodeconnect.apply import restore
    restored = restore(store, run_id)
    store.peer_set("local", f"undone:{run_id}", time.time())
    return restored


def undone(store: Any, run_id: str) -> bool:
    return store.peer_get("local", f"undone:{run_id}") is not None


__all__ = ["DEFAULT_RETENTION", "MAX_RETENTION", "MIN_RETENTION", "SETTING", "detail", "headline", "retention", "undo",
           "undone"]
