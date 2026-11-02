# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect wire (TC-NCON-050, -120, -140, NCON-NFR-030): the same two-database sync as
test_ncon_sync, but every request and answer goes through the JSON forms and ``MasterEndpoint`` and
is serialised to text and back on the way, as it would be over Connect Link."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from galileo.library.models.base import db as database
from galileo.replication import registry, tracking
from test_ncon_sync import INFO, World, q, seed_master

from nodeconnect.link import MasterLink
from nodeconnect.sync import IDLE
from nodeconnect.wire import API, MasterEndpoint, WireError, register_routes


class WireWorld(World):
    """The spoke talks to the Master only through ``MasterLink`` -> JSON -> ``MasterEndpoint``."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        from nodeconnect.sync import column_fingerprints
        self.endpoint = MasterEndpoint(self.master, lambda: self.master_info, column_fingerprints,
                                       clock=lambda: 0.1)
        self.link = MasterLink(self.transport)
        self.node_id = "spoke-1"
        self.seen_paths: list[str] = []

    def transport(self, path, body):
        if self.fail:
            raise ConnectionError("link down")
        self.seen_paths.append(path)
        wire_body = json.loads(json.dumps(body))                       # must survive the trip
        op = path.rsplit("/", 1)[1]
        with self.side("master"):
            tracking.configure(is_master=lambda: True)
            return json.loads(json.dumps(getattr(self.endpoint, op)(self.node_id, wire_body)))


@pytest.fixture
def world(tmp_path):
    w = WireWorld(tmp_path)
    yield w
    if not database.is_closed():
        database.close()
    database.init(None)
    tracking.configure(is_master=lambda: False)


@pytest.mark.requirement("TC-NCON-120")
@pytest.mark.priority("MVP")
def test_tc_ncon_120_a_full_sync_crosses_the_wire(world):
    """NCON-120: the spoke acquires the Master's configuration through the JSON wire alone, a second
    pass finds nothing, and an edit made on the spoke reaches the Master."""
    seed_master(world)
    r = world.sync.run_once(world.link)
    assert r.state == IDLE and r.first_sync and r.pulled >= 4
    assert q(world, "spoke", "SELECT server FROM device_configs")[0]["server"] == "pi.local"
    assert world.sync.run_once(world.link).pulled == 0
    with world.side("spoke"):
        database.execute_sql("UPDATE device_configs SET server = 'edited.local'")
    r = world.sync.run_once(world.link)
    assert r.pushed == 1 and r.conflicts == 0
    assert q(world, "master", "SELECT server FROM device_configs")[0]["server"] == "edited.local"
    assert {p.rsplit("/", 1)[1] for p in world.seen_paths} == {"handshake", "pull", "push", "ack"}


@pytest.mark.requirement("TC-NCON-140")
@pytest.mark.priority("MVP")
def test_tc_ncon_140_a_deletion_and_a_conflict_cross_the_wire(world):
    """NCON-140 / NCON-170 / NCON-180: a spoke-side deletion is shipped and applied on the Master;
    a stale edit comes back as a conflict the Master's value wins, and the spoke adopts it."""
    seed_master(world)
    world.sync.run_once(world.link)
    with world.side("master"):
        database.execute_sql("UPDATE app_settings SET value = '200' WHERE namespace = 'imaging' AND key = 'gain'")
    with world.side("spoke"):
        database.execute_sql("UPDATE app_settings SET value = '50' WHERE namespace = 'imaging' AND key = 'gain'")
    world.info = dict(INFO)
    r = world.sync.run_once(world.link)
    assert r.conflicts == 1
    assert json.loads(q(world, "spoke", "SELECT value FROM app_settings WHERE key = 'gain'")[0]["value"]) in (200, 50)
    assert (q(world, "spoke", "SELECT value FROM app_settings WHERE key = 'gain'")
            == q(world, "master", "SELECT value FROM app_settings WHERE key = 'gain'"))
    with world.side("spoke"):
        database.execute_sql("DELETE FROM device_configs")
    assert world.sync.run_once(world.link).pushed == 1
    assert q(world, "master", "SELECT * FROM device_configs") == []


@pytest.mark.requirement("TC-NCON-120")
@pytest.mark.priority("MVP")
def test_tc_ncon_120_pulls_are_paged_and_resume_part_way(world):
    """NCON-120 / NCON-NFR-010: a table with more changes than a page arrives in several pages; a page
    that does not finish its table reports its own last revision, so a stop part-way resumes there."""
    seed_master(world)
    world.endpoint._page = 1
    with world.side("master"):
        for i in range(4):
            database.execute_sql("INSERT INTO app_settings (namespace, key, value) VALUES ('imaging', ?, '1')",
                                 (f"k{i}",))
    with world.side("master"):
        reply = world.endpoint.pull("spoke-1", {"tables": None})
    settings = [p for p in reply["pages"] if p["table"] == "app_settings"]
    assert len(settings) >= 4 and all(len(p["changes"]) <= 1 for p in settings)
    revs = [p["rev"] for p in settings]
    assert revs == sorted(revs) and revs[-1] == max(c["rev"] for p in settings for c in p["changes"])
    assert world.sync.run_once(world.link).state == IDLE
    assert len(q(world, "spoke", "SELECT * FROM app_settings WHERE namespace = 'imaging'")) == 5


@pytest.mark.requirement("TC-NCON-050")
@pytest.mark.priority("MVP")
def test_tc_ncon_050_a_spoke_master_serves_nobody_and_a_bad_push_is_refused(world):
    """NCON-020 / NCON-050: a node that is itself a spoke refuses every request with 409; a malformed
    push or a table the Master does not replicate is a 400, not a crash."""
    ep = MasterEndpoint(world.master, lambda: INFO, dict, is_master=lambda: False)
    for op in ("handshake", "pull", "push", "ack"):
        with pytest.raises(WireError) as exc:
            getattr(ep, op)("n", {})
        assert exc.value.status == 409
    with pytest.raises(WireError) as exc:
        world.endpoint.push("n", {"changes": [{"table": "x"}]})
    assert exc.value.status == 400
    bad = {"table": "nonesuch", "op": "upsert", "key": ["a"], "wire": {}, "base_rev": 0, "modified": 1.0}
    with world.side("master"), pytest.raises(WireError) as exc:
        world.endpoint.push("n", {"changes": [bad], "offset": {"seconds": 0, "error": 0}})
    assert exc.value.status == 400


class FakeRoutes:
    def __init__(self):
        self.items = []

    def register(self, plugin, method, path, permission, handler):
        self.items.append((plugin, method, path, permission, handler))


@pytest.mark.requirement("TC-NCON-050")
@pytest.mark.priority("MVP")
def test_tc_ncon_050_routes_declare_least_permission_and_use_the_authenticated_peer(world):
    """NCON-050 / NCON-NFR-030: reads need config.read, pushing needs config.write, nothing asks for
    control or view; the node id the Master acts on is the authenticated principal, never the body."""
    routes = FakeRoutes()
    register_routes(routes, world.endpoint)
    assert {(m, p, perm) for _, m, p, perm, _ in routes.items} == {
        ("POST", f"{API}/handshake", "config.read"), ("POST", f"{API}/pull", "config.read"),
        ("POST", f"{API}/push", "config.write"), ("POST", f"{API}/ack", "config.read")}
    ack = next(h for _, _, p, _, h in routes.items if p.endswith("/ack"))

    class Req:
        can_read_body = True

        async def json(self):
            return {"revs": {"piers": 3}, "node_id": "someone-else"}

    with world.side("master"):
        resp = asyncio.run(ack(Req(), SimpleNamespace(peer_id="spoke-9")))
    assert resp.status == 200
    with world.side("master"):
        assert world.master_store.peer_get("spoke-9", "ack", {}) == {"piers": 3}
        assert not world.master_store.peer_get("someone-else", "ack", {})
    assert registry.get("piers") is not None
