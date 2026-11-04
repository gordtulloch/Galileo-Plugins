# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Approved browsers: pairing, roles, expiry and revocation (WCON-100, 110, 130, 170; SDD §3.3).

A browser is an ordinary Connect Link peer of this plugin. Its token is the *device credential*: the web half
receives it once at approval and keeps it in the browser's cookie. A browser is a **Viewer** (``view``) until a
person promotes it on this Master to **Operator** (``view`` + ``control``); that method is called from the
Master's own UI and is registered on no route (WCON-110). No browser, and not the web half's service
credential, can hold ``config.*`` (WCON-130).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from webconnect import PLUGIN

VIEWER, OPERATOR = "viewer", "operator"
ROLE_PERMISSIONS = {VIEWER: ("view",), OPERATOR: ("view", "control")}
DECLARED = ("view", "control")                    # everything this plugin may ever grant


class BrowserError(Exception):
    """A browser operation that cannot be done; the message is shown to the person."""


def role_of(permissions) -> str:
    return OPERATOR if "control" in permissions else VIEWER


class BrowserRegistry:
    def __init__(self, peers, pairing, leases, clock: Callable[[], float] = time.time,
                 on_revoked: Callable[[str], None] | None = None) -> None:
        self._peers, self._pairing, self._leases, self._clock = peers, pairing, leases, clock
        self._on_revoked = on_revoked                # ends that browser's upstream streams and holds
        peers.declare_plugin(PLUGIN, DECLARED)

    # -- pending approvals ------------------------------------------------------------------
    def pending(self) -> list[dict[str, Any]]:
        """What the person sees beside each confirmation code."""
        return [{"request_id": r.request_id, "code": r.code, "name": r.name, "platform": r.platform,
                 "source": r.source, "expires_at": r.expires_at}
                for r in self._pairing.pending() if r.plugin == PLUGIN and r.kind == "browser"]

    def approve(self, request_id: str, expires_at: float | None = None) -> str:
        """Approve as a Viewer. Returns the browser's peer id."""
        if request_id not in {r["request_id"] for r in self.pending()}:
            raise BrowserError("That pairing request is no longer waiting.")
        return self._pairing.approve(request_id, ROLE_PERMISSIONS[VIEWER], expires_at=expires_at).peer_id

    def reject(self, request_id: str) -> None:
        if request_id not in {r["request_id"] for r in self.pending()}:
            raise BrowserError("That pairing request is no longer waiting.")
        self._pairing.reject(request_id)

    # -- approved browsers --------------------------------------------------------------------
    def browsers(self) -> list[dict[str, Any]]:
        now = self._clock()
        return [{"peer_id": p.peer_id, "name": p.name, "platform": p.platform,
                 "role": role_of(p.permissions), "expires_at": p.expires_at, "last_contact": p.last_contact,
                 "created_at": p.created_at, "revoked": p.revoked_at is not None, "active": p.active(now)}
                for p in self._peers.list(PLUGIN) if not p.is_service]

    def promote(self, peer_id: str) -> None:
        """Viewer to Operator. Only the Master's own UI controller calls this; no route does (WCON-110)."""
        self._set_role(peer_id, OPERATOR)

    def demote(self, peer_id: str) -> None:
        peer = self._browser(peer_id)
        self._set_role(peer_id, VIEWER)
        self._leases.on_disconnect(f"{PLUGIN}:{peer.peer_id}", "role changed")     # a former Operator keeps no lease

    def set_expiry(self, peer_id: str, expires_at: float | None) -> None:
        self._browser(peer_id)
        from galileo.library.models.link import LinkPeerRecord
        LinkPeerRecord.update(expires_at=expires_at).where(LinkPeerRecord.peer_id == peer_id).execute()

    def revoke(self, peer_id: str) -> None:
        """Immediately: the credential stops working, leases are released and its streams end (WCON-170)."""
        peer = self._browser(peer_id)
        self._peers.revoke(peer_id)
        self._leases.on_disconnect(f"{PLUGIN}:{peer.peer_id}", "revoked")
        if self._on_revoked is not None:
            self._on_revoked(peer_id)

    def delete(self, peer_id: str) -> None:
        self.revoke(peer_id)
        self._peers.delete(peer_id)

    def _browser(self, peer_id: str):
        p = self._peers.get(peer_id)
        if p is None or p.plugin != PLUGIN or p.is_service:
            raise BrowserError("That is not one of this plugin's approved browsers.")
        return p

    def _set_role(self, peer_id: str, role: str) -> None:
        self._browser(peer_id)
        self._peers.set_permissions(peer_id, ROLE_PERMISSIONS[role])

    # -- the web half's service credential --------------------------------------------------------
    def mint_service_credential(self, name: str = "WebConnect web application") -> tuple[str, str]:
        """A fresh service credential for the web half (view + control, never config.*). Any earlier one is
        revoked first, so there is exactly one working credential. Returns ``(peer_id, token)``."""
        for p in self._peers.list(PLUGIN):
            if p.is_service and p.revoked_at is None:
                self._peers.revoke(p.peer_id)
        peer, token = self._peers.create_peer(PLUGIN, name, DECLARED, kind="service", is_service=True)
        return peer.peer_id, token
