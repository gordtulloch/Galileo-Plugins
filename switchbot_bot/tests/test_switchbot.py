# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""SBOT — SwitchBot Bot (BLE) switch plugin (TC-SBOT-010 … TC-SBOT-040)."""

import sys
import types
import zlib

import pytest

from switchbot_bot import protocol
from switchbot_bot.adapter import SwitchBotAdapter
from switchbot_bot.protocol import Action, SwitchBotError

# ---------------------------------------------------------------------------
# TC-SBOT-010 — command framing and response decoding
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-SBOT-010")
@pytest.mark.priority("MVP")
def test_tc_sbot_010_plain_commands():
    """SBOT-010: press/on/off are 57 01, 57 01 01, 57 01 02 without a password."""
    assert protocol.build_command(Action.PRESS) == b"\x57\x01"
    assert protocol.build_command(Action.ON) == b"\x57\x01\x01"
    assert protocol.build_command(Action.OFF) == b"\x57\x01\x02"


@pytest.mark.requirement("TC-SBOT-010")
@pytest.mark.priority("MVP")
def test_tc_sbot_010_password_commands_carry_big_endian_crc32():
    """SBOT-010: with a password the header is 57 11 + big-endian CRC32(password)."""
    crc = zlib.crc32(b"hunter2").to_bytes(4, "big")
    assert protocol.build_command(Action.ON, "hunter2") == b"\x57\x11" + crc + b"\x01"
    assert protocol.build_command(Action.PRESS, "hunter2") == b"\x57\x11" + crc


@pytest.mark.requirement("TC-SBOT-010")
@pytest.mark.priority("MVP")
def test_tc_sbot_010_status_codes():
    """SBOT-010: status 1 is success; others raise with a readable reason."""
    protocol.check_status(b"\x01")
    with pytest.raises(SwitchBotError, match="password is wrong"):
        protocol.check_status(b"\x09")
    with pytest.raises(SwitchBotError, match="busy"):
        protocol.check_status(b"\x03")
    with pytest.raises(SwitchBotError, match="status 42"):
        protocol.check_status(b"\x2a")


@pytest.mark.requirement("TC-SBOT-010")
@pytest.mark.priority("MVP")
def test_tc_sbot_010_parse_settings():
    """SBOT-010: battery is byte 1, firmware byte 2 / 10, dual-state is bit 4 of byte 9."""
    resp = bytes([1, 87, 61, 0, 0, 0, 0, 0, 0, 16, 0])
    assert protocol.parse_settings(resp) == {
        "battery": 87, "firmware": 6.1, "dual_state_mode": True}


# ---------------------------------------------------------------------------
# TC-SBOT-020 — adapter connect / switching
# ---------------------------------------------------------------------------

class _FakeBot:
    """Stands in for protocol.send; records commands, replies from a script."""

    def __init__(self, settings=bytes([1, 90, 60, 0, 0, 0, 0, 0, 0, 16, 0]), status=b"\x01"):
        self.sent = []
        self.settings = settings
        self.status = status

    async def __call__(self, address, command, **_kw):
        self.sent.append((address, command))
        return self.settings if command == protocol.GET_SETTINGS else self.status


@pytest.fixture
def bot(monkeypatch):
    fake = _FakeBot()
    monkeypatch.setattr(protocol, "send", fake)
    return fake


@pytest.mark.requirement("TC-SBOT-020")
@pytest.mark.priority("MVP")
async def test_tc_sbot_020_connect_reads_battery_and_mode(bot):
    """SBOT-020: connect() reads settings; one switch named after the Bot."""
    adapter = SwitchBotAdapter("AA:BB", name="Seestar power")
    await adapter.connect()
    assert adapter.is_connected and adapter.battery == 90
    assert adapter.press_mode is False
    assert [s.name for s in adapter.switches] == ["Seestar power"]


@pytest.mark.requirement("TC-SBOT-020")
@pytest.mark.priority("MVP")
async def test_tc_sbot_020_dual_state_on_off(bot):
    """SBOT-020: in dual-state mode On/Off send distinct commands and track state."""
    adapter = SwitchBotAdapter("AA:BB", name="S")
    await adapter.set_switch("S", True)
    assert bot.sent[-1] == ("AA:BB", b"\x57\x01\x01") and adapter.switches[0].state is True
    await adapter.set_switch("S", False)
    assert bot.sent[-1] == ("AA:BB", b"\x57\x01\x02") and adapter.switches[0].state is False


@pytest.mark.requirement("TC-SBOT-020")
@pytest.mark.priority("MVP")
async def test_tc_sbot_020_press_mode_presses_for_both(bot):
    """SBOT-020: a press-mode Bot presses for both On and Off (Seestar power button)."""
    adapter = SwitchBotAdapter("AA:BB", name="S", press_mode=True)
    await adapter.set_switch("S", True)
    await adapter.set_switch("S", False)
    assert [c for _a, c in bot.sent] == [b"\x57\x01", b"\x57\x01"]


# ---------------------------------------------------------------------------
# TC-SBOT-030 — errors
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-SBOT-030")
@pytest.mark.priority("MVP")
async def test_tc_sbot_030_unknown_switch_name(bot):
    """SBOT-030: an unknown switch name raises ValueError and sends nothing."""
    adapter = SwitchBotAdapter("AA:BB", name="S")
    with pytest.raises(ValueError):
        await adapter.set_switch("nope", True)
    assert bot.sent == []


@pytest.mark.requirement("TC-SBOT-030")
@pytest.mark.priority("MVP")
async def test_tc_sbot_030_failure_status_leaves_state_unchanged(bot):
    """SBOT-030: a wrong-password reply raises and the shown state is not flipped."""
    bot.status = b"\x09"
    adapter = SwitchBotAdapter("AA:BB", name="S", password="x")
    with pytest.raises(SwitchBotError):
        await adapter.set_switch("S", True)
    assert adapter.switches[0].state is False


@pytest.mark.requirement("TC-SBOT-030")
@pytest.mark.priority("MVP")
async def test_tc_sbot_030_missing_bleak_is_a_clear_error(monkeypatch):
    """SBOT-030: without bleak installed the error says how to fix it."""
    monkeypatch.setitem(sys.modules, "bleak", None)
    with pytest.raises(SwitchBotError, match="pip install bleak"):
        await protocol.send("AA:BB", b"\x57\x02")


# ---------------------------------------------------------------------------
# TC-SBOT-040 — scan filtering
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-SBOT-040")
@pytest.mark.priority("MVP")
async def test_tc_sbot_040_scan_keeps_only_bots_strongest_first(monkeypatch):
    """SBOT-040: scan keeps type-'H' SwitchBot advertisers, sorted by signal."""
    def adv(first_byte, rssi):
        return types.SimpleNamespace(
            service_data={protocol.SERVICE_DATA_UUID: bytes([first_byte, 0, 0])}, rssi=rssi)

    results = {
        "A": (types.SimpleNamespace(name="Far Bot"), adv(0x48, -80)),
        "B": (types.SimpleNamespace(name="Near Bot"), adv(0xC8, -40)),   # high bit = mode flag
        "C": (types.SimpleNamespace(name="Curtain"), adv(0x63, -30)),    # not a Bot
        "D": (types.SimpleNamespace(name="Other"),
              types.SimpleNamespace(service_data={}, rssi=-20)),
    }

    class FakeScanner:
        @staticmethod
        async def discover(timeout, return_adv):
            return results

    monkeypatch.setitem(sys.modules, "bleak", types.SimpleNamespace(BleakScanner=FakeScanner))
    assert await protocol.scan(0.1) == [("B", "Near Bot", -40), ("A", "Far Bot", -80)]


@pytest.mark.requirement("TC-SBOT-020")
@pytest.mark.priority("MVP")
async def test_tc_sbot_020_rename_changes_bot_and_switch_name(bot):
    """SBOT-020: renaming a Bot renames its switch; the old name stops working."""
    adapter = SwitchBotAdapter("AA:BB", name="WoHand")
    adapter.rename("Seestar power")
    assert [s.name for s in adapter.switches] == ["Seestar power"]
    await adapter.set_switch("Seestar power", True)
    with pytest.raises(ValueError):
        await adapter.set_switch("WoHand", True)
