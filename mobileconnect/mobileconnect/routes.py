# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The ``/mobileconnect/v1`` routes: the contract in Galileo-MobileConnect's ``api/openapi.yaml``.

Connect Link has authenticated the app and checked the route's permission before any of this runs. Every
operation then goes through the engine's shared ``Gateway`` with the app as the actor. Nothing here changes a role,
a setting or a limit. Paths are flat with query parameters because Connect Link's route table matches paths exactly.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any

from aiohttp import web
from galileo.connect_link.gateway import GatewayRequest

from mobileconnect import API, PLUGIN
from mobileconnect.clients import holder_of
from mobileconnect.state import now_text, plain

NOT_RUNNING = ("Galileo's control surface is not running. On the Galileo computer turn on Options > Equipment > "
               "\"Hold devices in the device service\" and restart Galileo.")
PLUGIN_API_VERSION = "1.0"
TAKEOVER_TTL_S = 60.0
HTTP_STATUS = {"invalid": 400, "refused": 409, "unavailable": 409, "busy": 429, "unreachable": 409}


class Routes:
    def __init__(self, peers, surface: Callable[[], Any], node_name: Callable[[], str], pump,
                 publish: Callable[[str, dict[str, Any]], Any], clock: Callable[[], float] = time.time,
                 sky: Any = None) -> None:
        self._peers, self._surface, self._node_name, self._pump = peers, surface, node_name, pump
        self.sky = sky
        self._publish, self._clock = publish, clock
        self._takeovers: dict[str, tuple[str, str, float]] = {}       # token -> (peer, pier, expiry)

    def register(self, routes) -> None:
        add = routes.register
        add(PLUGIN, "GET", f"{API}/capabilities", "view", self.capabilities)
        add(PLUGIN, "GET", f"{API}/catalogue", "view", self.catalogue)
        add(PLUGIN, "GET", f"{API}/piers", "view", self.piers)
        add(PLUGIN, "GET", f"{API}/alerts", "view", self.alerts)
        add(PLUGIN, "POST", f"{API}/snapshot", "view", self.snapshot)
        add(PLUGIN, "POST", f"{API}/ops", "view", self.ops)                  # a Viewer reaches it, is refused and audited
        add(PLUGIN, "POST", f"{API}/lease", "view", self.lease_take)
        add(PLUGIN, "DELETE", f"{API}/lease", "view", self.lease_release)
        add(PLUGIN, "GET", f"{API}/preview", "view", self.preview)
        add(PLUGIN, "GET", f"{API}/tile", "view", self.tile)
        if self.sky is not None:
            self.sky.register(routes)

    # -- who holds what ------------------------------------------------------------------------
    def describe_holder(self, holder: str) -> tuple[str | None, str | None]:
        """``(holder, display name)`` as the wire has them. An app's own lease reads as its peer id, which is how
        it recognises itself; ``whoami`` gives it that id."""
        if not holder:
            return None, None
        if holder == "desktop":
            return holder, "Desktop"
        plugin, _, peer_id = holder.partition(":")
        peer = self._peers.get(peer_id) if peer_id else None
        name = peer.name if peer is not None else holder
        return (peer_id if plugin == PLUGIN else holder), name

    # -- discovery -----------------------------------------------------------------------------
    async def capabilities(self, peer, request) -> dict[str, Any]:
        svc = self._surface()
        areas: list[str] = []
        if svc is not None:
            areas = sorted({op["name"].split(".")[0] for op in svc.registry.catalogue("")["operations"]})
        sky = await self.sky.capability() if self.sky is not None else {"available": False, "reason": "not_offered"}
        return {"plugin_api": PLUGIN_API_VERSION, "areas": areas, "sky_stream": sky}

    async def catalogue(self, peer, request) -> dict[str, Any]:
        svc = self._surface()
        if svc is None:
            return {"operations": []}
        entries = svc.registry.catalogue(request.query.get("pier", ""))["operations"]
        operations = []
        for entry in entries:
            wire = {"name": entry["name"], "area": entry["name"].split(".")[0], "class": entry["class"],
                    "lease": bool(entry["needs_lease"]), "args": plain(entry["args"]),
                    "available": bool(entry["available"]), "long_running": bool(entry["long_running"])}
            if entry.get("description"):
                wire["description"] = entry["description"]
            if entry.get("reason"):
                wire["unavailable_because"] = entry["reason"]
            operations.append(wire)
        return {"operations": operations}

    async def piers(self, peer, request) -> dict[str, Any]:
        svc = self._surface()
        if svc is None:
            return {"piers": [], "reason": NOT_RUNNING}
        access = "control" if "control" in peer.permissions else "view"
        return {"piers": [
            {"id": str(p["id"]), "name": str(p.get("name") or p["id"]), "node": self._node_name(),
             **({"observatory": str(p["observatory"])} if p.get("observatory") else {}),
             "access": access, "reachable": True}
            for p in plain(svc.piers())]}

    async def alerts(self, peer, request) -> dict[str, Any]:
        return {"alerts": [{k: v for k, v in a.items() if k != "type"} for a in reversed(self._pump.alerts)]}

    async def snapshot(self, peer, request) -> dict[str, Any]:
        """Puts a full snapshot on this app's stream. Connect Link's stream has no connection hook, so the app asks
        when it has seen ``hello`` with no state, or ``resync``. On the stream, so it stays in sequence with deltas."""
        svc = self._surface()
        self._pump.wake()
        await self._pump.sample_once()
        piers: dict[str, Any] = {}
        pier_ids = [str(p["id"]) for p in plain(svc.piers())] if svc is not None else []
        for pier in pier_ids:
            values = self._pump.tracker.snapshot(pier)
            holder, name = self.describe_holder(svc.leases.holder(pier))
            if holder:
                values["lease.holder"] = holder
                values["lease.holder_name"] = name
            piers[pier] = values
        seq = self._publish(peer.peer_id, {"type": "snapshot", "as_of": now_text(), "piers": piers})
        return {"ok": seq is not None}

    # -- operations ----------------------------------------------------------------------------
    async def ops(self, peer, request):
        svc = self._surface()
        name, pier = request.query.get("name", ""), request.query.get("pier", "")
        if svc is None:
            return self._error("unavailable", NOT_RUNNING)
        try:
            body = await request.json()
        except ValueError:
            return self._error("invalid", "the body must be JSON")
        if not isinstance(body, dict):
            return self._error("invalid", "the body must be a JSON object")
        spec = svc.registry.spec(name)
        if spec is not None and spec.permission_class == "emergency" and body.get("confirmed") is not True:
            return self._error("refused", "An emergency action needs a deliberate confirmation.")
        outcome = await svc.gateway.handle(GatewayRequest(
            client_id=peer.peer_id, plugin=PLUGIN, kind="peer", name=name, pier=pier,
            args=body.get("args") if isinstance(body.get("args"), dict) else {},
            permissions=peer.permissions,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            confirm_token=request.headers.get("X-Confirm-Token", ""),
            source=request.remote or ""))
        return self._outcome(svc, peer, spec, pier, outcome)

    def _outcome(self, svc, peer, spec, pier: str, outcome):
        if outcome.status == "ok":
            answer: dict[str, Any] = {"ok": True}
            if outcome.value is not None:
                answer["result"] = plain(outcome.value)
            if outcome.correlation_id:
                answer["correlation_id"] = outcome.correlation_id
            return answer
        if outcome.confirm_token:
            # Core says "refused" with a token; the wire names it. The wording comes from the catalogue.
            text = (spec.description.split(" (")[0] if spec is not None and spec.description else outcome.reason)
            return web.json_response({"error": "confirmation_required", "reason": text,
                                      "confirm_token": outcome.confirm_token, "expires_in": TAKEOVER_TTL_S}, status=409)
        detail: dict[str, Any] = {}
        reason = outcome.reason
        if outcome.status == "invalid" and outcome.path:
            detail["path"] = outcome.path
            reason = f"{outcome.path} {outcome.reason}"
        if outcome.status == "refused" and spec is not None and spec.needs_lease:
            held = svc.leases.holder(pier)
            if not held:
                detail["kind"] = "no_lease"
            elif held != holder_of(peer.peer_id):
                who, name = self.describe_holder(held)
                detail.update(kind="lease_held", holder=who, holder_name=name, can_take=True)
                reason = f"{name} holds the lease on this Pier."
        return self._error(outcome.status, reason, detail)

    @staticmethod
    def _error(kind: str, reason: str, detail: dict[str, Any] | None = None, **extra: Any):
        body: dict[str, Any] = {"error": kind, "reason": reason, **extra}
        if detail:
            body["detail"] = detail
        return web.json_response(body, status=HTTP_STATUS.get(kind, 409))

    # -- lease ---------------------------------------------------------------------------------
    def _new_takeover(self, peer_id: str, pier: str) -> str:
        now = self._clock()
        self._takeovers = {t: v for t, v in self._takeovers.items() if v[2] > now}
        token = secrets.token_urlsafe(16)
        self._takeovers[token] = (peer_id, pier, now + TAKEOVER_TTL_S)
        return token

    def _redeem_takeover(self, token: str, peer_id: str, pier: str) -> bool:
        entry = self._takeovers.get(token)
        if entry is None or entry[0] != peer_id or entry[1] != pier or entry[2] <= self._clock():
            return False
        del self._takeovers[token]                    # single use; a wrong presenter cannot burn it
        return True

    async def lease_take(self, peer, request):
        svc = self._surface()
        pier = request.query.get("pier", "")
        if svc is None:
            return self._error("unavailable", NOT_RUNNING)
        if "control" not in peer.permissions:
            return self._error("refused", "This device is a Viewer: only an Operator can hold a lease.")
        holder = holder_of(peer.peer_id)
        token = request.headers.get("X-Confirm-Token", "")
        confirmed = bool(token) and self._redeem_takeover(token, peer.peer_id, pier)
        result = svc.leases.take(pier, holder, True) if confirmed else svc.leases.acquire(pier, holder)
        if result.granted:
            return {"pier": pier, "held_by_me": True, "holder": peer.peer_id}
        who, name = self.describe_holder(result.holder)
        return self._error(
            "refused", f"{name} holds the lease on this Pier.",
            {"kind": "lease_held", "holder": who, "holder_name": name, "can_take": bool(result.can_take_over)},
            confirm_token=self._new_takeover(peer.peer_id, pier), expires_in=TAKEOVER_TTL_S)

    async def lease_release(self, peer, request) -> dict[str, Any]:
        svc = self._surface()
        pier = request.query.get("pier", "")
        if svc is not None:
            svc.leases.release(pier, holder_of(peer.peer_id))
        return {"pier": pier, "held_by_me": False}

    # -- pictures ------------------------------------------------------------------------------
    @staticmethod
    def _params(request):
        from galileo.preview import PreviewParams

        def number(name: str, default: int, low: int, high: int) -> int:
            try:
                return max(low, min(high, int(request.query.get(name, default))))
            except ValueError:
                return default
        return PreviewParams(stretch=number("stretch", 50, 0, 100), max_size=number("w", 1024, 64, 4096),
                             quality=number("q", 85, 10, 95))

    def _previews(self):
        return getattr(self._surface(), "previews", None)

    async def preview(self, peer, request):
        from galileo.preview import PreviewError
        previews = self._previews()
        if previews is None:
            return web.json_response({"error": "Galileo is not offering pictures yet"}, status=404)
        ref = f"current:{request.query.get('pier', '')}"
        try:
            shot = await previews.render(ref, self._params(request))
            info = await previews.info(ref)
        except PreviewError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.Response(body=shot.data, content_type=shot.content_type, headers={
            "X-Frame-Width": str(info["width"]), "X-Frame-Height": str(info["height"])})

    async def tile(self, peer, request):
        from galileo.preview import PreviewError
        previews = self._previews()
        if previews is None:
            return web.json_response({"error": "Galileo is not offering pictures yet"}, status=404)
        try:
            level, x, y = (int(request.query.get(k, "0")) for k in ("z", "x", "y"))
            data = await previews.tile(f"current:{request.query.get('pier', '')}", level, x, y, self._params(request))
        except ValueError:
            return web.json_response({"error": "z, x and y must be numbers"}, status=404)
        except PreviewError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.Response(body=data, content_type="image/jpeg")


__all__ = ["NOT_RUNNING", "PLUGIN_API_VERSION", "Routes"]
