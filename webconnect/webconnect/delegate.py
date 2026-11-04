# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The Master half's delegated routes (SDD §3.3, ADR-WCON-002; WCON-090, 120, 140-160, 180, 580, 700, 740, 750).

The web half calls these with its service credential and names the browser it acts for in the
``X-Acting-Client`` header. Core's ``PeerStore.resolve_client`` refuses a browser that is unknown, revoked,
expired, a service, or another plugin's (LINK-090); what is left is the browser's *own* role. Every operation is
then handed to the engine's shared ``Gateway`` (LINK-230), which does lease, confirmation, idempotency, safety
refusal and audit with the **browser** as the actor. Nothing registered here changes a role or a setting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from galileo.connect_link.gateway import GatewayRequest
from galileo.connect_link.peers import DelegationError

from webconnect import API, PLUGIN
from webconnect.browsers import role_of

logger = logging.getLogger(__name__)

ACTING_HEADER = "X-Acting-Client"
NOT_RUNNING = "Galileo's control surface is not running"


def plain(value: Any) -> Any:
    """Something JSON can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        import dataclasses
        return plain(dataclasses.asdict(value))
    return str(getattr(value, "value", value))


class Denied(Exception):
    """The request is refused before it reaches the pipeline; carries the HTTP-style status and a reason."""

    def __init__(self, status: int, reason: str, client: bool = False) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason
        self.client = client                      # the browser itself is refused (revoked, expired, unknown)


class Delegate:
    def __init__(self, peers, surface: Callable[[], Any], on_signout: Callable[[str], None] | None = None) -> None:
        self._peers, self._surface = peers, surface
        self._on_signout = on_signout

    # -- registration ------------------------------------------------------------------------
    def register(self, routes) -> None:
        """Connect Link checks the service credential holds the route's permission before calling it."""
        add = routes.register
        add(PLUGIN, "POST", f"{API}/signin", "view", self.signin)
        add(PLUGIN, "POST", f"{API}/signout", "view", self.signout)
        add(PLUGIN, "GET", f"{API}/piers", "view", self.piers)
        add(PLUGIN, "GET", f"{API}/catalogue", "view", self.catalogue)
        add(PLUGIN, "GET", f"{API}/state", "view", self.state)
        add(PLUGIN, "POST", f"{API}/ops", "view", self.ops)             # a Viewer reaches it, is refused and audited
        add(PLUGIN, "POST", f"{API}/lease/acquire", "view", self.lease_acquire)
        add(PLUGIN, "POST", f"{API}/lease/take", "view", self.lease_take)
        add(PLUGIN, "POST", f"{API}/lease/release", "view", self.lease_release)

    # -- who is asking -----------------------------------------------------------------------
    def _client(self, carrier, request):
        name = request.headers.get(ACTING_HEADER, "").strip()
        if not name:
            raise Denied(400, "the request does not name the browser it acts for")
        try:
            return self._peers.resolve_client(carrier, name)
        except DelegationError as exc:
            raise Denied(403, str(exc), client=True) from exc

    def _service(self):
        svc = self._surface()
        if svc is None:
            raise Denied(503, NOT_RUNNING)
        return svc

    @staticmethod
    def _holder(client) -> str:
        return f"{PLUGIN}:{client.peer_id}"

    @staticmethod
    async def _body(request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise Denied(400, "the body must be a JSON object")
        return body

    @staticmethod
    def _error(exc: Denied) -> dict[str, Any]:
        out = {"status": "unavailable" if exc.status == 503 else "refused", "reason": exc.reason, "http": exc.status}
        if exc.client:
            out["client_refused"] = True          # the web half ends that browser's session
        return out

    # -- routes -------------------------------------------------------------------------------
    async def signin(self, carrier, request) -> dict[str, Any]:
        """The web half presents a browser's device credential (its token); this says who it is.

        The one route that names no acting client: it is how the web half learns the name."""
        try:
            body = await self._body(request)
            token = str(body.get("device_credential", ""))
            browser = self._peers.authenticate(token) if token else None
            if browser is None or browser.plugin != PLUGIN or browser.is_service or not carrier.is_service:
                raise Denied(403, "that browser is not approved")
        except Denied as exc:
            return self._error(exc)
        return {"status": "ok", "client": browser.peer_id, "name": browser.name,
                "role": role_of(browser.permissions), "expires_at": browser.expires_at}

    async def signout(self, carrier, request) -> dict[str, Any]:
        """Release everything the browser holds (WCON-760)."""
        try:
            client = self._client(carrier, request)
            released = self._service().leases.on_disconnect(self._holder(client), "signed out")
        except Denied as exc:
            return self._error(exc)
        if self._on_signout is not None:
            self._on_signout(client.peer_id)
        return {"status": "ok", "released": list(released)}

    async def piers(self, carrier, request) -> dict[str, Any]:
        try:
            self._client(carrier, request)
            return {"status": "ok", "piers": plain(self._service().piers())}
        except Denied as exc:
            return self._error(exc)

    async def catalogue(self, carrier, request) -> dict[str, Any]:
        try:
            self._client(carrier, request)
            cat = self._service().registry.catalogue(request.query.get("pier", ""))
            return {"status": "ok", "catalogue": plain(cat)}
        except Denied as exc:
            return self._error(exc)

    async def state(self, carrier, request) -> dict[str, Any]:
        try:
            self._client(carrier, request)
            seq, values = self._service().state.snapshot(request.query.get("pier", ""))
            return {"status": "ok", "seq": seq, "values": plain(values)}
        except Denied as exc:
            return self._error(exc)

    async def ops(self, carrier, request) -> dict[str, Any]:
        try:
            client = self._client(carrier, request)
            body = await self._body(request)
            svc = self._service()
        except Denied as exc:
            return self._error(exc)
        req = GatewayRequest(
            client_id=client.peer_id, plugin=PLUGIN, carrier=carrier.peer_id, kind="peer",
            name=str(body.get("name", "")), pier=str(body.get("pier", "")),
            args=body.get("args") if isinstance(body.get("args"), dict) else {},
            permissions=client.permissions,                                  # the browser's, never the service's
            idempotency_key=str(body.get("idempotency_key", "")),
            confirm_token=str(body.get("confirm_token", "")),
            source=str(request.headers.get("X-Client-Address", "")))
        return plain((await svc.gateway.handle(req)).as_dict())

    async def _lease(self, carrier, request, action: str) -> dict[str, Any]:
        try:
            client = self._client(carrier, request)
            body = await self._body(request)
            svc = self._service()
            if "control" not in client.permissions:
                raise Denied(403, "only an Operator can hold a lease")
        except Denied as exc:
            return self._error(exc)
        pier, holder = str(body.get("pier", "")), self._holder(client)
        if action == "release":
            return {"status": "ok", "released": svc.leases.release(pier, holder), "holder": svc.leases.holder(pier)}
        result = (svc.leases.acquire(pier, holder) if action == "acquire"
                  else svc.leases.take(pier, holder, bool(body.get("confirmed"))))
        return {"status": "ok" if result.granted else "refused", "granted": result.granted,
                "holder": result.holder, "can_take_over": result.can_take_over, "reason": result.reason}

    async def lease_acquire(self, carrier, request):
        return await self._lease(carrier, request, "acquire")

    async def lease_take(self, carrier, request):
        return await self._lease(carrier, request, "take")

    async def lease_release(self, carrier, request):
        return await self._lease(carrier, request, "release")
