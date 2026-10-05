# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Kasa device backend — implements Galileo's Switch device port
(``galileo.core.devices.DeviceBackend`` / the ``_SwitchBackend`` protocol)
over the local-network protocol in :mod:`kasa_switch.protocol`.

Supports both a single-outlet plug (HS100/HS103/HS105/KP115, …) and a
multi-outlet power strip (HS300/KP303/EP40, …) through the same class: a
single-outlet device reports one switch named after the device itself;
a strip reports one switch per ``children`` entry in ``system.get_sysinfo``,
named after each outlet's own alias (``system.alias`` on the HS300, e.g.
"Camera", "Mount", "Dew Heater" — whatever the user named the socket in the
Kasa mobile app).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from galileo.core.capabilities import DeviceCapabilities
from galileo.core.devices import DeviceBackend, DeviceCategory

from kasa_switch.protocol import send_command

logger = logging.getLogger(__name__)


@dataclass
class KasaOutlet:
    """One controllable outlet — the whole device for a single plug, or one
    socket of a power strip. Matches the shape ``SwitchController``/the
    Equipment Switches page expects (``name``, ``state``, ``is_analog``)."""
    name: str
    state: bool
    is_analog: bool = False
    child_id: str | None = None  # None for a single-outlet plug; set for a strip's child


class KasaSwitchAdapter(DeviceBackend):
    """One physical Kasa device (plug or power strip) as a Galileo Switch backend."""

    backend = "kasa"

    def __init__(self, host: str, port: int = 9999, name: str = "") -> None:
        self.device_type = DeviceCategory.SWITCH.value
        self.host = host
        self.port = port
        self.name = name or host
        self.switches: list[KasaOutlet] = []
        self._connected = False
        self._alias: str = ""

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities()

    def get_properties(self) -> dict:
        return {
            "host": self.host,
            "alias": self._alias,
            "switches": {s.name: s.state for s in self.switches},
        }

    async def connect(self) -> None:
        """Query ``system.get_sysinfo`` and populate ``switches`` from it."""
        sysinfo = await asyncio.to_thread(
            send_command, self.host, {"system": {"get_sysinfo": {}}}, port=self.port
        )
        info = sysinfo.get("system", {}).get("get_sysinfo", {})
        self._alias = info.get("alias", self.host)
        children = info.get("children")
        if children:
            self.switches = [
                KasaOutlet(
                    name=child.get("alias") or f"Outlet {i + 1}",
                    state=bool(child.get("state", 0)),
                    child_id=child.get("id"),
                )
                for i, child in enumerate(children)
            ]
        else:
            self.switches = [
                KasaOutlet(name=self._alias, state=bool(info.get("relay_state", 0)))
            ]
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def refresh(self) -> None:
        """Re-read current relay state for every outlet without losing
        each outlet's identity (used by the panel's poll timer)."""
        await self.connect()

    async def set_switch(self, name: str, value: object) -> None:
        """Toggle the named outlet on/off (EQP-SW-010)."""
        outlet = next((s for s in self.switches if s.name == name), None)
        if outlet is None:
            raise ValueError(f"No such Kasa outlet: {name!r}")

        state = 1 if value else 0
        command: dict = {"system": {"set_relay_state": {"state": state}}}
        if outlet.child_id is not None:
            command = {
                "context": {"child_ids": [outlet.child_id]},
                "system": {"set_relay_state": {"state": state}},
            }
        await asyncio.to_thread(send_command, self.host, command, port=self.port)
        outlet.state = bool(state)
