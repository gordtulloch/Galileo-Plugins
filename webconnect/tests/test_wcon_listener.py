# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The web half's HTTPS client against a real Connect Link listener (WCON-090, 100, 170): core's ``LinkServer`` on
a loopback port, the real pairing endpoints, the certificate pin, the service credential and the acting-client
header, with the Master half's routes registered on it."""

import asyncio
import threading

import pytest
from galileo.connect_link.audit import AuditLog
from galileo.connect_link.certs import CertificateStore
from galileo.connect_link.secrets_store import MemorySecretStore
from galileo.connect_link.server import LinkServer

from webconnect.web.link import HttpLinkClient, LinkError

from .test_wcon_delegate import PIER, World


class Listener:
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
def net(link_db):
    world = World()
    certs = CertificateStore(MemorySecretStore(), "test-master")
    server = LinkServer(world.peers, world.pairing, certs, AuditLog())
    world.delegate.register(server.routes)
    listener = Listener(server)
    listener.run(server.start("127.0.0.1", 0))
    address = f"https://127.0.0.1:{server.port}"
    yield world, certs, address
    listener.stop()


@pytest.mark.requirement("TC-WCON-090")
@pytest.mark.priority("MVP")
@pytest.mark.integration
def test_tc_wcon_090_the_web_half_reaches_the_master_only_over_pinned_connect_link(net):
    """WCON-090: pair a browser, sign it in and operate, all over the real wire with the certificate pinned."""
    world, certs, address = net
    link = HttpLinkClient(address, certs.fingerprint(), world.service_token)
    assert link.reachable()

    pairing = link.pair_request("Tablet", "Android")
    assert pairing["http"] == 202 and pairing["code"]
    assert world.browsers.pending()[0]["code"] == pairing["code"]
    peer = world.browsers.approve(world.browsers.pending()[0]["request_id"])
    world.browsers.promote(peer)
    polled = link.pair_poll(pairing["request_id"])
    assert polled["state"] == "approved" and polled["token"]

    signed = link.call("POST", "/signin", body={"device_credential": polled["token"]})
    assert signed["status"] == "ok" and signed["client"] == peer and signed["role"] == "operator"
    assert link.call("GET", "/piers", client=peer)["piers"][0]["id"] == PIER

    assert link.call("POST", "/lease/acquire", client=peer, body={"pier": PIER})["granted"]
    done = link.call("POST", "/ops", client=peer, body={"pier": PIER, "name": "mount.slew", "args": {"ra": 7.0}})
    assert done["status"] == "ok" and world.moves == [7.0]

    world.browsers.revoke(peer)
    assert link.call("GET", "/piers", client=peer)["client_refused"] is True


@pytest.mark.requirement("TC-WCON-090")
@pytest.mark.priority("MVP")
@pytest.mark.integration
def test_tc_wcon_090_a_wrong_certificate_or_credential_is_refused(net):
    """WCON-090: a different certificate is not trusted, and a browser's token cannot stand in for the service's."""
    world, certs, address = net
    with pytest.raises(LinkError, match="certificate"):
        HttpLinkClient(address, "ab" * 32, world.service_token).call("GET", "/piers", client="x")
    assert not HttpLinkClient(address, "ab" * 32, world.service_token).reachable()
    with pytest.raises(LinkError, match="credential"):
        HttpLinkClient(address, certs.fingerprint(), "nope.nope").call("GET", "/piers", client="x")
    req = world.pairing.request("webconnect", "T", "browser", "x", "1.1.1.1")
    peer = world.browsers.approve(req.request_id)
    _, token = world.pairing.poll(req.request_id)
    browser_as_carrier = HttpLinkClient(address, certs.fingerprint(), token)
    out = browser_as_carrier.call("GET", "/piers", client=peer)                    # a browser naming itself
    assert out["status"] == "refused"
