# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Persistent Bot list for the SwitchBot panel, in ``QSettings`` under Galileo's
own organisation (same pattern as ``kasa_switch.settings``). Each entry is
``{"address", "label", "password"}``."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_ORG = "Galileo"
_APP = "SwitchBotBot"


class SwitchBotSettings:
    def __init__(self) -> None:
        from PySide6.QtCore import QSettings
        self._s = QSettings(_ORG, _APP)

    @property
    def devices(self) -> list[dict]:
        raw = self._s.value("devices", "[]")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            logger.exception("Could not parse stored SwitchBot list; discarding it")
            return []
        return data if isinstance(data, list) else []

    def add_device(self, address: str, label: str, password: str = "") -> None:
        devices = [d for d in self.devices if d.get("address") != address]
        devices.append({"address": address, "label": label, "password": password})
        self._save(devices)

    def rename_device(self, address: str, label: str) -> None:
        devices = self.devices
        for d in devices:
            if d.get("address") == address:
                d["label"] = label
        self._save(devices)

    def remove_device(self, address: str) -> None:
        self._save([d for d in self.devices if d.get("address") != address])

    def _save(self, devices: list[dict]) -> None:
        self._s.setValue("devices", json.dumps(devices))
        self._s.sync()
