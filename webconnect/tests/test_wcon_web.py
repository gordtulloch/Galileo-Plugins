# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The web half driving the real Master half through an in-process bridge (WCON-100, 120, 170, 180, 740, 770,
800, 830, 1000, 1010, 1020, 1030, 1050, 1060, 1490). The bridge calls the routes the Master half registered
with Connect Link's route registry, so the contract between the halves is exercised, not assumed."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from galileo.connect_link.routes import RouteRegistry

from webconnect import API, PLUGIN
from webconnect.web.app import WebSettings, create_app
from webconnect.web.link import ACTING, LinkError

from .test_wcon_delegate import PIER, World

HTTPS = "https://localhost"


class Bridge:
    """A ``LinkClient`` whose far end is the Master half in the same process."""

    def __init__(self, world: World):
        self.world = world
        self.routes = RouteRegistry()
        world.delegate.register(self.routes)
        self.down = False
        self.calls = []

    def _guard(self):
        if self.down:
            raise LinkError("Galileo is not running")

    def pair_request(self, name, platform):
        self._guard()
        r = self.world.pairing.request(PLUGIN, name, "browser", platform, "10.0.0.5")
        return {"http": 202, "request_id": r.request_id, "code": r.code, "expires_in": 300}

    def pair_poll(self, request_id):
        self._guard()
        state, token = self.world.pairing.poll(request_id)
        return {"http": 200, "state": state, **({"token": token} if token else {})}

    def call(self, method, route, *, client="", body=None, query=None, source="", timeout=None):
        self._guard()
        self.calls.append((method, route, client))
        found = self.routes.find(method, API + route)
        assert found is not None, f"no Master route {method} {route}"

        class Request:
            def __init__(self_):
                self_.headers = {ACTING: client} if client else {}
                self_.query = query or {}

            async def json(self_):
                if body is None:
                    raise ValueError
                return body
        return asyncio.run(found.handler(self.world.carrier, Request()))

    def call_raw(self, route, *, client, query=None, source="", timeout=None):
        """A GET whose answer may be a picture, through the same registry the Master half registered with."""
        self._guard()
        self.calls.append(("GET", route, client))
        found = self.routes.find("GET", API + route)
        assert found is not None, f"no Master route GET {route}"

        class Request:
            headers = {ACTING: client}
            query = {}

        Request.query = dict(query or {})
        result = asyncio.run(found.handler(self.world.carrier, Request()))
        if isinstance(result, dict):
            return result.get("http", 200), "application/json", {}, json.dumps(result).encode()
        return result.status, result.content_type, dict(result.headers), result.body

    def reachable(self):
        return not self.down


class Browser:
    """A Flask test client that behaves like a browser page: same origin, CSRF header after sign-in."""

    def __init__(self, app):
        self.c = app.test_client()
        self.csrf = ""

    def _headers(self, extra=None):
        h = {"Origin": HTTPS, "Sec-Fetch-Site": "same-origin"}
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return {**h, **(extra or {})}

    def get(self, path, **kw):
        return self.c.get(path, base_url=HTTPS, **kw)

    def post(self, path, json=None, headers=None):
        return self.c.post(path, json=json, base_url=HTTPS, headers=self._headers(headers))


@pytest.fixture
def stack(link_db):
    world = World()
    bridge = Bridge(world)
    app = create_app(bridge, WebSettings(allowed_hosts=("localhost",)))
    return world, bridge, app


def pair_and_sign_in(world, browser, name="Tablet", role="viewer"):
    assert browser.post("/pair/request", {"name": name}).get_json()["code"]
    (pending,) = world.browsers.pending()
    peer = world.browsers.approve(pending["request_id"])
    if role == "operator":
        world.browsers.promote(peer)
    assert browser.get("/pair/status").get_json()["status"] == "approved"
    out = browser.post("/signin").get_json()
    assert out["status"] == "ok"
    browser.csrf = out["csrf"]
    return peer


@pytest.mark.requirement("TC-WCON-100")
@pytest.mark.priority("MVP")
def test_tc_wcon_100_a_browser_pairs_with_a_code_and_signs_in(stack):
    """WCON-100: ask, compare the code with the Master, get approved, sign in; the cookie holds the credential."""
    world, _, app = stack
    b = Browser(app)
    out = b.post("/pair/request", {"name": "Tablet"}).get_json()
    assert out["status"] == "pending" and out["code"] == world.browsers.pending()[0]["code"]
    assert b.get("/pair/status").get_json()["status"] == "pending"
    world.browsers.approve(world.browsers.pending()[0]["request_id"])
    assert b.get("/pair/status").get_json()["status"] == "approved"
    assert b.c.get_cookie("__Host-wc_device") is not None
    signed = b.post("/signin").get_json()
    assert signed["status"] == "ok" and signed["role"] == "viewer" and signed["csrf"]
    assert b.c.get_cookie("__Host-wc_session") is not None


@pytest.mark.requirement("TC-WCON-1010")
@pytest.mark.priority("MVP")
def test_tc_wcon_1010_cookies_are_host_bound_strict_and_httponly(stack):
    """WCON-1010: __Host- names, Secure, HttpOnly, SameSite=Strict, Path=/ and no Domain."""
    world, _, app = stack
    b = Browser(app)
    b.post("/pair/request", {"name": "Tablet"})
    raw = [h for h in b.post("/pair/request", {"name": "Tablet"}).headers.getlist("Set-Cookie")]
    world.browsers.approve(world.browsers.pending()[0]["request_id"])
    raw += b.get("/pair/status").headers.getlist("Set-Cookie")
    raw += b.post("/signin").headers.getlist("Set-Cookie")
    assert raw
    for cookie in raw:
        assert cookie.startswith("__Host-wc_")
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
        assert "Domain" not in cookie


@pytest.mark.requirement("TC-WCON-1000")
@pytest.mark.priority("MVP")
def test_tc_wcon_1000_state_changes_need_csrf_and_a_same_origin_request(stack):
    """WCON-1000: no token, a wrong token, a foreign Origin or a cross-site fetch is refused before the Master."""
    world, bridge, app = stack
    b = Browser(app)
    pair_and_sign_in(world, b, role="operator")
    path = f"/api/v1/pier/{PIER}/lease/acquire"
    before = len(bridge.calls)
    assert b.c.post(path, base_url=HTTPS, headers={"Origin": HTTPS}).status_code == 403                  # no CSRF token
    assert b.post(path, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert b.post(path, headers={"Origin": "https://evil.example"}).status_code == 403
    assert b.post(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert b.c.post(path, base_url=HTTPS, headers={"X-CSRF-Token": b.csrf}).status_code == 403          # no Origin at all
    assert len(bridge.calls) == before
    assert b.post(path).get_json()["granted"] is True


@pytest.mark.requirement("TC-WCON-1020")
@pytest.mark.priority("MVP")
def test_tc_wcon_1020_security_headers_on_every_response(stack):
    """WCON-1020: a strict CSP, nosniff, no referrer, no framing, and no-store on state."""
    world, _, app = stack
    b = Browser(app)
    pair_and_sign_in(world, b)
    for resp in (b.get("/"), b.get("/api/v1/me"), b.get("/static/app.js"), b.get("/nope")):
        h = resp.headers
        assert "default-src 'none'" in h["Content-Security-Policy"] and "script-src 'self'" in h["Content-Security-Policy"]
        assert h["X-Content-Type-Options"] == "nosniff" and h["Referrer-Policy"] == "no-referrer"
        assert h["X-Frame-Options"] == "DENY"
    assert b.get("/api/v1/me").headers["Cache-Control"] == "no-store"


@pytest.mark.requirement("TC-WCON-800")
@pytest.mark.priority("MVP")
def test_tc_wcon_800_pages_contain_no_inline_script_handler_or_style(stack):
    """WCON-800: the CSP forbids inline code, so no template may contain any."""
    _, _, app = stack
    root = Path(app.template_folder)
    for page in root.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), page.name
        assert not re.search(r"\son[a-z]+\s*=", text), page.name
        assert "style=" not in text and "<style" not in text, page.name
        assert "http://" not in text and not re.search(r'(src|href)="https?://', text), page.name


@pytest.mark.requirement("TC-WCON-1050")
@pytest.mark.priority("MVP")
def test_tc_wcon_1050_every_route_has_an_authentication_class_and_only_listed_ones_are_public(stack):
    """WCON-1050: the URL map has no route that bypassed the registry, and the public list is exactly this."""
    _, _, app = stack
    sec = app.extensions["wc_security"]
    assert sec.audit_routes() == []
    public = sorted(i.rule for i in sec.routes.values() if i.auth == "public")
    assert public == ["/", "/healthz", "/pair/request", "/pair/status", "/signin"]
    from webconnect.web.security import Security
    with pytest.raises(ValueError):
        Security(app, ["x"]).route("/x", auth="")                                  # a class is mandatory


@pytest.mark.requirement("TC-WCON-1050")
@pytest.mark.priority("MVP")
def test_tc_wcon_1050_session_routes_refuse_an_anonymous_browser(stack):
    """WCON-1050: every session route answers 401 without a session, before the Master hears anything."""
    _, bridge, app = stack
    b = Browser(app)
    sec = app.extensions["wc_security"]
    for info in sec.routes.values():
        if info.auth != "session":
            continue
        rule = re.sub(r"<[^>]+>", "x", info.rule)
        resp = b.get(rule) if "GET" in info.methods else b.post(rule)
        assert resp.status_code == 401, rule
    assert bridge.calls == []


@pytest.mark.requirement("TC-WCON-1060")
@pytest.mark.priority("MVP")
def test_tc_wcon_1060_an_unexpected_host_is_refused(stack):
    """WCON-1060: DNS-rebinding defence — only the configured names are served."""
    _, _, app = stack
    c = app.test_client()
    assert c.get("/", base_url="https://evil.example").status_code == 421
    assert c.get("/", base_url="https://localhost:8443").status_code == 200


@pytest.mark.requirement("TC-WCON-120")
@pytest.mark.priority("MVP")
def test_tc_wcon_120_a_viewer_is_refused_by_the_master_through_the_page(stack):
    """WCON-120: hiding controls is a convenience; the Master refuses a Viewer's operation with a reason."""
    world, _, app = stack
    b = Browser(app)
    pair_and_sign_in(world, b)
    out = b.post(f"/api/v1/pier/{PIER}/ops/mount.slew", {"args": {"ra": 1.0}}).get_json()
    assert out["status"] == "refused" and out["reason"] and world.moves == []
    assert b.post(f"/api/v1/pier/{PIER}/lease/acquire").get_json()["status"] == "refused"
    assert b.get(f"/api/v1/pier/{PIER}/catalogue").get_json()["status"] == "ok"


@pytest.mark.requirement("TC-WCON-180")
@pytest.mark.priority("MVP")
def test_tc_wcon_180_740_an_operator_slews_with_a_lease_a_confirmation_and_a_retry_key(stack):
    """WCON-180/740: lease, two-step confirmation, idempotent retry — all carried by headers the page sets."""
    world, _, app = stack
    b = Browser(app)
    pair_and_sign_in(world, b, role="operator")
    assert b.post(f"/api/v1/pier/{PIER}/lease/acquire").get_json()["granted"]
    ops = f"/api/v1/pier/{PIER}/ops/"
    assert b.post(ops + "mount.slew", {"args": {"ra": 5.0}}, {"Idempotency-Key": "a"}).get_json()["status"] == "ok"
    assert b.post(ops + "mount.slew", {"args": {"ra": 5.0}}, {"Idempotency-Key": "a"}).get_json()["status"] == "ok"
    assert world.moves == [5.0]
    first = b.post(ops + "mount.erase").get_json()
    assert first["confirm_token"]
    assert b.post(ops + "mount.erase", None, {"X-Confirm-Token": first["confirm_token"]}).get_json()["status"] == "ok"
    assert b.post(ops + "Bad Name").status_code in (400, 404)
    assert b.post("/api/v1/pier/bad pier/ops/mount.slew").status_code in (400, 404)


@pytest.mark.requirement("TC-WCON-170")
@pytest.mark.priority("MVP")
def test_tc_wcon_170_revoking_a_browser_ends_its_session_on_the_next_request(stack):
    """WCON-170: after revocation the next request is 401, the session is gone and the cookie cleared."""
    world, _, app = stack
    b = Browser(app)
    peer = pair_and_sign_in(world, b, role="operator")
    assert b.get("/api/v1/piers").get_json()["status"] == "ok"
    world.browsers.revoke(peer)
    resp = b.get("/api/v1/piers")
    assert resp.status_code == 401 and len(app.extensions["wc_sessions"]) == 0
    assert b.post("/signin").status_code == 401                                      # and it cannot sign in again


@pytest.mark.requirement("TC-WCON-1030")
@pytest.mark.priority("P2")
def test_tc_wcon_1030_pairing_and_sign_in_are_rate_limited(stack):
    """WCON-1030: repeated pairing attempts from one source are slowed down."""
    _, _, app = stack
    b = Browser(app)
    codes = [b.post("/pair/request", {"name": "T"}).status_code for _ in range(8)]
    assert codes[0] == 200 and 429 in codes


@pytest.mark.requirement("TC-WCON-1490")
@pytest.mark.priority("MVP")
def test_tc_wcon_1490_master_down_is_a_page_not_a_stack_trace(stack):
    """WCON-1490: with no Master the browser gets a plain page (HTML) or a typed 503 (API), never a 500."""
    world, bridge, app = stack
    b = Browser(app)
    pair_and_sign_in(world, b)
    bridge.down = True
    api = b.get("/api/v1/piers")
    assert api.status_code == 503 and api.get_json()["status"] == "unavailable"
    page = b.post("/pair/request", {"name": "Tablet"})
    assert page.status_code == 503


@pytest.mark.requirement("TC-WCON-830")
@pytest.mark.priority("MVP")
def test_tc_wcon_830_sessions_expire_when_idle(link_db):
    """WCON-830/1040: an idle session is gone; the device credential stays, so signing in again is one click."""
    from webconnect.web.session import SessionStore
    now = [0.0]
    store = SessionStore(idle_s=60, absolute_s=3600, clock=lambda: now[0])
    s = store.create("peer", "Tablet", "viewer")
    now[0] = 30
    assert store.get(s.sid) is s
    now[0] = 100
    assert store.get(s.sid) is None
    s2 = store.create("peer", "Tablet", "viewer")
    assert s2.sid != s.sid and s2.csrf != s.csrf
    now[0] = 100 + 3601
    assert store.get(s2.sid) is None


@pytest.mark.requirement("TC-WCON-800")
@pytest.mark.priority("MVP")
def test_tc_wcon_800_the_pier_screen_is_served_and_builds_nothing_from_strings(stack):
    """WCON-800: the Pier screen ships as a same-origin module, and no script writes markup from a string (no
    innerHTML, eval or inline handler), so a name or value from the Master can never become code."""
    _, _, app = stack
    b = Browser(app)
    page = b.get("/").get_data(as_text=True)
    assert 'id="pier-view"' in page
    served = b.get("/static/pier.js")
    assert served.status_code == 200 and "javascript" in served.mimetype
    for name in ("app.js", "pier.js"):
        text = (Path(app.static_folder) / name).read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function", text), name
        assert not re.search(r"https?://", text), name


@pytest.mark.requirement("TC-WCON-1040")
@pytest.mark.priority("MVP")
def test_tc_wcon_1040_a_reloaded_page_signs_in_again_and_can_still_pair(stack):
    """WCON-1040: a reload keeps the session cookie but loses the CSRF token; signing in and asking to pair must
    still work without it, and /api/v1/me hands the token back."""
    world, _, app = stack
    b = Browser(app)
    pair_and_sign_in(world, b)
    token = b.csrf
    b.csrf = ""                                                       # the page was reloaded
    assert b.post("/api/v1/piers").status_code in (403, 405)          # state-changing calls still need the token
    assert b.get("/api/v1/me").get_json()["csrf"] == token
    again = b.post("/signin")
    assert again.status_code == 200 and again.get_json()["csrf"]
    b.csrf = ""
    assert b.post("/pair/request", {"name": "Another"}).status_code != 403


@pytest.mark.requirement("TC-WCON-800")
@pytest.mark.priority("MVP")
def test_tc_wcon_800_the_page_and_every_script_it_imports_are_served_without_inline_code(stack):
    """WCON-800: the page and each module it imports are served, and none of it relies on inline script or style,
    which the content-security-policy would refuse."""
    import re
    from pathlib import Path

    _, _, app = stack
    b = Browser(app)
    html = b.get("/").get_data(as_text=True)
    assert 'id="tabs"' in html and 'id="theme-btn"' in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and " style=" not in html
    static = Path(app.static_folder)
    seen, todo = set(), ["app.js"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        resp = b.get(f"/static/{name}")
        assert resp.status_code == 200, name
        source = resp.get_data(as_text=True)
        assert ".style." not in source and "innerHTML" not in source, name
        todo += re.findall(r'from "\./([\w.]+\.js)"', source)
    assert {"app.js", "pier.js", "mount.js", "operations.js", "dom.js", "common.js", "camera.js", "focuser.js", "guiding.js",
            "solve.js", "sessions.js", "history.js", "imageview.js"} <= seen
    assert (static / "app.css").is_file()
