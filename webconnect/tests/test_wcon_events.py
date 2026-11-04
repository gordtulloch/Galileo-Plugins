# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Live updates to the browser (WCON-300, 310, 350, 780): the Master half serves core's event feed to a named
browser, and the web half turns it into Server-Sent Events. The feed is core's real `EventFeed`."""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest
from galileo.bus import EventBus
from galileo.control_surface.feed import EventFeed

from webconnect.supervisor import BOOT
from webconnect.web.app import MAX_STREAMS, WebSettings, create_app
from webconnect.web.session import SessionStore

from .test_wcon_delegate import PIER, Req, World
from .test_wcon_web import Bridge, Browser, pair_and_sign_in

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def with_feed(world: World, capacity: int = 2000) -> EventFeed:
    feed = EventFeed(state=world.state, leases=world.leases, bus=EventBus(), pier_ids=lambda: [PIER],
                     capacity=capacity)
    world.surface = dataclasses.replace(world.surface, feed=feed)
    return feed


def frames(body: bytes) -> list[tuple[str, dict, str]]:
    """(event, data, id) for each Server-Sent Event in a response body; keep-alive comments are skipped."""
    out = []
    for block in body.decode().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in fields:
            out.append((fields["event"], json.loads(fields["data"]), fields.get("id", "")))
    return out


@pytest.mark.requirement("TC-WCON-310")
@pytest.mark.priority("MVP")
async def test_tc_wcon_310_master_serves_the_feed_to_a_named_browser(link_db):
    """WCON-310: a Viewer reads state, lease and operation events from core's feed after a cursor; `latest`
    starts from now; a revoked browser, a bad query and a missing engine are refused with a reason."""
    world = World()
    feed = with_feed(world)
    viewer = world.pair()
    start = await world.delegate.events(world.carrier, Req(viewer, query={"after": "latest"}))
    assert start == {"status": "ok", "entries": [], "last": 0, "gap": False, "closed": False}

    world.state.publish(PIER, "mount.tracking", True)
    world.leases.acquire(PIER, "desktop")
    got = await world.delegate.events(world.carrier, Req(viewer, query={"after": "0", "wait": "0"}))
    assert [e["type"] for e in got["entries"]] == ["state", "lease"] and got["last"] == 2
    assert (await world.delegate.events(world.carrier, Req(viewer, query={"after": "2"})))["entries"] == []

    bad = await world.delegate.events(world.carrier, Req(viewer, query={"after": "soon"}))
    assert bad["status"] == "refused" and "after" in bad["reason"]
    assert (await world.delegate.events(world.carrier, Req("", query={})))["status"] == "refused"   # names no browser

    world.browsers.revoke(viewer)
    gone = await world.delegate.events(world.carrier, Req(viewer, query={"after": "0"}))
    assert gone["status"] == "refused" and gone["client_refused"] is True

    world.surface = dataclasses.replace(world.surface, feed=None)
    other = world.pair("Phone")
    assert (await world.delegate.events(world.carrier, Req(other, query={"after": "0"})))["status"] == "unavailable"
    feed.close()


@pytest.mark.requirement("TC-WCON-310")
@pytest.mark.priority("MVP")
def test_tc_wcon_310_browser_receives_events_as_server_sent_events(link_db):
    """WCON-310, 350: the browser gets a hello, then ordered events carrying ids so it can resume; the stream
    ends cleanly with a `down` event when Galileo shuts the feed."""
    world = World()
    feed = with_feed(world)
    app = create_app(Bridge(world), WebSettings(allowed_hosts=("localhost",)))
    b = Browser(app)
    pair_and_sign_in(world, b)
    world.state.publish(PIER, "mount.tracking", True)
    world.leases.acquire(PIER, "desktop")
    feed.close()                                                 # so the stream ends once it has the entries

    r = b.get("/api/v1/events", headers={"Last-Event-ID": "0"})
    assert r.status_code == 200 and r.mimetype == "text/event-stream"
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Accel-Buffering"] == "no"
    got = frames(r.get_data())
    assert [g[0] for g in got] == ["hello", "state", "lease", "down"]
    assert got[1][1]["key"] == "mount.tracking" and got[1][2] == "1" and got[2][2] == "2"   # ids to resume from
    assert got[0][1]["role"] == "viewer"


@pytest.mark.requirement("TC-WCON-350")
@pytest.mark.priority("MVP")
def test_tc_wcon_350_a_reader_that_fell_behind_is_told_to_resync(link_db):
    """WCON-350: when the feed has dropped entries the reader needed, the browser is told to fetch state again
    rather than be shown a picture with holes in it."""
    world = World()
    feed = with_feed(world, capacity=10)
    app = create_app(Bridge(world), WebSettings(allowed_hosts=("localhost",)))
    b = Browser(app)
    pair_and_sign_in(world, b)
    for i in range(40):
        world.state.publish(PIER, "focuser.position", i)
    feed.close()
    got = frames(b.get("/api/v1/events", headers={"Last-Event-ID": "1"}).get_data())
    kinds = [g[0] for g in got]
    assert kinds[:2] == ["hello", "resync"] and kinds[-1] == "down"


@pytest.mark.requirement("TC-WCON-310")
@pytest.mark.priority("MVP")
def test_tc_wcon_310_stream_ends_for_a_revoked_browser_and_is_capped(link_db):
    """WCON-310, 740: revoking a browser ends its stream with `signed-out`; one sign-in cannot hold more than
    a few streams open; and the Master being down is reported as `down`, not an error page."""
    world = World()
    with_feed(world)
    bridge = Bridge(world)
    app = create_app(bridge, WebSettings(allowed_hosts=("localhost",)))
    b = Browser(app)
    peer = pair_and_sign_in(world, b)
    world.browsers.revoke(peer)
    got = frames(b.get("/api/v1/events", headers={"Last-Event-ID": "0"}).get_data())
    assert [g[0] for g in got] == ["hello", "signed-out"]

    c = Browser(app)
    pair_and_sign_in(world, c, name="Phone")
    held = [c.get("/api/v1/events") for _ in range(MAX_STREAMS)]       # opened, not read to the end
    assert all(r.status_code == 200 for r in held)
    over = c.get("/api/v1/events")
    assert over.status_code == 429 and over.get_json()["status"] == "refused"
    for r in held:
        r.close()
    assert c.get("/api/v1/events").status_code == 200                  # closing frees the slots

    d = Browser(app)
    pair_and_sign_in(world, d, name="Laptop")
    bridge.down = True
    assert [g[0] for g in frames(d.get("/api/v1/events").get_data())] == ["hello", "down"]


@pytest.mark.requirement("TC-WCON-310")
@pytest.mark.priority("MVP")
def test_tc_wcon_310_an_open_stream_does_not_keep_a_session_alive():
    """WCON-310: watching the event stream is not activity, so an idle tab still signs out on schedule."""
    now = [0.0]
    store = SessionStore(idle_s=100, absolute_s=1000, clock=lambda: now[0])
    s = store.create("peer", "Tablet", "viewer")
    now[0] = 90
    assert store.alive(s.sid) is True
    now[0] = 101                                    # alive() did not refresh last_seen, so the idle limit passes
    assert store.alive(s.sid) is False and store.get(s.sid) is None


CONCURRENCY_SCRIPT = """
{patch}
import sys, threading, time, http.client
sys.path[:0] = [{plugin!r}]
from gevent.pywsgi import WSGIServer
from webconnect.web.app import WebSettings, create_app

class SlowLink:
    def reachable(self):
        time.sleep(0.5)
        return True
    def pair_request(self, *a): return {{}}
    def pair_poll(self, *a): return {{}}
    def call(self, *a, **k): return {{"status": "ok"}}

app = create_app(SlowLink(), WebSettings(allowed_hosts=("localhost",)))
app.add_url_rule("/slow", "slow", lambda: ("ok", 200 if SlowLink().reachable() else 500))
server = WSGIServer(("127.0.0.1", 0), app, log=None)
server.start()

def fetch():
    c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
    c.request("GET", "/slow", headers={{"Host": "localhost"}})
    c.getresponse().read()

t0 = time.monotonic()
threads = [threading.Thread(target=fetch) for _ in range(6)]
[t.start() for t in threads]
import gevent
while any(t.is_alive() for t in threads):
    gevent.sleep(0.02)                    # the main thread must yield, as the real process's loop does
print(round(time.monotonic() - t0, 2))
server.stop()
"""


@pytest.mark.requirement("TC-WCON-310")
@pytest.mark.priority("MVP")
def test_tc_wcon_310_slow_calls_to_the_master_do_not_serialise_browsers(tmp_path):
    """WCON-310: with the boot-time gevent patch, six browsers waiting on a slow Master are served together.
    Unpatched, each blocking call stalls the one process and they take six times as long."""
    patch = BOOT.split("import sys")[0]                              # the monkey-patch line the child really runs
    script = tmp_path / "concurrency.py"
    script.write_text(CONCURRENCY_SCRIPT.format(patch=patch, plugin=str(PLUGIN_DIR)))
    out = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr[-800:]
    assert float(out.stdout.strip().splitlines()[-1]) < 1.8, out.stdout
