# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect logic modules (TC-NCON-020, -100, -110, -160, -170, -180, -210, -305).

Written against core's real replication registry and database (``galileo.replication``); the
plugin shell, sync engine, apply step and transfer worker are not built yet, so their TC-NCON IDs
have no tests."""

import pytest
from galileo.replication import ReplicableSpec, registry

from nodeconnect import compat, conflict
from nodeconnect.codec import PAGE_SIZE, ItemFilter, encode_pages, merge_fields
from nodeconnect.roles import MASTER, SPOKE, STANDALONE, RoleError, RoleGuard


@pytest.fixture
def db(tmp_path):
    from galileo.library.database import db as database
    from galileo.library.database import init_db
    init_db(tmp_path / "nc.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)


# -- roles (NCON-020) -------------------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-020")
@pytest.mark.priority("MVP")
def test_tc_ncon_020_a_node_is_never_both_spoke_and_master():
    """NCON-020: the role is derived from facts; designating a Master is refused while spokes are
    paired, and approving a spoke is refused while a Master is designated."""
    state = {"master": None, "spokes": 0}
    guard = RoleGuard(lambda: state["master"], lambda: state["spokes"])
    assert guard.role() == STANDALONE
    guard.check_designate_master("pi-master.local:8765")
    with pytest.raises(RoleError):
        guard.check_designate_master("  ")
    state["master"] = "pi-master.local:8765"
    assert guard.role() == SPOKE
    with pytest.raises(RoleError, match="spoke"):
        guard.check_approve_spoke()
    state["master"], state["spokes"] = None, 2
    assert guard.role() == MASTER
    with pytest.raises(RoleError, match="Master for other nodes"):
        guard.check_designate_master("other:8765")
    guard.check_approve_spoke()


def make_pier():
    from galileo.library.models import ObservatoryRecord, PierRecord
    return PierRecord.create(observatory=ObservatoryRecord.create(name="Home", timezone="UTC"), name="Pier 1").id


# -- registry, filtering, codec (NCON-100, 110, 210) ------------------------------------------
@pytest.mark.requirement("TC-NCON-100")
@pytest.mark.priority("MVP")
def test_tc_ncon_100_everything_registered_replicates_in_dependency_order(db):
    """NCON-100: the replicable set is whatever core's registry holds, dependencies first, so a table
    registered later (a plugin's) is picked up with no change here."""
    names = [s.table for s in registry.specs()]
    assert "app_settings" in names and names.index("piers") < names.index("device_configs")

    def ident(row):
        return row
    registry.register(ReplicableSpec("zz_plugin_table", ("id",), depends_on=("piers",), to_wire=ident, from_wire=ident))
    try:
        later = [s.table for s in registry.specs()]
        assert later.index("piers") < later.index("zz_plugin_table")
    finally:
        registry.unregister("zz_plugin_table")


@pytest.mark.requirement("TC-NCON-110")
@pytest.mark.priority("MVP")
def test_tc_ncon_110_node_local_items_stay_home_unless_opted_in(db):
    """NCON-110: the link's own settings are never sent, a node-local field is stripped from the wire,
    and an explicit opt-in lets one item through; device addresses do replicate."""
    from galileo import settings_store
    from galileo.replication import tracking
    settings_store.set_setting("connect_link", "port", 9000)
    settings_store.set_setting("imaging", "gain", 100)
    spec = registry.get("app_settings")
    rows = tracking.dirty_rows("app_settings")
    pages = list(encode_pages(spec, rows, ItemFilter()))
    assert [w["namespace"] for p in pages for w in p] == ["imaging"]
    opted = list(encode_pages(spec, rows, ItemFilter(["app_settings:*"])))
    assert {w["namespace"] for p in opted for w in p} == {"connect_link", "imaging"}
    # a node-local field in a wire row is removed unless opted in
    dev = registry.get("device_configs")
    row = {"id": 1, "pier_id": make_pier(), "category": "c", "slot": "s", "driver": "d", "server": "h", "port": 1,
           "focuser_position": 5, "dirty": 1, "base_rev": 0}
    assert "focuser_position" not in ItemFilter().wire_of(dev, row) and ItemFilter().wire_of(dev, row)["server"] == "h"
    assert ItemFilter(["device_configs:focuser_position"]).wire_of(dev, row)["focuser_position"] == 5
    assert not {"id", "dirty", "base_rev"} & ItemFilter().wire_of(dev, row).keys()


@pytest.mark.requirement("TC-NCON-210")
@pytest.mark.priority("MVP")
def test_tc_ncon_210_secrets_are_never_sent_or_merged_unless_opted_in(db):
    """NCON-210: a secret field is stripped from outgoing rows and ignored on incoming ones."""
    solver = registry.get("solver_settings")
    row = {"id": 1, "pier_id": make_pier(), "backend": "astap", "nova_api_key": "K"}
    assert "nova_api_key" not in ItemFilter().wire_of(solver, row)
    assert ItemFilter(["solver_settings:nova_api_key"]).wire_of(solver, row)["nova_api_key"] == "K"
    local = {"id": 1, "backend": "astrometry", "nova_api_key": "mine", "executable": "C:/a.exe"}
    incoming = {"backend": "astap", "nova_api_key": "theirs", "executable": "/usr/bin/astap"}
    merged = merge_fields(solver, local, incoming)
    assert merged == {"id": 1, "backend": "astap", "nova_api_key": "mine", "executable": "C:/a.exe"}
    assert merge_fields(solver, local, incoming, ItemFilter(["solver_settings:nova_api_key"]))["nova_api_key"] == "theirs"
    assert "nova_api_key" not in merge_fields(solver, None, incoming)                 # not even on insert
    assert merge_fields(solver, None, incoming)["executable"] == "/usr/bin/astap"     # node-local: insert default only


@pytest.mark.requirement("TC-NCON-100")
@pytest.mark.priority("MVP")
def test_tc_ncon_100_rows_travel_in_pages_without_building_the_table(db):
    """NCON-100 / PC1: pages hold at most 200 rows and a generator is consumed lazily."""
    spec = registry.get("session_templates")
    consumed = []

    def rows():
        for i in range(450):
            consumed.append(i)
            yield {"id": i, "name": f"t{i}", "blocks_json": "[]", "modified": 0.0, "rev": 0, "base_rev": 0, "dirty": 1}
    pages = encode_pages(spec, rows(), ItemFilter())
    first = next(pages)
    assert len(first) == PAGE_SIZE == 200 and len(consumed) == 200
    assert [len(p) for p in pages] == [200, 50]


# -- arbitration, clock, tombstones (NCON-160, 170, 180) --------------------------------------
@pytest.mark.requirement("TC-NCON-160")
@pytest.mark.priority("MVP")
def test_tc_ncon_160_clock_offset_is_measured_from_a_round_trip():
    """NCON-160: the offset between a node's clock and the Master's comes from one round trip, with
    its error bound, and corrects a node's timestamps."""
    off = conflict.ClockOffset.measure(t0=100.0, t1=1100.2, t2=1100.3, t3=100.5)    # node 1000 s behind
    assert off.seconds == pytest.approx(1000.0, abs=0.01) and off.error == pytest.approx(0.2, abs=0.01)
    assert off.to_master(200.0) == pytest.approx(1200.0, abs=0.01)


@pytest.mark.requirement("TC-NCON-170")
@pytest.mark.priority("MVP")
def test_tc_ncon_170_stale_base_conflicts_later_edit_wins_ties_go_to_master():
    """NCON-170: a matching base revision commits; a stale one conflicts, the later offset-corrected
    edit wins, a tie goes to the Master, and the loser is named so it can be snapshotted."""
    none = conflict.ClockOffset(0.0, 0.0)
    assert conflict.arbitrate(4, 4, 10.0, 99.0, none) == conflict.Decision("commit", "node")
    node_wins = conflict.arbitrate(3, 4, 200.0, 100.0, none)
    assert (node_wins.kind, node_wins.winner, node_wins.loser) == ("conflict", "node", "master")
    master_wins = conflict.arbitrate(3, 4, 100.0, 200.0, none)
    assert (master_wins.winner, master_wins.loser) == ("master", "node")
    assert conflict.arbitrate(3, 4, 150.0, 150.0, none).winner == "master"
    # a node whose clock runs 1000 s slow: its raw 500 looks old, but corrected (1500) it is later
    slow = conflict.ClockOffset(1000.0, 0.1)
    assert conflict.arbitrate(3, 4, 500.0, 1000.0, slow).winner == "node"
    assert conflict.arbitrate(3, 4, 500.0, 1000.0, none).winner == "master"


@pytest.mark.requirement("TC-NCON-180")
@pytest.mark.priority("MVP")
def test_tc_ncon_180_tombstones_wait_for_the_slowest_paired_node():
    """NCON-180: tombstones may be released only up to what every paired node has acknowledged; a
    revoked node still holds them back until it is deleted."""
    acks = {"a": 40, "b": 12, "revoked": 5}
    assert conflict.releasable_up_to(acks) == 5
    del acks["revoked"]                                             # the node was deleted
    assert conflict.releasable_up_to(acks) == 12
    assert conflict.releasable_up_to({}) == 0


# -- handshake policy (NCON-305) --------------------------------------------------------------
@pytest.mark.requirement("TC-NCON-305")
@pytest.mark.priority("MVP")
def test_tc_ncon_305_full_partial_or_refused_by_version():
    """NCON-305: equal schema syncs everything; an older node syncs only matching tables and reports
    the rest; an incompatible protocol is refused naming what to upgrade."""
    fp = {"app_settings": compat.column_fingerprint(["id", "key", "value"]),
          "piers": compat.column_fingerprint(["id", "name"])}
    same = {"protocol": 1, "schema": 35}
    assert compat.decide(same, same, fp, fp).mode == compat.FULL
    older = {"protocol": 1, "schema": 30}
    remote_fp = {"app_settings": fp["app_settings"], "piers": compat.column_fingerprint(["id", "name", "extra"])}
    p = compat.decide(same, older, fp, remote_fp)
    assert p.mode == compat.PARTIAL and p.tables == ("app_settings",) and "piers" in p.skipped and "30" in p.message
    missing = compat.decide(same, older, fp, {"app_settings": fp["app_settings"]})
    assert "does not have" in missing.skipped["piers"]
    refused = compat.decide({"protocol": 1, "schema": 35}, {"protocol": 2, "schema": 35}, fp, fp)
    assert refused.mode == compat.REFUSE and "update Galileo on this node" in refused.message
    assert "the Master" in compat.decide({"protocol": 2, "schema": 1}, {"protocol": 1, "schema": 1}, fp, fp).message
    assert compat.column_fingerprint(["b", "a"]) == compat.column_fingerprint(["a", "b"])
