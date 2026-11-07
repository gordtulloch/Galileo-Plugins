# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The ``/mobileconnect/v1/sky/*`` routes: core's ``galileo.skystream`` on the wire, plus what it leaves to its host.

The service serves positions, magnitudes and indexes, and takes the ephemeris and site as inputs, so those come from
here: the planets from astropy, the site and horizon from the Pier's Observatory record, and names from the same
catalog order ``bundled_catalog`` uses. All of it is read-only and runs at the Gateway's lowest priority: while a
session runs the service lowers detail rather than delay anything.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import web
from galileo.skystream import SkyStreamError, SkyStreamService, bundled_catalog

from mobileconnect import API, PLUGIN

logger = logging.getLogger(__name__)

def planet_positions(when: dt.datetime) -> list[dict[str, Any]]:
    """Core's own ephemeris, the one the desktop's sky views use. Slow the first time (astropy's tables), so this
    always runs on a worker thread."""
    from galileo.planning.star_atlas import solar_system_positions
    return [{"name": b["name"], "ra_deg": b["ra_deg"], "dec_deg": b["dec_deg"], "magnitude": b["mag"]}
            for b in solar_system_positions(when)]


def constellations() -> dict[str, Any]:
    """Core's figures and names. Its first use may fetch and cache them, so this runs on a worker thread; offline with
    no copy they're empty, and the app simply draws none."""
    from galileo.planning.star_atlas import load_constellation_boundaries, load_constellation_lines
    lines, outlines = load_constellation_lines(), load_constellation_boundaries()
    return {
        "lines": [[[round(float(ra), 3), round(float(dec), 3)]
                   for ra, dec in zip(lines.ra[lines.polyline(i)], lines.dec[lines.polyline(i)], strict=True)]
                  for i in range(len(lines))],
        "names": [{"name": name, "code": outlines.codes[i] if i < len(outlines.codes) else "",
                   "ra_deg": round(float(outlines.center_ra[i]), 3), "dec_deg": round(float(outlines.center_dec[i]), 3)}
                  for i, name in enumerate(outlines.names)]}


def site_of(pier: str) -> dict[str, Any] | None:
    """The Pier's Observatory as the wire has it, or ``None`` if it has no location. Read-only: the app can't change it."""
    from galileo.library.models.observatory import PierRecord
    from galileo.observatory import list_horizon_points
    try:
        record = PierRecord.get_or_none(PierRecord.id == int(pier))
    except ValueError:
        return None
    observatory = getattr(record, "observatory", None)
    if observatory is None or observatory.latitude is None or observatory.longitude is None:
        return None
    horizon = [{"az_deg": az, "alt_deg": alt} for az, alt in list_horizon_points(observatory)]
    body = {"name": observatory.name, "latitude_deg": observatory.latitude, "longitude_deg": observatory.longitude,
            "elevation_m": observatory.elevation_m or 0.0, "horizon": horizon}
    body["version"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    return body


def altitude_azimuth(site: dict[str, Any], ra_deg: float, dec_deg: float, when: dt.datetime) -> tuple[float, float]:
    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    location = EarthLocation(lat=site["latitude_deg"] * u.deg, lon=site["longitude_deg"] * u.deg,
                             height=site.get("elevation_m", 0.0) * u.m)
    frame = AltAz(obstime=Time(when), location=location)
    place = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs").transform_to(frame)
    return float(place.alt.deg), float(place.az.deg)


def horizon_altitude(horizon: list[dict[str, float]], azimuth: float) -> float:
    """The skyline's height at an azimuth. No profile means a flat horizon."""
    from galileo.planning.visibility import HorizonProfile
    return HorizonProfile([(p["az_deg"], p["alt_deg"]) for p in horizon]).min_altitude_at(azimuth)


def object_names() -> dict[int, dict[str, Any]]:
    """Everything with a name, by its index in ``bundled_catalog``: stars first, then the atlas in the same order."""
    from galileo.planning.sky_atlas import SkyAtlas
    from galileo.planning.star_atlas import load_star_catalog
    stars = load_star_catalog()
    named = {i: {"name": stars.names[i] or stars.designations[i]}
             for i in range(len(stars)) if stars.names[i] or stars.designations[i]}
    named.update({len(stars) + i: {"name": o.primary_name, "type": str(o.object_type)}
                  for i, o in enumerate(SkyAtlas().filter())})
    return named


class SkyRoutes:
    def __init__(self, *, service: SkyStreamService | None = None, ephemeris: Callable[[dt.datetime], list[dict[str, Any]]] = planet_positions,
                 site: Callable[[str], dict[str, Any] | None] = site_of, names: Callable[[], dict[int, dict[str, Any]]] = object_names,
                 figures: Callable[[], dict[str, Any]] = constellations, busy: Callable[[], bool] = lambda: False) -> None:
        self._ephemeris_of, self._site, self._names_of, self._figures_of = ephemeris, site, names, figures
        self._figures: dict[str, Any] | None = None
        self._names: dict[int, dict[str, Any]] | None = None
        self._service = service or SkyStreamService(bundled_catalog, ephemeris=ephemeris, busy=busy)

    def register(self, routes) -> None:
        add = routes.register
        add(PLUGIN, "GET", f"{API}/sky/catalog", "view", self.catalog)
        add(PLUGIN, "GET", f"{API}/sky/ephemeris", "view", self.ephemeris)
        add(PLUGIN, "GET", f"{API}/sky/site", "view", self.site)
        add(PLUGIN, "GET", f"{API}/sky/constellations", "view", self.constellations)
        add(PLUGIN, "GET", f"{API}/sky/check", "view", self.check)
        add(PLUGIN, "GET", f"{API}/sky/identify", "view", self.identify)

    async def capability(self) -> dict[str, Any]:
        """Measured on first use, which reads the catalog, so never on the listener's own thread."""
        cap = await asyncio.to_thread(self._service.capability)
        out: dict[str, Any] = {"available": cap.available, "reduced": not cap.vr_available,
                               "max_concurrent": cap.max_clients, "max_page": cap.max_page, "rate_per_s": cap.rate_per_s}
        if cap.reason:
            out["reason"] = cap.reason
        return out

    @staticmethod
    def _error(exc: SkyStreamError):
        return web.json_response({"error": str(exc)}, status=exc.status)

    async def catalog(self, peer, request):
        q = request.query
        try:
            ra, dec, radius = float(q["ra"]), float(q["dec"]), float(q["radius"])
            mag, lod = float(q.get("mag", 99.0)), int(q.get("lod", 4))
        except (KeyError, ValueError):
            return web.json_response({"error": "ra, dec and radius are required, as numbers"}, status=400)
        try:
            await asyncio.to_thread(self._service.capability)
            with self._service.stream(peer.peer_id):
                page = await self._service.catalog(ra, dec, radius, mag, lod, q.get("cursor", ""))
        except SkyStreamError as exc:
            return self._error(exc)
        return web.Response(body=page["data"], content_type="application/octet-stream", headers={
            "X-Sky-Next": page["next"], "X-Sky-Total": str(page["total"]), "X-Sky-As-Of": page["as_of"],
            "X-Sky-Degraded": "1" if page["degraded"] else "0"})

    async def ephemeris(self, peer, request):
        q = request.query

        def when(name: str) -> dt.datetime | None:
            return dt.datetime.fromisoformat(q[name].replace("Z", "+00:00")) if q.get(name) else None
        try:
            start, end = when("from"), when("to")
            instant, step = when("t"), float(q.get("step", 600.0))
        except ValueError:
            return web.json_response({"error": "times must be RFC 3339, and step a number"}, status=400)
        try:
            if start is not None and end is not None:
                out = await self._service.ephemeris(start, end, step)
                samples = [{"t": s["time"], "bodies": s["bodies"]} for s in out["series"]]
            else:
                moment = instant or dt.datetime.now(dt.UTC)
                out = await self._service.ephemeris(moment)
                samples = [{"t": moment.isoformat(), "bodies": out["bodies"]}]
        except SkyStreamError as exc:
            return self._error(exc)
        return {"as_of": out["as_of"], "samples": samples}

    async def constellations(self, peer, request) -> dict[str, Any]:
        if self._figures is None:
            found = await asyncio.to_thread(self._figures_of)
            if not found["lines"]:
                return found                 # not kept: core may be able to fetch them next time
            self._figures = found
        return self._figures

    async def _site_or_none(self, pier: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._site, pier)

    async def site(self, peer, request):
        found = await self._site_or_none(request.query.get("pier", ""))
        if found is None:
            return web.json_response({"error": "This Pier's Observatory has no location set."}, status=404)
        return found

    async def check(self, peer, request):
        q = request.query
        try:
            ra, dec = float(q["ra"]), float(q["dec"])
        except (KeyError, ValueError):
            return web.json_response({"error": "ra and dec are required, as numbers"}, status=404)
        found = await self._site_or_none(q.get("pier", ""))
        if found is None:
            return web.json_response({"error": "This Pier's Observatory has no location set."}, status=404)
        alt, az = await asyncio.to_thread(altitude_azimuth, found, ra, dec, dt.datetime.now(dt.UTC))
        skyline = horizon_altitude(found["horizon"], az)
        above = alt > skyline
        out: dict[str, Any] = {"altitude_deg": alt, "azimuth_deg": az, "above_horizon": above}
        if not above:
            out["reason"] = (f"Below the horizon: altitude {alt:.1f}° where the skyline is {skyline:.1f}°."
                             if skyline > 0 else f"Below the horizon: altitude {alt:.1f}°.")
        return out

    async def identify(self, peer, request):
        try:
            index = int(request.query["index"])
        except (KeyError, ValueError):
            return web.json_response({"error": "index must be a number"}, status=404)
        catalog = await asyncio.to_thread(self._service._cat)
        if not 0 <= index < len(catalog.ra):
            return web.json_response({"error": "no such object"}, status=404)
        if self._names is None:
            self._names = await asyncio.to_thread(self._names_of)
        named = self._names.get(index, {})
        out: dict[str, Any] = {"index": index, "kind": "deep_sky" if int(catalog.kind[index]) else "star",
                               "ra_deg": float(catalog.ra[index]), "dec_deg": float(catalog.dec[index]), **named}
        if float(catalog.mag[index]) < 90:
            out["magnitude"] = float(catalog.mag[index])
        return out


__all__ = ["SkyRoutes", "altitude_azimuth", "constellations", "horizon_altitude", "object_names", "planet_positions",
           "site_of"]
