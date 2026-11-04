# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Headers, host checks, rate limits and the route audit (SDD §3.5; WCON-770, 800, 1000, 1020, 1030, 1050, 1060)."""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, jsonify, request

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SESSION, PUBLIC, SERVICE = "session", "public", "service"
OPERATOR_ROLE = "operator"


@dataclass(frozen=True)
class RouteInfo:
    rule: str
    methods: frozenset[str]
    auth: str
    role: str


class RateLimiter:
    """Token buckets keyed by source (WCON-1030, 770). In memory; a restart forgets, which is acceptable."""

    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate, self._burst, self._clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self._burst), now))
            tokens = min(float(self._burst), tokens + (now - last) * self._rate)
            ok = tokens >= cost
            self._buckets[key] = (tokens - cost if ok else tokens, now)
            if len(self._buckets) > 4096:                      # a flood of sources cannot grow this without bound
                for k in sorted(self._buckets, key=lambda k: self._buckets[k][1])[:1024]:
                    self._buckets.pop(k, None)
            return ok


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:].split("]")[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _deny(reason: str) -> None:
    abort(Response(json.dumps({"status": "refused", "reason": reason}), status=403, mimetype="application/json"))


class Security:
    """Wraps one Flask app. ``route`` is the only way to add a route, and it forces an authentication class."""

    def __init__(self, app: Flask, allowed_hosts: Iterable[str], outreach_subnets: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self._outreach = [ipaddress.ip_network(n, strict=False) for n in outreach_subnets]
        self.routes: dict[str, RouteInfo] = {}
        app.before_request(self._before)
        app.after_request(self._after)

    # -- route registry (WCON-1050) --------------------------------------------------------------
    def route(self, rule: str, *, auth: str, methods: Iterable[str] = ("GET",), role: str = "viewer") -> Callable:
        if auth not in (SESSION, PUBLIC, SERVICE):
            raise ValueError(f"{rule}: an authentication class is required")
        methods = frozenset(m.upper() for m in methods)

        def deco(fn: Callable) -> Callable:
            @wraps(fn)
            def guarded(*a: Any, **kw: Any):
                if auth == SESSION:
                    sess = g.get("wc_session")
                    if sess is None:
                        return jsonify({"status": "refused", "reason": "sign in first"}), 401
                    if role == OPERATOR_ROLE and sess.role != OPERATOR_ROLE:
                        return jsonify({"status": "refused", "reason": "this needs an Operator"}), 403
                return fn(*a, **kw)
            endpoint = f"{fn.__name__}:{rule}"
            self.app.add_url_rule(rule, endpoint=endpoint, view_func=guarded, methods=sorted(methods))
            self.routes[endpoint] = RouteInfo(rule, methods, auth, role)
            return guarded
        return deco

    def audit_routes(self) -> list[str]:
        """Rules in the URL map that did not come through ``route`` (and so have no authentication class)."""
        known = {i.rule for i in self.routes.values()}
        return sorted(r.rule for r in self.app.url_map.iter_rules() if r.rule not in known and r.endpoint != "static")

    # -- request checks ---------------------------------------------------------------------------
    def source(self) -> str:
        return request.remote_addr or ""

    def _before(self):
        host = _host_only(request.host or "")
        if host not in self.allowed_hosts:                                    # DNS-rebinding defence (WCON-1060)
            abort(Response("Unknown host.", status=421, mimetype="text/plain"))
        src = self.source()
        if self._outreach and src:
            try:
                ip = ipaddress.ip_address(src)
            except ValueError:
                abort(403)
            if any(ip in net for net in self._outreach):                      # outreach visitors never reach this app
                abort(403)
        if request.method not in SAFE_METHODS:
            self._same_origin()

    def _same_origin(self) -> None:
        """A state-changing request must come from this application's own page (WCON-1000)."""
        site = request.headers.get("Sec-Fetch-Site")
        origin = request.headers.get("Origin")
        if site is None and origin is None:
            _deny("missing Origin")
        if site is not None and site != "same-origin":
            _deny("cross-site request")
        if origin is not None and _host_only(urlsplit(origin).netloc) not in self.allowed_hosts:
            _deny("unexpected Origin")

    def _after(self, resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.path.startswith("/api/") or resp.mimetype.startswith("image/") or request.path in ("/signin", "/pair"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
