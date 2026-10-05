# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Generic Internet Relay device backend — implements Galileo's Switch
device port (``galileo.core.devices.DeviceBackend`` / the
``_SwitchBackend`` protocol) for relay boards that toggle each relay via a
plain HTTP GET, rather than any structured API.

This covers the common cheap ESP8266/Arduino-style Ethernet relay board,
where turning relay 1 on and off is two different fixed URLs, e.g.::

    http://10.0.0.101/30000/01      # relay 1 on
    http://10.0.0.101/30000/00      # relay 1 off

Because boards like this vary in how the relay index and desired state are
encoded in the path, the URL is built from a user-supplied **template**
rather than hard-coded. The template is a ``str.format()`` string with three
placeholders:

``{ip}``
    The device's configured host/IP.
``{port}``
    The *zero-based* relay index (relay 1 -> ``0``, relay 2 -> ``1``, …).
``{state}``
    ``1`` to turn the relay on, ``0`` to turn it off.

The default template, ``"http://{ip}/30000/{port}{state}"``, reproduces the
example above exactly: relay 1 (``port=0``) on (``state=1``) renders
``http://10.0.0.101/30000/01``; off (``state=0``) renders
``http://10.0.0.101/30000/00``. A board with a different scheme — a
different fixed path segment, zero-padded fields, state before the relay
index, etc. — is supported by editing the template, not the code.

Most boards in this class have no readback endpoint at all: a GET either
succeeds or the board is unreachable, with no way to ask "is relay 3
currently on?". ``switches[i].state`` therefore reflects the last state
*this adapter* commanded, not a polled hardware readout — the same
necessary compromise every project wiki for these boards documents.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import requests

from galileo.core.capabilities import DeviceCapabilities
from galileo.core.devices import DeviceBackend, DeviceCategory

logger = logging.getLogger(__name__)

DEFAULT_URL_TEMPLATE = "http://{ip}/30000/{port}{state}"
_DEFAULT_TIMEOUT_S = 5.0


@dataclass
class RelayOutlet:
    """One relay on the board. Matches the shape ``SwitchController``/the
    Equipment Switches page expects (``name``, ``state``, ``is_analog``)."""
    name: str
    state: bool
    is_analog: bool = False


class GenericRelayAdapter(DeviceBackend):
    """One relay board, addressed purely over HTTP via a configurable URL template."""

    backend = "generic_relay"

    def __init__(
        self,
        host: str,
        port_count: int = 1,
        url_template: str = DEFAULT_URL_TEMPLATE,
        name: str = "",
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.device_type = DeviceCategory.SWITCH.value
        self.host = host
        self.port_count = port_count
        self.url_template = url_template
        self.name = name or host
        self.timeout = timeout
        self.switches: list[RelayOutlet] = [
            RelayOutlet(name=f"Relay {n}", state=False) for n in range(1, port_count + 1)
        ]
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities()

    def get_properties(self) -> dict:
        return {
            "host": self.host,
            "switches": {s.name: s.state for s in self.switches},
        }

    async def connect(self) -> None:
        """There is no handshake for a bare HTTP relay board — connecting
        just marks the adapter ready to issue commands. The relay list was
        already built from ``port_count`` at construction time, since these
        boards have no sysinfo-style enumeration call to query it from."""
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def build_url(self, relay_index_0based: int, state: int) -> str:
        return self.url_template.format(ip=self.host, port=relay_index_0based, state=state)

    async def set_switch(self, name: str, value: object) -> None:
        """Toggle the named relay on/off (EQP-SW-010) by GETing the templated URL."""
        index = next((i for i, s in enumerate(self.switches) if s.name == name), None)
        if index is None:
            raise ValueError(f"No such relay: {name!r}")

        state = 1 if value else 0
        url = self.build_url(index, state)

        def _request() -> None:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()

        await asyncio.to_thread(_request)
        self.switches[index].state = bool(state)
