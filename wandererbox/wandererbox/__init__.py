# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""wandererbox — Galileo first-party plugin: switch profiles for the Wanderer Astro WandererBox Pro V3,
WandererBox Plus V3 and Dew Terminator (WBOX-010 … WBOX-100).

Those three INDI drivers use vendor-specific properties instead of INDI's standard power interface, so
Galileo core cannot map them on its own. This plugin supplies the mapping as data and registers it; core then
shows each device on Equipment > Switches exactly like any other power box. The plugin has no panel, no settings
and no connection of its own, and the WandererBox Lite V3 is not covered: it has no control interface.

Installed via Options > Plugins; discovered by Galileo's plugin loader at startup.
"""

from __future__ import annotations

import logging

from galileo.plugins import PluginBase

from wandererbox.profiles import DEW_TERMINATOR, PLUS_V3, PRO_V3, PROFILES

logger = logging.getLogger(__name__)


class WandererBoxPlugin(PluginBase):
    """Registers the three Wanderer switch profiles and nothing else (WBOX-010)."""

    name = "WandererBoxPlugin"
    version = "1.0.0"
    api_version = "1"
    # No panel_label / dock_label / options_label: core's Switches page does all the presenting.

    def activate(self, ctx) -> None:
        register = getattr(ctx, "register_switch_profile", None)
        if register is None:
            logger.warning(
                "The WandererBox plugin needs a Galileo with switch-profile support (PLUG-210); "
                "update Galileo to use the Wanderer boxes on the Switches page."
            )
            return
        for profile in PROFILES:
            if not register(profile):
                logger.error("Galileo rejected the %s switch profile; see the earlier log entry for why.", profile.name)

    def deactivate(self) -> None:
        pass     # core drops this plugin's profiles when it is unloaded or disabled (PLUG-060, PLUG-120)


__all__ = ["DEW_TERMINATOR", "PLUS_V3", "PRO_V3", "WandererBoxPlugin"]
