# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Pairing a node with its Master (NCON-020, 030, 040, 050, 060; ADR-NCON-008).

Core's Connect Link owns the pairing handshake itself (request, confirmation code, approval, one-time
token). This module is NodeConnect's use of it:

* ``PairingDesk``, on the **Master**: lists pending requests and paired nodes, approves with exactly
  the four node permissions (never ``control`` or ``view``), and refuses to approve while this node
  is itself a spoke. Deleting a node forgets what it had acknowledged, so it stops holding back
  tombstones (NCON-180).
* ``PairingClient``, on the **spoke**: asks the Master to pair, shows the confirmation code, collects
  the token once approved, and keeps the credentials node-locally: address and certificate
  fingerprint in the node-local settings namespace (never replicated), the token in the OS
  credential store (never in the database, an export or a log; NCON-060).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from nodeconnect.roles import RoleGuard

logger = logging.getLogger(__name__)

PLUGIN = "nodeconnect"
NODE_PERMISSIONS = ("config.read", "config.write", "data.push", "data.pull")
NAMESPACE = "plugin.NodeConnect"                  # node-local: core's registry never replicates it
_TOKEN = "nodeconnect.master.token"

PENDING, APPROVED, REJECTED, EXPIRED = "pending", "approved", "rejected", "expired"


class PairingFailed(Exception):
    """Pairing could not proceed; the message is shown to the user."""


# -- the Master's side ---------------------------------------------------------------------------------
class PairingDesk:
    def __init__(self, peers, pairing, store, roles: RoleGuard) -> None:
        self._peers, self._pairing, self._store, self._roles = peers, pairing, store, roles
        peers.declare_plugin(PLUGIN, NODE_PERMISSIONS)

    def pending(self) -> list[dict[str, Any]]:
        """What the person sees beside each code. No token, ever."""
        return [{"request_id": r.request_id, "code": r.code, "name": r.name, "platform": r.platform,
                 "source": r.source, "expires_at": r.expires_at}
                for r in self._pairing.pending() if r.plugin == PLUGIN]

    def approve(self, request_id: str, cert_fingerprint: str = "") -> str:
        """Approve a pending request; returns the new node's peer id."""
        self._roles.check_approve_spoke()
        if request_id not in {r["request_id"] for r in self.pending()}:
            raise PairingFailed("That pairing request is no longer waiting.")
        return self._pairing.approve(request_id, NODE_PERMISSIONS, cert_fingerprint=cert_fingerprint).peer_id

    def reject(self, request_id: str) -> None:
        if request_id not in {r["request_id"] for r in self.pending()}:
            raise PairingFailed("That pairing request is no longer waiting.")
        self._pairing.reject(request_id)

    def nodes(self) -> list[dict[str, Any]]:
        """Paired nodes with name, address-independent identity, last contact and database version."""
        out = []
        for p in self._peers.list(PLUGIN):
            info = self._store.peer_get(p.peer_id, "info", {}) or {}
            out.append({"peer_id": p.peer_id, "name": p.name, "platform": p.platform,
                        "last_contact": p.last_contact, "revoked": p.revoked_at is not None,
                        "schema": info.get("schema"), "galileo": info.get("galileo")})
        return out

    def node_ids(self) -> list[str]:
        """Nodes that still count when deciding which tombstones may be released: paired, including
        revoked ones (they may be reinstated), not deleted ones."""
        return [p.peer_id for p in self._peers.list(PLUGIN)]

    def revoke(self, peer_id: str) -> None:
        self._own(peer_id)
        self._peers.revoke(peer_id)

    def delete(self, peer_id: str) -> None:
        """Remove the node for good, with its acknowledgements and anything queued for it."""
        self._own(peer_id)
        self._peers.delete(peer_id)
        self._store.peer_forget(peer_id)

    def _own(self, peer_id: str) -> None:
        p = self._peers.get(peer_id)
        if p is None or p.plugin != PLUGIN:
            raise PairingFailed("That is not one of this node's paired nodes.")


# -- the spoke's side ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Credentials:
    address: str                  # https://host:port of the Master's Connect Link
    fingerprint: str              # SHA-256 of its certificate, pinned at pairing
    token: str

    @property
    def master_id(self) -> str:
        """Stable identity of this Master for the per-Master sync state."""
        return self.fingerprint


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class CredentialStore:
    """Address and fingerprint in node-local settings, token in the credential store."""

    def __init__(self, secrets: SecretStore, get_setting: Callable[..., Any], set_setting: Callable[..., Any],
                 delete_setting: Callable[..., Any]) -> None:
        self._secrets, self._get, self._set, self._del = secrets, get_setting, set_setting, delete_setting

    def save(self, c: Credentials) -> None:
        self._secrets.set(_TOKEN, c.token)
        self._set(NAMESPACE, "master_address", c.address)
        self._set(NAMESPACE, "master_fingerprint", c.fingerprint)

    def load(self) -> Credentials | None:
        address, fp = self._get(NAMESPACE, "master_address", ""), self._get(NAMESPACE, "master_fingerprint", "")
        token = self._secrets.get(_TOKEN)
        return Credentials(address, fp, token) if address and fp and token else None

    def designated(self) -> str | None:
        """The Master's address once chosen, paired or not (what makes this node a spoke)."""
        return self._get(NAMESPACE, "master_address", "") or None

    def clear(self) -> None:
        self._secrets.delete(_TOKEN)
        for key in ("master_address", "master_fingerprint"):
            self._del(NAMESPACE, key)


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict[str, Any]
    fingerprint: str = ""         # the certificate the connection actually presented


# (method, path, body) -> Reply, to a Master whose certificate is not yet trusted
OpenTransport = Callable[[str, str, dict[str, Any] | None], Reply]


@dataclass(frozen=True)
class PendingPairing:
    request_id: str
    code: str                     # shown here and on the Master; the person compares them
    fingerprint: str
    address: str


class PairingClient:
    def __init__(self, transport_for: Callable[[str], OpenTransport], creds: CredentialStore,
                 roles: RoleGuard, name: str, platform: str = "") -> None:
        self._transport_for, self._creds, self._roles = transport_for, creds, roles
        self._name, self._platform = name, platform

    def request(self, address: str) -> PendingPairing:
        self._roles.check_designate_master(address)
        send = self._transport_for(address)
        reply = send("POST", "/connect/v1/pair", {"plugin": PLUGIN, "name": self._name,
                                                  "kind": "node", "platform": self._platform})
        if reply.status == 429:
            raise PairingFailed("The Master has had too many pairing requests; try again in a minute.")
        if reply.status == 503:
            raise PairingFailed("The Master has too many pairing requests waiting.")
        if reply.status != 202:
            raise PairingFailed("The Master refused the pairing request. Is NodeConnect enabled there?")
        claimed = str(reply.body.get("fingerprint", "")).replace(":", "").lower()
        if not claimed or claimed != reply.fingerprint.replace(":", "").lower():
            # whoever answered is not presenting the certificate the Master says is its own
            raise PairingFailed("The Master's certificate does not match what it reported. "
                                "Pairing was stopped; check the address and the network.")
        return PendingPairing(str(reply.body["request_id"]), str(reply.body["code"]), claimed, address)

    def poll(self, pending: PendingPairing) -> str:
        """``pending`` / ``approved`` / ``rejected`` / ``expired``. On approval the credentials are
        saved, and the Master becomes this node's Master."""
        send = self._transport_for(pending.address)
        reply = send("GET", f"/connect/v1/pair/{pending.request_id}", None)
        if reply.status != 200:
            raise PairingFailed("Could not ask the Master about the pairing request.")
        if reply.fingerprint.replace(":", "").lower() != pending.fingerprint:
            raise PairingFailed("The Master's certificate changed during pairing. Pairing was stopped.")
        state = str(reply.body.get("state", EXPIRED))
        if state == APPROVED:
            token = reply.body.get("token")
            if not token:
                return EXPIRED                      # the one-time token was already collected
            self._creds.save(Credentials(pending.address, pending.fingerprint, str(token)))
            return APPROVED
        return state if state in (PENDING, REJECTED) else EXPIRED

    def unpair(self) -> None:
        self._creds.clear()
