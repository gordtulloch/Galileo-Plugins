# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""SwitchBot Bot device backend — implements Galileo's Switch device port
(``galileo.core.devices.DeviceBackend``) over :mod:`switchbot_bot.protocol`.

One Bot is one switch. In the Bot's dual-state mode ON/OFF extend and retract
the arm; in its standard (press) mode every command is a press, which is what
powering a Seestar needs (one press to start it, one to stop it). A Bot cannot
report which way it was last switched, so the state shown is the last one
*commanded*, as with the Generic Relay plugin's boards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from galileo.core.capabilities import DeviceCapabilities
from galileo.core.devices import DeviceBackend, DeviceCategory

from switchbot_bot import protocol
from switchbot_bot.protocol import Action

logger = logging.getLogger(__name__)


@dataclass
class BotSwitch:
    """Matches the shape ``SwitchController`` / the Equipment Switches page expects."""
    name: str
    state: bool = False
    is_analog: bool = False


class SwitchBotAdapter(DeviceBackend):
    """One physical SwitchBot Bot as a Galileo Switch backend."""

    backend = "switchbot"

    def __init__(self, address: str, name: str = "", password: str = "",
                 press_mode: bool = False) -> None:
        self.device_type = DeviceCategory.SWITCH.value
        self.address = address
        self.name = name or address
        self.password = password or None
        self.press_mode = press_mode
        self.switches: list[BotSwitch] = [BotSwitch(self.name)]
        self.battery: int | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities()

    def get_properties(self) -> dict:
        return {
            "address": self.address,
            "battery": self.battery,
            "switches": {s.name: s.state for s in self.switches},
        }

    async def connect(self) -> None:
        """Read the Bot's settings (battery, mode) — proves it is in range."""
        response = await protocol.send(self.address, protocol.GET_SETTINGS)
        settings = protocol.parse_settings(response)
        self.battery = settings["battery"]
        if "dual_state_mode" in settings:
            self.press_mode = not settings["dual_state_mode"]
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def refresh(self) -> None:
        await self.connect()

    async def press(self) -> None:
        """Press the Bot's button once, whatever its mode."""
        await self._command(Action.PRESS)

    async def set_switch(self, name: str, value: object) -> None:
        """Switch the Bot on/off (EQP-SW-010); in press mode both are a press."""
        if name != self.switches[0].name:
            raise ValueError(f"No such SwitchBot switch: {name!r}")
        want = bool(value)
        if self.press_mode:
            action = Action.PRESS
        else:
            action = Action.ON if want else Action.OFF
        await self._command(action)
        self.switches[0].state = want

    async def _command(self, action: Action) -> None:
        response = await protocol.send(
            self.address, protocol.build_command(action, self.password)
        )
        protocol.check_status(response)
        logger.info("SwitchBot %s (%s): %s sent", self.name, self.address, action.name)
