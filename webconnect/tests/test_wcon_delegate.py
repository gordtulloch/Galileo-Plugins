# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The Master half over core's real peers, pairing, lease, registry and pipeline (WCON-020, 110-130, 140, 160,
170, 180, 580, 740, 750, 760). Only the HTTP request object is faked."""

import json

import pytest
from galileo.connect_link.audit import AuditLog
from galileo.connect_link.gateway import Gateway
from galileo.connect_link.pairing import PairingService
from galileo.connect_link.peers import PeerStore
from galileo.connect_link.routes import RouteRegistry
from galileo.control_surface import (
    LeaseBroker,
    ObservableState,
    OperationSpec,
    Registry,
)
from galileo.control_surface.schema import Arg
from galileo.control_surface.service import ControlSurfaceService

from webconnect import PLUGIN
from webconnect.browsers import OPERATOR, VIEWER, BrowserError, BrowserRegistry
from webconnect.config import WebConnectConfig
from webconnect.delegate import ACTING_HEADER, Delegate

PIER = "p1"


class Req:
    def __init__(self, acting="", body=None, query=None):
        self.headers = {ACTING_HEADER: acting} if acting else {}
        self.query = query or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class World:
    def __init__(self):
        self.moves = []
        self.registry = Registry()
        self.registry.register(OperationSpec("mount.slew", "operate", (Arg("ra", "float"),), needs_lease=True),
                               lambda ctx, a: self.moves.append(a["ra"]) or "slewed")
        self.registry.register(OperationSpec("mount.status", "read"), lambda ctx, a: "tracking")
        self.registry.register(OperationSpec("mount.erase", "confirm", needs_lease=True), lambda ctx, a: "erased")
        self.leases = LeaseBroker()
        self.gateway = Gateway(self.registry, self.leases, AuditLog())
        self.state = ObservableState()
        self.surface = ControlSurfaceService(self.registry, self.leases, self.gateway, self.state,
                                             lambda: [{"id": PIER, "name": "Pier 1"}])
        self.peers = PeerStore()
        self.pairing = PairingService(self.peers)
        self.browsers = BrowserRegistry(self.peers, self.pairing, self.leases)
        self.delegate = Delegate(self.peers, lambda: self.surface)
        self.service_id, self.service_token = self.browsers.mint_service_credential()
        self.carrier = self.peers.get(self.service_id)

    def pair(self, name="Tablet", role=VIEWER):
        req = self.pairing.request(PLUGIN, name, "browser", "Android", "10.0.0.5")
        peer_id = self.browsers.approve(req.request_id)
        if role == OPERATOR:
            self.browsers.promote(peer_id)
        return peer_id

    async def op(self, peer_id, name, args=None, **extra):
        return await self.delegate.ops(self.carrier, Req(peer_id, {"name": name, "pier": PIER, "args": args or {},
                                                                    **extra}))


@pytest.fixture
def world(link_db):
    return World()


@pytest.mark.requirement("TC-WCON-110")
@pytest.mark.priority("MVP")
def test_tc_wcon_110_new_browsers_are_viewers_until_promoted_on_the_master(world):
    """WCON-110: a new pairing is a Viewer; promotion is a deliberate action on the Master, by no route."""
    peer = world.pair()
    assert world.browsers.browsers()[0]["role"] == VIEWER
    world.browsers.promote(peer)
    assert world.browsers.browsers()[0]["role"] == OPERATOR
    routes = RouteRegistry()
    world.delegate.register(routes)
    assert not [r for r in routes.routes(PLUGIN) if "promote" in r.path or "role" in r.path]


@pytest.mark.requirement("TC-WCON-120")
@pytest.mark.priority("MVP")
async def test_tc_wcon_120_a_viewer_cannot_operate_and_the_refusal_is_audited(world):
    """WCON-120: the role is enforced on the Master; a Viewer's operation is refused with a reason, audited."""
    viewer = world.pair()
    out = await world.op(viewer, "mount.slew", {"ra": 1.0})
    assert out["status"] == "refused" and out["reason"] and world.moves == []
    assert (await world.op(viewer, "mount.status"))["status"] == "ok"         # a read is allowed
    assert any(e.outcome != "ok" for e in world.gateway._audit.query(viewer))


@pytest.mark.requirement("TC-WCON-130")
@pytest.mark.priority("MVP")
def test_tc_wcon_130_no_browser_and_not_the_service_holds_config_scope(world):
    """WCON-130: nothing here ever grants config.*, and no route edits configuration."""
    from galileo.connect_link.peers import PeerError
    from galileo.connect_link.permissions import CONFIG_PERMISSIONS
    peer = world.pair(role=OPERATOR)
    assert not (world.peers.get(peer).permissions & CONFIG_PERMISSIONS)
    assert not (world.carrier.permissions & CONFIG_PERMISSIONS)
    with pytest.raises(ValueError):                      # GrantError: the plugin does not declare config.*
        world.peers.set_permissions(peer, ("config.write",))
    with pytest.raises((PeerError, ValueError)):
        world.peers.create_peer(PLUGIN, "x", ("config.read",), is_service=True)
    routes = RouteRegistry()
    world.delegate.register(routes)
    assert all(r.permission in ("view", "control") for r in routes.routes(PLUGIN))


@pytest.mark.requirement("TC-WCON-140")
@pytest.mark.priority("MVP")
async def test_tc_wcon_140_an_operator_needs_the_lease_and_it_is_shared_with_the_desktop(world):
    """WCON-140: an Operator acts only while holding the Pier's lease, and the desktop contends for the same one."""
    op = world.pair(role=OPERATOR)
    assert (await world.op(op, "mount.slew", {"ra": 1.0}))["status"] != "ok"        # no lease yet
    got = await world.delegate.lease_acquire(world.carrier, Req(op, {"pier": PIER}))
    assert got["granted"] and world.leases.holder(PIER) == f"{PLUGIN}:{op}"
    assert (await world.op(op, "mount.slew", {"ra": 2.0}))["status"] == "ok" and world.moves == [2.0]
    other = world.pair("Phone", role=OPERATOR)
    refused = await world.delegate.lease_acquire(world.carrier, Req(other, {"pier": PIER}))
    assert not refused["granted"] and refused["can_take_over"] and refused["holder"] == f"{PLUGIN}:{op}"
    viewer = world.pair("Spectator")
    denied = await world.delegate.lease_acquire(world.carrier, Req(viewer, {"pier": PIER}))
    assert denied["status"] == "refused"


@pytest.mark.requirement("TC-WCON-170")
@pytest.mark.priority("MVP")
async def test_tc_wcon_170_revoking_ends_the_credential_and_releases_leases(world):
    """WCON-170: revoke at once; the credential stops working, leases are released, streams are told."""
    ended = []
    world.browsers._on_revoked = ended.append
    op = world.pair(role=OPERATOR)
    await world.delegate.lease_acquire(world.carrier, Req(op, {"pier": PIER}))
    world.browsers.revoke(op)
    assert world.leases.holder(PIER) == "" and ended == [op]
    out = await world.op(op, "mount.status")
    assert out["status"] == "refused" and "revoked" in out["reason"]
    assert world.browsers.browsers()[0]["revoked"]


@pytest.mark.requirement("TC-WCON-170")
@pytest.mark.priority("MVP")
async def test_tc_wcon_170_an_expired_browser_is_refused(world):
    """WCON-170: a per-browser expiry; an expired browser needs a new approval."""
    op = world.pair(role=OPERATOR)
    world.browsers.set_expiry(op, 1.0)                                              # long past
    assert (await world.op(op, "mount.status"))["status"] == "refused"


@pytest.mark.requirement("TC-WCON-090")
@pytest.mark.priority("MVP")
async def test_tc_wcon_090_only_the_service_credential_may_name_a_browser(world):
    """WCON-090/LINK-090: a browser cannot act as itself or another browser; nor can an unnamed or unknown one."""
    a, b = world.pair("A", OPERATOR), world.pair("B")
    other_browser = world.peers.get(a)
    assert (await world.delegate.piers(other_browser, Req(b)))["status"] == "refused"
    assert (await world.delegate.piers(world.carrier, Req()))["status"] == "refused"
    assert (await world.delegate.piers(world.carrier, Req("nobody")))["status"] == "refused"
    ok = await world.delegate.piers(world.carrier, Req(a))
    assert ok["status"] == "ok" and ok["piers"][0]["id"] == PIER


@pytest.mark.requirement("TC-WCON-180")
@pytest.mark.priority("MVP")
async def test_tc_wcon_180_confirm_class_operations_take_two_steps(world):
    """WCON-180: the first request returns a token; only a second one carrying it runs the operation."""
    op = world.pair(role=OPERATOR)
    await world.delegate.lease_acquire(world.carrier, Req(op, {"pier": PIER}))
    first = await world.op(op, "mount.erase")
    assert first.get("confirm_token") and first["status"] != "ok"
    second = await world.op(op, "mount.erase", confirm_token=first["confirm_token"])
    assert second["status"] == "ok"


@pytest.mark.requirement("TC-WCON-740")
@pytest.mark.priority("MVP")
async def test_tc_wcon_740_a_retried_idempotency_key_does_not_repeat_the_operation(world):
    """WCON-740: the same key returns the original result without moving the mount again."""
    op = world.pair(role=OPERATOR)
    await world.delegate.lease_acquire(world.carrier, Req(op, {"pier": PIER}))
    one = await world.op(op, "mount.slew", {"ra": 3.0}, idempotency_key="k1")
    two = await world.op(op, "mount.slew", {"ra": 3.0}, idempotency_key="k1")
    assert one == two and world.moves == [3.0]


@pytest.mark.requirement("TC-WCON-580")
@pytest.mark.priority("MVP")
async def test_tc_wcon_580_bad_arguments_are_a_typed_error_not_an_exception(world):
    """WCON-580: validation is on the Master; the browser gets ``invalid`` with the offending argument."""
    op = world.pair(role=OPERATOR)
    await world.delegate.lease_acquire(world.carrier, Req(op, {"pier": PIER}))
    out = await world.op(op, "mount.slew", {"ra": "abc"})
    assert out["status"] == "invalid" and world.moves == []
    assert (await world.delegate.ops(world.carrier, Req(op, ["not", "an", "object"])))["status"] == "refused"


@pytest.mark.requirement("TC-WCON-760")
@pytest.mark.priority("MVP")
async def test_tc_wcon_760_signing_out_releases_the_lease(world):
    """WCON-760: a lease does not outlive its holder."""
    op = world.pair(role=OPERATOR)
    await world.delegate.lease_acquire(world.carrier, Req(op, {"pier": PIER}))
    gone = []
    world.delegate._on_signout = gone.append
    out = await world.delegate.signout(world.carrier, Req(op))
    assert out["status"] == "ok" and world.leases.holder(PIER) == "" and gone == [op]


@pytest.mark.requirement("TC-WCON-750")
@pytest.mark.priority("MVP")
async def test_tc_wcon_750_no_engine_is_a_reason_not_a_crash(world):
    """WCON-750: with no engine running every route says so with a reason."""
    op = world.pair(role=OPERATOR)
    world.delegate._surface = lambda: None
    for out in (await world.delegate.piers(world.carrier, Req(op)),
                await world.op(op, "mount.status")):
        assert out["status"] == "unavailable" and "not running" in out["reason"]


@pytest.mark.requirement("TC-WCON-100")
@pytest.mark.priority("MVP")
def test_tc_wcon_100_pairing_shows_a_code_and_the_token_is_returned_once(world):
    """WCON-100: the request and its code appear on the Master; approval hands the device credential over once."""
    req = world.pairing.request(PLUGIN, "Laptop", "browser", "Windows", "10.0.0.9")
    shown = world.browsers.pending()
    assert shown[0]["code"] == req.code and shown[0]["name"] == "Laptop"
    world.browsers.approve(req.request_id)
    state, token = world.pairing.poll(req.request_id)
    assert state == "approved" and token
    assert world.pairing.poll(req.request_id)[1] is None
    with pytest.raises(BrowserError):
        world.browsers.approve(req.request_id)                                    # no longer waiting


@pytest.mark.requirement("TC-WCON-100")
@pytest.mark.priority("MVP")
async def test_tc_wcon_100_signin_turns_a_device_credential_into_a_named_browser(world):
    """WCON-100/WCON-170: the device credential identifies the browser; a service token or garbage does not."""
    req = world.pairing.request(PLUGIN, "Laptop", "browser", "Windows", "10.0.0.9")
    peer_id = world.browsers.approve(req.request_id)
    _, token = world.pairing.poll(req.request_id)
    ok = await world.delegate.signin(world.carrier, Req(body={"device_credential": token}))
    assert ok["status"] == "ok" and ok["client"] == peer_id and ok["role"] == VIEWER
    for bad in (world.service_token, "garbage", ""):
        assert (await world.delegate.signin(world.carrier, Req(body={"device_credential": bad})))["status"] == "refused"
    world.browsers.revoke(peer_id)
    assert (await world.delegate.signin(world.carrier, Req(body={"device_credential": token})))["status"] == "refused"


@pytest.mark.requirement("TC-WCON-020")
@pytest.mark.priority("MVP")
def test_tc_wcon_020_a_spoke_starts_nothing(link_db):
    """WCON-020: on a node that has designated a Master the plugin registers no route and takes no listener."""
    from galileo import settings_store

    from webconnect import plugin as plugin_module
    settings_store.set_setting(plugin_module.NODECONNECT_NAMESPACE, "master_address", "https://m:1")
    p = plugin_module.WebConnectPlugin()
    p.activate(None)
    assert p.runtime is None and "Master" in p.inert_reason


@pytest.mark.requirement("TC-WCON-040")
@pytest.mark.priority("MVP")
def test_tc_wcon_040_disable_gives_connect_link_back_and_keeps_the_browsers(world):
    """WCON-040: a disabled plugin holds no listener or route; approved browsers survive re-enabling."""
    from webconnect.plugin import Runtime

    class Host:
        def __init__(self):
            self.held = set()

        def acquire(self, plugin, permissions=(), install=None):
            self.held.add(plugin)

        def release(self, plugin):
            self.held.discard(plugin)

    host = Host()
    rt = Runtime(host, world.browsers, world.delegate)
    peer = world.pair()
    rt.start()
    assert host.held == {PLUGIN}
    rt.stop()
    assert host.held == set()
    rt.start()
    assert [b["peer_id"] for b in world.browsers.browsers()] == [peer]


@pytest.mark.requirement("TC-WCON-060")
@pytest.mark.priority("MVP")
def test_tc_wcon_060_binding_every_interface_needs_an_explicit_setting():
    """WCON-060: 0.0.0.0 is ignored unless wide_bind is set; every value is clamped."""
    assert WebConnectConfig.from_dict({"bind_address": "0.0.0.0"}).bind_address == ""
    assert WebConnectConfig.from_dict({"bind_address": "0.0.0.0", "wide_bind": True}).bind_address == "0.0.0.0"
    c = WebConnectConfig.from_dict({"port": 5, "session_idle_s": "x", "preview_quality": 1000,
                                    "bind_address": "not-an-ip"})
    assert c.port == 1024 and c.session_idle_s == 1800 and c.preview_quality == 95 and c.bind_address == ""
    assert json.dumps(c.to_dict())


@pytest.mark.requirement("TC-WCON-700")
@pytest.mark.priority("MVP")
def test_tc_wcon_700_the_service_credential_has_exactly_one_live_copy(world):
    """A new service credential revokes the old, so there is one working credential at a time."""
    old = world.service_id
    new_id, _ = world.browsers.mint_service_credential()
    assert world.peers.get(old).revoked_at is not None and world.peers.get(new_id).revoked_at is None
