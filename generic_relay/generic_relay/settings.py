# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Persistent device list for the Internet Relay panel.

Backed by ``QSettings`` under Galileo's own organisation, the same pattern
``vstarget.settings``/``kasa_switch.settings`` use. Each entry carries the
per-board configuration the adapter needs: host, relay count, and the URL
template (since, unlike Kasa, there is no single wire protocol to assume —
every board's toggle URL is whatever the user says it is).
"""

from __future__ import annotations

import json
import logging

from generic_relay.adapter import DEFAULT_URL_TEMPLATE

logger = logging.getLogger(__name__)

_ORG = "Galileo"
_APP = "GenericRelay"


class GenericRelaySettings:
    """Typed accessor over the plugin's ``QSettings`` store."""

    def __init__(self) -> None:
        from PySide6.QtCore import QSettings
        self._s = QSettings(_ORG, _APP)

    @property
    def devices(self) -> list[dict]:
        """Every configured relay board, in the order they were added."""
        raw = self._s.value("devices", "[]")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            logger.exception("Could not parse stored relay device list; discarding it")
            return []
        return data if isinstance(data, list) else []

    def add_device(
        self, host: str, label: str, port_count: int, url_template: str = DEFAULT_URL_TEMPLATE
    ) -> None:
        devices = self.devices
        devices.append({
            "host": host,
            "label": label,
            "port_count": port_count,
            "url_template": url_template,
        })
        self._save(devices)

    def remove_device(self, host: str) -> None:
        self._save([d for d in self.devices if d.get("host") != host])

    def _save(self, devices: list[dict]) -> None:
        self._s.setValue("devices", json.dumps(devices))
        self._s.sync()
