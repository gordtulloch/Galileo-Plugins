# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""History transfer (TC-NCON-400): idempotent, resumable, collision-free across nodes."""

import pytest
from galileo.history import SessionHistory

from nodeconnect.history import HistoryEndpoint, HistoryPush


class MemState:
    def __init__(self):
        self.d = {}

    def peer_state_get(self, peer, key, default=None):
        return self.d.get((peer, key), default)

    def peer_state_set(self, peer, key, value):
        self.d[(peer, key)] = value


def seed(h, session, stamps):
    from galileo.history import FrameRecord, SessionRecord
    h._sessions[session] = SessionRecord(session, [FrameRecord(t, 2.0, 50) for t in stamps])


@pytest.mark.requirement("TC-NCON-400")
@pytest.mark.priority("MVP")
def test_tc_ncon_400_history_is_sent_once_resumes_and_never_collides(tmp_path):
    """NCON-400: only new records are sent after each acknowledgement, a repeat produces one record, a failed
    batch is resent, and two nodes' records of the same session name stay apart on the Master."""
    master = SessionHistory(tmp_path / "m.json")
    endpoint = HistoryEndpoint(master, lambda: "master")
    wire = []

    def send(path, body):
        wire.append(len(body["records"]))
        return endpoint.receive(body)

    a, b = SessionHistory(tmp_path / "a.json"), SessionHistory(tmp_path / "b.json")
    seed(a, "M31", ["2026-01-01T01:00", "2026-01-01T02:00", "2026-01-01T03:00"])
    seed(b, "M31", ["2026-01-01T01:00"])
    state = MemState()
    assert HistoryPush(a, state, "m", "node-a", send, batch=2).run() == 3
    assert wire == [2, 1]
    assert HistoryPush(a, state, "m", "node-a", send).run() == 0                   # nothing new
    assert HistoryPush(b, MemState(), "m", "node-b", send).run() == 1
    assert len([s for s in master._sessions]) == 2                                  # M31 from a and from b apart

    seed(a, "M31", ["2026-01-01T01:00", "2026-01-01T02:00", "2026-01-01T03:00", "2026-01-01T04:00"])
    assert HistoryPush(a, state, "m", "node-a", send).run() == 1

    # a record sent again (cursor lost) is an upsert, not a duplicate
    HistoryPush(a, MemState(), "m", "node-a", send).run()
    assert sum(s.frame_count for s in master._sessions.values()) == 5

    def fail(path, body):
        return {"ok": False}
    seed(a, "M31", ["2026-01-01T01:00", "2026-01-01T05:00"])
    s2 = MemState()
    s2.peer_state_set("m", "history_since", "2026-01-01T04:00")
    assert HistoryPush(a, s2, "m", "node-a", fail).run() == 0
    assert s2.peer_state_get("m", "history_since") == "2026-01-01T04:00"            # cursor unmoved
