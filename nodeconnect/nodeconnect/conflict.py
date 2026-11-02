# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Arbitration and tombstone release (NCON-160, NCON-170, NCON-180; ADR-NCON-001, ADR-NCON-004).

Pure decisions, no I/O. A change conflicts exactly when its ``base_rev`` is not the record's current
revision; the later edit by offset-corrected timestamp wins, ties go to the Master, and the loser is
reported so it can be kept in the snapshot. A conflict never blocks a sync.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ClockOffset:
    """Offset to add to a node's clock to get Master time, with the error bound of the measurement."""
    seconds: float
    error: float

    @classmethod
    def measure(cls, t0: float, t1: float, t2: float, t3: float) -> ClockOffset:
        """From one round trip: client send *t0*, server receive *t1*, server send *t2*, client
        receive *t3* (the usual NTP estimate)."""
        return cls(((t1 - t0) + (t2 - t3)) / 2.0, ((t3 - t0) - (t2 - t1)) / 2.0)

    def to_master(self, node_time: float) -> float:
        return node_time + self.seconds


COMMIT, CONFLICT = "commit", "conflict"
NODE, MASTER = "node", "master"


@dataclass(frozen=True)
class Decision:
    kind: str                   # COMMIT or CONFLICT
    winner: str                 # NODE or MASTER (always NODE for a clean commit)
    loser: str = ""             # "" for a clean commit, otherwise the side whose value is kept as a snapshot


def arbitrate(base_rev: int, current_rev: int, node_modified: float, master_modified: float,
              offset: ClockOffset) -> Decision:
    """Decide a pushed change against the Master's current row."""
    if base_rev == current_rev:
        return Decision(COMMIT, NODE)
    node_time = offset.to_master(node_modified)
    if node_time > master_modified:
        return Decision(CONFLICT, NODE, loser=MASTER)
    return Decision(CONFLICT, MASTER, loser=NODE)            # a tie goes to the Master


def releasable_up_to(acks: Mapping[str, int]) -> int:
    """The highest position every paired node has acknowledged: tombstones at or below it may be
    released (NCON-180). *acks* maps node id to its acknowledged position. A revoked but not deleted
    node stays in *acks* and holds tombstones back; a **deleted** node is removed from it by the
    caller and no longer does. With no nodes there is nothing to wait for and the answer is 0 (the
    caller then has no one to propagate to)."""
    return min(acks.values()) if acks else 0
