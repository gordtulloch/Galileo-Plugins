# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""From what core reports to the state keys the apps read (``api/scenarios/README.md`` in Galileo-MobileConnect).

Core has two sources. ``ObservableState`` publishes a few fragments on change (``mount.position``,
``mount.tracking``, ``guiding.state``, ``guiding.rms``, ``weather.readings``). Everything else is only in the
read-class ``*.status`` operations, so :class:`StateTracker` samples those while an app is connected and sends what
changed. These are pure functions of core's dictionaries, so a change on either side fails a test here.
"""

from __future__ import annotations

import datetime
from typing import Any

# Sampled every tick, in this order; anything else is only worth the occasional look.
SAMPLED = ("mount.status", "camera.status", "focus.status", "guiding.status", "imaging.status", "solve.status",
           "flip.status", "session.status", "safety.status", "safety.events", "rotator.status", "dome.status",
           "weather.readings")
SAMPLED_SLOWLY = ("filter.names", "scheduler.jobs", "session.templates")
# Reads that say "this device is connected" just by being available.
DEVICES = {"mount.status": "mount", "camera.status": "camera", "rotator.status": "rotator", "dome.status": "dome",
           "focus.status": "focus", "filter.names": "filter"}
TRACE_KEY = "guiding.error"


def now_text() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def plain(value: Any) -> Any:
    """Something JSON can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "__dataclass_fields__"):
        import dataclasses
        return plain(dataclasses.asdict(value))
    return str(getattr(value, "value", value))


def _mount(s: dict[str, Any]) -> dict[str, Any]:
    ra = s.get("right_ascension")
    tracking, slewing, parked = bool(s.get("tracking")), bool(s.get("slewing")), bool(s.get("at_park"))
    activity = "Slewing" if slewing else "Parked" if parked else "Tracking" if tracking else "Idle"
    return {
        "mount.tracking": tracking, "mount.slewing": slewing, "mount.parked": parked,
        "mount.side_of_pier": s.get("side_of_pier"), "mount.tracking_rate": s.get("tracking_rate"),
        "mount.position": {"ra_deg": None if ra is None else ra * 15.0, "dec_deg": s.get("declination"),
                           "alt_deg": s.get("altitude"), "az_deg": s.get("azimuth")},
        "activity.mount": activity,
    }


def _camera(s: dict[str, Any]) -> dict[str, Any]:
    power = s.get("cooler_power")
    return {"camera.temperature": s.get("temperature"), "camera.cooler_power": power, "camera.cooling": bool(power)}


def _focus(s: dict[str, Any]) -> dict[str, Any]:
    return {"focus.position": s.get("position"), "focus.temperature": s.get("temperature")}


def _guiding(s: dict[str, Any]) -> dict[str, Any]:
    connected = bool(s.get("connected"))
    state = s.get("app_state") if connected else "Disconnected"
    rms = s.get("rms") or {}
    trace = s.get("trace") or []
    return {
        "guiding.connected": connected, "guiding.state": state, "guiding.rms_ra": rms.get("ra"),
        "guiding.rms_dec": rms.get("dec"), "guiding.star_snr": trace[-1][4] if trace else None,
        "guiding.why_not": s.get("why_not_connect") or None, "activity.guiding": state,
    }


def _imaging(s: dict[str, Any]) -> dict[str, Any]:
    capturing = bool(s.get("capturing"))
    done, total = s.get("series_done"), s.get("series_total")
    out: dict[str, Any] = {
        "imaging.state": "Series" if capturing else "Idle", "imaging.frame": done, "imaging.count": total,
        "imaging.progress": done / total if capturing and done is not None and total else None,
        "imaging.why_not": s.get("why_not") or None, "camera.state": "Exposing" if capturing else "Idle",
        "activity.capture": "Exposing" if capturing else "Idle",
    }
    exposure = s.get("exposure") or {}
    seconds, elapsed = exposure.get("seconds"), exposure.get("elapsed")
    out["imaging.exposure_s"] = seconds if exposure else None
    out["imaging.exposure_progress"] = (min(1.0, max(0.0, elapsed / seconds)) if exposure and seconds and elapsed is not None
                                        else None)
    # The live stack: how far through the series it is, and what it holds. None when no stack is being built, so a
    # bar the app was drawing goes away.
    stack = s.get("stack") or {}
    frames, requested = stack.get("frames"), stack.get("requested")
    out["imaging.stack.count"] = frames if stack else None
    out["imaging.stack.requested"] = requested if stack else None
    out["imaging.stack.progress"] = min(1.0, frames / requested) if stack and frames is not None and requested else None
    out["imaging.stack.total_exposure_s"] = stack.get("total_exposure_s") if stack else None
    out["imaging.stack.method"] = (stack.get("method") or None) if stack else None
    frame = s.get("frame") or {}
    stats = frame.get("stats") or {}
    if stats:
        out["imaging.stats"] = {"stars": stats.get("star_count", stats.get("stars")), "hfr": stats.get("hfr"),
                                "fwhm": stats.get("fwhm")}
    histogram = frame.get("histogram") or {}
    if histogram.get("counts"):
        out["imaging.histogram"] = _downsample(histogram["counts"])
    return out


def _downsample(counts: list[float], size: int = 64) -> list[float]:
    """The app draws a small chart; a 65,536-bin histogram every tick would be wasted bytes on the link."""
    if len(counts) <= size:
        return list(counts)
    step = len(counts) / size
    return [sum(counts[int(i * step):int((i + 1) * step)]) for i in range(size)]


def _solve(s: dict[str, Any]) -> dict[str, Any]:
    running = bool(s.get("running"))
    return {"solve.running": running, "solve.why_not": s.get("why_not") or None, "solve.last": s.get("last"),
            "activity.solve": "Solving" if running else "Idle"}


def _flip(s: dict[str, Any]) -> dict[str, Any]:
    phase = str(s.get("phase") or "unavailable")
    return {"flip.state": phase.replace("_", " ").capitalize(), "flip.minutes": s.get("minutes_to_flip"),
            "flip.enabled": s.get("enabled"), "flip.pointing": s.get("pointing"), "flip.why_not": s.get("why_not") or None,
            "flip.last": s.get("last"), "activity.flip": phase.replace("_", " ").capitalize()}


def _session(s: dict[str, Any] | None) -> dict[str, Any]:
    s = s or {"state": "idle"}
    state = str(s.get("state") or "idle").capitalize()
    done, total = s.get("frames_done"), s.get("frames_total")
    index, blocks = s.get("block_index"), s.get("block_total")
    progress = done / total if done is not None and total else index / blocks if index is not None and blocks else None
    return {"session.state": state, "session.name": s.get("name"), "session.block": s.get("block") or None,
            "session.message": s.get("message") or None, "session.progress": progress, "session.frames_done": done,
            "session.frames_total": total, "session.block_index": index, "session.block_total": blocks,
            "activity.session": state}


def _safety(s: dict[str, Any]) -> dict[str, Any]:
    safe = s.get("safe")
    overall = "unknown" if safe is None else "safe" if safe else "unsafe"
    return {"safety.overall": overall, "safety.inputs": {"Safety monitor": s.get("explanation") or overall.capitalize()},
            "safety.last_abort": s.get("last_abort") or None}


def _safety_events(s: dict[str, Any]) -> dict[str, Any]:
    events = s.get("events") or []
    latest = events[-1] if events else {}
    reason = latest.get("reason") or latest.get("trigger") or latest.get("explanation")
    return {"safety.pending": s.get("pending", 0), "safety.acknowledged": not s.get("pending"),
            "session.stop_reason": reason}


def _rotator(s: dict[str, Any]) -> dict[str, Any]:
    return {"rotator.angle": s.get("position"), "rotator.moving": s.get("is_moving")}


def _dome(s: dict[str, Any]) -> dict[str, Any]:
    return {"dome.state": s.get("shutter_state"), "dome.azimuth": s.get("azimuth")}


def _weather(s: Any) -> dict[str, Any]:
    if not isinstance(s, dict):
        return {}
    return {f"weather.{key}": value for key, value in s.items() if not isinstance(value, (dict, list))}


TRANSLATORS = {
    "mount.status": _mount, "camera.status": _camera, "focus.status": _focus, "guiding.status": _guiding,
    "imaging.status": _imaging, "solve.status": _solve, "flip.status": _flip, "session.status": _session,
    "safety.status": _safety, "safety.events": _safety_events, "rotator.status": _rotator, "dome.status": _dome,
    "weather.readings": _weather,
    "filter.names": lambda names: {"filter.names": names},
    "scheduler.jobs": lambda s: {"scheduler.jobs": (s or {}).get("jobs", []), "scheduler.next": (s or {}).get("next") or None},
    "session.templates": lambda s: {"session.templates": s.get("templates", []) if isinstance(s, dict) else s},
}


def translate(operation: str, result: Any) -> dict[str, Any]:
    """The state keys one status result sets."""
    keys = dict(TRANSLATORS[operation](plain(result)))
    device = DEVICES.get(operation)
    if device:
        keys[f"{device}.connected"] = True
    return keys


def translate_observable(key: str, value: Any) -> dict[str, Any]:
    """The state keys one ``ObservableState`` fragment sets. Unknown fragments pass through under their own name:
    a new core key should reach a new app without a plugin release."""
    value = plain(value)
    if key == "mount.position":
        if not isinstance(value, dict):
            return {"mount.position": None}
        return {"mount.position.ra_deg": value.get("ra_deg"), "mount.position.dec_deg": value.get("dec_deg"),
                "mount.slewing": bool(value.get("slewing"))}
    if key == "mount.tracking":
        return {"mount.tracking_rate": value, "mount.tracking": value is not None}
    if key == "guiding.rms":
        value = value or {}
        return {"guiding.rms_ra": value.get("ra"), "guiding.rms_dec": value.get("dec")}
    if key == "guiding.state":
        value = value or {}
        connected = bool(value.get("connected"))
        state = value.get("app_state") if connected else "Disconnected"
        return {"guiding.connected": connected, "guiding.state": state, "activity.guiding": state}
    if key == "weather.readings":
        return _weather(value)
    return {key: value}


class StateTracker:
    """The latest value of every key, per Pier. ``update`` returns only what changed, so sampling sends nothing
    while nothing happens."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, Any]] = {}
        self._trace_seen: dict[str, Any] = {}

    def update(self, pier: str, keys: dict[str, Any]) -> dict[str, Any]:
        current = self._values.setdefault(pier, {})
        changed = {k: v for k, v in keys.items() if k not in current or current[k] != v}
        current.update(changed)
        return changed

    def new_trace_samples(self, pier: str, status: Any) -> list[dict[str, float]]:
        """Guide samples not sent yet, as ``{ra, dec}``. A trace row is ``[t, frame, ra, dec, snr]``."""
        trace = (status or {}).get("trace") or []
        seen = self._trace_seen.get(pier)
        fresh = [row for row in trace if seen is None or row[0] > seen]
        if trace:
            self._trace_seen[pier] = trace[-1][0]
        return [{"ra": row[2], "dec": row[3]} for row in fresh]

    def snapshot(self, pier: str) -> dict[str, Any]:
        return dict(self._values.get(pier, {}))

    def piers(self) -> list[str]:
        return list(self._values)

    def forget(self) -> None:
        self._values.clear()
        self._trace_seen.clear()


__all__ = ["DEVICES", "SAMPLED", "SAMPLED_SLOWLY", "TRACE_KEY", "StateTracker", "now_text", "plain", "translate",
           "translate_observable"]
