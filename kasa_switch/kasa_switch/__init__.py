# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""kasa_switch — Galileo first-party plugin for TP-Link Kasa smart plugs
and power strips as Switch devices (EQP-SW-010).

Installed via Options > Plugins; discovered by Galileo's plugin loader at
startup.
"""

from __future__ import annotations

from galileo.plugins import PluginBase

from kasa_switch.adapter import KasaOutlet, KasaSwitchAdapter


class KasaSwitchPlugin(PluginBase):
    """Registers the Kasa Switch device backend and its panel."""

    name = "KasaSwitchPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "secondary"
    panel_label = "Kasa Switches"

    def activate(self, ctx) -> None:
        from galileo.core.devices import DeviceCategory
        ctx.register_device_backend(DeviceCategory.SWITCH, KasaSwitchAdapter)

    def deactivate(self) -> None:
        pass

    def build_page(self):
        from kasa_switch.ui import build_kasa_page
        return build_kasa_page()


__all__ = ["KasaOutlet", "KasaSwitchAdapter", "KasaSwitchPlugin"]
