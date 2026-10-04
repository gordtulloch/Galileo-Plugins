# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""AAVSO Variable Star Plotter (VSP) comparison-star client (VST-EXT-010).

The second half of this plugin's external AAVSO interface: where
:class:`~vstarget.planning.aavso_client.AavsoTargetToolClient` answers *what to
observe*, VSP answers *what to measure it against* - the sequence of
comparison stars, with their standard magnitudes, that
:class:`~vstarget.analysis.VariableStarAnalysis` performs ensemble differential
photometry against (`VST-AN-040`).

Stars come back in the shape the analysis service consumes directly: ``ra`` and
``dec`` in decimal degrees (VSP publishes them sexagesimally) and one
``mag_<band>`` key per requested filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_VSP_CHART_URL = "https://www.aavso.org/apps/vsp/api/chart/"

# The VSP chart API occasionally stalls under load rather than answering
# promptly, so a read timeout is retried with increasing waits instead of
# failing a whole analysis run on one slow response.
_MAX_ATTEMPTS = 5
_RETRY_DELAYS_S = (10, 20, 40, 80)
_TIMEOUT_S = 20


@dataclass
class ComparisonStarChart:
    """One VSP chart: its AAVSO chart ID and the usable comparison stars on it.

    The chart ID is not decoration - an AAVSO WebObs report cites the chart its
    comparison magnitudes came from, so it has to survive alongside the stars.
    """

    chart_id: str = "na"
    stars: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.stars)

    def __iter__(self):
        return iter(self.stars)


class AavsoVspClient:
    """Downloads comparison-star sequences from the AAVSO VSP API (VST-EXT-010)."""

    def __init__(self, maglimit: float = 18.5) -> None:
        self.maglimit = maglimit

    async def fetch_comparison_stars(
        self,
        target,
        filter_band: str = "V",
        field_of_view: float = 18.5,
    ) -> ComparisonStarChart:
        """Return the comparison-star chart for *target*.

        *target* may be an ``AAVSOTarget``, an ``AavsoTarget``, a mapping with
        ra/dec keys, or any object carrying ``ra``/``dec`` (or ``ra_deg``/
        ``dec_deg``) in decimal degrees. *field_of_view* is in arcminutes.

        A failed lookup returns an empty chart rather than raising: a missing
        sequence should leave the rest of an analysis run intact, exactly as a
        failed target download does.
        """
        import asyncio

        ra_deg, dec_deg = target_coords(target)
        try:
            payload = await asyncio.to_thread(
                self._get_chart, ra_deg, dec_deg, field_of_view
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as an empty chart
            logger.warning("AAVSO VSP fetch failed: %s", exc)
            return ComparisonStarChart()

        chart = parse_chart(payload, filter_band)
        logger.info(
            "Downloaded %d comparison star(s) in %s (chart %s)",
            len(chart.stars), filter_band, chart.chart_id,
        )
        return chart

    def _get_chart(self, ra_deg: float, dec_deg: float, field_of_view: float) -> dict:
        """Fetch the chart JSON, retrying a stalled request."""
        import time

        import requests

        params = {
            "format": "json",
            "fov": field_of_view,
            "maglimit": self.maglimit,
            "ra": ra_deg,
            "dec": dec_deg,
        }
        attempt = 1
        while True:
            try:
                resp = requests.get(_VSP_CHART_URL, params=params, timeout=_TIMEOUT_S)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.ReadTimeout:
                if attempt >= _MAX_ATTEMPTS:
                    raise
                delay = _RETRY_DELAYS_S[attempt - 1]
                logger.warning(
                    "AAVSO VSP request timed out (attempt %d/%d); retrying in %ds…",
                    attempt, _MAX_ATTEMPTS, delay,
                )
                time.sleep(delay)
                attempt += 1


def parse_chart(payload: dict, filter_band: str = "V") -> ComparisonStarChart:
    """Build a :class:`ComparisonStarChart` from a VSP chart JSON response.

    Stars without a magnitude in *filter_band* are dropped - they cannot take
    part in an ensemble measured in that band.
    """
    band_key = f"mag_{filter_band.lower()}"
    chart = ComparisonStarChart(chart_id=str(payload.get("chartid", "na")))

    for star in payload.get("photometry", []) or []:
        magnitude = error = None
        for band in star.get("bands", []) or []:
            if band.get("band") != filter_band:
                continue
            try:
                magnitude = float(band["mag"])
                error = float(band.get("error") or 0.0)
            except (TypeError, ValueError, KeyError):
                magnitude = None
            break
        if magnitude is None:
            continue

        try:
            ra_deg = parse_ra(star["ra"])
            dec_deg = parse_dec(star["dec"])
        except (KeyError, TypeError, ValueError):
            logger.debug("Skipping VSP star with unparseable coordinates: %r", star)
            continue

        chart.stars.append({
            "auid": star.get("auid", ""),
            "label": str(star.get("label", star.get("auid", ""))),
            "ra": ra_deg,
            "dec": dec_deg,
            band_key: magnitude,
            "error": error,
        })

    return chart


def target_coords(target) -> tuple[float, float]:
    """Pull (ra_deg, dec_deg) out of whichever target representation is given."""
    if isinstance(target, dict):
        ra = target.get("ra", target.get("ra_deg"))
        dec = target.get("dec", target.get("dec_deg"))
    else:
        ra = getattr(target, "ra", getattr(target, "ra_deg", None))
        dec = getattr(target, "dec", getattr(target, "dec_deg", None))
    if ra is None or dec is None:
        raise ValueError(f"Cannot determine coordinates for target {target!r}")
    return float(ra), float(dec)


def parse_ra(value) -> float:
    """VSP right ascension to decimal degrees.

    VSP publishes sexagesimal hours ('08:53:44.67'); a plain number is taken as
    degrees, since that is what every target in this plugin already stores.
    """
    if isinstance(value, (int, float)):
        return float(value)
    hours, minutes, seconds = _sexagesimal(value)
    return (abs(hours) + minutes / 60.0 + seconds / 3600.0) * 15.0


def parse_dec(value) -> float:
    """VSP declination to decimal degrees ('+57:48:40.6')."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    degrees, minutes, seconds = _sexagesimal(text)
    magnitude = abs(degrees) + minutes / 60.0 + seconds / 3600.0
    return -magnitude if text.startswith("-") else magnitude


def _sexagesimal(value: str) -> tuple[float, float, float]:
    """Split 'DD:MM:SS.s' (or space-separated) into three floats.

    A bare number parses as the leading field, so '12.5' is 12.5 units.
    """
    parts = [p for p in str(value).replace(":", " ").split() if p]
    if not parts:
        raise ValueError(f"empty coordinate: {value!r}")
    fields = [float(p) for p in parts[:3]]
    while len(fields) < 3:
        fields.append(0.0)
    return fields[0], fields[1], fields[2]
