# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect over a real Connect Link listener (TC-NCON-030, -050, -060, NCON-NFR-030): core's
LinkServer on a loopback port with its own self-signed certificate, a real pairing through
``open_transport``, and the four routes through the certificate-pinning ``https_transport``.

One database serves both ends here (the process has a single global one), so this checks the network
path -- TLS pin, token, permissions, routes, JSON -- and not the sync logic, which test_ncon_sync and
test_ncon_wire cover between two databases."""

import asyncio
import threading

import pytest
from galileo import settings_store
from galileo.connect_link.audit import AuditLog
from galileo.connect_link.certs import CertificateStore
from galileo.connect_link.pairing import PairingService
from galileo.connect_link.peers import PeerStore
from galileo.connect_link.secrets_store import MemorySecretStore
from galileo.connect_link.server import LinkServer
from galileo.library.database import init_db
from galileo.library.models.base import db as database
from galileo.replication import RevisionIssuer

from nodeconnect import conflict
from nodeconnect.apply import Applier
from nodeconnect.link import LinkError, MasterLink, https_transport, open_transport
from nodeconnect.master import MasterService
from nodeconnect.pairing import CredentialStore, PairingClient, PairingDesk
from nodeconnect.roles import RoleGuard
from nodeconnect.store import NodeStore
from nodeconnect.sync import column_fingerprints
from nodeconnect.wire import MasterEndpoint, register_routes

INFO = {"galileo": "t", "plugin_api": "1", "schema": 36, "protocol": 1}


class Listener:
    """A LinkServer running on its own event loop in a thread."""

    def __init__(self, server):
        self.server = server
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(30)

    def stop(self):
        self.run(self.server.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@pytest.fixture
def net(tmp_path):
    init_db(tmp_path / "n.db")
    peers = PeerStore()
    pairing = PairingService(peers)
    certs = CertificateStore(MemorySecretStore(), "test-master")
    server = LinkServer(peers, pairing, certs, AuditLog())
    store = NodeStore()
    desk = PairingDesk(peers, pairing, store, RoleGuard(lambda: None, lambda: 0))
    endpoint = MasterEndpoint(MasterService(store, RevisionIssuer(lambda: True), Applier(store)), lambda: INFO, column_fingerprints)
    register_routes(server.routes, endpoint)
    listener = Listener(server)
    listener.run(server.start("127.0.0.1", 0))
    simple = SimpleNet(server, desk, certs, store, f"https://127.0.0.1:{server.port}")
    simple.listener = listener
    yield simple
    listener.stop()
    if not database.is_closed():
        database.close()
    database.init(None)


class SimpleNet:
    def __init__(self, server, desk, certs, store, address):
        self.server, self.desk, self.certs, self.store, self.address = server, desk, certs, store, address


def pair(net):
    creds = CredentialStore(MemorySecretStore(), settings_store.get_setting, settings_store.set_setting,
                            settings_store.delete_setting)
    client = PairingClient(open_transport, creds, RoleGuard(lambda: None, lambda: 0), "Dome PC", "linux")
    pending = client.request(net.address)
    assert pending.fingerprint == net.certs.fingerprint()
    assert client.poll(pending) == "pending"
    net.desk.approve(net.desk.pending()[0]["request_id"])
    assert client.poll(pending) == "approved"
    return creds.load()


@pytest.mark.requirement("TC-NCON-030")
@pytest.mark.priority("MVP")
def test_tc_ncon_030_real_pairing_then_the_four_routes_work_over_tls(net):
    """NCON-030 / NCON-NFR-030: a node pairs with a real listener (pinned to the certificate the Master
    reported), and with its token handshake, pull, push and ack all answer through the pinned
    transport; the Master records the node's version and acknowledgement."""
    c = pair(net)
    link = MasterLink(https_transport(c.address, c.token, c.fingerprint))
    reply = link.handshake(INFO, {})
    assert reply["info"]["schema"] == 36 and "piers" in reply["fingerprints"]
    pages = link.pull({}, ["observatories", "piers"])
    assert [p["table"] for p in pages] == ["observatories", "piers"] and all(p["changes"] == [] for p in pages)
    assert link.push([], conflict.ClockOffset(0.0, 0.0)) == []
    link.ack({"piers": 2})
    (node,) = net.desk.nodes()
    assert node["schema"] == 36
    assert net.store.peer_get(node["peer_id"], "ack", {}) == {"piers": 2}


@pytest.mark.requirement("TC-NCON-060")
@pytest.mark.priority("MVP")
def test_tc_ncon_060_a_wrong_certificate_or_token_sends_nothing_useful(net):
    """NCON-060 / NCON-030: a transport pinned to another certificate refuses before sending the
    token; a bad token gets the bare 401 and no data."""
    c = pair(net)
    with pytest.raises(LinkError, match="certificate"):
        MasterLink(https_transport(c.address, c.token, "cd" * 32)).handshake(INFO, {})
    with pytest.raises(LinkError) as exc:
        MasterLink(https_transport(c.address, "bogus.token", c.fingerprint)).handshake(INFO, {})
    assert exc.value.status == 401


@pytest.mark.requirement("TC-NCON-050")
@pytest.mark.priority("MVP")
def test_tc_ncon_050_a_revoked_node_is_locked_out_and_a_down_master_is_a_link_error(net):
    """NCON-050 / NCON-140: revoking the node ends its access at once; an unreachable Master raises a
    LinkError (which the sync engine turns into back-off) and never anything else."""
    c = pair(net)
    link = MasterLink(https_transport(c.address, c.token, c.fingerprint))
    link.handshake(INFO, {})
    net.desk.revoke(net.desk.nodes()[0]["peer_id"])
    with pytest.raises(LinkError) as exc:
        link.handshake(INFO, {})
    assert exc.value.status == 401
    dead = MasterLink(https_transport("https://127.0.0.1:1", c.token, c.fingerprint, timeout=2))
    with pytest.raises(LinkError, match="Cannot reach"):
        dead.handshake(INFO, {})


@pytest.mark.requirement("TC-NCON-NFR-030")
@pytest.mark.priority("MVP")
def test_tc_ncon_nfr_030_every_route_refuses_a_forged_empty_or_revoked_token(net):
    """NCON-NFR-030: all four routes ride the encrypted, token-authenticated listener; a forged, an empty and a
    revoked token each get 401 on every route and no data."""
    c = pair(net)
    revoked = c.token
    net.desk.revoke(net.desk.nodes()[0]["peer_id"])
    for token in ("bogus.token", "", revoked):
        link = MasterLink(https_transport(c.address, token, c.fingerprint))
        calls = (lambda link=link: link.handshake(INFO, {}), lambda link=link: link.pull({}, ["piers"]),
                 lambda link=link: link.push([], conflict.ClockOffset(0.0, 0.0)), lambda link=link: link.ack({"piers": 1}))
        for call in calls:
            with pytest.raises(LinkError) as exc:
                call()
            assert exc.value.status == 401


def _wait(predicate, seconds=10.0):
    import time
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.requirement("TC-NCON-150")
@pytest.mark.priority("MVP")
def test_tc_ncon_150_the_master_hints_connected_nodes_but_not_the_one_that_caused_it(net):
    """NCON-150: a hint travels from the Master's event stream to a connected node, which then pulls; the node that
    pushed the change is not hinted; a node with no stream is skipped (it pulls when it connects)."""
    from nodeconnect.push import HintBroadcaster, HintListener

    c = pair(net)
    node_id = net.desk.nodes()[0]["peer_id"]
    hits = []
    listener = HintListener(c.address, c.token, c.fingerprint, lambda: hits.append(1), retry_s=(0.1, 0.5))
    listener.start()
    try:
        assert listener.connected.wait(10)
        assert _wait(lambda: len(hits) >= 1)                              # on connecting it catches up once
        before = len(hits)
        async def on_loop(fn):
            return fn()

        broadcaster = HintBroadcaster(lambda: net.server.streams, lambda: [node_id, "someone-else"],
                                      post=lambda fn: net.listener.run(on_loop(fn)))
        assert broadcaster.notify(except_node=node_id, from_thread=True) == 0     # the originator is excluded
        assert broadcaster.notify(from_thread=True) == 1                  # the other has no stream: only this node
        assert _wait(lambda: len(hits) == before + 1)
        assert len(hits) == before + 1
    finally:
        listener.stop()


@pytest.mark.requirement("TC-NCON-150")
@pytest.mark.priority("MVP")
def test_tc_ncon_150_a_listener_with_a_bad_token_or_certificate_never_connects(net):
    """NCON-150 / NCON-NFR-030: the stream is as protected as the routes — a forged token or a different pinned
    certificate gets no connection and so no hints."""
    from nodeconnect.push import HintListener

    c = pair(net)
    for token, fingerprint in (("bogus.token", c.fingerprint), (c.token, "cd" * 32)):
        listener = HintListener(c.address, token, fingerprint, lambda: None, retry_s=(0.1, 0.2))
        listener.start()
        try:
            assert not listener.connected.wait(1.5)
        finally:
            listener.stop()


@pytest.mark.requirement("TC-NCON-150")
@pytest.mark.priority("MVP")
def test_tc_ncon_150_a_watch_hints_once_per_change_in_the_masters_own_edits_and_a_push_hints_the_others():
    """NCON-150: the Master's own edits (rows marked dirty) hint every node once per change, not while they stay
    dirty; an accepted push calls the hook with the pusher so the others can be hinted."""
    from nodeconnect.push import HintBroadcaster, MasterWatch
    from nodeconnect.wire import MasterEndpoint

    sent = []

    class Hub:
        def publish(self, peer, data):
            sent.append((peer, data))
            return 1

    state = {"sig": ()}
    watch = MasterWatch(HintBroadcaster(lambda: Hub(), lambda: ["a", "b"], post=lambda fn: fn()), lambda: state["sig"])
    assert watch.check() is False and sent == []                          # nothing dirty
    state["sig"] = (("piers", 1),)
    assert watch.check() is True and sorted(p for p, _ in sent) == ["a", "b"]
    assert watch.check() is False and len(sent) == 2                      # still the same edit: no repeat
    state["sig"] = (("piers", 2),)
    assert watch.check() is True and len(sent) == 4
    state["sig"] = ()
    assert watch.check() is False                                         # edits synced: quiet again

    class Result:
        def __init__(self, status):
            self.status, self.table, self.key, self.winner, self.rev, self.op, self.wire = status, "t", [], "", 1, "upsert", None

    class Master:
        def receive_push(self, node_id, pushed, offset):
            return [Result("committed")] if pushed else []

    called = []
    endpoint = MasterEndpoint(Master(), dict, dict, on_committed=called.append)
    endpoint.push("node-1", {"changes": []})
    assert called == []                                                   # nothing accepted: no hint
    endpoint.push("node-1", {"changes": [{"table": "t", "op": "upsert", "key": [], "wire": {}, "base_rev": 0, "modified": 0.0}]})
    assert called == ["node-1"]
