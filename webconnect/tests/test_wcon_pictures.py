# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Pictures through both halves (WCON-600, 610, 630): the browser asks the web half, which asks the Master half,
which renders through core's real preview facade. Only the Library file read is replaced."""

import dataclasses
import io

import numpy as np
import pytest
from galileo.bus import EventBus, FrameCapturedEvent
from galileo.control_surface.frames import FramePreviews, FrameSources
from PIL import Image

from .test_wcon_delegate import PIER, Req
from .test_wcon_web import Browser, Bridge, World, create_app, WebSettings, pair_and_sign_in


def _frame(h=300, w=400, seed=1):
    rng = np.random.default_rng(seed)
    f = rng.normal(1000, 20, (h, w)).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(8):
        cy, cx = rng.integers(30, h - 30), rng.integers(30, w - 30)
        f += 8000 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 2.0 ** 2))
    return f


class _Imaging:
    def __init__(self, frame):
        self.current_frame = frame


@pytest.fixture
def pictures(link_db):
    """A world whose Control Surface offers previews, with one Library frame and a bus to announce frames on."""
    world = World()
    bus = EventBus()
    loads = []

    def loader(frame_id):
        loads.append(frame_id)
        return ("v1", _frame(seed=4)) if frame_id == "abc" else None

    sources = FrameSources(bus, loader=loader)
    world.surface = dataclasses.replace(world.surface, previews=FramePreviews(
        sources, recent=lambda n: [{"id": "abc", "name": "m42.fits", "object": "M 42", "local": True}][:n]))
    world.bus, world.loads = bus, loads
    app = create_app(Bridge(world), WebSettings(allowed_hosts=("localhost",)))
    yield world, app
    sources.close()


def _jpeg(response):
    return Image.open(io.BytesIO(response.data))


@pytest.mark.requirement("TC-WCON-600")
@pytest.mark.priority("MVP")
def test_tc_wcon_600_a_browser_sees_the_current_exposure_and_a_library_frame_as_pictures(pictures):
    """WCON-600: pictures are server-rendered JPEGs, sized as asked, for the current exposure and a Library frame."""
    world, app = pictures
    b = Browser(app)
    pair_and_sign_in(world, b)
    offered = b.get(f"/api/v1/pier/{PIER}/frames").get_json()
    assert offered["current"]["available"] is False and offered["frames"][0]["id"] == "abc"
    world.bus.publish(FrameCapturedEvent("imaging", pier=PIER, service=_Imaging(_frame())))
    assert b.get(f"/api/v1/pier/{PIER}/frames").get_json()["current"]["available"] is True
    shot = b.get(f"/api/v1/pier/{PIER}/preview?ref=current&size=200&stretch=70")
    assert shot.status_code == 200 and shot.mimetype == "image/jpeg" and max(_jpeg(shot).size) <= 200
    assert shot.headers["X-Preview-Width"] and shot.headers["Cache-Control"] == "no-store"   # never kept on a shared device
    library = b.get(f"/api/v1/pier/{PIER}/preview?ref=library:abc&size=128")
    assert library.status_code == 200 and max(_jpeg(library).size) <= 128


@pytest.mark.requirement("TC-WCON-600")
@pytest.mark.priority("MVP")
def test_tc_wcon_600_meta_reports_size_histogram_and_stars_and_a_missing_frame_says_why(pictures):
    """WCON-600: the controls beside a picture get the frame's size, histogram and star statistics; a frame that is
    not there is a plain reason, not a broken image."""
    world, app = pictures
    b = Browser(app)
    pair_and_sign_in(world, b)
    meta = b.get(f"/api/v1/pier/{PIER}/preview/meta?ref=library:abc").get_json()
    assert meta["status"] == "ok" and (meta["width"], meta["height"]) == (400, 300)
    assert len(meta["histogram"]["counts"]) == 256 and meta["stats"]["star_count"] >= 1
    missing = b.get(f"/api/v1/pier/{PIER}/preview?ref=current")
    assert missing.status_code == 404 and missing.get_json()["reason"]
    assert b.get(f"/api/v1/pier/{PIER}/preview?ref=library:nope").status_code == 404


@pytest.mark.requirement("TC-WCON-610")
@pytest.mark.priority("P2")
def test_tc_wcon_610_tiles_are_served_for_pan_and_zoom_and_read_the_file_once(pictures):
    """WCON-610: a pyramid of tiles with a fixed stretch, and repeated requests do not read the file again."""
    world, app = pictures
    b = Browser(app)
    pair_and_sign_in(world, b)
    first = b.get(f"/api/v1/pier/{PIER}/tile?ref=library:abc&level=0&x=0&y=0")
    edge = b.get(f"/api/v1/pier/{PIER}/tile?ref=library:abc&level=0&x=1&y=1")
    assert _jpeg(first).size == (256, 256) and _jpeg(edge).size == (144, 44)       # 400 x 300: a short last row and column
    assert b.get(f"/api/v1/pier/{PIER}/tile?ref=library:abc&level=0&x=9&y=0").status_code == 404
    assert world.loads == ["abc"]


@pytest.mark.requirement("TC-WCON-630")
@pytest.mark.priority("P2")
def test_tc_wcon_630_a_browser_cannot_ask_for_more_than_the_limits_and_odd_references_fall_back(pictures):
    """WCON-630: size and quality are bounded however the page asks, and a reference that is not a frame is never
    passed on."""
    world, app = pictures
    b = Browser(app)
    pair_and_sign_in(world, b)
    bridge_calls = []
    real = Bridge.call_raw

    def spy(self, route, *, client, query=None, **kw):
        bridge_calls.append(dict(query or {}))
        return real(self, route, client=client, query=query, **kw)

    Bridge.call_raw = spy
    try:
        b.get(f"/api/v1/pier/{PIER}/preview?ref=library:abc&size=999999&quality=500&bin=99&stretch=-4")
        b.get(f"/api/v1/pier/{PIER}/preview?ref=../../etc/passwd&size=abc")
    finally:
        Bridge.call_raw = real
    seen = bridge_calls
    assert seen[0]["size"] == "2048" and seen[0]["quality"] == "92" and seen[0]["bin"] == "4" and seen[0]["stretch"] == "0"
    assert seen[1]["ref"] == "current" and "size" not in seen[1]


@pytest.mark.requirement("TC-WCON-600")
@pytest.mark.priority("MVP")
def test_tc_wcon_600_pictures_need_a_signed_in_browser_and_a_known_pier(pictures):
    """WCON-600: no picture without a session, and a malformed Pier name is refused before any call."""
    world, app = pictures
    anonymous = Browser(app)
    assert anonymous.get(f"/api/v1/pier/{PIER}/preview").status_code == 401
    assert anonymous.get(f"/api/v1/pier/{PIER}/frames").status_code == 401
    b = Browser(app)
    pair_and_sign_in(world, b)
    assert b.get("/api/v1/pier/bad%20pier/preview").status_code in (400, 404)


@pytest.mark.requirement("TC-WCON-600")
@pytest.mark.priority("MVP")
async def test_tc_wcon_600_the_master_refuses_pictures_for_a_stranger_or_without_previews(pictures):
    """WCON-600: the Master half checks who is asking before it renders, and says so when there is nothing to
    render with."""
    world, _ = pictures
    stranger = await world.delegate.frames(world.carrier, Req("not-a-browser", query={"pier": PIER}))
    assert stranger["status"] == "refused" and stranger["client_refused"] is True
    peer = world.pair()
    world.surface = dataclasses.replace(world.surface, previews=None)
    out = await world.delegate.frames(world.carrier, Req(peer, query={"pier": PIER}))
    assert out["status"] == "unavailable" and "pictures" in out["reason"]


@pytest.mark.requirement("TC-WCON-300")
@pytest.mark.priority("MVP")
async def test_tc_wcon_300_a_frame_event_never_carries_the_imaging_service_to_a_browser(pictures):
    """WCON-300: the engine announces a frame with the imaging service object attached; the event feed given to a
    browser holds data only."""
    world, _ = pictures
    peer = world.pair()

    class Feed:
        last_id = 1

        def read(self, after, wait):
            return {"entries": [{"id": 1, "type": "event", "name": "FrameCapturedEvent",
                                 "payload": {"pier": 7, "service": _Imaging(_frame())}}], "last": 1}

    world.surface = dataclasses.replace(world.surface, feed=Feed())
    out = await world.delegate.events(world.carrier, Req(peer, query={"after": "0"}))
    assert out["entries"][0]["payload"] == {"pier": 7}
