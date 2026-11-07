# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The sky routes over the real wire, with core's real streaming service over a small catalog."""

import datetime as dt

import aiohttp
import pytest
from galileo.skystream import decode_objects

from mobileconnect.clients import VIEWER
from mobileconnect.sky import altitude_azimuth, horizon_altitude, site_of
from tests.test_mcon_routes import App
from tests.world import PIER, World


@pytest.fixture
async def world(link_db):
    w = World()
    await w.start()
    yield w
    await w.stop()


@pytest.fixture
async def viewer(world):
    async with aiohttp.ClientSession() as http:
        yield await App(world, http).pair(role=VIEWER)


@pytest.mark.requirement("TC-MCON-640")
@pytest.mark.priority("MVP")
async def test_tc_mcon_640_catalog_pages_brightest_first_in_cores_encoding(viewer):
    """MCON-640, 660: a Viewer may look; the body decodes with core's own decoder; the cursor continues."""
    status, body, kind = await viewer.request(
        "GET", "/mobileconnect/v1/sky/catalog", query={"ra": 83.8, "dec": -5.4, "radius": 5, "lod": 4})
    assert status == 200 and "octet-stream" in kind
    objects = decode_objects(body)
    assert {o.index for o in objects} == {0, 1, 3}
    assert {o.kind for o in objects} == {0, 1}
    deep = next(o for o in objects if o.index == 3)
    assert deep.mag == 4.0 and deep.ra == 83.82


@pytest.mark.requirement("TC-MCON-680")
@pytest.mark.priority("MVP")
async def test_tc_mcon_680_bad_regions_and_cursors_say_so_and_over_the_rate_is_429(viewer):
    """MCON-680: a typed answer; a client over its rate is told to back off, not failed."""
    path = "/mobileconnect/v1/sky/catalog"
    assert (await viewer.request("GET", path, query={"ra": "x", "dec": 0, "radius": 5}))[0] == 400
    assert (await viewer.request("GET", path, query={"ra": 400, "dec": 0, "radius": 5}))[0] == 400
    assert (await viewer.request("GET", path, query={"ra": 1, "dec": 0, "radius": 5, "cursor": "nonsense"}))[0] == 400
    codes = [(await viewer.request("GET", path, query={"ra": 1, "dec": 0, "radius": 5}))[0] for _ in range(80)]
    assert 429 in codes and 200 in codes and set(codes) <= {200, 429}


@pytest.mark.requirement("TC-MCON-660")
@pytest.mark.priority("MVP")
async def test_tc_mcon_660_ephemeris_for_an_instant_and_a_range(viewer):
    """MCON-660: a time series the client interpolates."""
    path = "/mobileconnect/v1/sky/ephemeris"
    status, out, _ = await viewer.request("GET", path, query={"t": "2026-11-06T03:00:00Z"})
    assert status == 200 and out["samples"][0]["bodies"][0] == {"name": "Moon", "ra_deg": 43.0, "dec_deg": 10.0}
    status, out, _ = await viewer.request("GET", path, query={
        "from": "2026-11-06T00:00:00Z", "to": "2026-11-06T02:00:00Z", "step": 3600})
    assert status == 200 and [s["bodies"][0]["ra_deg"] for s in out["samples"]] == [40.0, 41.0, 42.0]
    assert (await viewer.request("GET", path, query={"t": "not a time"}))[0] == 400


@pytest.mark.requirement("TC-MCON-650")
@pytest.mark.priority("MVP")
async def test_tc_mcon_650_site_check_and_identify(viewer):
    """MCON-650: the site and horizon; a position's altitude and the horizon check; a tapped object's name."""
    status, site, _ = await viewer.request("GET", "/mobileconnect/v1/sky/site", query={"pier": PIER})
    assert status == 200 and site["latitude_deg"] == 49.9 and len(site["horizon"]) == 2
    assert (await viewer.request("GET", "/mobileconnect/v1/sky/site", query={"pier": "99"}))[0] == 404
    status, check, _ = await viewer.request("GET", "/mobileconnect/v1/sky/check", query={"pier": PIER, "ra": 83.8, "dec": -5.4})
    assert status == 200 and -90 <= check["altitude_deg"] <= 90 and isinstance(check["above_horizon"], bool)
    assert check["above_horizon"] or "Below the horizon" in check["reason"]
    status, named, _ = await viewer.request("GET", "/mobileconnect/v1/sky/identify", query={"index": 3})
    assert status == 200 and named["name"] == "M 42" and named["kind"] == "deep_sky" and named["magnitude"] == 4.0
    status, star, _ = await viewer.request("GET", "/mobileconnect/v1/sky/identify", query={"index": 0})
    assert status == 200 and star["kind"] == "star" and star["name"] == "Rigel"
    status, unnamed, _ = await viewer.request("GET", "/mobileconnect/v1/sky/identify", query={"index": 1})
    assert status == 200 and unnamed["kind"] == "star" and "name" not in unnamed
    assert (await viewer.request("GET", "/mobileconnect/v1/sky/identify", query={"index": 99}))[0] == 404


@pytest.mark.requirement("TC-MCON-650")
@pytest.mark.priority("MVP")
async def test_tc_mcon_650_constellations(viewer):
    """MCON-650: the figures and names come from the Master, so the app ships none."""
    status, out, _ = await viewer.request("GET", "/mobileconnect/v1/sky/constellations")
    assert status == 200 and out["lines"] == [[[83.0, -0.3], [84.05, -1.2]]] and out["names"][0]["name"] == "Orion"


@pytest.mark.requirement("TC-MCON-650")
@pytest.mark.priority("MVP")
def test_tc_mcon_650_core_figures_translate_to_the_wire(monkeypatch):
    """Core's real shapes, whichever data it has: polylines of pairs, a name with a place."""
    import numpy as np
    from galileo.planning import star_atlas

    from mobileconnect.sky import constellations
    lines = star_atlas.ConstellationLines(np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0, 6.0]), [0, 3])
    outlines = star_atlas.ConstellationBoundaries(
        np.empty(0), np.empty(0), [0], ["Orion"], np.array([84.0]), np.array([0.0]), ["ORI"])
    monkeypatch.setattr(star_atlas, "load_constellation_lines", lambda: lines)
    monkeypatch.setattr(star_atlas, "load_constellation_boundaries", lambda: outlines)
    assert constellations() == {"lines": [[[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]]],
                                "names": [{"name": "Orion", "code": "ORI", "ra_deg": 84.0, "dec_deg": 0.0}]}


@pytest.mark.requirement("TC-MCON-660")
@pytest.mark.priority("MVP")
def test_tc_mcon_660_planets_come_from_cores_ephemeris():
    from mobileconnect.sky import planet_positions
    bodies = planet_positions(dt.datetime(2026, 11, 6, tzinfo=dt.UTC))
    assert {b["name"] for b in bodies} >= {"Sun", "Moon", "Jupiter"}
    sun = next(b for b in bodies if b["name"] == "Sun")
    assert 215 < sun["ra_deg"] < 230 and -20 < sun["dec_deg"] < -10      # early November
    assert set(sun) == {"name", "ra_deg", "dec_deg", "magnitude"}


@pytest.mark.requirement("TC-MCON-650")
@pytest.mark.priority("MVP")
def test_tc_mcon_650_the_skyline_interpolates_round_the_circle():
    profile = [{"az_deg": 0.0, "alt_deg": 10.0}, {"az_deg": 90.0, "alt_deg": 30.0}]
    assert horizon_altitude(profile, 45.0) == 20.0
    assert horizon_altitude(profile, 225.0) == 20.0         # between 90 and 360: back down to 10
    assert horizon_altitude([], 45.0) == 0.0


@pytest.mark.requirement("TC-MCON-650")
@pytest.mark.priority("MVP")
def test_tc_mcon_650_altitude_agrees_with_a_known_place():
    """Polaris from 50 N stands about 50 degrees up, whenever you look."""
    site = {"latitude_deg": 50.0, "longitude_deg": 0.0, "elevation_m": 0.0}
    alt, _ = altitude_azimuth(site, 37.95, 89.26, dt.datetime(2026, 11, 6, tzinfo=dt.UTC))
    assert 48.5 < alt < 51.5


@pytest.mark.requirement("TC-MCON-650")
@pytest.mark.priority("MVP")
def test_tc_mcon_650_the_site_comes_from_the_real_records(link_db):
    """The Observatory and horizon as Galileo stores them. A Pier with no location has no site."""
    from galileo.library.models.observatory import ObservatoryRecord, PierRecord
    from galileo.observatory import save_horizon_points
    found = ObservatoryRecord.create(name="Home", latitude=49.9, longitude=-97.1, elevation_m=240.0)
    save_horizon_points(found, [(0.0, 5.0), (180.0, 15.0)])
    pier = PierRecord.create(name="East", observatory=found)
    site = site_of(str(pier.id))
    assert site["name"] == "Home" and site["latitude_deg"] == 49.9
    assert site["horizon"] == [{"az_deg": 0.0, "alt_deg": 5.0}, {"az_deg": 180.0, "alt_deg": 15.0}]
    assert site["version"]
    assert site_of("99999") is None and site_of("not a number") is None
