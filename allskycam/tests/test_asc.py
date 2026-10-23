# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""ASC — AllSkyCam plugin (TC-ASC-010 … TC-ASC-090)."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest
import requests

from allskycam import cycle
from allskycam.buffer import FrameBuffer
from allskycam.client import (
    AllSkyClient,
    AllSkyError,
    Frame,
    latest_image_url,
    normalize_base_url,
)
from allskycam.config import AllSkyConfig
from allskycam.daytime import check_pause

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeResponse:
    def __init__(self, url, body=b"\xff\xd8jpegbytes", content_type="image/jpeg", status=200, location=None):
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location
        self._body = body
        self.body_read = False
        self.closed = False

    def iter_content(self, chunk_size=1):
        self.body_read = True
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    """Answers every GET with *response*, or per-URL from *routes* (as the real wire does: a 302, then the image)."""

    def __init__(self, response=None, error=None, routes=None):
        self.response = response
        self.error = error
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        if self.routes is not None:
            return self.routes[url]
        return self.response


def _wire(latest, image, body=b"JPEGDATA"):
    """The two-step exchange indi-allsky does: /latestimage 302s to the image."""
    return {
        latest: FakeResponse(latest, status=302, location=image.replace("http://h", ""), body=b""),
        image: FakeResponse(image, body=body),
    }


def _frame(n=1, size=10):
    return Frame(data=b"x" * size, url=f"http://h/img{n}.jpg", fetched_at=1000.0 + n)


# ---------------------------------------------------------------------------
# TC-ASC-010 — retrieve the newest image from indi-allsky
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-ASC-010")
@pytest.mark.priority("MVP")
@pytest.mark.parametrize("typed, expected", [
    ("http://allsky.local/indi-allsky", "http://allsky.local/indi-allsky"),
    ("allsky.local/indi-allsky/", "http://allsky.local/indi-allsky"),
    ("  https://allsky.local:8443  ", "https://allsky.local:8443"),
    ("http://allsky.local/indi-allsky/latestimage", "http://allsky.local/indi-allsky"),
    ("192.168.1.50", "http://192.168.1.50"),
])
def test_tc_asc_010_base_url_is_normalised(typed, expected):
    """ASC-010: the server address is accepted with or without scheme, trailing slash, or a pasted /latestimage."""
    assert normalize_base_url(typed) == expected


@pytest.mark.requirement("TC-ASC-010")
@pytest.mark.priority("MVP")
@pytest.mark.parametrize("bad", ["", "   ", "ftp://host/x", "http://"])
def test_tc_asc_010_bad_addresses_are_rejected(bad):
    """ASC-010: an empty or non-http address is a ValueError with a readable message, not a crash later."""
    with pytest.raises(ValueError):
        normalize_base_url(bad)


@pytest.mark.requirement("TC-ASC-010")
@pytest.mark.priority("MVP")
def test_tc_asc_010_latest_url_uses_latestimage_and_optional_camera():
    """ASC-010: the endpoint is <root>/latestimage, with ?camera_id only for a non-default camera."""
    assert latest_image_url("http://h/indi-allsky") == "http://h/indi-allsky/latestimage"
    assert latest_image_url("http://h/indi-allsky", 0) == "http://h/indi-allsky/latestimage"
    assert latest_image_url("http://h/indi-allsky", 2) == "http://h/indi-allsky/latestimage?camera_id=2"


@pytest.mark.requirement("TC-ASC-010")
@pytest.mark.priority("MVP")
def test_tc_asc_010_tls_certificates_are_not_verified_for_self_signed_servers():
    """ASC-010: indi-allsky usually uses a self-signed certificate, so every request skips verification."""
    latest = "https://h/latestimage"
    image = "https://h/img.jpg"
    session = FakeSession(routes=_wire(latest, image))
    AllSkyClient("https://h", session=session).fetch_latest()
    assert session.calls and all(kw.get("verify") is False for _u, kw in session.calls)


def test_tc_asc_010_fetch_follows_the_redirect_and_returns_the_image_bytes():
    """ASC-010: the image behind /latestimage's redirect is downloaded and returned with its final URL."""
    image = "http://h/indi-allsky/images/2026/img_001.jpg"
    routes = _wire("http://h/indi-allsky/latestimage", image)
    session = FakeSession(routes=routes)
    frame = AllSkyClient("http://h/indi-allsky", session=session).fetch_latest()
    assert frame.data == b"JPEGDATA"
    assert frame.url == image
    assert [c[0] for c in session.calls] == ["http://h/indi-allsky/latestimage", image]
    assert all(c[1]["timeout"] > 0 for c in session.calls)
    assert all(r.closed for r in routes.values())


# ---------------------------------------------------------------------------
# TC-ASC-020 — background fetching: dedupe and failures
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_unchanged_image_is_not_downloaded():
    """ASC-020: when the redirect target matches the previous image, the body is never read."""
    routes = _wire("http://h/latestimage", "http://h/images/img_001.jpg")
    session = FakeSession(routes=routes)
    result = AllSkyClient("http://h", session=session).fetch_latest(known_url="http://h/images/img_001.jpg")
    assert result is None
    assert [c[0] for c in session.calls] == ["http://h/latestimage"]     # the image itself was never requested
    assert routes["http://h/latestimage"].closed
    assert routes["http://h/images/img_001.jpg"].body_read is False


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_redirect_loop_is_an_error():
    """ASC-020: a server that redirects forever ends in a readable error, not a hang."""
    loop = {"http://h/latestimage": FakeResponse("http://h/latestimage", status=302, location="/latestimage", body=b"")}
    with pytest.raises(AllSkyError, match="redirects"):
        AllSkyClient("http://h", session=FakeSession(routes=loop)).fetch_latest()


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
@pytest.mark.parametrize("response, error, expect", [
    (FakeResponse("http://h/x", status=500), None, "HTTP 500"),
    (FakeResponse("http://h/x", status=404), None, "HTTP 404"),
    (FakeResponse("http://h/login", body=b"<html>", content_type="text/html; charset=utf-8"), None, "login"),
    (FakeResponse("http://h/x", content_type="application/json"), None, "not an image"),
    (FakeResponse("http://h/x", body=b""), None, "empty"),
    (None, requests.ConnectionError("refused"), "Could not reach"),
    (None, requests.Timeout("slow"), "Could not reach"),
])
def test_tc_asc_020_failures_become_readable_errors(response, error, expect):
    """ASC-020: unreachable servers, HTTP errors, login pages and non-images raise AllSkyError with a useful message."""
    client = AllSkyClient("http://h", session=FakeSession(response, error))
    with pytest.raises(AllSkyError, match=expect):
        client.fetch_latest()
    if response is not None:
        assert response.closed


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_oversized_download_is_abandoned():
    """ASC-020: a response larger than the cap is dropped rather than buffered whole."""
    resp = FakeResponse("http://h/x", body=b"z" * 5000)
    with pytest.raises(AllSkyError, match="larger"):
        AllSkyClient("http://h", max_bytes=1000, session=FakeSession(resp)).fetch_latest()


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_cycle_reports_every_outcome_and_never_raises():
    """ASC-020: run_cycle returns FRAME / UNCHANGED / ERROR results; no exception escapes the worker."""
    cfg = AllSkyConfig(url="http://h", pause_daytime=False)

    class Ok:
        def fetch_latest(self, known):
            return _frame()

    class Same:
        def fetch_latest(self, known):
            return None

    class Boom:
        def fetch_latest(self, known):
            raise AllSkyError("down")

    class Weird:
        def fetch_latest(self, known):
            raise RuntimeError("bug")

    assert cycle.run_cycle(cfg, None, None, client_factory=lambda c: Ok()).kind == cycle.FRAME
    assert cycle.run_cycle(cfg, None, "u", client_factory=lambda c: Same()).kind == cycle.UNCHANGED
    err = cycle.run_cycle(cfg, None, None, client_factory=lambda c: Boom())
    assert (err.kind, err.message) == (cycle.ERROR, "down")
    assert cycle.run_cycle(cfg, None, None, client_factory=lambda c: Weird()).kind == cycle.ERROR
    bad = cycle.run_cycle(AllSkyConfig(url="ftp://nope"), None, None)
    assert bad.kind == cycle.ERROR


# ---------------------------------------------------------------------------
# TC-ASC-030 — optional daytime pause, computed locally
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-ASC-030")
@pytest.mark.priority("MVP")
def test_tc_asc_030_pauses_only_while_the_sun_is_above_the_threshold():
    """ASC-030: paused iff the Sun's altitude exceeds the configured limit."""
    site = object()
    assert check_pause(site, 0.0, altitude_fn=lambda loc: 35.0)[0] is True
    assert check_pause(site, 0.0, altitude_fn=lambda loc: -20.0)[0] is False
    assert check_pause(site, -6.0, altitude_fn=lambda loc: -3.0)[0] is True    # twilight counts as day at -6
    assert check_pause(site, 0.0, altitude_fn=lambda loc: 0.0)[0] is False     # exactly on the limit is not "above"
    assert "+35.0" in check_pause(site, 0.0, altitude_fn=lambda loc: 35.0)[1]


@pytest.mark.requirement("TC-ASC-030")
@pytest.mark.priority("MVP")
def test_tc_asc_030_unknown_sun_never_pauses():
    """ASC-030: with no Observatory location, or no computable altitude, fetching continues and says why."""
    paused, msg = check_pause(None, 0.0, altitude_fn=lambda loc: 99.0)
    assert paused is False and "latitude" in msg
    paused, msg = check_pause(object(), 0.0, altitude_fn=lambda loc: None)
    assert paused is False and msg


@pytest.mark.requirement("TC-ASC-030")
@pytest.mark.priority("MVP")
def test_tc_asc_030_cycle_skips_the_download_when_paused_and_honours_the_option():
    """ASC-030: a paused cycle never contacts the server; with the option off the check is not even made."""
    contacted = []

    def factory(c):
        contacted.append(c)
        return SimpleNamespace(fetch_latest=lambda known: _frame())

    on = AllSkyConfig(url="http://h", pause_daytime=True, sun_altitude_deg=0.0)
    r = cycle.run_cycle(on, object(), None, client_factory=factory, pause_fn=lambda loc, thr: (True, "Paused for daytime"))
    assert (r.kind, r.message) == (cycle.PAUSED, "Paused for daytime") and contacted == []

    off = AllSkyConfig(url="http://h", pause_daytime=False)
    r = cycle.run_cycle(off, object(), None, client_factory=factory,
                        pause_fn=lambda loc, thr: pytest.fail("must not be consulted"))
    assert r.kind == cycle.FRAME and len(contacted) == 1


@pytest.mark.requirement("TC-ASC-030")
@pytest.mark.priority("P2")
def test_tc_asc_030_real_sun_altitude_separates_day_from_night():
    """ASC-030: with Galileo's real solar calculation, local noon is paused and local midnight is not."""
    pytest.importorskip("astropy")
    from astropy.time import Time
    from galileo.planning.visibility import ObservingLocation, sun_altitude_deg

    site = ObservingLocation(name="Greenwich", latitude=51.48, longitude=0.0)
    noon = Time("2026-06-21 12:00:00")
    midnight = Time("2026-06-21 00:00:00")
    paused_noon, _ = check_pause(site, 0.0, altitude_fn=lambda loc: sun_altitude_deg(loc, noon))
    paused_midnight, _ = check_pause(site, 0.0, altitude_fn=lambda loc: sun_altitude_deg(loc, midnight))
    assert paused_noon is True and paused_midnight is False


# ---------------------------------------------------------------------------
# TC-ASC-040 — options
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-ASC-040")
@pytest.mark.priority("MVP")
def test_tc_asc_040_defaults_and_roundtrip():
    """ASC-040: defaults are sensible and an options dict round-trips unchanged."""
    c = AllSkyConfig()
    assert not c.configured and c.pause_daytime and c.frame_count >= 2 and c.interval_s >= 10
    again = AllSkyConfig.from_dict(AllSkyConfig(url="http://h", camera_id=2, interval_s=120, frame_count=30,
                                                pause_daytime=False, sun_altitude_deg=-6.0, fps=8.0,
                                                show_dock=True).to_dict())
    assert (again.url, again.camera_id, again.interval_s, again.frame_count) == ("http://h", 2, 120, 30)
    assert (again.pause_daytime, again.sun_altitude_deg, again.fps, again.show_dock) == (False, -6.0, 8.0, True)


@pytest.mark.requirement("TC-ASC-040")
@pytest.mark.priority("MVP")
def test_tc_asc_040_out_of_range_and_garbage_values_are_clamped():
    """ASC-040: stored junk never produces a zero interval, a negative frame count or a crash."""
    c = AllSkyConfig.from_dict({"interval_s": 0, "frame_count": 100000, "fps": -3, "sun_altitude_deg": 500,
                                "camera_id": -4, "pause_daytime": "false", "show_dock": "yes", "url": 42})
    assert c.interval_s == 10 and c.frame_count == 120 and c.fps == 0.5
    assert c.sun_altitude_deg == 10.0 and c.camera_id == 0
    assert c.pause_daytime is False and c.show_dock is True and c.url == ""
    junk = AllSkyConfig.from_dict({"interval_s": "soon", "frame_count": None, "fps": object()})
    assert junk.interval_s == AllSkyConfig().interval_s and junk.fps == AllSkyConfig().fps
    assert AllSkyConfig.from_dict(None) == AllSkyConfig()


# ---------------------------------------------------------------------------
# TC-ASC-060 — frame buffer behind the animation
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-ASC-060")
@pytest.mark.priority("MVP")
def test_tc_asc_060_buffer_keeps_only_the_newest_n_frames_in_order():
    """ASC-060: the animation holds the last N images, oldest first."""
    buf = FrameBuffer(3)
    for n in range(1, 6):
        buf.append(_frame(n))
    assert [f.url for f in buf.frames()] == ["http://h/img3.jpg", "http://h/img4.jpg", "http://h/img5.jpg"]
    assert buf.latest.url == "http://h/img5.jpg" and len(buf) == 3


@pytest.mark.requirement("TC-ASC-060")
@pytest.mark.priority("MVP")
def test_tc_asc_060_buffer_resize_keeps_the_newest():
    """ASC-060: lowering the image count drops the oldest frames; raising it keeps what is there."""
    buf = FrameBuffer(5)
    for n in range(1, 6):
        buf.append(_frame(n))
    buf.resize(2)
    assert [f.url for f in buf.frames()] == ["http://h/img4.jpg", "http://h/img5.jpg"]
    buf.resize(10)
    assert len(buf) == 2 and buf.maxlen == 10
    buf.clear()
    assert len(buf) == 0 and buf.latest is None and buf.total_bytes == 0


@pytest.mark.requirement("TC-ASC-060")
@pytest.mark.priority("MVP")
def test_tc_asc_060_buffer_memory_is_capped_but_keeps_two_frames():
    """ASC-060: a byte cap drops the oldest frames first, never below two so an animation still exists."""
    buf = FrameBuffer(100, max_bytes=250)
    for n in range(1, 8):
        buf.append(_frame(n, size=100))
    assert buf.total_bytes <= 250 and len(buf) == 2
    assert buf.latest.url == "http://h/img7.jpg"
    huge = FrameBuffer(10, max_bytes=10)
    huge.append(_frame(1, size=100))
    huge.append(_frame(2, size=100))
    assert len(huge) == 2


# ---------------------------------------------------------------------------
# Qt-backed tests: service, views, settings form, plugin wiring
# ---------------------------------------------------------------------------

class MemorySettings:
    """Stands in for galileo.plugins.PluginSettings."""

    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def all(self):
        return dict(self.data)

    def update(self, values):
        self.data.update(values)
        return True

    def set(self, key, value):
        self.data[key] = value
        return True


@pytest.fixture
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _jpeg_bytes(width=64, height=48, color="red"):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    raw = QByteArray()
    buf = QBuffer(raw)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buf, "JPEG")
    return bytes(raw)


def _wait(qapp, predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _service(qapp, results, **initial):
    from allskycam.service import AllSkyService
    queue = list(results)
    calls = []

    def fake_cycle(config, location, known):
        calls.append((config, location, known))
        return queue.pop(0) if queue else cycle.CycleResult(cycle.UNCHANGED)

    settings = MemorySettings({"url": "http://h/indi-allsky", **initial})
    svc = AllSkyService(settings, site_provider=lambda: "SITE", cycle_fn=fake_cycle)
    svc.calls = calls
    svc.settings = settings
    return svc


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_service_fetches_on_a_worker_thread_and_fills_the_buffer(qapp):
    """ASC-020: the service downloads off the UI thread, appends frames, remembers the last URL, and reports status."""
    f1, f2 = _frame(1), _frame(2)
    svc = _service(qapp, [cycle.CycleResult(cycle.FRAME, frame=f1), cycle.CycleResult(cycle.FRAME, frame=f2)])
    seen, statuses = [], []
    svc.frame_added.connect(seen.append)
    svc.status_changed.connect(statuses.append)
    svc.start()
    assert _wait(qapp, lambda: len(seen) == 1)
    svc.fetch_now()
    assert _wait(qapp, lambda: len(seen) == 2)
    svc.stop()
    assert [f.url for f in svc.buffer.frames()] == [f1.url, f2.url]
    assert svc.calls[1][2] == f1.url                         # the second fetch was told what it already had
    assert svc.calls[0][1] == "SITE"                         # the Observatory location was passed along
    assert any("Updated" in s for s in statuses)


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_service_reports_errors_and_pauses_and_keeps_going(qapp):
    """ASC-020/030: an error or a daytime pause shows in the status line and does not stop later fetches."""
    svc = _service(qapp, [cycle.CycleResult(cycle.ERROR, "down"), cycle.CycleResult(cycle.PAUSED, "Paused for daytime"),
                          cycle.CycleResult(cycle.FRAME, frame=_frame(1))])
    statuses = []
    svc.status_changed.connect(statuses.append)
    svc.start()
    assert _wait(qapp, lambda: any("down" in s for s in statuses))
    svc.fetch_now()
    assert _wait(qapp, lambda: "Paused for daytime" in statuses)
    svc.fetch_now()
    assert _wait(qapp, lambda: len(svc.buffer) == 1)
    svc.stop()


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_unconfigured_service_does_not_fetch(qapp):
    """ASC-020: with no address set nothing is requested and the status says what to do."""
    svc = _service(qapp, [], url="")
    svc.start()
    svc.fetch_now()
    qapp.processEvents()
    assert svc.calls == [] and "Options" in svc.status
    svc.stop()


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_stopped_service_discards_late_results(qapp):
    """ASC-090: results that arrive after stop() (a disabled plugin) change nothing."""
    svc = _service(qapp, [cycle.CycleResult(cycle.FRAME, frame=_frame(1))])
    svc.start()
    svc.stop()
    _wait(qapp, lambda: False, timeout=0.3)
    assert len(svc.buffer) == 0 and svc.running is False


@pytest.mark.requirement("TC-ASC-040")
@pytest.mark.priority("MVP")
def test_tc_asc_040_apply_config_persists_resizes_and_restarts_the_animation(qapp):
    """ASC-040: saving options writes them to the settings store; a new server clears held images."""
    svc = _service(qapp, [cycle.CycleResult(cycle.FRAME, frame=_frame(n)) for n in range(1, 5)])
    svc.start()
    assert _wait(qapp, lambda: len(svc.buffer) == 1)
    changes = []
    svc.config_changed.connect(lambda: changes.append(1))

    cfg = svc.config
    cfg.frame_count = 5
    cfg.interval_s = 30
    svc.apply_config(cfg)                                    # same server: images are kept
    assert svc.settings.data["frame_count"] == 5 and svc.settings.data["interval_s"] == 30
    assert svc.buffer.maxlen == 5 and changes

    cfg = svc.config
    cfg.url = "http://other/indi-allsky"
    svc.apply_config(cfg)                                    # new server: start over
    assert len(svc.buffer) == 0 and svc.settings.data["url"] == "http://other/indi-allsky"
    svc.stop()


@pytest.mark.requirement("TC-ASC-040")
@pytest.mark.priority("MVP")
def test_tc_asc_040_test_connection_reports_success_and_failure(qapp, monkeypatch):
    """ASC-040: Test connection runs off-thread and reports a readable result without saving anything."""
    import allskycam.service as service_mod
    svc = _service(qapp, [])
    results = []
    svc.test_finished.connect(lambda ok, msg: results.append((ok, msg)))

    monkeypatch.setattr(service_mod, "AllSkyClient",
                        lambda url, cam: SimpleNamespace(fetch_latest=lambda known: _frame(1, size=2048)))
    svc.test_connection(AllSkyConfig(url="http://h"))
    assert _wait(qapp, lambda: len(results) == 1)
    assert results[0][0] is True and "2 kB" in results[0][1]

    def _fail(url, cam):
        raise AllSkyError("refused")
    monkeypatch.setattr(service_mod, "AllSkyClient", _fail)
    svc.test_connection(AllSkyConfig(url="http://h"))
    assert _wait(qapp, lambda: len(results) == 2)
    assert results[1] == (False, "refused")


@pytest.mark.requirement("TC-ASC-050")
@pytest.mark.priority("MVP")
def test_tc_asc_050_latest_view_shows_the_newest_image(qapp):
    """ASC-050: the Latest tab decodes and shows the newest frame, with when it was fetched."""
    from allskycam.ui import LatestView
    svc = _service(qapp, [])
    view = LatestView(svc)
    view.resize(300, 200)
    assert view.image.pixmap().isNull() and "No image" in view.image.text()
    frame = Frame(data=_jpeg_bytes(80, 40), url="http://h/a.jpg", fetched_at=time.time())
    svc.buffer.append(frame)
    svc.frame_added.emit(frame)
    assert not view.image.pixmap().isNull()
    assert "http://h/a.jpg" in view.caption.text()
    bad = Frame(data=b"not an image", url="http://h/b.jpg", fetched_at=time.time())
    view.show_frame(bad)
    assert "could not be decoded" in view.caption.text()


@pytest.mark.requirement("TC-ASC-060")
@pytest.mark.priority("MVP")
def test_tc_asc_060_animation_loops_through_held_frames_only_while_visible(qapp):
    """ASC-060: the animation cycles through the buffer in order and wraps; its timer runs only while shown."""
    from allskycam.ui import AnimationView
    svc = _service(qapp, [], fps=10.0)
    for n, colour in enumerate(("red", "green", "blue"), start=1):
        svc.buffer.append(Frame(data=_jpeg_bytes(color=colour), url=f"http://h/{n}.jpg", fetched_at=1000.0 + n))
    view = AnimationView(svc)
    view.resize(200, 150)
    assert view._timer.interval() == 100                     # 10 frames per second
    assert not view._timer.isActive()                        # hidden: no timer, no CPU

    view.advance()
    assert "Image 1 of 3" in view.caption.text()
    view.advance()
    view.advance()
    assert "Image 3 of 3" in view.caption.text()
    view.advance()
    assert "Image 1 of 3" in view.caption.text()             # wraps

    view.show()
    assert view._timer.isActive()
    view.hide()
    assert not view._timer.isActive()


@pytest.mark.requirement("TC-ASC-060")
@pytest.mark.priority("MVP")
def test_tc_asc_060_animation_handles_zero_and_one_frame(qapp):
    """ASC-060: an empty buffer shows the placeholder and a single frame shows as a still, without errors."""
    from allskycam.ui import AnimationView
    svc = _service(qapp, [])
    view = AnimationView(svc)
    view.advance()
    assert "Collecting" in view.image.text()
    svc.buffer.append(Frame(data=_jpeg_bytes(), url="http://h/1.jpg", fetched_at=1000.0))
    view.advance()
    view.advance()
    assert "Image 1 of 1" in view.caption.text()


@pytest.mark.requirement("TC-ASC-060")
@pytest.mark.priority("MVP")
def test_tc_asc_060_animation_speed_follows_the_option(qapp):
    """ASC-060: changing the animation speed in Options retimes a running animation."""
    from allskycam.ui import AnimationView
    svc = _service(qapp, [], fps=4.0)
    view = AnimationView(svc)
    assert view._timer.interval() == 250
    cfg = svc.config
    cfg.fps = 20.0
    svc.apply_config(cfg)
    assert view._timer.interval() == 50


@pytest.mark.requirement("TC-ASC-040")
@pytest.mark.priority("MVP")
def test_tc_asc_040_settings_form_loads_validates_and_saves(qapp):
    """ASC-040: the form shows saved options, refuses a bad address, and Save stores a normalised config."""
    from allskycam.ui import SettingsForm
    svc = _service(qapp, [], url="http://h/indi-allsky", interval_s=90, frame_count=12, camera_id=3)
    form = SettingsForm(svc)
    assert form.url_edit.text() == "http://h/indi-allsky"
    assert (form.interval_spin.value(), form.frames_spin.value(), form.camera_spin.value()) == (90, 12, 3)

    form.url_edit.setText("ftp://nope")
    form.save_btn.click()
    assert "http" in form.message.text() and svc.settings.data["url"] == "http://h/indi-allsky"

    form.url_edit.setText("allsky.local/indi-allsky/")
    form.interval_spin.setValue(45)
    form.pause_check.setChecked(False)
    assert not form.sun_spin.isEnabled()                      # the limit only matters with the pause on
    form.dock_check.setChecked(True)
    form.save_btn.click()
    assert svc.settings.data["url"] == "http://allsky.local/indi-allsky"
    assert svc.settings.data["interval_s"] == 45 and svc.settings.data["pause_daytime"] is False
    assert svc.settings.data["show_dock"] is True


@pytest.mark.requirement("TC-ASC-040")
@pytest.mark.priority("MVP")
def test_tc_asc_040_settings_form_test_button_rejects_a_bad_address_without_a_request(qapp, monkeypatch):
    """ASC-040: Test connection validates locally first; nothing is sent for an unusable address."""
    from allskycam.ui import SettingsForm
    svc = _service(qapp, [])
    monkeypatch.setattr(svc, "test_connection", lambda cfg: pytest.fail("must not be called"))
    form = SettingsForm(svc)
    form.url_edit.setText("")
    form.test_btn.click()
    assert form.message.text()


@pytest.mark.requirement("TC-ASC-070")
@pytest.mark.priority("MVP")
def test_tc_asc_070_dock_close_does_not_clobber_unsaved_form_edits(qapp):
    """ASC-070: closing the pane updates only its own checkbox; text typed but not yet saved survives."""
    from allskycam.ui import SettingsForm
    svc = _service(qapp, [], show_dock=True)
    form = SettingsForm(svc)
    form.url_edit.setText("http://being-typed")
    svc.set_show_dock(False)
    assert form.dock_check.isChecked() is False
    assert form.url_edit.text() == "http://being-typed"
    assert svc.settings.data["show_dock"] is False


# ---------------------------------------------------------------------------
# Plugin class wiring
# ---------------------------------------------------------------------------

class FakeContext:
    def __init__(self, settings=None, site=None):
        self._settings = settings or MemorySettings({"url": "http://h/indi-allsky"})
        self._site = site
        self.dock_calls = []

    def settings(self):
        return self._settings

    def site_location(self):
        return self._site

    def set_dock_visible(self, visible):
        self.dock_calls.append(visible)


@pytest.mark.requirement("TC-ASC-080")
@pytest.mark.priority("MVP")
def test_tc_asc_080_plugin_declares_a_top_level_section_dock_and_icon():
    """ASC-080: the plugin asks for its own sidebar entry, a dock pane, and paints its own icon."""
    from allskycam import AllSkyCamPlugin
    p = AllSkyCamPlugin()
    assert (p.panel_level, p.panel_label, p.dock_label) == ("top", "AllSkyCam", "AllSkyCam")
    assert callable(p.panel_icon_painter())


@pytest.mark.requirement("TC-ASC-080")
@pytest.mark.priority("MVP")
def test_tc_asc_080_icon_paints_without_error(qapp):
    """ASC-080: the sidebar icon renders through Galileo's real icon pipeline."""
    from galileo.ui import icons

    from allskycam import AllSkyCamPlugin
    icons.ICONS["plugin.test_asc"] = AllSkyCamPlugin().panel_icon_painter()
    try:
        icon = icons.make_icon("plugin.test_asc", "#ffffff", 26)
        assert not icon.pixmap(26, 26).isNull()
    finally:
        icons.ICONS.pop("plugin.test_asc", None)


@pytest.mark.requirement("TC-ASC-070")
@pytest.mark.priority("MVP")
def test_tc_asc_070_plugin_builds_page_and_dock_over_one_shared_service(qapp):
    """ASC-070: the page and the dock pane read one service (one download for both), and the saved pane state is honoured."""
    from allskycam import AllSkyCamPlugin
    from allskycam.ui import AllSkyCamPage, DockPane
    p = AllSkyCamPlugin()
    ctx = FakeContext(MemorySettings({"url": "http://h/indi-allsky", "show_dock": True}))
    p.activate(ctx)
    try:
        page, dock = p.build_page(), p.build_dock_widget()
        assert isinstance(page, AllSkyCamPage) and isinstance(dock, DockPane)
        assert p.dock_initially_visible() is True
        p.on_dock_visibility_changed(False)
        assert ctx.settings().data["show_dock"] is False
        assert p._service.config.show_dock is False
    finally:
        p.deactivate()


@pytest.mark.requirement("TC-ASC-070")
@pytest.mark.priority("MVP")
def test_tc_asc_070_saving_the_pane_option_opens_or_closes_the_pane(qapp):
    """ASC-070: ticking 'Show animation pane' and saving asks the host to open the dock (and unticking closes it)."""
    from allskycam import AllSkyCamPlugin
    p = AllSkyCamPlugin()
    ctx = FakeContext()
    p.activate(ctx)
    try:
        cfg = p._service.config
        cfg.show_dock = True
        p._service.apply_config(cfg)
        assert ctx.dock_calls[-1] is True
        cfg.show_dock = False
        p._service.apply_config(cfg)
        assert ctx.dock_calls[-1] is False
    finally:
        p.deactivate()


@pytest.mark.requirement("TC-ASC-100")
@pytest.mark.priority("P2")
def test_tc_asc_100_plugin_offers_a_dashboard_view_over_the_shared_service(qapp):
    """ASC-100: the plugin offers a Dashboard view that plays the shared service's animation and scales to the space it is given."""
    from allskycam import AllSkyCamPlugin
    from allskycam.ui import AnimationView, DashboardView
    p = AllSkyCamPlugin()
    assert p.dashboard_label == "AllSkyCam"
    p.activate(FakeContext())
    try:
        view = p.build_dashboard_widget()
        assert isinstance(view, DashboardView)
        assert isinstance(view.animation, AnimationView)
        assert view.animation._service is p._service
        view.resize(640, 360)                      # takes whatever height the Dashboard gives it
        assert view.animation.image.height() > 120
    finally:
        p.deactivate()


@pytest.mark.requirement("TC-ASC-090")
@pytest.mark.priority("MVP")
def test_tc_asc_090_disabled_plugin_is_inert(qapp):
    """ASC-090: disabling stops fetching and closes the pane; enabling resumes both."""
    from allskycam import AllSkyCamPlugin
    p = AllSkyCamPlugin()
    ctx = FakeContext(MemorySettings({"url": "http://h/indi-allsky", "show_dock": True}))
    p.activate(ctx)
    try:
        assert p._service.running
        p.on_enabled_changed(False)
        assert not p._service.running and ctx.dock_calls[-1] is False
        p.on_enabled_changed(True)
        assert p._service.running and ctx.dock_calls[-1] is True
    finally:
        p.deactivate()
    assert not p._service.running


@pytest.mark.requirement("TC-ASC-090")
@pytest.mark.priority("MVP")
def test_tc_asc_090_plugin_help_covers_every_control_on_the_page(qapp):
    """ASC-090: the page is tagged with the plugin's screen id and every control has a help entry."""
    from pathlib import Path

    from galileo import help as help_content
    from galileo.ui.help import audit, tag_help_screen

    import allskycam
    from allskycam import AllSkyCamPlugin
    help_content.register_dir(Path(allskycam.__file__).parent / "help")
    p = AllSkyCamPlugin()
    p.activate(FakeContext())
    try:
        for built in (p.build_page(), p.build_options_page()):
            page = tag_help_screen(built, "plugin.AllSkyCamPlugin")
            assert help_content.load("plugin.AllSkyCamPlugin") is not None
            assert audit(page) == []
    finally:
        p.deactivate()


# ---------------------------------------------------------------------------
# End to end against a local server that behaves like indi-allsky's /latestimage
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_allsky():
    """A real HTTP server: /indi-allsky/latestimage 302s to the newest timestamped image."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = {"newest": "img_0001.jpg", "login": False, "hits": []}
    body = b"\xff\xd8\xff\xe0realjpegbytes"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["hits"].append(self.path)
            path = self.path.split("?")[0]
            if path == "/indi-allsky/latestimage":
                self.send_response(302)
                self.send_header("Location", "/indi-allsky/login" if state["login"] else f"/indi-allsky/images/{state['newest']}")
                self.end_headers()
            elif path.startswith("/indi-allsky/images/"):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/indi-allsky/login":
                page = b"<html>log in</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["base"] = f"http://127.0.0.1:{server.server_port}/indi-allsky"
    state["body"] = body
    yield state
    server.shutdown()
    server.server_close()


@pytest.mark.requirement("TC-ASC-010")
@pytest.mark.priority("MVP")
def test_tc_asc_010_end_to_end_redirect_download_and_unchanged_detection(fake_allsky):
    """ASC-010/020: against a real server, the redirect is followed, the bytes arrive intact, a repeat fetch is
    recognised as unchanged without re-downloading, and a newer image is picked up."""
    client = AllSkyClient(fake_allsky["base"])
    first = client.fetch_latest()
    assert first.data == fake_allsky["body"] and first.url.endswith("/images/img_0001.jpg")

    hits_before = len(fake_allsky["hits"])
    assert client.fetch_latest(known_url=first.url) is None
    assert not any("/images/" in h for h in fake_allsky["hits"][hits_before:])   # redirect only; no image GET

    fake_allsky["newest"] = "img_0002.jpg"
    second = client.fetch_latest(known_url=first.url)
    assert second.url.endswith("/images/img_0002.jpg")


@pytest.mark.requirement("TC-ASC-010")
@pytest.mark.priority("MVP")
def test_tc_asc_010_end_to_end_camera_id_is_sent_and_login_page_is_explained(fake_allsky):
    """ASC-010/020: ?camera_id reaches the server; a login redirect becomes an explanatory error."""
    AllSkyClient(fake_allsky["base"], camera_id=2).fetch_latest()
    assert any(h.endswith("latestimage?camera_id=2") for h in fake_allsky["hits"])

    fake_allsky["login"] = True
    with pytest.raises(AllSkyError, match="login"):
        AllSkyClient(fake_allsky["base"]).fetch_latest()


@pytest.mark.requirement("TC-ASC-020")
@pytest.mark.priority("MVP")
def test_tc_asc_020_end_to_end_unreachable_server_is_a_readable_error():
    """ASC-020: connection refused surfaces as AllSkyError, not a raw requests exception."""
    with pytest.raises(AllSkyError, match="Could not reach"):
        AllSkyClient("http://127.0.0.1:1/indi-allsky", timeout=2).fetch_latest()
