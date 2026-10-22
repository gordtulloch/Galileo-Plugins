# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""SwitchBot Bot Bluetooth LE protocol — a small re-implementation of the
GATT command set documented by https://github.com/RoButton/switchbotpy (MIT)
and the OpenWonderLabs SwitchBot BLE API, on top of ``bleak``.

switchbotpy itself drives BlueZ's ``gatttool`` through ``pygatt``/``pexpect``,
which only works on Linux; ``bleak`` uses the native Bluetooth stack on
Windows, macOS and Linux, which Galileo needs. A Bot takes commands by
writing to one GATT characteristic and answers on a notify characteristic
whose first byte is a status code.

``bleak`` is imported lazily so the plugin loads (and its panel can explain
what is missing) on a machine where it is not installed.
"""

from __future__ import annotations

import asyncio
import zlib
from enum import Enum

WRITE_UUID = "cba20002-224d-11e6-9fb8-0002a5d5c51b"
NOTIFY_UUID = "cba20003-224d-11e6-9fb8-0002a5d5c51b"
# SwitchBot devices advertise service data under this UUID; the low 7 bits of
# its first byte are the device type ("H" = Bot).
SERVICE_DATA_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"
_BOT_TYPE = 0x48

_DEFAULT_TIMEOUT_S = 10.0

GET_SETTINGS = b"\x57\x02"


class Action(Enum):
    PRESS = b""          # standard mode: extend, hold, retract
    ON = b"\x01"         # dual-state mode: extend arm
    OFF = b"\x02"        # dual-state mode: retract arm


class SwitchBotError(Exception):
    """A Bot answered with a non-success status, or could not be reached."""


_STATUS_MESSAGES = {
    3: "the Bot is busy",
    7: "the Bot has a password set — enter it for this device",
    8: "the Bot has no password set — clear the password for this device",
    9: "the Bot's password is wrong",
    11: "the Bot is unreachable",
}


def build_command(action: Action, password: str | None = None) -> bytes:
    """Command bytes for *action*. With a password the header becomes ``57 11``
    followed by the big-endian CRC32 of the password."""
    if password:
        crc = zlib.crc32(password.encode("utf-8")).to_bytes(4, "big")
        return b"\x57\x11" + crc + action.value
    return b"\x57\x01" + action.value


def check_status(response: bytes) -> None:
    """Raise :class:`SwitchBotError` unless *response* reports success (1)."""
    if not response:
        raise SwitchBotError("the Bot sent an empty response")
    if response[0] != 1:
        raise SwitchBotError(
            _STATUS_MESSAGES.get(response[0], f"the Bot reported status {response[0]}")
        )


def parse_settings(response: bytes) -> dict:
    """Decode a ``57 02`` settings response."""
    check_status(response)
    settings: dict = {"battery": response[1], "firmware": response[2] / 10.0}
    if len(response) > 9:
        settings["dual_state_mode"] = bool(response[9] & 16)
    return settings


def _load_bleak():
    try:
        import bleak
    except ImportError as exc:
        raise SwitchBotError(
            "Bluetooth support needs the 'bleak' package — run: pip install bleak"
        ) from exc
    return bleak


async def scan(timeout: float = 5.0) -> list[tuple[str, str, int | None]]:
    """Find SwitchBot Bots in range. Returns ``(address, name, rssi)``, strongest first.

    *address* is the MAC on Windows/Linux and a CoreBluetooth UUID on macOS,
    which is the identifier ``bleak`` needs to reconnect on that platform.
    """
    bleak = _load_bleak()
    try:
        found = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
    except Exception as exc:
        raise SwitchBotError(f"Bluetooth scan failed: {exc}") from exc
    bots = []
    for address, (device, adv) in found.items():
        data = adv.service_data.get(SERVICE_DATA_UUID)
        if data and (data[0] & 0x7F) == _BOT_TYPE:
            bots.append((address, device.name or "SwitchBot Bot", adv.rssi))
    return sorted(bots, key=lambda b: -(b[2] if b[2] is not None else -999))


async def send(
    address: str, command: bytes, *, timeout: float = _DEFAULT_TIMEOUT_S
) -> bytes:
    """Connect to the Bot at *address*, write *command*, return its response.

    A fresh connection per command: Bots sleep between uses, and a dropped
    link then fails only this call (ARCH-060) instead of poisoning the next.
    """
    bleak = _load_bleak()
    reply: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def _on_notify(_char, data: bytearray) -> None:
        if not reply.done():
            reply.set_result(bytes(data))

    try:
        async with bleak.BleakClient(address, timeout=timeout) as client:
            await client.start_notify(NOTIFY_UUID, _on_notify)
            await client.write_gatt_char(WRITE_UUID, command, response=True)
            return await asyncio.wait_for(reply, timeout)
    except TimeoutError as exc:
        raise SwitchBotError("the Bot did not respond") from exc
    except Exception as exc:
        raise SwitchBotError(f"could not reach the Bot at {address}: {exc}") from exc
