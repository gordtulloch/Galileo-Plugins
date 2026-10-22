# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""switchbot_bot — Galileo first-party plugin for SwitchBot Bot devices over
Bluetooth LE as Switch devices (EQP-SW-010), e.g. to power a Seestar on/off.

Installed via Options > Plugins; discovered by Galileo's plugin loader at
startup.
"""

from __future__ import annotations

from galileo.plugins import PluginBase

from switchbot_bot.adapter import BotSwitch, SwitchBotAdapter


class SwitchBotPlugin(PluginBase):
    """Registers the SwitchBot device backend and its panel."""

    name = "SwitchBotPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "equipment:switch"
    panel_label = "SwitchBot"

    def activate(self, ctx) -> None:
        from galileo.core.devices import DeviceCategory
        ctx.register_device_backend(DeviceCategory.SWITCH, SwitchBotAdapter)

    def deactivate(self) -> None:
        pass

    def build_page(self):
        from switchbot_bot.ui import build_switchbot_page
        return build_switchbot_page()


__all__ = ["BotSwitch", "SwitchBotAdapter", "SwitchBotPlugin"]
