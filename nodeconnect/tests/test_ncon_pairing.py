# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect pairing (TC-NCON-020, -030, -040, -050, -060, -180): the Master's desk over core's real
PeerStore and PairingService, and the spoke's client against a stand-in for the Master's two
unauthenticated routes that behaves like core's (same PairingService)."""

import pytest
from galileo import settings_store
from galileo.connect_link.pairing import PairingService
from galileo.connect_link.peers import PeerStore
from galileo.connect_link.secrets_store import MemorySecretStore
from galileo.library.database import init_db
from galileo.library.models.base import db as database

from nodeconnect.pairing import (
    NODE_PERMISSIONS,
    CredentialStore,
    PairingClient,
    PairingDesk,
    PairingFailed,
    Reply,
)
from nodeconnect.roles import RoleError, RoleGuard
from nodeconnect.store import NodeStore

FP = "ab" * 32


@pytest.fixture
def db(tmp_path):
    init_db(tmp_path / "p.db")
    yield database
    if not database.is_closed():
        database.close()
    database.init(None)


class MasterStandIn:
    """Answers the two pairing routes from a real PairingService, as core's server does."""

    def __init__(self, pairing, fingerprint=FP, presented=FP):
        self.pairing, self.fp, self.presented = pairing, fingerprint, presented

    def __call__(self, method, path, body):
        if method == "POST":
            req = self.pairing.request(body["plugin"], body["name"], body["kind"], body["platform"], "10.0.0.5")
            return Reply(202, {"request_id": req.request_id, "code": req.code, "fingerprint": self.fp}, self.presented)
        state, token = self.pairing.poll(path.rsplit("/", 1)[1])
        return Reply(200, {"state": state, **({"token": token} if token else {})}, self.presented)


class Node:
    def __init__(self, master_designated=False, spokes=0):
        self.secrets = MemorySecretStore()
        self.creds = CredentialStore(self.secrets, settings_store.get_setting, settings_store.set_setting,
                                     settings_store.delete_setting)
        self.roles = RoleGuard(lambda: self.creds.designated() if master_designated else None, lambda: spokes)


def make_master(spokes=0, is_spoke=False):
    peers = PeerStore()
    pairing = PairingService(peers)
    store = NodeStore()
    desk = PairingDesk(peers, pairing, store, Node(master_designated=is_spoke, spokes=spokes).roles)
    return peers, pairing, store, desk


@pytest.mark.requirement("TC-NCON-030")
@pytest.mark.priority("MVP")
def test_tc_ncon_030_050_nothing_until_approved_then_exactly_the_node_permissions(db):
    """NCON-030 / NCON-050: a request stays pending with no token until a person approves it on the
    Master; approval grants config.read/write and data.push/pull only, never control or view."""
    peers, pairing, _store, desk = make_master()
    spoke = Node()
    client = PairingClient(lambda addr: MasterStandIn(pairing), spoke.creds, spoke.roles, "Dome PC")
    pend = client.request("https://master.local:4400")
    assert len(pend.code) == 6 and client.poll(pend) == "pending" and spoke.creds.load() is None
    (shown,) = desk.pending()
    assert shown["code"] == pend.code and shown["name"] == "Dome PC" and "token" not in shown
    peer_id = desk.approve(shown["request_id"])
    assert set(peers.get(peer_id).permissions) == set(NODE_PERMISSIONS)
    assert client.poll(pend) == "approved"
    creds = spoke.creds.load()
    assert creds and creds.address == "https://master.local:4400" and creds.fingerprint == FP
    assert creds.token.startswith(peer_id)
    assert client.poll(pend) == "expired"                      # the token is collected once


@pytest.mark.requirement("TC-NCON-060")
@pytest.mark.priority("MVP")
def test_tc_ncon_060_token_is_in_the_credential_store_not_the_database(db):
    """NCON-060: the pairing token lives in the credential store; the node-local settings hold the
    address and fingerprint only, in a namespace core never replicates."""
    from galileo.replication.registry import NODE_LOCAL_NAMESPACES
    _peers, pairing, _store, desk = make_master()
    spoke = Node()
    client = PairingClient(lambda a: MasterStandIn(pairing), spoke.creds, spoke.roles, "n")
    pend = client.request("https://m:1")
    desk.approve(desk.pending()[0]["request_id"])
    client.poll(pend)
    token = spoke.creds.load().token
    rows = database.execute_sql("SELECT namespace, key, value FROM app_settings WHERE namespace = 'plugin.NodeConnect'").fetchall()
    assert {r[1] for r in rows} == {"master_address", "master_fingerprint"} and all(token not in str(r) for r in rows)
    assert "plugin.NodeConnect" in NODE_LOCAL_NAMESPACES
    spoke.creds.clear()
    assert spoke.creds.load() is None and spoke.creds.designated() is None


@pytest.mark.requirement("TC-NCON-030")
@pytest.mark.priority("MVP")
def test_tc_ncon_030_a_certificate_that_is_not_the_masters_stops_pairing(db):
    """NCON-030: if the certificate that answers is not the one the Master reports for itself, or it
    changes between request and collection, pairing stops and no credentials are saved."""
    _peers, pairing, _store, _desk = make_master()
    spoke = Node()
    with pytest.raises(PairingFailed, match="certificate"):
        PairingClient(lambda a: MasterStandIn(pairing, presented="cd" * 32), spoke.creds, spoke.roles, "n").request("https://m:1")
    ok = PairingClient(lambda a: MasterStandIn(pairing), spoke.creds, spoke.roles, "n")
    pend = ok.request("https://m:1")
    swapped = PairingClient(lambda a: MasterStandIn(pairing, presented="cd" * 32), spoke.creds, spoke.roles, "n")
    with pytest.raises(PairingFailed, match="changed"):
        swapped.poll(pend)
    assert spoke.creds.load() is None


@pytest.mark.requirement("TC-NCON-020")
@pytest.mark.priority("MVP")
def test_tc_ncon_020_no_node_is_both_spoke_and_master(db):
    """NCON-020: a node that is a Master for others cannot pair as a spoke; a spoke cannot approve."""
    _peers, pairing, _store, desk = make_master(is_spoke=True)
    spoke_node = Node(spokes=1)
    with pytest.raises(RoleError):
        PairingClient(lambda a: MasterStandIn(pairing), spoke_node.creds, spoke_node.roles, "n").request("https://m:1")
    other = Node()
    pend = PairingClient(lambda a: MasterStandIn(pairing), other.creds, other.roles, "n").request("https://m:1")
    settings_store.set_setting("plugin.NodeConnect", "master_address", "https://elsewhere:1")   # now designated
    with pytest.raises(RoleError):
        desk.approve(desk.pending()[0]["request_id"])
    assert pend.request_id


@pytest.mark.requirement("TC-NCON-040")
@pytest.mark.priority("MVP")
def test_tc_ncon_040_nodes_are_listed_revoked_and_deleted_and_deletion_releases_tombstones(db):
    """NCON-040 / NCON-180: paired nodes are listed with their reported versions; revoking keeps a
    node (it still holds tombstones back); deleting forgets it and its acknowledgements."""
    _peers, pairing, store, desk = make_master()
    spoke = Node()
    client = PairingClient(lambda a: MasterStandIn(pairing), spoke.creds, spoke.roles, "Dome PC", "linux")
    client.request("https://m:1")
    pid = desk.approve(desk.pending()[0]["request_id"])
    store.peer_set(pid, "info", {"schema": 36, "galileo": "1.2"})
    store.peer_set(pid, "ack", {"piers": 4})
    (n,) = desk.nodes()
    assert (n["name"], n["platform"], n["schema"], n["galileo"], n["revoked"]) == ("Dome PC", "linux", 36, "1.2", False)
    desk.revoke(pid)
    assert desk.nodes()[0]["revoked"] and desk.node_ids() == [pid]
    desk.delete(pid)
    assert desk.nodes() == [] and desk.node_ids() == [] and store.peer_get(pid, "ack", None) is None
    with pytest.raises(PairingFailed):
        desk.revoke("not-a-node")


@pytest.mark.requirement("TC-NCON-030")
@pytest.mark.priority("MVP")
def test_tc_ncon_030_only_nodeconnect_requests_are_the_desks_to_approve(db):
    """NCON-030: another plugin's pending request is neither listed nor approvable here."""
    peers, pairing, _store, desk = make_master()
    peers.declare_plugin("mobileconnect", ["view"])
    other = pairing.request("mobileconnect", "Phone", "phone", "ios", "10.0.0.9")
    assert desk.pending() == []
    with pytest.raises(PairingFailed):
        desk.approve(other.request_id)
    with pytest.raises(PairingFailed):
        desk.reject(other.request_id)
