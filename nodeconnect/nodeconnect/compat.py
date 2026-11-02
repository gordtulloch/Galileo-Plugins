# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Compatibility policy for the version handshake (NCON-305).

Equal schema levels sync everything; an older node syncs only the tables whose column fingerprints
both sides agree on and reports the rest; an incompatible Connect Link protocol is refused with a
message naming what to upgrade. Unknown columns are never guessed at.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

FULL, PARTIAL, REFUSE = "full", "partial", "refuse"


def column_fingerprint(columns: Iterable[str]) -> str:
    """Order-independent fingerprint of a table's columns."""
    return hashlib.sha256("\n".join(sorted(columns)).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Policy:
    mode: str
    tables: tuple[str, ...] = ()                 # the tables to sync (FULL: all of the local ones)
    skipped: dict[str, str] = field(default_factory=dict)      # table -> why it is not synced
    message: str = ""


def decide(local: Mapping[str, object], remote: Mapping[str, object],
           local_fingerprints: Mapping[str, str], remote_fingerprints: Mapping[str, str]) -> Policy:
    """*local* / *remote* are handshake payloads (``protocol``, ``schema``, ...)."""
    lp, rp = int(local["protocol"]), int(remote["protocol"])         # type: ignore[call-overload]
    if lp != rp:
        who = "this node" if rp > lp else "the Master"
        return Policy(REFUSE, message=f"Connect Link protocol {lp} here and {rp} there are not compatible; "
                                      f"update Galileo on {who}.")
    ls, rs = int(local["schema"]), int(remote["schema"])             # type: ignore[call-overload]
    tables = tuple(local_fingerprints)
    if ls == rs:
        return Policy(FULL, tables)
    ok, skipped = [], {}
    for t in tables:
        if t not in remote_fingerprints:
            skipped[t] = "the other node does not have this table"
        elif remote_fingerprints[t] != local_fingerprints[t]:
            skipped[t] = "the tables differ between versions"
        else:
            ok.append(t)
    return Policy(PARTIAL, tuple(ok), skipped,
                  f"The databases are at different versions ({ls} here, {rs} there); "
                  f"{len(ok)} table(s) will sync and {len(skipped)} will be skipped until both are updated.")
