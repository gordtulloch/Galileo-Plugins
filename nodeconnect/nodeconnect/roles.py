# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Roles are derived from facts, never stored (NCON-020, ADR-NCON-008).

A node is a **spoke** when it has designated a Master, a **Master** when at least one spoke is paired
with it, and standalone otherwise. The two are exclusive (no chains), so the guard refuses the action
that would make a node both.
"""

from __future__ import annotations

from collections.abc import Callable

MASTER, SPOKE, STANDALONE = "master", "spoke", "standalone"


class RoleError(Exception):
    """An action that would give the node two roles; the message is shown to the user."""


class RoleGuard:
    def __init__(self, designated_master: Callable[[], str | None], paired_spokes: Callable[[], int]) -> None:
        self._master, self._spokes = designated_master, paired_spokes

    def role(self) -> str:
        if self._master():
            return SPOKE
        return MASTER if self._spokes() > 0 else STANDALONE

    def check_designate_master(self, address: str) -> None:
        """Before storing a Master for this node."""
        if not address.strip():
            raise RoleError("Enter the address of the Master, or choose one from the list.")
        if self._spokes() > 0:
            raise RoleError("This node is the Master for other nodes. Remove their pairings before "
                            "making it a spoke of another Master.")

    def check_approve_spoke(self) -> None:
        """Before approving a pairing request from another node."""
        if self._master():
            raise RoleError("This node is a spoke of another Master and cannot also be a Master. "
                            "Clear its Master first.")
