# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""AAVSO Target Tool API client (adapted from VSTarget planning/aavso_client.py)."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_AAVSO_TARGET_TOOL_URL = "https://targettool.aavso.org/TargetTool/api/v1/targets/"


class AavsoTargetToolClient:
    """Downloads variable-star targets from the AAVSO Target Tool REST API (EXT-100)."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    async def fetch_targets(self, section: str = "") -> list[dict]:
        """Return target dicts for the given observing *section*."""
        params: dict[str, str] = {}
        if section:
            params["obscode"] = section
        if self.api_key:
            params["apikey"] = self.api_key

        url = _AAVSO_TARGET_TOOL_URL
        if params:
            url += "?" + urllib.parse.urlencode(params)

        try:
            import asyncio
            response = await asyncio.to_thread(self._get_sync, url)
            return response
        except Exception as exc:
            logger.warning("AAVSO Target Tool fetch failed: %s", exc)
            return []

    def _get_sync(self, url: str) -> list[dict]:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list):
            return data
        return data.get("targets", [])


# ---------------------------------------------------------------------------
# Target Tool v1 client + background fetch thread (restored from standalone)
# ---------------------------------------------------------------------------
#
# `AavsoTargetToolClient` above is the planner service's async, dependency-free
# client.  The planning panel needs the richer v1 `/targets` call (section
# selection, observability filtering by site) and a QThread wrapper so the
# download never blocks the Qt event loop, which is what these provide.

_BASE_URL = "https://targettool.aavso.org/TargetTool/api/v1"


class AAVSOClient:
    """Synchronous client for the AAVSO Target Tool REST API v1 (VST-EXT-010).

    Authentication is HTTP Basic with the API key as the username and the
    literal string ``api_token`` as the password.
    """

    def __init__(self, api_key: str) -> None:
        self._auth = (api_key, "api_token")

    def get_targets(
        self,
        sections: list[str] | None = None,
        observable: bool = False,
        latitude: float | None = None,
        longitude: float | None = None,
        target_altitude: float | None = None,
        sun_altitude: float | None = None,
    ) -> list:
        """Fetch variable-star targets.

        *sections* are section codes (e.g. ``["ac", "cv"]``), defaulting to
        Alerts & Campaigns.  When *observable* is true the API returns only
        targets visible from the given site during the next nighttime period.
        """
        import requests
        from vstarget.planning.models import AAVSOTarget

        params: dict = {"obs_section": sections or ["ac"]}
        if observable:
            params["observable"] = "true"
        if latitude is not None:
            params["latitude"] = latitude
        if longitude is not None:
            params["longitude"] = longitude
        if target_altitude is not None:
            params["targetaltitude"] = target_altitude
        if sun_altitude is not None:
            params["sunaltitude"] = sun_altitude

        resp = requests.get(f"{_BASE_URL}/targets", auth=self._auth, params=params, timeout=60)
        resp.raise_for_status()
        return [AAVSOTarget.from_api(t) for t in resp.json().get("targets", [])]


_FETCH_THREAD_CLS = None


def _fetch_targets_thread_cls():
    """Build (once) and return the FetchTargetsThread class.

    Defined inside a function so importing this module never requires PySide6 —
    the planner service and its tests import `AavsoTargetToolClient` from here
    in headless environments where Qt may not be installed.
    """
    global _FETCH_THREAD_CLS
    if _FETCH_THREAD_CLS is not None:
        return _FETCH_THREAD_CLS

    from PySide6.QtCore import QThread, Signal

    class FetchTargetsThread(QThread):
        """Downloads AAVSO targets off the UI thread."""

        finished = Signal(list)   # success: the target list
        error = Signal(str)       # failure: a human-readable message
        status = Signal(str)      # short progress messages for the status strip

        def __init__(
            self,
            api_key: str,
            sections: list[str],
            observable: bool,
            latitude: float,
            longitude: float,
            target_altitude: float,
            sun_altitude: float,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._api_key = api_key
            self._sections = sections
            self._observable = observable
            self._latitude = latitude
            self._longitude = longitude
            self._target_altitude = target_altitude
            self._sun_altitude = sun_altitude

        def run(self) -> None:
            import requests

            self.status.emit("Connecting to AAVSO Target Tool…")
            try:
                targets = AAVSOClient(self._api_key).get_targets(
                    sections=self._sections,
                    observable=self._observable,
                    latitude=self._latitude,
                    longitude=self._longitude,
                    target_altitude=self._target_altitude,
                    sun_altitude=self._sun_altitude,
                )
                self.status.emit(f"Downloaded {len(targets)} targets.")
                self.finished.emit(targets)

            except requests.exceptions.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else "?"
                if code == 401:
                    self.error.emit(
                        "Authentication failed (HTTP 401).\n"
                        "Please check your API key in Settings."
                    )
                elif code == 429:
                    self.error.emit(
                        "Rate limit exceeded (HTTP 429).\n"
                        "You have made too many requests. Please try again later."
                    )
                elif code == 410:
                    self.error.emit(
                        "API no longer in service (HTTP 410).\n"
                        "Please check https://targettool.aavso.org for updates."
                    )
                else:
                    self.error.emit(f"HTTP error {code}: {exc}")

            except requests.exceptions.ConnectionError:
                self.error.emit("Connection failed.\nPlease check your internet connection.")
            except requests.exceptions.Timeout:
                self.error.emit("Request timed out. Please try again.")
            except Exception as exc:  # noqa: BLE001
                self.error.emit(f"Unexpected error: {exc}")

    _FETCH_THREAD_CLS = FetchTargetsThread
    return FetchTargetsThread


def __getattr__(name: str):
    """Expose `FetchTargetsThread` as a module attribute without importing Qt at
    module level (PEP 562)."""
    if name == "FetchTargetsThread":
        return _fetch_targets_thread_cls()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
