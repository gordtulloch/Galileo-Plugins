# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""generic_relay — Galileo first-party plugin for generic HTTP-toggled
Internet relay boards as Switch devices (EQP-SW-010).

Installed via Options > Plugins; discovered by Galileo's plugin loader at
startup.
"""

from __future__ import annotations

from galileo.plugins import PluginBase

from generic_relay.adapter import DEFAULT_URL_TEMPLATE, GenericRelayAdapter, RelayOutlet


class GenericRelayPlugin(PluginBase):
    """Registers the generic relay Switch device backend and its panel."""

    name = "GenericRelayPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "secondary"
    panel_label = "Internet Relay"

    def activate(self, ctx) -> None:
        from galileo.core.devices import DeviceCategory
        ctx.register_device_backend(DeviceCategory.SWITCH, GenericRelayAdapter)

    def deactivate(self) -> None:
        pass

    def build_page(self):
        from generic_relay.ui import build_generic_relay_page
        return build_generic_relay_page()


__all__ = ["DEFAULT_URL_TEMPLATE", "GenericRelayAdapter", "GenericRelayPlugin", "RelayOutlet"]
