# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Frame ingest on the Master and the spoke's confirm step (TC-NCON-410)."""

import asyncio
from pathlib import Path

import pytest
from test_ncon_transfer import (  # noqa: F401
    FakeLibrary,
    FakeUploader,
    store,
    worker,
)

from nodeconnect import transfer as tr
from nodeconnect.ingest import FrameIngest
from nodeconnect.wire import WireError


@pytest.mark.requirement("TC-NCON-410")
@pytest.mark.priority("MVP")
def test_tc_ncon_410_frame_is_done_and_freed_only_after_the_master_catalogues_it(store, tmp_path):  # noqa: F811
    """NCON-410/460: an uploaded frame the Master could not catalogue stays queued and keeps its local copy."""
    lib = FakeLibrary(tmp_path, n=1)
    up = FakeUploader(lib)
    up.ingest_fail = "422"
    tr.scan(store, lib, "peer")
    on = tr.Limits(remove_after_receipt=True)
    assert asyncio.run(worker(store, lib, up, limits=lambda: on).drain()) == 0
    assert lib.deleted == [] and tr.queue_counts(store, "peer")["items"] == 1
    up.ingest_fail = None
    assert asyncio.run(worker(store, lib, up, limits=lambda: on).drain()) == 1
    assert lib.deleted == ["id0"]


@pytest.mark.requirement("TC-NCON-410")
@pytest.mark.priority("MVP")
def test_tc_ncon_410_master_ingests_through_the_library_once(tmp_path):
    """NCON-410/430/470: the Master registers an uploaded object via the Library path with a safe file name;
    a hash it already holds is acknowledged, not added again; a missing object is refused."""
    sha = "ab" * 32
    (tmp_path / sha).write_bytes(b"SIMPLE")
    registered, held = [], set()

    def register(directory, name):
        registered.append((name, (Path(directory) / name).read_bytes()))
        held.add(sha)
        return "frame-1"
    ing = FrameIngest(lambda h: tmp_path / h, register, lambda h: h in held, tmp_path)
    out = ing.ingest({"hash": sha, "name": "../../evil/m31.fits"})
    assert out["ingested"] and out["frame_id"] == "frame-1" and registered == [("m31.fits", b"SIMPLE")]
    assert ing.ingest({"hash": sha})["duplicate"] and len(registered) == 1
    with pytest.raises(WireError) as e:
        ing.ingest({"hash": "cd" * 32})
    assert e.value.status == 404
