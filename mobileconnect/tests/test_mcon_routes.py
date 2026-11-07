# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The routes over the real wire: core's listener, peers, pairing, lease, registry and Gateway. Devices are fake."""

import asyncio
import json

import aiohttp
import pytest

from mobileconnect import PLUGIN
from mobileconnect.clients import OPERATOR, VIEWER, ClientError
from mobileconnect.routes import NOT_RUNNING
from tests.world import PIER, World


class App:
    """What the app does, over HTTPS with a real token."""

    def __init__(self, world: World, http: aiohttp.ClientSession, token: str = "") -> None:
        self.world, self.http, self.token = world, http, token
        self.base = f"https://127.0.0.1:{world.server.port}"
        self.peer_id = ""

    @property
    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def request(self, method, path, *, query=None, body=None, headers=None):
        async with self.http.request(method, self.base + path, params=query, json=body,
                                     headers={**self._auth, **(headers or {})}, ssl=False) as resp:
            raw = await resp.read()
            kind = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(raw) if "json" in kind else raw), kind

    async def pair(self, name="iPad", role=OPERATOR):
        status, started, _ = await self.request(
            "POST", "/connect/v1/pair", body={"plugin": PLUGIN, "name": name, "kind": "app", "platform": "ipados"})
        assert status == 202
        self.peer_id = self.world.clients.approve(started["request_id"], role)
        _, polled, _ = await self.request("GET", f"/connect/v1/pair/{started['request_id']}")
        self.token = polled["token"]
        return self

    async def op(self, name, args=None, *, key="", confirm="", confirmed=None):
        headers = {}
        if key:
            headers["Idempotency-Key"] = key
        if confirm:
            headers["X-Confirm-Token"] = confirm
        body = {"args": args or {}}
        if confirmed is not None:
            body["confirmed"] = confirmed
        status, out, _ = await self.request("POST", "/mobileconnect/v1/ops", query={"pier": PIER, "name": name},
                                            body=body, headers=headers)
        return status, out

    async def lease(self, token=""):
        headers = {"X-Confirm-Token": token} if token else {}
        status, out, _ = await self.request("POST", "/mobileconnect/v1/lease", query={"pier": PIER}, headers=headers)
        return status, out


@pytest.fixture
async def world(link_db):
    w = World()
    await w.start()
    yield w
    await w.stop()


@pytest.fixture
async def http():
    async with aiohttp.ClientSession() as session:
        yield session


async def next_frame(ws, wanted=None, timeout=5.0):
    """The next frame, optionally the next whose payload has this type."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        assert left > 0, f"no {wanted} frame arrived"
        msg = await asyncio.wait_for(ws.receive(), left)
        frame = json.loads(msg.data)
        if wanted is None or frame.get("data", {}).get("type") == wanted:
            return frame


@pytest.mark.requirement("TC-MCON-100")
@pytest.mark.priority("MVP")
async def test_tc_mcon_100_only_a_paired_app_reaches_a_route(world, http):
    """MCON-100: without a token nothing answers but 401, and an app is paired only by approval on the Master."""
    stranger = App(world, http)
    for path in ("capabilities", "catalogue", "piers", "alerts"):
        assert (await stranger.request("GET", f"/mobileconnect/v1/{path}"))[0] == 401
    assert (await stranger.request("POST", "/mobileconnect/v1/ops", query={"pier": PIER, "name": "mount.park"}, body={}))[0] == 401
    status, started, _ = await stranger.request(
        "POST", "/connect/v1/pair", body={"plugin": PLUGIN, "name": "iPad", "kind": "app", "platform": "ipados"})
    assert status == 202
    assert [r["code"] for r in world.clients.pending()] == [started["code"]]
    _, polled, _ = await stranger.request("GET", f"/connect/v1/pair/{started['request_id']}")
    assert polled == {"state": "pending"}


@pytest.mark.requirement("TC-MCON-110")
@pytest.mark.priority("MVP")
async def test_tc_mcon_110_new_apps_are_viewers_and_no_route_changes_a_role(world, http):
    """MCON-110: Viewer unless the person chooses otherwise, and a role changes only on the Master."""
    app = await App(world, http).pair(role=VIEWER)
    _, me, _ = await app.request("GET", "/connect/v1/whoami")
    assert me["permissions"] == ["view"]
    assert not [r for r in world.server.routes.routes(PLUGIN) if "role" in r.path or "promote" in r.path]
    listed = world.clients.clients()
    assert [(c["name"], c["role"], c["revoked"]) for c in listed] == [("iPad", VIEWER, False)]
    world.clients.set_role(app.peer_id, OPERATOR)
    _, me, _ = await app.request("GET", "/connect/v1/whoami")
    assert set(me["permissions"]) == {"view", "control"}


@pytest.mark.requirement("TC-MCON-120")
@pytest.mark.priority("MVP")
async def test_tc_mcon_120_a_viewer_is_refused_and_audited(world, http):
    """MCON-120: enforced on the Master, with a reason, and recorded."""
    app = await App(world, http).pair(role=VIEWER)
    status, out = await app.op("mount.track", {"enabled": False})
    assert status == 409 and out["error"] == "refused" and out["reason"]
    assert world.devices.mount.calls == []
    assert any(e.outcome != "ok" for e in world.audit.query(app.peer_id))
    assert (await app.op("mount.status"))[1]["ok"] is True                 # a read is allowed
    assert (await app.lease())[0] == 409


@pytest.mark.requirement("TC-MCON-130")
@pytest.mark.priority("MVP")
async def test_tc_mcon_130_no_config_scope_no_config_route(world, http):
    """MCON-130: an app can't be granted config.*, and every route needs only view."""
    app = await App(world, http).pair()
    with pytest.raises(ValueError):
        world.peers.set_permissions(app.peer_id, ("config.write",))
    assert {r.permission for r in world.server.routes.routes(PLUGIN)} == {"view"}


@pytest.mark.requirement("TC-MCON-140")
@pytest.mark.priority("MVP")
async def test_tc_mcon_140_operating_needs_the_lease_shared_with_the_desktop(world, http):
    """MCON-140: one lease per Pier, in core's broker; the app is told when it needs it."""
    app = await App(world, http).pair()
    status, out = await app.op("mount.track", {"enabled": False})
    assert status == 409 and out["detail"]["kind"] == "no_lease" and world.devices.mount.calls == []
    status, out = await app.lease()
    assert status == 200 and out["held_by_me"] and out["holder"] == app.peer_id
    assert world.leases.holder(PIER) == f"{PLUGIN}:{app.peer_id}"
    assert (await app.op("mount.track", {"enabled": False}))[1]["ok"] is True
    assert world.devices.mount.calls == [("track", False)]
    status, out, _ = await app.request("DELETE", "/mobileconnect/v1/lease", query={"pier": PIER})
    assert status == 200 and not out["held_by_me"] and world.leases.holder(PIER) == ""


@pytest.mark.requirement("TC-MCON-150")
@pytest.mark.priority("MVP")
async def test_tc_mcon_150_a_held_lease_names_its_holder_and_take_over_needs_the_token(world, http):
    """MCON-150: the refusal names the holder; taking over takes the token the refusal offered, once, for that Pier."""
    world.leases.acquire(PIER, "desktop")
    app = await App(world, http).pair()
    status, out = await app.op("mount.track", {"enabled": False})
    assert status == 409 and out["detail"] == {"kind": "lease_held", "holder": "desktop", "holder_name": "Desktop", "can_take": True}
    assert "Desktop" in out["reason"] and "desktop" not in out["reason"]
    status, refused = await app.lease()
    assert status == 409 and refused["detail"]["can_take"] and world.leases.holder(PIER) == "desktop"
    assert (await app.lease("not-the-token"))[0] == 409 and world.leases.holder(PIER) == "desktop"
    other = await App(world, http).pair("Phone")
    assert (await other.lease(refused["confirm_token"]))[0] == 409            # bound to the app it was offered to
    assert (await app.lease(refused["confirm_token"]))[0] == 200
    assert world.leases.holder(PIER) == f"{PLUGIN}:{app.peer_id}"
    assert (await other.lease(refused["confirm_token"]))[0] == 409            # used


@pytest.mark.requirement("TC-MCON-170")
@pytest.mark.priority("MVP")
async def test_tc_mcon_170_revoking_ends_token_stream_and_lease(world, http):
    """MCON-170: at once."""
    app = await App(world, http).pair()
    await app.lease()
    ws = await http.ws_connect(f"{app.base}/connect/v1/stream", headers=app._auth, ssl=False)
    await next_frame(ws)
    world.clients.revoke(app.peer_id)
    assert world.leases.holder(PIER) == ""
    assert (await app.request("GET", "/connect/v1/whoami"))[0] == 401
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED)
    with pytest.raises(ClientError):
        world.clients.revoke(app.peer_id)


@pytest.mark.requirement("TC-MCON-180")
@pytest.mark.priority("MVP")
async def test_tc_mcon_180_confirmation_is_enforced_by_the_protocol(world, http):
    """MCON-180: a confirm operation runs only with the token, which is single-use; the wire names the state."""
    app = await App(world, http).pair()
    await app.lease()
    status, asked = await app.op("mount.park", key="k1")
    assert status == 409 and asked["error"] == "confirmation_required" and asked["confirm_token"]
    assert asked["reason"] == "Park the mount" and world.devices.mount.calls == []
    status, out = await app.op("mount.park", key="k2", confirm=asked["confirm_token"])
    assert status == 200 and out["ok"] and world.devices.mount.calls == [("park",)]
    status, again = await app.op("mount.park", key="k3", confirm=asked["confirm_token"])
    assert status == 409 and again["error"] == "confirmation_required" and len(world.devices.mount.calls) == 1


@pytest.mark.requirement("TC-MCON-400")
@pytest.mark.priority("MVP")
async def test_tc_mcon_400_catalogue_in_the_wire_shape_with_real_availability(world, http):
    """MCON-400: name, area, class, lease and args from the Control Surface; availability per Pier, with the reason."""
    app = await App(world, http).pair()
    status, out, _ = await app.request("GET", "/mobileconnect/v1/catalogue", query={"pier": PIER})
    assert status == 200
    ops = {o["name"]: o for o in out["operations"]}
    assert ops["mount.park"]["class"] == "confirm" and ops["mount.park"]["lease"] and ops["mount.park"]["area"] == "mount"
    assert ops["system.stop_everything"]["class"] == "emergency" and not ops["system.stop_everything"]["lease"]
    slew_ra = next(a for a in ops["mount.slew"]["args"] if a["name"] == "ra_deg")
    assert slew_ra["max"] == 360 and slew_ra["type"] == "float" and slew_ra["required"]
    assert ops["mount.slew"]["long_running"]
    assert ops["dome.open"]["available"] is False and "dome" in ops["dome.open"]["unavailable_because"]
    assert all(o["class"] in ("read", "operate", "confirm", "emergency") for o in out["operations"])


@pytest.mark.requirement("TC-MCON-410")
@pytest.mark.priority("MVP")
async def test_tc_mcon_410_typed_outcomes_and_the_invalid_path(world, http):
    """MCON-410, MCON-570: a typed error with a reason, and the argument at fault. Never a 5xx for a typed outcome."""
    app = await App(world, http).pair()
    await app.lease()
    status, out = await app.op("mount.slew", {"ra_deg": 400, "dec_deg": 0})
    assert status == 400 and out["error"] == "invalid" and out["detail"]["path"] == "ra_deg" and "ra_deg" in out["reason"]
    status, out = await app.op("no.such_operation")
    assert status == 409 and out["error"] == "unavailable"
    status, out = await app.op("dome.open")
    assert status == 409 and out["error"] == "unavailable" and "dome" in out["reason"]


@pytest.mark.requirement("TC-MCON-710")
@pytest.mark.priority("MVP")
async def test_tc_mcon_710_emergency_needs_no_lease_but_a_deliberate_flag(world, http):
    """MCON-710: Operator only, lease-free, one round trip, and only with the app's confirmation flag."""
    app = await App(world, http).pair()
    world.leases.acquire(PIER, "desktop")
    status, out = await app.op("system.stop_everything")
    assert status == 409 and out["error"] == "refused" and world.devices.mount.calls == []
    status, out = await app.op("system.stop_everything", confirmed=True)
    assert status == 200 and out["ok"] and ("abort",) in world.devices.mount.calls
    viewer = await App(world, http).pair("Phone", role=VIEWER)
    world.devices.mount.calls.clear()
    status, out = await viewer.op("system.stop_everything", confirmed=True)
    assert status == 409 and world.devices.mount.calls == []


@pytest.mark.requirement("TC-MCON-730")
@pytest.mark.priority("MVP")
async def test_tc_mcon_730_a_hold_start_beat_stop(world, http):
    """MCON-730: the app's hold reaches core's manager, which owns the deadman."""
    app = await App(world, http).pair()
    await app.lease()
    status, out = await app.op("focus.hold_start", {"direction": 1})
    assert status == 200 and world.devices.focus.holds == 1
    hold = out["result"]
    assert world.holds.active(PIER) == [hold]
    assert (await app.op("focus.hold_beat", {"hold_id": hold}))[1]["result"] is True
    assert (await app.op("focus.hold_stop", {"hold_id": hold}))[1]["result"] is True
    assert world.holds.active(PIER) == []


@pytest.mark.requirement("TC-MCON-740")
@pytest.mark.priority("MVP")
async def test_tc_mcon_740_the_same_key_returns_the_original_result(world, http):
    """MCON-740: a retried key does not run twice."""
    app = await App(world, http).pair()
    await app.lease()
    await app.op("mount.track", {"enabled": False}, key="k1")
    await app.op("mount.track", {"enabled": False}, key="k1")
    assert world.devices.mount.calls == [("track", False)]


@pytest.mark.requirement("TC-MCON-300")
@pytest.mark.priority("MVP")
async def test_tc_mcon_300_snapshot_on_request_then_deltas_on_the_core_stream(world, http):
    """MCON-300, 310, 330: the app asks after hello; the snapshot is sequenced on its stream; changes follow."""
    app = await App(world, http).pair()
    ws = await http.ws_connect(f"{app.base}/connect/v1/stream", headers=app._auth, ssl=False)
    hello = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
    assert hello["type"] == "hello"
    status, out, _ = await app.request("POST", "/mobileconnect/v1/snapshot")
    assert status == 200 and out["ok"]
    snapshot = await next_frame(ws, "snapshot")
    values = snapshot["data"]["piers"][PIER]
    assert values["mount.tracking"] is True and values["mount.connected"] is True and values["camera.cooling"] is True
    assert values["mount.position"]["ra_deg"] == 82.5 and values["focus.position"] == 21000
    assert values["activity.mount"] == "Tracking"

    # A change the sampler sees, as a delta, and nothing for what didn't change.
    world.devices.mount.parked = True
    await world.runtime.pump.sample_once()
    delta = await next_frame(ws, "delta")
    seen = {delta["data"]["key"]}
    while "activity.mount" not in seen:
        seen.add((await next_frame(ws, "delta"))["data"]["key"])
    assert seen == {"mount.parked", "activity.mount"}
    assert delta["seq"] > snapshot["seq"]

    # The observable fragments core publishes, translated.
    world.state.publish(PIER, "mount.position", {"ra_deg": 10.0, "dec_deg": 20.0, "slewing": True})
    world.runtime.pump.handle_feed_entry({"type": "state", "pier": PIER, "key": "mount.position",
                                          "value": {"ra_deg": 10.0, "dec_deg": 20.0, "slewing": True}})
    delta = await next_frame(ws, "delta")
    assert delta["data"]["key"].startswith("mount.")
    await ws.close()


@pytest.mark.requirement("TC-MCON-320")
@pytest.mark.priority("MVP")
async def test_tc_mcon_320_a_lease_change_reaches_the_stream_in_the_apps_own_terms(world, http):
    """MCON-150, 320: its own lease reads as its peer id; anyone else's by name."""
    app = await App(world, http).pair()
    ws = await http.ws_connect(f"{app.base}/connect/v1/stream", headers=app._auth, ssl=False)
    await next_frame(ws)
    await app.request("POST", "/mobileconnect/v1/snapshot")
    await next_frame(ws, "snapshot")
    await app.lease()
    lease = await next_frame(ws, "lease")
    assert lease["data"]["holder"] == app.peer_id and lease["data"]["pier"] == PIER
    world.leases.take(PIER, "desktop", True)
    lease = await next_frame(ws, "lease")
    assert lease["data"]["holder"] == "desktop" and lease["data"]["holder_name"] == "Desktop"
    await ws.close()


@pytest.mark.requirement("TC-MCON-330")
@pytest.mark.priority("MVP")
async def test_tc_mcon_330_a_long_running_operation_is_tied_by_its_correlation_id(world, http):
    """MCON-330: ok with a correlation id, then op.started and op.finished carrying it."""
    app = await App(world, http).pair()
    await app.lease()
    ws = await http.ws_connect(f"{app.base}/connect/v1/stream", headers=app._auth, ssl=False)
    await next_frame(ws)
    await app.request("POST", "/mobileconnect/v1/snapshot")
    await next_frame(ws, "snapshot")
    from galileo.control_surface.events import OperationFinished, OperationStarted
    world.bus.publish(OperationStarted("control_surface", correlation_id="c1", operation="mount.slew", pier=PIER, principal="x"))
    world.bus.publish(OperationFinished("control_surface", correlation_id="c1", operation="mount.slew", pier=PIER, outcome="refused", reason="Clouds"))
    started = await next_frame(ws, "op.started")
    assert started["data"]["correlation_id"] == "c1"
    finished = await next_frame(ws, "op.finished")
    assert finished["data"]["outcome"] == "refused" and finished["data"]["reason"] == "Clouds"
    await ws.close()


@pytest.mark.requirement("TC-MCON-310")
@pytest.mark.priority("MVP")
async def test_tc_mcon_310_piers_and_a_stopped_engine_said_in_words(world, http):
    """MCON-410: Piers with their access; a Master whose engine isn't running says so rather than failing."""
    app = await App(world, http).pair()
    status, out, _ = await app.request("GET", "/mobileconnect/v1/piers")
    assert status == 200 and out["piers"] == [
        {"id": PIER, "name": "East Pier", "node": "Test Master", "access": "control", "reachable": True}]
    viewer = await App(world, http).pair("Phone", role=VIEWER)
    assert (await viewer.request("GET", "/mobileconnect/v1/piers"))[1]["piers"][0]["access"] == "view"
    status, caps, _ = await app.request("GET", "/mobileconnect/v1/capabilities")
    assert "mount" in caps["areas"] and caps["sky_stream"]["available"] is True and caps["sky_stream"]["rate_per_s"] > 0

    world.engine_running = False
    status, out, _ = await app.request("GET", "/mobileconnect/v1/piers")
    assert status == 200 and out == {"piers": [], "reason": NOT_RUNNING}
    assert (await app.op("mount.status"))[1]["reason"] == NOT_RUNNING
    assert (await app.request("GET", "/mobileconnect/v1/catalogue"))[1] == {"operations": []}


@pytest.mark.requirement("TC-MCON-600")
@pytest.mark.priority("MVP")
async def test_tc_mcon_600_no_picture_is_a_clean_404(world, http):
    """MCON-600: a Master with no preview service answers 404 with a reason, as the contract says."""
    app = await App(world, http).pair()
    status, out, _ = await app.request("GET", "/mobileconnect/v1/preview", query={"pier": PIER})
    assert status == 404 and out["error"]


@pytest.mark.requirement("TC-MCON-340")
@pytest.mark.priority("MVP")
async def test_tc_mcon_340_an_unsafe_monitor_raises_one_alert(world, http):
    """MCON-340: on the change to unsafe, not on every sample."""
    pump = world.runtime.pump
    pump._alert_on_safety(PIER, {"safety.overall": "unsafe", "safety.inputs": {"Safety monitor": "Rain"}})
    pump._alert_on_safety(PIER, {"safety.overall": "unsafe", "safety.inputs": {"Safety monitor": "Rain"}})
    app = await App(world, http).pair()
    _, out, _ = await app.request("GET", "/mobileconnect/v1/alerts")
    assert len(out["alerts"]) == 1 and out["alerts"][0]["severity"] == "critical" and "Rain" in out["alerts"][0]["message"]
    assert "type" not in out["alerts"][0]
