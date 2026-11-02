# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect sync, end to end between two real databases (TC-NCON-120, -130, -140, -150, -160,
-170, -180, -300, -305). The Connect Link between them is replaced by a direct call that points the
process at the Master's database or the spoke's for the duration of each request."""

import contextlib
import json

import pytest
from galileo import settings_store
from galileo.library.database import init_db
from galileo.library.models.base import db as database
from galileo.replication import RevisionIssuer, tracking

from nodeconnect.apply import Applier, ApplyGate
from nodeconnect.master import MasterService
from nodeconnect.store import NodeStore
from nodeconnect.sync import BACKOFF, BLOCKED, IDLE, SpokeSync, column_fingerprints

INFO = {"galileo": "t", "plugin_api": "1", "schema": 36, "protocol": 1}


class World:
    """Two databases and the link between them."""

    def __init__(self, tmp_path):
        self.paths = {"master": tmp_path / "master.db", "spoke": tmp_path / "spoke.db"}
        self.current = None
        self.info = dict(INFO)
        self.master_info = dict(INFO)
        self.fail = False
        self.busy = False
        self.callbacks = []
        for side in self.paths:
            self.at(side)
        self.master_store = self.in_("master", NodeStore)
        self.spoke_store = self.in_("spoke", NodeStore)
        self.master = MasterService(self.master_store, RevisionIssuer(lambda: self.current == "master"),
                                    Applier(self.master_store))
        spoke_applier = Applier(self.spoke_store)
        gate = ApplyGate(self.spoke_store, spoke_applier, lambda: self.busy, self.callbacks.append)
        self.sync = SpokeSync(self.spoke_store, spoke_applier, gate, "master-1", lambda: self.info,
                              clock=iter_clock())
        self.at("spoke")

    def at(self, side):
        if self.current != side:
            init_db(self.paths[side])
            self.current = side

    @contextlib.contextmanager
    def side(self, side):
        before = self.current
        self.at(side)
        try:
            yield
        finally:
            self.at(before)

    def in_(self, side, fn):
        with self.side(side):
            return fn()

    # the MasterLinkPort the spoke talks to
    def handshake(self, info, fingerprints):
        if self.fail:
            raise ConnectionError("link down")
        with self.side("master"):
            return {"info": self.master_info, "fingerprints": column_fingerprints(), "received": 0.1, "sent": 0.1}

    def pull(self, since, tables):
        with self.side("master"):
            return self.master.changes_since(since, tables)

    def push(self, pushed, offset):
        with self.side("master"):
            return self.master.receive_push("spoke-1", pushed, offset)

    def ack(self, revs):
        with self.side("master"):
            self.master.record_ack("spoke-1", revs)


def iter_clock():
    state = {"t": -0.2}

    def clock():
        state["t"] += 0.2
        return state["t"]
    return clock


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    if not database.is_closed():
        database.close()
    database.init(None)


def seed_master(w):
    from galileo.library.models import DeviceConfigRecord, ObservatoryRecord, PierRecord
    with w.side("master"):
        o = ObservatoryRecord.create(name="Home", timezone="UTC")
        p = PierRecord.create(observatory=o, name="Pier 1")
        DeviceConfigRecord.create(pier=p, category="camera", slot="primary", driver="indi_asi", server="pi.local", port=7624)
        settings_store.set_setting("imaging", "gain", 100)
        settings_store.set_setting("connect_link", "port", 9000)         # node-local: never replicates


def q(w, side, sql, *p):
    with w.side(side):
        cur = database.execute_sql(sql, p)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


@pytest.mark.requirement("TC-NCON-130")
@pytest.mark.priority("MVP")
def test_tc_ncon_130_first_sync_brings_everything_replicable_and_nothing_node_local(world):
    """NCON-130 / NCON-120: a node that has never synced receives every replicable table in dependency
    order, as clean rows at the Master's revisions; node-local settings stay on the Master; a second
    pass finds nothing new."""
    seed_master(world)
    r = world.sync.run_once(world)
    assert r.state == IDLE and r.first_sync and r.pulled >= 4 and not r.deferred
    dev = q(world, "spoke", "SELECT * FROM device_configs")[0]
    assert (dev["driver"], dev["server"], dev["dirty"]) == ("indi_asi", "pi.local", 0)
    assert q(world, "spoke", "SELECT name FROM piers")[0]["name"] == "Pier 1"
    keys = {(x["namespace"], x["key"]) for x in q(world, "spoke", "SELECT namespace, key FROM app_settings")}
    assert ("imaging", "gain") in keys and ("connect_link", "port") not in keys
    with world.side("spoke"):
        assert tracking.dirty_rows("device_configs") == [] and tracking.dirty_rows("app_settings") == []
    assert world.sync.last_rev()["device_configs"] == 1
    again = world.sync.run_once(world)
    assert again.state == IDLE and not again.first_sync and again.pulled == 0 and again.pushed == 0


@pytest.mark.requirement("TC-NCON-120")
@pytest.mark.priority("MVP")
def test_tc_ncon_120_only_what_changed_since_the_last_revision_crosses_the_wire(world):
    """NCON-120 / NCON-160: after a sync, a Master edit is the only thing the next pass carries, found
    by revision and not by comparing clocks."""
    seed_master(world)
    world.sync.run_once(world)
    with world.side("master"):
        settings_store.set_setting("imaging", "gain", 250)
    r = world.sync.run_once(world)
    assert r.pulled == 1
    assert q(world, "spoke", "SELECT value FROM app_settings WHERE key = 'gain'")[0]["value"] == "250"


@pytest.mark.requirement("TC-NCON-140")
@pytest.mark.priority("MVP")
def test_tc_ncon_140_a_spoke_edit_is_pushed_committed_and_becomes_clean(world):
    """NCON-140: a local edit on the spoke is pushed with its base revision, the Master commits it at
    a new revision, and the spoke's row is clean afterwards."""
    seed_master(world)
    world.sync.run_once(world)
    with world.side("spoke"):
        database.execute_sql("UPDATE device_configs SET port = 7625")
        assert [d["port"] for d in tracking.dirty_rows("device_configs")] == [7625]
    r = world.sync.run_once(world)
    assert r.pushed == 1 and r.conflicts == 0
    assert q(world, "master", "SELECT port, rev FROM device_configs")[0] == {"port": 7625, "rev": 2}
    spoke = q(world, "spoke", "SELECT port, rev, dirty FROM device_configs")[0]
    assert spoke == {"port": 7625, "rev": 2, "dirty": 0}
    assert world.sync.run_once(world).pushed == 0


@pytest.mark.requirement("TC-NCON-170")
@pytest.mark.priority("MVP")
def test_tc_ncon_170_the_later_edit_wins_and_the_loser_is_kept_in_a_snapshot(world):
    """NCON-170: both sides edit the same row; the later edit wins on both nodes, and the value that
    lost is kept in a snapshot on the node that lost it."""
    seed_master(world)
    world.sync.run_once(world)
    with world.side("master"):
        database.execute_sql("UPDATE device_configs SET driver = 'master_edit'")
        database.execute_sql("UPDATE device_configs SET modified = 100.0")      # earlier
    with world.side("spoke"):
        database.execute_sql("UPDATE device_configs SET driver = 'spoke_edit'")  # now: later
    r = world.sync.run_once(world)
    assert r.conflicts == 1
    assert q(world, "master", "SELECT driver FROM device_configs")[0]["driver"] == "spoke_edit"
    assert q(world, "spoke", "SELECT driver, dirty FROM device_configs")[0] == {"driver": "spoke_edit", "dirty": 0}
    lost = [json.loads(x["before"])["old"]["driver"] for x in q(world, "master", "SELECT before FROM sync_snapshots")
            if x["before"] and json.loads(x["before"])["table"] == "device_configs"]
    assert "master_edit" in lost
    # now the other way: the Master edits later
    with world.side("master"):
        database.execute_sql("UPDATE device_configs SET driver = 'master_later'")
    with world.side("spoke"):
        database.execute_sql("UPDATE device_configs SET driver = 'spoke_later', modified = 50.0")
    r = world.sync.run_once(world)
    assert r.conflicts == 1
    assert q(world, "spoke", "SELECT driver, dirty FROM device_configs")[0] == {"driver": "master_later", "dirty": 0}
    assert any(s["data"]["old"]["driver"] == "spoke_later" for rid in r.run_ids for s in world.spoke_store.snapshot_get(rid))


@pytest.mark.requirement("TC-NCON-180")
@pytest.mark.priority("MVP")
def test_tc_ncon_180_deletions_propagate_both_ways_and_tombstones_wait_for_the_ack(world):
    """NCON-180: a Master deletion removes the row on the spoke; a spoke deletion removes it on the
    Master; the Master's tombstones are released only once the spoke has acknowledged them."""
    seed_master(world)
    world.sync.run_once(world)
    with world.side("master"):
        database.execute_sql("DELETE FROM device_configs")
    world.sync.run_once(world)
    assert q(world, "spoke", "SELECT * FROM device_configs") == []
    with world.side("spoke"):
        database.execute_sql("DELETE FROM piers")                                 # cascades; the pier is the change
    r = world.sync.run_once(world)
    assert r.pushed == 1 and q(world, "master", "SELECT * FROM piers") == []
    with world.side("spoke"):
        assert tracking.tombstones("piers") == []                                 # accepted: no longer pending
    with world.side("master"):
        assert len(tracking.tombstones("piers")) == 1
        assert world.master.prune_tombstones(["never-synced-node"]) == 0          # that node holds them back
        world.master.prune_tombstones(["spoke-1"])
        assert len(tracking.tombstones("piers")) == 1       # spoke has not yet seen it
    world.sync.run_once(world)                                                    # it pulls its own commit and acks
    with world.side("master"):
        assert world.master.prune_tombstones(["spoke-1"]) >= 1
        assert tracking.tombstones("piers") == []


@pytest.mark.requirement("TC-NCON-305")
@pytest.mark.priority("MVP")
def test_tc_ncon_305_an_incompatible_protocol_blocks_the_sync_with_a_reason(world):
    """NCON-305: a Connect Link protocol mismatch refuses before anything is exchanged."""
    seed_master(world)
    world.master_info["protocol"] = 2
    r = world.sync.run_once(world)
    assert r.state == BLOCKED and "update Galileo" in r.message
    assert q(world, "spoke", "SELECT * FROM piers") == [] and not world.sync.last_rev()


@pytest.mark.requirement("TC-NCON-305")
@pytest.mark.priority("MVP")
def test_tc_ncon_305_an_older_schema_syncs_only_the_tables_both_understand(world):
    """NCON-305: when the schema levels differ, tables whose columns differ are skipped and reported."""
    seed_master(world)
    world.master_info["schema"] = 30
    with world.side("master"):
        database.execute_sql("ALTER TABLE piers ADD COLUMN future_column TEXT")
    r = world.sync.run_once(world)
    assert r.state == IDLE and "piers" in r.skipped_tables and "device_configs" not in r.skipped_tables
    assert q(world, "spoke", "SELECT * FROM piers") == []


@pytest.mark.requirement("TC-NCON-200")
@pytest.mark.priority("MVP")
def test_tc_ncon_200_changes_received_during_a_session_wait_and_nothing_is_skipped(world):
    """NCON-200 / NCON-120: while a session runs the pull is staged, last_rev does not advance, and
    nothing is lost; when the session ends the staged changes apply."""
    seed_master(world)
    world.busy = True
    r = world.sync.run_once(world)
    assert r.deferred and not any(world.sync.last_rev().values()) and q(world, "spoke", "SELECT * FROM piers") == []
    world.busy = False
    world.callbacks[0]()                                                      # the session ended
    assert q(world, "spoke", "SELECT name FROM piers")[0]["name"] == "Pier 1"
    again = world.sync.run_once(world)
    assert not again.deferred and again.state == IDLE and world.sync.last_rev()["piers"] == 1


@pytest.mark.requirement("TC-NCON-300")
@pytest.mark.priority("MVP")
def test_tc_ncon_300_a_failure_backs_off_without_raising_and_recovers(world):
    """NCON-300 / NCON-530: a dead link yields a backoff result, never an exception; delays grow from
    5 s to a 15 min ceiling with jitter; the next pass after recovery works."""
    seed_master(world)
    world.fail = True
    r = world.sync.run_once(world)
    assert r.state == BACKOFF and "link down" in r.message and world.sync.failures == 1
    world.sync.run_once(world)
    delays = [SpokeSync.backoff_delay(n, rand=lambda: 0.5) for n in (1, 2, 3, 20)]
    assert delays == [5.0, 10.0, 20.0, 900.0]
    assert 3.75 <= SpokeSync.backoff_delay(1, rand=lambda: 0.0) < SpokeSync.backoff_delay(1, rand=lambda: 1.0) <= 6.25
    world.fail = False
    assert world.sync.run_once(world).state == IDLE and world.sync.failures == 0
