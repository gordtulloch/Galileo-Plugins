# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Persistent device list for the Kasa Switches panel.

Backed by ``QSettings`` under Galileo's own organisation, the same pattern
``vstarget.settings`` uses, so the plugin's state sits beside the host
application's rather than in its own vendor namespace. The panel can host
any number of Kasa devices side by side (one tab each), so what persists
is a JSON-encoded list of ``{"host": ..., "label": ...}`` entries rather
than a single device's settings.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_ORG = "Galileo"
_APP = "KasaSwitch"


class KasaSettings:
    """Typed accessor over the plugin's ``QSettings`` store."""

    def __init__(self) -> None:
        from PySide6.QtCore import QSettings
        self._s = QSettings(_ORG, _APP)

    @property
    def devices(self) -> list[dict]:
        """Every configured device, in the order they were added."""
        raw = self._s.value("devices", "[]")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            logger.exception("Could not parse stored Kasa device list; discarding it")
            return []
        return data if isinstance(data, list) else []

    def add_device(self, host: str, label: str) -> None:
        devices = self.devices
        devices.append({"host": host, "label": label})
        self._save(devices)

    def remove_device(self, host: str) -> None:
        self._save([d for d in self.devices if d.get("host") != host])

    def _save(self, devices: list[dict]) -> None:
        self._s.setValue("devices", json.dumps(devices))
        self._s.sync()
