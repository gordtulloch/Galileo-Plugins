# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Daytime detection for the optional "pause during daytime" option (ASC-030).

Computed locally from the selected Observatory's latitude/longitude with Galileo's own
``galileo.planning.visibility.sun_altitude_deg``, not by asking indi-allsky -- the camera's idea
of day and night is its own configuration and may not match the Observatory's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _default_altitude(location: Any) -> float | None:
    from galileo.planning.visibility import sun_altitude_deg
    return sun_altitude_deg(location)


def check_pause(
    location: Any,
    threshold_deg: float,
    altitude_fn: Callable[[Any], float | None] = _default_altitude,
) -> tuple[bool, str]:
    """``(paused, message)``: whether the Sun is above *threshold_deg* at *location* right now.

    Never pauses when the answer is unknown (no Observatory location set, or the altitude cannot
    be computed): an image of unknown darkness is better than silently showing nothing. The
    message says which case applied, for the status line.
    """
    if location is None:
        return False, "Daytime pause is on but the Observatory has no latitude/longitude set"
    altitude = altitude_fn(location)
    if altitude is None:
        return False, "Could not work out the Sun's altitude; not pausing"
    if altitude > threshold_deg:
        return True, f"Paused for daytime (Sun {altitude:+.1f}°)"
    return False, ""
