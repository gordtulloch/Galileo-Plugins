# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The operating web application (SDD §3.4-3.6, ADR-WCON-002).

A backend-for-frontend: it authenticates the browser itself, then asks the Master half on the browser's behalf. It
decides nothing about what a browser may do; the Master enforces role, lease, confirmation and safety, and this
layer only presents the typed result (WCON-580, 750). It holds no per-area code: whatever the Control Surface
catalogue offers is reachable through the one operations route.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass, field

from flask import Flask, Response, g, jsonify, make_response, render_template, request

from webconnect.web.link import LinkClient, LinkError
from webconnect.web.security import PUBLIC, SESSION, RateLimiter, Security
from webconnect.web.session import CookiePolicy, SessionStore, csrf_ok

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z0-9 ._'-]{1,60}$")
_PIER = re.compile(r"^[A-Za-z0-9:_.-]{1,80}$")
_OP = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MAX_STREAMS = 4                                  # event streams one sign-in may hold open (a few tabs)
EVENT_WAIT_S = 8                                 # how long the Master may hold one poll
_PLATFORMS = ("Windows", "Android", "iPhone", "iPad", "Macintosh", "Linux")


@dataclass
class WebSettings:
    allowed_hosts: tuple[str, ...] = ("localhost",)
    idle_s: int = 1800
    absolute_s: int = 28800
    cookies: CookiePolicy = field(default_factory=CookiePolicy)
    outreach_subnets: tuple[str, ...] = ()


def _platform() -> str:
    ua = request.headers.get("User-Agent", "")
    return next((p for p in _PLATFORMS if p in ua), "Browser")


def create_app(link: LinkClient, settings: WebSettings | None = None) -> Flask:
    cfg = settings or WebSettings()
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.update(MAX_CONTENT_LENGTH=64 * 1024, JSON_SORT_KEYS=False)
    sec = Security(app, cfg.allowed_hosts, cfg.outreach_subnets)
    sessions = SessionStore(cfg.idle_s, cfg.absolute_s)
    pair_limit, signin_limit = RateLimiter(5 / 60, 5), RateLimiter(10 / 60, 10)
    pairing: dict[str, str] = {}                     # pairing-cookie value -> Master request id
    app.extensions["wc_sessions"], app.extensions["wc_security"] = sessions, sec

    @app.before_request
    def _load_session():
        g.wc_session = sessions.get(request.cookies.get(cfg.cookies.session, ""))
        s = g.wc_session
        if (s is not None and request.method not in ("GET", "HEAD", "OPTIONS")
                and not csrf_ok(s, request.headers.get("X-CSRF-Token", ""))):
            return jsonify({"status": "refused", "reason": "bad CSRF token"}), 403
        return None

    @app.errorhandler(LinkError)
    def _master_down(exc: LinkError):
        """Galileo is not running, or is not the Galileo this was paired with (WCON-1490, 930)."""
        body = {"status": "unavailable", "reason": "Galileo is not reachable. It may not be running."}
        if request.path.startswith("/api/") or request.is_json or request.method != "GET":
            return jsonify(body), 503
        return render_template("down.html", reason=str(exc)), 503

    def _passthrough(out: dict, session=None):
        """Relay the Master's typed result; end the session if it is the browser itself that was refused."""
        if out.get("client_refused") and session is not None:
            sessions.end(session.sid)
            resp = jsonify({"status": "refused", "reason": out.get("reason", "")})
            resp.set_cookie(cfg.cookies.session, "", **{**cfg.cookies.attrs(), "max_age": 0})
            return resp, 401
        out.pop("http", None)
        return jsonify(out), 200

    # -- pages ------------------------------------------------------------------------------------
    @sec.route("/", auth=PUBLIC)
    def index():
        return render_template("index.html")

    @sec.route("/healthz", auth=PUBLIC)
    def healthz():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return Response(status=404)
        return jsonify({"status": "ok"})

    # -- pairing (WCON-100) ---------------------------------------------------------------------------
    @sec.route("/pair/request", auth=PUBLIC, methods=("POST",))
    def pair_request():
        if not pair_limit.allow(sec.source()):
            return jsonify({"status": "busy", "reason": "too many pairing attempts; wait a minute"}), 429
        body = request.get_json(silent=True) or {}
        name = str(body.get("name", "")).strip()
        if not _NAME.match(name):
            return jsonify({"status": "invalid", "reason": "give this device a short name"}), 400
        out = link.pair_request(name, _platform())
        if out.get("http") != 202:
            return jsonify({"status": "refused", "reason": "Galileo did not accept the request"}), 502
        handle = out["request_id"]
        key = secrets.token_urlsafe(24)
        pairing[key] = handle
        resp = make_response(jsonify({"status": "pending", "code": out["code"], "expires_in": out.get("expires_in")}))
        resp.set_cookie(cfg.cookies.pairing, key, **cfg.cookies.attrs(max_age=int(out.get("expires_in") or 300)))
        return resp

    @sec.route("/pair/status", auth=PUBLIC)
    def pair_status():
        key = request.cookies.get(cfg.cookies.pairing, "")
        handle = pairing.get(key)
        if handle is None:
            return jsonify({"status": "none"})
        out = link.pair_poll(handle)
        state = out.get("state", "expired")
        resp = make_response(jsonify({"status": state}))
        if state == "approved" and out.get("token"):
            pairing.pop(key, None)
            resp.set_cookie(cfg.cookies.device, out["token"], **cfg.cookies.attrs(max_age=365 * 86400))
            resp.delete_cookie(cfg.cookies.pairing, path=cfg.cookies.path)
        elif state in ("rejected", "expired", "approved"):
            pairing.pop(key, None)
            resp.delete_cookie(cfg.cookies.pairing, path=cfg.cookies.path)
        return resp

    # -- sign-in (WCON-1040) ----------------------------------------------------------------------------
    @sec.route("/signin", auth=PUBLIC, methods=("POST",))
    def signin():
        if not signin_limit.allow(sec.source()):
            return jsonify({"status": "busy", "reason": "too many sign-in attempts"}), 429
        token = request.cookies.get(cfg.cookies.device, "")
        if not token:
            return jsonify({"status": "refused", "reason": "this browser has not been approved yet"}), 401
        out = link.call("POST", "/signin", body={"device_credential": token})
        if out.get("status") != "ok":
            resp = make_response(jsonify({"status": "refused", "reason": "this browser is no longer approved"}), 401)
            resp.delete_cookie(cfg.cookies.device, path=cfg.cookies.path)
            return resp
        s = sessions.create(out["client"], out.get("name", ""), out.get("role", "viewer"))
        resp = make_response(jsonify({"status": "ok", "name": s.name, "role": s.role, "csrf": s.csrf}))
        resp.set_cookie(cfg.cookies.session, s.sid, **cfg.cookies.attrs())
        return resp

    @sec.route("/signout", auth=SESSION, methods=("POST",))
    def signout():
        s = g.wc_session
        link.call("POST", "/signout", client=s.client)
        sessions.end(s.sid)
        resp = jsonify({"status": "ok"})
        resp.set_cookie(cfg.cookies.session, "", **{**cfg.cookies.attrs(), "max_age": 0})
        return resp

    # -- the browser-facing API (WCON-400..560 through the catalogue) -------------------------------------
    @sec.route("/api/v1/me", auth=SESSION)
    def me():
        s = g.wc_session
        return jsonify({"status": "ok", "name": s.name, "role": s.role, "csrf": s.csrf})

    @sec.route("/api/v1/piers", auth=SESSION)
    def piers():
        s = g.wc_session
        return _passthrough(link.call("GET", "/piers", client=s.client), s)

    def _pier(value: str) -> str | None:
        return value if _PIER.match(value) else None

    @sec.route("/api/v1/pier/<pier>/catalogue", auth=SESSION)
    def catalogue(pier: str):
        s = g.wc_session
        if _pier(pier) is None:
            return jsonify({"status": "invalid", "reason": "unknown Pier"}), 400
        return _passthrough(link.call("GET", "/catalogue", client=s.client, query={"pier": pier}), s)

    @sec.route("/api/v1/pier/<pier>/state", auth=SESSION)
    def state(pier: str):
        s = g.wc_session
        if _pier(pier) is None:
            return jsonify({"status": "invalid", "reason": "unknown Pier"}), 400
        return _passthrough(link.call("GET", "/state", client=s.client, query={"pier": pier}), s)

    streams: dict[str, int] = {}                     # session id -> open event streams

    @sec.route("/api/v1/events", auth=SESSION)
    def events():
        """State, lease, device and operation events as Server-Sent Events (WCON-300, 310, 350).

        The browser gets a ``hello`` first and then only changes: it fetches state itself, so a reconnect never
        replays a stale picture. A ``resync`` event means entries were missed and state must be fetched again."""
        s = g.wc_session
        if streams.get(s.sid, 0) >= MAX_STREAMS:
            return jsonify({"status": "refused", "reason": "too many open event streams for this sign-in"}), 429
        last = request.headers.get("Last-Event-ID", "")
        cursor = last if last.isdigit() else "latest"
        streams[s.sid] = streams.get(s.sid, 0) + 1
        return Response(_stream(s, cursor), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def _frame(event: str, data: dict, event_id: int | None = None) -> str:
        head = f"id: {event_id}\n" if event_id is not None else ""
        return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"

    def _stream(s, cursor: str):
        try:
            yield _frame("hello", {"name": s.name, "role": s.role})
            while sessions.alive(s.sid):        # signing out, expiry or revocation end the stream
                try:
                    out = link.call("GET", "/events", client=s.client, timeout=EVENT_WAIT_S + 6,
                                    query={"after": cursor, "wait": str(EVENT_WAIT_S)})
                except LinkError:
                    yield _frame("down", {"reason": "Galileo is not reachable"})
                    return
                if out.get("client_refused"):
                    sessions.end(s.sid)
                    yield _frame("signed-out", {"reason": out.get("reason", "")})
                    return
                if out.get("status") != "ok":
                    yield _frame("down", {"reason": out.get("reason", "")})
                    return
                if out.get("gap"):
                    yield _frame("resync", {})
                for entry in out.get("entries", ()):
                    yield _frame(entry.get("type", "event"), entry, entry.get("id"))
                cursor = str(out.get("last", cursor))
                if out.get("closed"):
                    yield _frame("down", {"reason": "Galileo is shutting down"})
                    return
                if not out.get("entries"):
                    yield ": keepalive\n\n"
        finally:
            streams[s.sid] = max(0, streams.get(s.sid, 1) - 1)

    @sec.route("/api/v1/pier/<pier>/ops/<name>", auth=SESSION, methods=("POST",))
    def operation(pier: str, name: str):
        """Every operation, one route: the Master validates, leases, confirms, dedupes and audits (WCON-580)."""
        s = g.wc_session
        if _pier(pier) is None or not _OP.match(name):
            return jsonify({"status": "invalid", "reason": "unknown operation"}), 400
        body = request.get_json(silent=True)
        if body is not None and not isinstance(body, dict):
            return jsonify({"status": "invalid", "reason": "the body must be a JSON object"}), 400
        body = body or {}
        out = link.call("POST", "/ops", client=s.client, source=sec.source(), body={
            "pier": pier, "name": name, "args": body.get("args") if isinstance(body.get("args"), dict) else {},
            "idempotency_key": request.headers.get("Idempotency-Key", ""),
            "confirm_token": request.headers.get("X-Confirm-Token", "")})
        return _passthrough(out, s)

    @sec.route("/api/v1/pier/<pier>/lease/<action>", auth=SESSION, methods=("POST",))
    def lease(pier: str, action: str):
        s = g.wc_session
        if _pier(pier) is None or action not in ("acquire", "take", "release"):
            return jsonify({"status": "invalid", "reason": "unknown lease action"}), 400
        body = request.get_json(silent=True) or {}
        out = link.call("POST", f"/lease/{action}", client=s.client,
                        body={"pier": pier, "confirmed": bool(body.get("confirmed"))})
        return _passthrough(out, s)

    unrouted = sec.audit_routes()
    if unrouted:                                      # WCON-1050: refuse to serve a route with no authentication class
        raise RuntimeError(f"routes without an authentication class: {unrouted}")
    return app
