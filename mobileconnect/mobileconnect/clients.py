# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Paired apps and their roles. A role changes only here, from the Master's own page: no route can reach it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mobileconnect import PLUGIN

VIEWER, OPERATOR = "Viewer", "Operator"
ROLE_PERMISSIONS = {VIEWER: ("view",), OPERATOR: ("view", "control")}
DECLARED = ("view", "control")                    # never config.*, which the store would refuse for this plugin anyway


class ClientError(Exception):
    """A client operation that cannot be done; the message is shown to the person."""


def role_of(permissions) -> str:
    return OPERATOR if "control" in permissions else VIEWER


def holder_of(peer_id: str) -> str:
    """The identity under which an app holds a lease (``GatewayRequest.holder``)."""
    return f"{PLUGIN}:{peer_id}"


class ClientRegistry:
    def __init__(self, peers, pairing, leases: Callable[[], Any], fingerprint: Callable[[], str] = lambda: "",
                 close_stream: Callable[[str], None] | None = None) -> None:
        self._peers, self._pairing, self._leases = peers, pairing, leases
        self._fingerprint, self._close_stream = fingerprint, close_stream
        peers.declare_plugin(PLUGIN, DECLARED)

    def set_stream_closer(self, close_stream: Callable[[str], None] | None) -> None:
        self._close_stream = close_stream

    # -- pending approvals ------------------------------------------------------------------
    def pending(self) -> list[dict[str, Any]]:
        """What the person sees beside each code. It must match the code on the device."""
        return [{"request_id": r.request_id, "code": r.code, "name": r.name, "platform": r.platform,
                 "source": r.source, "expires_at": r.expires_at}
                for r in self._pairing.pending() if r.plugin == PLUGIN]

    def _waiting(self, request_id: str) -> None:
        if request_id not in {r["request_id"] for r in self.pending()}:
            raise ClientError("That pairing request is no longer waiting.")

    def approve(self, request_id: str, role: str = VIEWER) -> str:
        """Approve; a new app is a Viewer unless the person says otherwise. Returns its peer id."""
        self._waiting(request_id)
        peer = self._pairing.approve(request_id, ROLE_PERMISSIONS[role], cert_fingerprint=self._fingerprint())
        return peer.peer_id

    def reject(self, request_id: str) -> None:
        self._waiting(request_id)
        self._pairing.reject(request_id)

    # -- approved apps --------------------------------------------------------------------------
    def clients(self) -> list[dict[str, Any]]:
        return [{"peer_id": p.peer_id, "name": p.name, "role": role_of(p.permissions), "revoked": p.revoked_at is not None,
                 "last_seen": p.last_contact}
                for p in self._peers.list(PLUGIN) if not p.is_service]

    def _active(self, peer_id: str):
        peer = self._peers.get(peer_id)
        if peer is None or peer.plugin != PLUGIN or peer.revoked_at is not None:
            raise ClientError("That app is no longer paired.")
        return peer

    def set_role(self, peer_id: str, role: str) -> None:
        self._active(peer_id)
        self._peers.set_permissions(peer_id, ROLE_PERMISSIONS[role])
        if role == VIEWER:
            self._release(peer_id, "no longer an Operator")

    def revoke(self, peer_id: str) -> None:
        """At once: the token stops working, the stream closes, and any lease is let go."""
        self._active(peer_id)
        self._peers.revoke(peer_id)
        if self._close_stream is not None:
            self._close_stream(peer_id)
        self._release(peer_id, "revoked")

    def _release(self, peer_id: str, reason: str) -> None:
        leases = self._leases()
        if leases is not None:
            leases.on_disconnect(holder_of(peer_id), reason)


__all__ = ["DECLARED", "OPERATOR", "ROLE_PERMISSIONS", "VIEWER", "ClientError", "ClientRegistry", "holder_of", "role_of"]
