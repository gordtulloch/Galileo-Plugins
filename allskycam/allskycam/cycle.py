# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""One fetch cycle (ASC-020, ASC-030): pause check, then download. No Qt, so it runs on a worker
thread and is tested directly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from allskycam.client import AllSkyClient, AllSkyError, Frame
from allskycam.config import AllSkyConfig
from allskycam.daytime import check_pause

PAUSED = "paused"
UNCHANGED = "unchanged"
FRAME = "frame"
ERROR = "error"


@dataclass(frozen=True)
class CycleResult:
    kind: str                      # PAUSED | UNCHANGED | FRAME | ERROR
    message: str = ""
    frame: Frame | None = None


def run_cycle(
    config: AllSkyConfig,
    location: Any,
    known_url: str | None,
    client_factory: Callable[[AllSkyConfig], AllSkyClient] = lambda c: AllSkyClient(c.url, c.camera_id),
    pause_fn: Callable[[Any, float], tuple[bool, str]] = check_pause,
) -> CycleResult:
    """Do one scheduled fetch. Never raises: every failure comes back as an ERROR result."""
    try:
        if config.pause_daytime:
            paused, message = pause_fn(location, config.sun_altitude_deg)
            if paused:
                return CycleResult(PAUSED, message)
        frame = client_factory(config).fetch_latest(known_url)
    except AllSkyError as exc:
        return CycleResult(ERROR, str(exc))
    except ValueError as exc:            # bad URL typed into the options
        return CycleResult(ERROR, str(exc))
    except Exception as exc:  # noqa: BLE001 -- a worker thread must never die silently
        return CycleResult(ERROR, f"Unexpected error: {exc}")
    if frame is None:
        return CycleResult(UNCHANGED)
    return CycleResult(FRAME, frame=frame)
