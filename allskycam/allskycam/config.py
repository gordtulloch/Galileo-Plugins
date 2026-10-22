# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The AllSkyCam plugin's options (ASC-040).

Plain data, no Qt: ``from_dict`` accepts whatever the settings store hands back (including
garbage from an older or hand-edited database) and always yields in-range values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# (minimum, maximum) for each numeric option.
INTERVAL_RANGE = (10, 3600)          # seconds between fetches
FRAME_COUNT_RANGE = (2, 120)         # images kept for the animation
FPS_RANGE = (0.5, 30.0)              # animation speed
SUN_ALTITUDE_RANGE = (-18.0, 10.0)   # degrees; the Sun above this counts as daytime
CAMERA_ID_RANGE = (0, 999)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def _number(raw: Any, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _flag(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(raw, int | float):
        return bool(raw)
    return default


@dataclass
class AllSkyConfig:
    url: str = ""                    # indi-allsky web root, e.g. http://allsky.local/indi-allsky
    camera_id: int = 0               # 0 = indi-allsky's default (most recent) camera
    interval_s: int = 60             # how often to fetch
    frame_count: int = 20            # images kept for the animation
    pause_daytime: bool = True       # skip fetching while the Sun is up
    sun_altitude_deg: float = 0.0    # "up" means above this altitude
    fps: float = 4.0                 # animation speed
    show_dock: bool = False          # the dockable animation pane is open

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AllSkyConfig:
        d = data or {}
        base = cls()
        url = d.get("url", base.url)
        return cls(
            url=url.strip() if isinstance(url, str) else base.url,
            camera_id=int(_clamp(_number(d.get("camera_id"), base.camera_id), CAMERA_ID_RANGE)),
            interval_s=int(_clamp(_number(d.get("interval_s"), base.interval_s), INTERVAL_RANGE)),
            frame_count=int(_clamp(_number(d.get("frame_count"), base.frame_count), FRAME_COUNT_RANGE)),
            pause_daytime=_flag(d.get("pause_daytime"), base.pause_daytime),
            sun_altitude_deg=_clamp(_number(d.get("sun_altitude_deg"), base.sun_altitude_deg), SUN_ALTITUDE_RANGE),
            fps=_clamp(_number(d.get("fps"), base.fps), FPS_RANGE),
            show_dock=_flag(d.get("show_dock"), base.show_dock),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def configured(self) -> bool:
        return bool(self.url.strip())
