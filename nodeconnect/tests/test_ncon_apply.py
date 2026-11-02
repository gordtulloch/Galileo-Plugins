# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect apply step (TC-NCON-190, -200, -220, -230): staging, session gate, per-table
transactions, snapshots and undo, run reports. Real core database and replication registry."""

import pytest
from galileo.library.models.base import db as database
from galileo.replication import SyncStore, tracking

from nodeconnect.apply import DELETE, UPSERT, Applier, ApplyGate, Change, restore


@pytest.fixture
def db(tmp_path):
    from galileo.library.database import init_db
    init_db(tmp_path / "nc.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)


@pytest.fixture
def store(db):
    return SyncStore()


def obs(name="Home", rev=3):
    return Change("observatories", UPSERT, [name], {"key": [name], "fields": {"timezone": "UTC"}}, rev)


def pier(name="Pier 1", observatory="Home", rev=4):
    return Change("piers", UPSERT, [observatory, name],
                  {"key": [observatory, name], "fields": {"connect_on_startup": 1}}, rev)


def device(driver="indi_asi", host="pi.local", port=7624, rev=5, key=("Home", "Pier 1", "camera", "primary")):
    return Change("device_configs", UPSERT, list(key), {"key": list(key), "fields": {
        "driver": driver, "server": host, "port": port}}, rev)


def rows(sql, *p):
    cur = database.execute_sql(sql, p)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


@pytest.mark.requirement("TC-NCON-190")
@pytest.mark.priority("MVP")
def test_tc_ncon_190_changes_apply_parents_first_clean_and_untracked(db, store):
    """NCON-190: received changes, given in any order, apply in dependency order; the rows carry the
    Master's revision, are not dirty and leave no tombstones, so they are never shipped back."""
    seen = []
    report = Applier(store, on_changed=seen.append).apply([device(), pier(), obs()])
    assert report.outcome == "ok" and report.applied == {"observatories": 1, "piers": 1, "device_configs": 1}
    (d,) = rows("SELECT * FROM device_configs")
    assert (d["driver"], d["server"], d["rev"], d["base_rev"], d["dirty"]) == ("indi_asi", "pi.local", 5, 5, 0)
    assert tracking.dirty_rows("device_configs") == [] and tracking.dirty_rows("observatories") == []
    assert seen == [["observatories", "piers", "device_configs"]]
    assert store.reports()[0]["outcome"] == "ok"


@pytest.mark.requirement("TC-NCON-190")
@pytest.mark.priority("MVP")
def test_tc_ncon_190_one_table_failing_rolls_back_alone(db, store):
    """NCON-190 / NCON-305: a table whose apply fails leaves none of its rows behind and does not
    stop the others; the failure is in the report."""
    bad = device(key=("Home", "Pier 1", "mount", "primary"))
    bad.wire["fields"]["driver"] = None                              # violates NOT NULL
    report = Applier(store).apply([obs(), pier(), device(), bad])
    assert report.outcome == "partial" and "device_configs" in report.errors
    assert rows("SELECT * FROM device_configs") == []                # the good device rolled back with it
    assert len(rows("SELECT * FROM piers")) == 1 and "device_configs" not in report.applied


@pytest.mark.requirement("TC-NCON-190")
@pytest.mark.priority("MVP")
def test_tc_ncon_190_missing_parent_is_deferred_and_a_local_edit_is_not_overwritten(db, store):
    """NCON-190: a row whose parent is absent is reported, not inserted dangling; a row with an
    unacknowledged local edit is left for the Master to arbitrate."""
    report = Applier(store).apply([device(), obs()])
    assert rows("SELECT * FROM device_configs") == [] and "Pier 1" in report.deferred[0]
    Applier(store).apply([pier(), device(driver="a")])
    database.execute_sql("UPDATE device_configs SET driver = 'mine'")        # a local edit: dirty
    again = Applier(store).apply([device(driver="theirs", rev=9)])
    assert rows("SELECT driver FROM device_configs")[0]["driver"] == "mine" and len(again.skipped_dirty) == 1


@pytest.mark.requirement("TC-NCON-220")
@pytest.mark.priority("MVP")
def test_tc_ncon_220_replaced_and_deleted_values_are_snapshotted_and_restorable(db, store):
    """NCON-220: every value replaced or deleted is kept; restoring a run writes it back as ordinary
    local edits, which therefore replicate."""
    Applier(store).apply([obs(), pier(), device(driver="old")])
    report = Applier(store).apply([device(driver="new", rev=7), Change("piers", DELETE, ["Home", "Pier 1"])])
    assert rows("SELECT driver FROM device_configs") == []                   # cascaded with the pier
    assert report.deleted["piers"] == 1
    snaps = store.snapshot_get(report.run_id)
    assert [s["data"]["table"] for s in snaps] == ["device_configs", "device_configs", "piers"]
    assert restore(store, report.run_id) == 3                                # pier, and its device twice
    assert rows("SELECT name FROM piers")[0]["name"] == "Pier 1"
    (dev,) = rows("SELECT driver, pier_id FROM device_configs")
    assert dev["driver"] == "old" and dev["pier_id"] == rows("SELECT id FROM piers")[0]["id"]
    assert {d["name"] for d in tracking.dirty_rows("piers")} == {"Pier 1"}   # restore is a local edit
    assert [d["driver"] for d in tracking.dirty_rows("device_configs")] == ["old"]


@pytest.mark.requirement("TC-NCON-110")
@pytest.mark.priority("MVP")
def test_tc_ncon_110_apply_never_rewrites_node_local_values_or_receives_secrets(db, store):
    """NCON-110 / NCON-210: a received solver row keeps this node's executable path and API key."""
    Applier(store).apply([obs(), pier()])
    pid = rows("SELECT id FROM piers")[0]["id"]
    database.execute_sql("INSERT INTO solver_settings (pier_id, executable, nova_api_key, backend) "
                         "VALUES (?, 'C:/a.exe', 'KEY', 'astap')", (pid,))
    database.execute_sql("UPDATE solver_settings SET dirty = 0")
    wire = {"key": ["Home", "Pier 1"], "fields": {"backend": "astrometry", "executable": "/x", "nova_api_key": "THEIRS"}}
    report = Applier(store).apply([Change("solver_settings", UPSERT, ["Home", "Pier 1"], wire, 8)])
    (s,) = rows("SELECT * FROM solver_settings")
    assert (s["backend"], s["executable"], s["nova_api_key"]) == ("astrometry", "C:/a.exe", "KEY")
    assert report.secrets_changed and all("KEY" not in str(snap) for snap in store.snapshot_get(report.run_id))


@pytest.mark.requirement("TC-NCON-230")
@pytest.mark.priority("MVP")
def test_tc_ncon_230_loopback_addresses_are_applied_but_flagged(db, store):
    """NCON-230: a received device address that means this machine is applied as received and listed
    in the run report."""
    report = Applier(store).apply([obs(), pier(), device(host="localhost")])
    assert rows("SELECT server FROM device_configs")[0]["server"] == "localhost"
    assert len(report.loopback) == 1 and "server=localhost" in report.loopback[0]


@pytest.mark.requirement("TC-NCON-200")
@pytest.mark.priority("MVP")
def test_tc_ncon_200_a_running_session_stages_changes_and_they_apply_when_it_ends(db, store):
    """NCON-200: while a session runs received changes are staged durably (and can be acknowledged),
    nothing is applied, and they apply by themselves when the session ends."""
    state = {"busy": True}
    callbacks = []
    gate = ApplyGate(store, Applier(store), lambda: state["busy"], callbacks.append)
    assert gate.submit("master", [obs(), pier()]) is None
    assert len(store.inbox_pending()) == 2 and rows("SELECT * FROM observatories") == []
    gate.submit("master", [device()])                                       # more arrives: still one wait
    assert len(callbacks) == 1 and len(store.inbox_pending()) == 3
    state["busy"] = False
    callbacks[0]()                                                          # the session ended
    assert store.inbox_pending() == [] and len(rows("SELECT * FROM device_configs")) == 1
    report = gate.submit("master", [obs("Other")])                          # idle: applies at once
    assert report is not None and report.applied["observatories"] == 1


@pytest.mark.requirement("TC-NCON-200")
@pytest.mark.priority("MVP")
def test_tc_ncon_200_staged_rows_of_a_failed_table_are_retried(db, store):
    """NCON-200 / NCON-305: rows of a table that rolled back stay staged for the next attempt."""
    gate = ApplyGate(store, Applier(store), lambda: False, lambda cb: None)
    bad = device()
    bad.wire["fields"]["driver"] = None
    gate.submit("master", [obs(), pier(), bad])
    assert [r["table_name"] for r in store.inbox_pending()] == ["device_configs"]
