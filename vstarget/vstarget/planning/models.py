# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Data models for variable star target planning (adapted from VSTarget planning/models.py)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AavsoTarget:
    """One variable-star target from the AAVSO Target Tool."""
    name: str
    ra_deg: float
    dec_deg: float
    section: str = ""
    priority: int = 3
    solar_conjunction: bool = False
    magnitude_range: str = ""
    type_code: str = ""
    notes: str = ""


@dataclass
class FilterConfig:
    """Per-filter exposure parameters for an observation plan."""
    filter_name: str
    exposure_s: float
    count: int
    interval_s: float = 120.0
    binning: int = 1


@dataclass
class ObservationPlan:
    """Observation plan for one variable star target."""
    target_name: str
    ra_deg: float = 0.0
    dec_deg: float = 0.0
    filter_configs: list[FilterConfig] = field(default_factory=list)

    def add_filter_config(
        self,
        filter_name: str,
        exposure_s: float,
        count: int,
        interval_s: float = 120.0,
        binning: int = 1,
    ) -> None:
        self.filter_configs.append(FilterConfig(
            filter_name=filter_name,
            exposure_s=exposure_s,
            count=count,
            interval_s=interval_s,
            binning=binning,
        ))

    def __str__(self) -> str:
        return self.target_name


@dataclass
class TransformationCoefficients:
    """Photometric transformation coefficients for one telescope/filter set (VST-AN-060)."""
    Tbv: float = 0.0
    Tvr: float = 0.0
    Tr: float = 0.0
    Tv: float = 0.0
    Tb: float = 0.0


# ---------------------------------------------------------------------------
# AAVSO Target Tool list models (restored from the standalone VSTarget app)
# ---------------------------------------------------------------------------
#
# These sit alongside the planner's `AavsoTarget`/`ObservationPlan` above:
# `AavsoTarget` is the planner service's reduced view of a target, while
# `AAVSOTarget` below is the full Target Tool record the planning panel's
# variable list displays, sorts, filters and tooltips.  `ObservingTarget`
# pairs one of those with the per-target capture parameters the user edits in
# the Observation Targets table.

# Maps the obs_section display values returned by the AAVSO API to their
# corresponding query codes used in the /targets endpoint.
SECTION_CODES: dict[str, str] = {
    "Alerts / Campaigns": "ac",
    "Cataclysmic Variables": "cv",
    "Eclipsing Variables": "eb",
    "Short Period Pulsators": "spp",
    "Long Period Variables": "lpv",
    "Young Stellar Objects": "yso",
    "High Energy Targets": "het",
    "Exoplanets": "ep",
    "Miscellaneous": "misc",
}

# Reverse map: code -> display name (used in the UI checkboxes)
SECTION_NAMES: dict[str, str] = {v: k for k, v in SECTION_CODES.items()}


# Pre-configured telescope location presets.  Coordinates are approximate;
# users should verify them, or pick "Custom" and enter their own site.
TELESCOPE_PRESETS: dict[str, dict[str, float]] = {
    "T5 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025, "longitude": -105.5319,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
    "T7 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025, "longitude": -105.5319,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
    "T11 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025, "longitude": -105.5319,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
    "T24 – New Mexico Skies (Mayhill, NM)": {
        "latitude": 32.9025, "longitude": -105.5319,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
    "T17 – Siding Spring (NSW, Australia)": {
        "latitude": -31.2733, "longitude": 149.0644,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
    "T30 – Siding Spring (NSW, Australia)": {
        "latitude": -31.2733, "longitude": 149.0644,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
    "T21 – Sierra Remote Obs. (Auberry, CA)": {
        "latitude": 37.0703, "longitude": -119.4097,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
    "Custom": {
        "latitude": 0.0, "longitude": 0.0,
        "target_altitude": 20.0, "sun_altitude": -5.0,
    },
}


@dataclass
class AAVSOTarget:
    """One full variable-star record from the AAVSO Target Tool API (VST-010)."""

    star_name: str
    ra: float               # decimal degrees (as the API returns it)
    dec: float              # decimal degrees
    var_type: str = ""
    min_mag: float | None = None
    min_mag_band: str = ""
    max_mag: float | None = None
    max_mag_band: str = ""
    period: float | None = None
    obs_cadence: float | None = None
    obs_mode: str = ""
    obs_section: list[str] = field(default_factory=list)
    filters: str = ""       # suggested filters from AAVSO
    other_info: str = ""
    last_data_point: int | None = None
    priority: bool = False
    constellation: str = ""
    solar_conjunction: bool = False

    @property
    def ra_hours(self) -> float:
        """RA in decimal hours — how the variable list and session targets show it."""
        return self.ra / 15.0

    @classmethod
    def from_api(cls, data: dict) -> AAVSOTarget:
        """Construct from one entry of the AAVSO Target Tool JSON response."""
        return cls(
            star_name=data.get("star_name", ""),
            ra=float(data.get("ra", 0.0)),
            dec=float(data.get("dec", 0.0)),
            var_type=data.get("var_type", ""),
            min_mag=data.get("min_mag"),
            min_mag_band=data.get("min_mag_band", "") or "",
            max_mag=data.get("max_mag"),
            max_mag_band=data.get("max_mag_band", "") or "",
            period=data.get("period"),
            obs_cadence=data.get("obs_cadence"),
            obs_mode=data.get("obs_mode", "") or "",
            obs_section=data.get("obs_section") or [],
            filters=data.get("filter", "") or "",   # API key is 'filter'
            other_info=data.get("other_info", "") or "",
            last_data_point=data.get("last_data_point"),
            priority=bool(data.get("priority", False)),
            constellation=data.get("constellation", "") or "",
            solar_conjunction=bool(data.get("solar_conjunction", False)),
        )


@dataclass
class ObservingTarget:
    """An AAVSO target added to the observation plan, with the per-target capture
    parameters that become its session Image blocks (VST-050, VST-060).

    The four parameter strings are parallel comma-separated lists — one value
    per filter — exactly as the Observation Targets table edits them.
    """

    aavso: AAVSOTarget
    script_filters: str = "V,B,I"
    script_counts: str = "4,4,4"
    script_intervals: str = "30,30,30"
    script_binning: str = "1,1,1"
