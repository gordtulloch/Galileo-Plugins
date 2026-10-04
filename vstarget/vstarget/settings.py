# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Persistent settings for the VSTarget plugin's planning panel.

Backed by ``QSettings`` under Galileo's own organisation so the plugin's state
sits beside the host application's rather than in its own vendor namespace.
On first use the store is seeded from the standalone VSTarget application's
settings (``AAVSO``/``VSTarget``) if that is present, so a user moving from the
standalone app keeps their API key, site and capture defaults.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_ORG = "Galileo"
_APP = "VSTarget"

# The standalone application this plugin replaces - read once, to seed defaults.
_LEGACY_ORG = "AAVSO"
_LEGACY_APP = "VSTarget"

_DEFAULT_TELESCOPE = "T5 – New Mexico Skies (Mayhill, NM)"


class VstSettings:
    """Typed accessors over the plugin's ``QSettings`` store."""

    def __init__(self) -> None:
        from PySide6.QtCore import QSettings
        self._s = QSettings(_ORG, _APP)
        if not self._s.allKeys():
            self._seed_from_standalone()

    def _seed_from_standalone(self) -> None:
        """Copy the standalone app's settings across, once, on a virgin store."""
        from PySide6.QtCore import QSettings
        try:
            legacy = QSettings(_LEGACY_ORG, _LEGACY_APP)
            keys = legacy.allKeys()
            if not keys:
                return
            for key in keys:
                self._s.setValue(key, legacy.value(key))
            self._s.sync()
            logger.info(
                "Seeded VSTarget plugin settings from the standalone app (%d keys)", len(keys)
            )
        except Exception:
            logger.exception("Could not read standalone VSTarget settings")

    def _value(self, key: str, default: Any, **kwargs: Any) -> Any:
        """QSettings.value() is typed as returning `object`; narrow it to Any."""
        return self._s.value(key, default, **kwargs)

    def sync(self) -> None:
        self._s.sync()

    # --- AAVSO API -------------------------------------------------------

    @property
    def api_key(self) -> str:
        return self._value("api/key", "")

    @api_key.setter
    def api_key(self, v: str) -> None:
        self._s.setValue("api/key", v)

    @property
    def observer_code(self) -> str:
        """AAVSO observer code (AAVSO ID), used on submitted reports."""
        return self._value("api/observer_code", "")

    @observer_code.setter
    def observer_code(self, v: str) -> None:
        self._s.setValue("api/observer_code", v)

    @property
    def selected_sections(self) -> list[str]:
        v = self._value("api/sections", ["ac"])
        if isinstance(v, list):
            return v
        return [v] if v else ["ac"]

    @selected_sections.setter
    def selected_sections(self, v: list[str]) -> None:
        self._s.setValue("api/sections", v)

    # --- Telescope / site ------------------------------------------------

    @property
    def telescope_name(self) -> str:
        return self._value("telescope/name", _DEFAULT_TELESCOPE)

    @telescope_name.setter
    def telescope_name(self, v: str) -> None:
        self._s.setValue("telescope/name", v)

    @property
    def latitude(self) -> float:
        return float(self._value("telescope/latitude", 32.9025))

    @latitude.setter
    def latitude(self, v: float) -> None:
        self._s.setValue("telescope/latitude", float(v))

    @property
    def longitude(self) -> float:
        return float(self._value("telescope/longitude", -105.5319))

    @longitude.setter
    def longitude(self, v: float) -> None:
        self._s.setValue("telescope/longitude", float(v))

    @property
    def target_altitude(self) -> float:
        """Minimum altitude for a target to count as observable."""
        return float(self._value("telescope/target_altitude", 20.0))

    @target_altitude.setter
    def target_altitude(self, v: float) -> None:
        self._s.setValue("telescope/target_altitude", float(v))

    @property
    def sun_altitude(self) -> float:
        """Sun altitude defining dusk/dawn."""
        return float(self._value("telescope/sun_altitude", -5.0))

    @sun_altitude.setter
    def sun_altitude(self, v: float) -> None:
        self._s.setValue("telescope/sun_altitude", float(v))

    # --- New-target capture defaults -------------------------------------

    @property
    def default_filters(self) -> str:
        return self._value("defaults/filters", "V,B,I")

    @default_filters.setter
    def default_filters(self, v: str) -> None:
        self._s.setValue("defaults/filters", v)

    @property
    def default_counts(self) -> str:
        return self._value("defaults/counts", "4,4,4")

    @default_counts.setter
    def default_counts(self, v: str) -> None:
        self._s.setValue("defaults/counts", v)

    @property
    def default_intervals(self) -> str:
        return self._value("defaults/intervals", "30,30,30")

    @default_intervals.setter
    def default_intervals(self, v: str) -> None:
        self._s.setValue("defaults/intervals", v)

    @property
    def default_binning(self) -> str:
        return self._value("defaults/binning", "1,1,1")

    @default_binning.setter
    def default_binning(self, v: str) -> None:
        self._s.setValue("defaults/binning", v)

    # --- Session block options -------------------------------------------
    #
    # These replace the standalone app's iTelescope script directives
    # (#defocus / #vphot / #platesolve / #filteroffsets): a Galileo session is
    # built from blocks, so the panel offers the blocks that have meaning here.

    @property
    def block_platesolve(self) -> bool:
        return self._value("session/platesolve", True, type=bool)

    @block_platesolve.setter
    def block_platesolve(self, v: bool) -> None:
        self._s.setValue("session/platesolve", bool(v))

    @property
    def block_autofocus(self) -> bool:
        return self._value("session/autofocus", False, type=bool)

    @block_autofocus.setter
    def block_autofocus(self, v: bool) -> None:
        self._s.setValue("session/autofocus", bool(v))

    @property
    def block_dither(self) -> bool:
        return self._value("session/dither", False, type=bool)

    @block_dither.setter
    def block_dither(self, v: bool) -> None:
        self._s.setValue("session/dither", bool(v))

    @property
    def block_guiding(self) -> bool:
        return self._value("session/guiding", False, type=bool)

    @block_guiding.setter
    def block_guiding(self, v: bool) -> None:
        self._s.setValue("session/guiding", bool(v))

    # --- Analysis --------------------------------------------------------

    @property
    def analysis_working_dir(self) -> str:
        return self._value("analysis/working_dir", "")

    @analysis_working_dir.setter
    def analysis_working_dir(self, v: str) -> None:
        self._s.setValue("analysis/working_dir", v)

    # --- Exposure calibrations (per telescope + filter, JSON-encoded) -----

    def save_exposure_calibration(self, calib: dict) -> None:
        """Store one calibration keyed by telescope + filter (overwrites)."""
        key = "expcal/{}_{}".format(calib["telescope"], calib["filter_band"])
        self._s.setValue(key, json.dumps(calib))

    def load_exposure_calibration(self, telescope: str, filter_band: str) -> dict | None:
        raw = self._value(f"expcal/{telescope}_{filter_band}", "")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def exposure_calibrations(self) -> list[dict]:
        """Every stored calibration."""
        self._s.beginGroup("expcal")
        try:
            out = []
            for k in self._s.childKeys():
                try:
                    out.append(json.loads(self._value(k, "")))
                except (TypeError, ValueError):
                    continue
            return out
        finally:
            self._s.endGroup()
