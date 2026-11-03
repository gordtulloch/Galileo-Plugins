# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect run reports (TC-NCON-320): written for every sync, readable, kept under a limit, undoable."""

import pytest
from galileo.library.models.base import db as database

from nodeconnect import reports
from nodeconnect.apply import DELETE, UPSERT, Applier, Change
from nodeconnect.store import NodeStore


@pytest.fixture
def db(tmp_path):
    from galileo.library.database import init_db
    init_db(tmp_path / "nc.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)


def obs(name="Home", rev=3):
    return Change("observatories", UPSERT, [name], {"name": name, "timezone": "UTC"}, rev)


def pier(name="Pier 1", observatory="Home", rev=4):
    return Change("piers", UPSERT, [observatory, name],
                  {"observatory": observatory, "name": name, "connect_on_startup": 1}, rev)


def finish(store, run_id, outcome="ok", summary=None):
    store.report_start(run_id, "peer")
    store.report_finish(run_id, outcome, summary or {"applied": {"piers": 2}, "deleted": {}})


@pytest.mark.requirement("TC-NCON-320")
@pytest.mark.priority("MVP")
def test_tc_ncon_320_reports_are_kept_under_the_limit_with_their_snapshots(db):
    """NCON-320: reports are retained under a configurable limit, newest first; the before-copies of a run whose
    report was removed go with it, and those of a kept run stay."""
    store = NodeStore(retention=lambda: 3)
    applier = Applier(store)
    applier.apply([obs(), pier()])
    old = applier.apply([Change("piers", DELETE, ["Home", "Pier 1"])])             # leaves a snapshot
    finish(store, old.run_id, summary=old.summary())
    assert store.has_snapshots(old.run_id)
    for n in range(3):
        finish(store, f"run-{n}")
    kept = [r["run_id"] for r in store.reports(50)]
    assert kept == ["run-2", "run-1", "run-0"]                                      # the oldest was pruned
    assert not store.has_snapshots(old.run_id)                                      # and its snapshots with it

    store2 = NodeStore()
    assert store2.prune(2) == 1 and [r["run_id"] for r in store2.reports(50)] == ["run-2", "run-1"]
    assert store2.prune(10) == 0


@pytest.mark.requirement("TC-NCON-320")
@pytest.mark.priority("MVP")
def test_tc_ncon_320_retention_setting_is_clamped_and_reports_read_well():
    """NCON-320: the limit is clamped to a sane range; a report reads as counts per table, skipped and failed work,
    and never shows a secret's value."""
    class Settings(dict):
        pass

    assert reports.retention(None) == reports.DEFAULT_RETENTION
    assert reports.retention(Settings()) == reports.DEFAULT_RETENTION
    assert reports.retention(Settings(report_retention=1)) == reports.MIN_RETENTION
    assert reports.retention(Settings(report_retention=10**6)) == reports.MAX_RETENTION
    assert reports.retention(Settings(report_retention="40")) == 40
    assert reports.retention(Settings(report_retention="many")) == reports.DEFAULT_RETENTION

    row = {"run_id": "r", "started_at": 0.0, "outcome": "partial", "summary": {
        "applied": {"piers": 2, "device_configs": 1}, "deleted": {"piers": 1}, "skipped_dirty": ["piers: East"],
        "deferred": [], "errors": {"profiles": "locked"}, "secrets_changed": ["smtp_password"]}}
    line = reports.headline(row)
    assert "partial" in line and "3 applied" in line and "1 deleted" in line and "1 left to the Master" in line
    assert "1 failed" in line
    text = reports.detail(row)
    assert "device_configs 1, piers 2" in text and "Failed profiles: locked" in text
    assert "Secrets changed (names only): smtp_password" in text
    assert reports.headline({"summary": None, "started_at": "bad"}).startswith("unknown time")


@pytest.mark.requirement("TC-NCON-320")
@pytest.mark.priority("MVP")
def test_tc_ncon_320_a_run_can_be_undone_once(db):
    """NCON-320 / NCON-220: undoing a run puts its rows back and is recorded, so the list can offer it only once."""
    store = NodeStore()
    applier = Applier(store)
    applier.apply([obs(), pier()])
    report = applier.apply([Change("piers", DELETE, ["Home", "Pier 1"])])
    finish(store, report.run_id, summary=report.summary())
    assert not reports.undone(store, report.run_id)
    assert reports.undo(store, report.run_id) == 1
    assert reports.undone(store, report.run_id)
    cur = database.execute_sql("SELECT name FROM piers")
    assert [r[0] for r in cur.fetchall()] == ["Pier 1"]
    assert reports.undo(store, "no-such-run") == 0                                  # nothing kept: nothing to put back
