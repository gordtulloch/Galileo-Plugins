# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Translation from core's status dictionaries to the keys the apps read."""

import pytest

from mobileconnect.state import StateTracker, translate, translate_observable


@pytest.mark.requirement("TC-MCON-320")
@pytest.mark.priority("MVP")
def test_tc_mcon_320_mount_status_uses_degrees_and_says_what_it_is_doing():
    keys = translate("mount.status", {"right_ascension": 6.0, "declination": 10.0, "altitude": 30.0, "azimuth": 90.0,
                                      "tracking": True, "slewing": False, "at_park": False})
    assert keys["mount.position"] == {"ra_deg": 90.0, "dec_deg": 10.0, "alt_deg": 30.0, "az_deg": 90.0}
    assert keys["mount.connected"] is True and keys["activity.mount"] == "Tracking"
    assert translate("mount.status", {"at_park": True})["activity.mount"] == "Parked"
    assert translate("mount.status", {"right_ascension": None})["mount.position"]["ra_deg"] is None


@pytest.mark.requirement("TC-MCON-320")
@pytest.mark.priority("MVP")
def test_tc_mcon_320_imaging_series_and_a_large_histogram_is_shrunk():
    keys = translate("imaging.status", {
        "capturing": True, "series_done": 2, "series_total": 4,
        "frame": {"stats": {"star_count": 90, "hfr": 2.5}, "histogram": {"counts": list(range(1000)), "bins": []}}})
    assert keys["imaging.state"] == "Series" and keys["imaging.progress"] == 0.5 and keys["camera.state"] == "Exposing"
    assert keys["imaging.stats"] == {"stars": 90, "hfr": 2.5, "fwhm": None}
    assert len(keys["imaging.histogram"]) == 64 and sum(keys["imaging.histogram"]) == sum(range(1000))
    assert translate("imaging.status", {"capturing": False})["imaging.state"] == "Idle"


@pytest.mark.requirement("TC-MCON-320")
@pytest.mark.priority("MVP")
def test_tc_mcon_320_exposure_and_live_stack_progress_are_published_and_cleared():
    keys = translate("imaging.status", {
        "capturing": True, "series_done": 3, "series_total": 10, "exposure": {"seconds": 60.0, "elapsed": 15.0},
        "stack": {"enabled": True, "frames": 3, "requested": 10, "total_exposure_s": 180.0, "method": "astroalign"}})
    assert keys["imaging.exposure_s"] == 60.0 and keys["imaging.exposure_progress"] == 0.25
    assert keys["imaging.stack.count"] == 3 and keys["imaging.stack.progress"] == 0.3
    assert keys["imaging.stack.total_exposure_s"] == 180.0 and keys["imaging.stack.method"] == "astroalign"
    idle = translate("imaging.status", {"capturing": False})
    assert idle["imaging.exposure_progress"] is None and idle["imaging.stack.count"] is None
    assert idle["imaging.stack.progress"] is None                         # an app drawing the bars drops them


@pytest.mark.requirement("TC-MCON-320")
@pytest.mark.priority("MVP")
def test_tc_mcon_320_session_flip_guiding_and_safety():
    assert translate("session.status", None)["session.state"] == "Idle"
    session = translate("session.status", {"name": "M42", "state": "running", "frames_done": 3, "frames_total": 12, "block": "Series"})
    assert session["session.state"] == "Running" and session["session.progress"] == 0.25 and session["session.name"] == "M42"
    flip = translate("flip.status", {"phase": "due_soon", "minutes_to_flip": 3.5, "enabled": True, "why_not": ""})
    assert flip["flip.state"] == "Due soon" and flip["flip.minutes"] == 3.5 and flip["flip.why_not"] is None
    guiding = translate("guiding.status", {"connected": True, "app_state": "Guiding", "rms": {"ra": 0.4, "dec": 0.3},
                                           "trace": [[1.0, 1, 0.1, 0.2, 40]]})
    assert guiding["guiding.state"] == "Guiding" and guiding["guiding.rms_ra"] == 0.4 and guiding["guiding.star_snr"] == 40
    assert translate("guiding.status", {"connected": False})["guiding.state"] == "Disconnected"
    safety = translate("safety.status", {"safe": False, "explanation": "Rain"})
    assert safety["safety.overall"] == "unsafe" and safety["safety.inputs"] == {"Safety monitor": "Rain"}
    assert translate("safety.status", {"safe": None})["safety.overall"] == "unknown"
    assert translate("safety.events", {"events": [], "pending": 0})["safety.acknowledged"] is True


@pytest.mark.requirement("TC-MCON-320")
@pytest.mark.priority("MVP")
def test_tc_mcon_320_observable_fragments_and_an_unknown_one_passes_through():
    assert translate_observable("guiding.rms", {"ra": 0.4, "dec": 0.3}) == {"guiding.rms_ra": 0.4, "guiding.rms_dec": 0.3}
    assert translate_observable("guiding.state", {"connected": False})["guiding.state"] == "Disconnected"
    assert translate_observable("mount.tracking", "Sidereal") == {"mount.tracking_rate": "Sidereal", "mount.tracking": True}
    assert translate_observable("mount.position", None) == {"mount.position": None}
    assert translate_observable("weather.readings", {"wind_kmh": 8, "nested": {}}) == {"weather.wind_kmh": 8}
    assert translate_observable("focus.new_thing", 1) == {"focus.new_thing": 1}


@pytest.mark.requirement("TC-MCON-310")
@pytest.mark.priority("MVP")
def test_tc_mcon_310_the_tracker_sends_only_what_changed_and_each_trace_sample_once():
    tracker = StateTracker()
    assert tracker.update("p", {"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert tracker.update("p", {"a": 1, "b": 3}) == {"b": 3}
    assert tracker.snapshot("p") == {"a": 1, "b": 3}
    first = {"trace": [[1.0, 1, 0.1, 0.2, 40], [2.0, 2, 0.3, 0.4, 40]]}
    assert tracker.new_trace_samples("p", first) == [{"ra": 0.1, "dec": 0.2}, {"ra": 0.3, "dec": 0.4}]
    assert tracker.new_trace_samples("p", first) == []
    first["trace"].append([3.0, 3, 0.5, 0.6, 40])
    assert tracker.new_trace_samples("p", first) == [{"ra": 0.5, "dec": 0.6}]
