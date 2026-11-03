# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect frame transfer (TC-NCON-410, -420, -430, -440, -450, -460): durable queue, scan, drain,
limits and removal, with a fake Library and uploader over core's real queue."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from galileo.library.models.base import db as database
from galileo.replication import SyncStore

from nodeconnect import transfer as tr


@pytest.fixture
def store(tmp_path):
    from galileo.library.database import init_db
    init_db(tmp_path / "t.db")
    yield SyncStore()
    if not database.is_closed():
        database.close()
    database.init(None)


class FakeLibrary:
    def __init__(self, tmp_path, n=2):
        self.frames = {}
        for i in range(n):
            p = tmp_path / f"f{i}.fits"
            p.write_bytes(bytes([i]) * 10)
            sha = f"{i:064x}"
            self.frames[sha] = tr.LocalFrame(f"id{i}", p, sha, 10)
        self.marked, self.deleted, self.used = [], [], False

    def local_frames(self):
        return list(self.frames.values())

    def frame_for_hash(self, sha):
        return self.frames.get(sha)

    def in_use(self, frame_id):
        return self.used

    def mark_on_master(self, frame_id, holder, verified_hash):
        self.marked.append((frame_id, holder, verified_hash))

    def delete_local_copy(self, frame_id):
        self.deleted.append(frame_id)
        return True


class FakeUploader:
    ingest_fail = None

    async def ingest(self, sha256, name):
        if self.ingest_fail:
            raise RuntimeError(self.ingest_fail)

    def __init__(self, library, fail=None):
        self.lib, self.fail, self.sent = library, fail, []

    async def upload(self, path: Path, *, progress=None):
        if self.fail:
            raise ConnectionError(self.fail)
        self.sent.append(path.name)
        return next(s for s, f in self.lib.frames.items() if f.path == path)


def worker(store, lib, up, **kw):
    return tr.TransferWorker(store, lib, up, "peer", "node-1", **kw)


@pytest.mark.requirement("TC-NCON-420")
@pytest.mark.priority("MVP")
def test_tc_ncon_420_scan_queues_each_frame_once(store, tmp_path):
    """NCON-420: a scan queues local frames durably, and a repeated scan queues nothing twice."""
    lib = FakeLibrary(tmp_path)
    assert tr.scan(store, lib, "peer") == 2
    assert tr.scan(store, lib, "peer") == 0
    assert tr.queue_counts(store, "peer") == {"items": 2, "bytes": 20}


@pytest.mark.requirement("TC-NCON-430")
@pytest.mark.priority("MVP")
def test_tc_ncon_430_drain_sends_and_verifies_each_frame_once(store, tmp_path):
    """NCON-430: every queued frame is uploaded and completed once; nothing is left pending."""
    lib = FakeLibrary(tmp_path)
    up = FakeUploader(lib)
    tr.scan(store, lib, "peer")
    assert asyncio.run(worker(store, lib, up).drain()) == 2
    assert sorted(up.sent) == ["f0.fits", "f1.fits"]
    assert tr.queue_counts(store, "peer")["items"] == 0
    assert asyncio.run(worker(store, lib, up).drain()) == 0
    assert lib.deleted == []                                  # removal is off by default


@pytest.mark.requirement("TC-NCON-440")
@pytest.mark.priority("MVP")
def test_tc_ncon_440_unreachable_master_keeps_the_queue_and_session_has_priority(store, tmp_path):
    """NCON-440: a failed upload leaves the frame queued for the next pass; a running session pauses transfer."""
    lib = FakeLibrary(tmp_path)
    tr.scan(store, lib, "peer")
    w = worker(store, lib, FakeUploader(lib, fail="no route"))
    assert asyncio.run(w.drain()) == 0
    assert "no route" in w.last_error
    assert tr.queue_counts(store, "peer")["items"] == 2
    assert asyncio.run(worker(store, lib, FakeUploader(lib), session_busy=lambda: True).drain()) == 0
    assert asyncio.run(worker(store, lib, FakeUploader(lib)).drain()) == 2          # drains on reconnection


@pytest.mark.requirement("TC-NCON-450")
@pytest.mark.priority("P2")
def test_tc_ncon_450_allowed_hours_window():
    """NCON-450: transfer runs only inside the allowed hours, including a window that crosses midnight."""
    at = lambda h: datetime(2026, 1, 1, h)  # noqa: DTZ001
    assert tr.Limits().open_now(at(3))
    day = tr.Limits(window=(9, 17))
    assert day.open_now(at(10)) and not day.open_now(at(20))
    night = tr.Limits(window=(22, 6))
    assert night.open_now(at(23)) and night.open_now(at(2)) and not night.open_now(at(12))


@pytest.mark.requirement("TC-NCON-460")
@pytest.mark.priority("MVP")
def test_tc_ncon_460_local_copy_removed_only_when_enabled_verified_and_not_in_use(store, tmp_path):
    """NCON-460: with the option on, a verified frame is marked On Master then removed; never while in use,
    and never when the Master reported a different hash."""
    lib = FakeLibrary(tmp_path)
    tr.scan(store, lib, "peer")
    on = tr.Limits(remove_after_receipt=True)
    lib.used = True
    asyncio.run(worker(store, lib, FakeUploader(lib), limits=lambda: on).drain())
    assert lib.deleted == []                                  # in use: kept (and not marked)
    tr.scan(store, lib, "peer")                               # the done rows stay done; requeue one by hand
    database.execute_sql("UPDATE sync_transfer_queue SET state = 'queued'")
    lib.used = False
    asyncio.run(worker(store, lib, FakeUploader(lib), limits=lambda: on).drain())
    assert sorted(lib.deleted) == ["id0", "id1"]
    assert all(m[1] == "node-1" for m in lib.marked)

    class Wrong(FakeUploader):
        async def upload(self, path, *, progress=None):
            return "f" * 64
    lib2 = FakeLibrary(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, n=1)
    database.execute_sql("DELETE FROM sync_transfer_queue")
    tr.scan(store, lib2, "peer")
    asyncio.run(worker(store, lib2, Wrong(lib2), limits=lambda: on).drain())
    assert lib2.deleted == []


@pytest.mark.requirement("TC-NCON-450")
@pytest.mark.priority("P2")
def test_tc_ncon_450_limits_come_from_settings(settings_db):
    """NCON-450/500: the window and the removal option are read from the plugin settings, with nonsense ignored."""
    from galileo import settings_store
    from galileo.plugins import PluginSettings
    st = PluginSettings("plugin.NodeConnect")
    assert tr.limits_from(st) == tr.Limits(None, False)
    settings_store.set_setting("plugin.NodeConnect", "transfer_from_hour", 22)
    settings_store.set_setting("plugin.NodeConnect", "transfer_to_hour", 30)       # clamped into a day
    settings_store.set_setting("plugin.NodeConnect", "remove_after_receipt", True)
    assert tr.limits_from(st) == tr.Limits((22, 6), True)
