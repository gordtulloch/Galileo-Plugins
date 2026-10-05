# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""KASA — Kasa smart plug / power strip switch plugin (TC-KASA-010 … TC-KASA-040)."""

import json
import struct

import pytest

from kasa_switch import protocol
from kasa_switch.adapter import KasaSwitchAdapter


# ---------------------------------------------------------------------------
# TC-KASA-010 — wire protocol
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-KASA-010")
@pytest.mark.priority("MVP")
def test_tc_kasa_010_encrypt_decrypt_round_trip():
    """KASA-010: the XOR cipher is its own inverse — decrypt(encrypt(x)) == x."""
    original = '{"system":{"get_sysinfo":{}}}'
    framed = protocol.encrypt(original)

    # First 4 bytes are a big-endian length prefix for the remaining payload.
    (length,) = struct.unpack(">I", framed[:4])
    payload = framed[4:]
    assert length == len(payload)

    assert protocol.decrypt(payload) == original


@pytest.mark.requirement("TC-KASA-010")
@pytest.mark.priority("MVP")
def test_tc_kasa_010_different_inputs_produce_different_ciphertext():
    """KASA-010: distinct commands don't collide on the wire."""
    a = protocol.encrypt('{"a":1}')
    b = protocol.encrypt('{"a":2}')
    assert a != b


@pytest.mark.requirement("TC-KASA-010")
@pytest.mark.priority("MVP")
def test_tc_kasa_010_send_command_uses_length_framing(monkeypatch):
    """KASA-010: send_command frames the request and parses the length-prefixed response."""
    sent = {}

    class FakeSocket:
        def __init__(self):
            self._response = None

        def sendall(self, data):
            sent["data"] = data
            # Echo back a minimal sysinfo response, correctly framed.
            reply = protocol.encrypt('{"system":{"get_sysinfo":{"alias":"Test"}}}')
            self._response = reply

        def recv(self, n):
            chunk, self._response = self._response[:n], self._response[n:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_socket = FakeSocket()
    monkeypatch.setattr(protocol.socket, "create_connection", lambda *a, **k: fake_socket)

    request = {"system": {"get_sysinfo": {}}}
    result = protocol.send_command("10.0.0.5", request)
    assert result == {"system": {"get_sysinfo": {"alias": "Test"}}}
    # The outgoing payload itself round-trips back to the original command.
    (length,) = struct.unpack(">I", sent["data"][:4])
    assert length == len(sent["data"]) - 4
    assert protocol.decrypt(sent["data"][4:]) == json.dumps(request)


# ---------------------------------------------------------------------------
# TC-KASA-020 — single-plug device
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-KASA-020")
@pytest.mark.priority("MVP")
async def test_tc_kasa_020_connect_single_plug(monkeypatch):
    """KASA-020: a single-outlet plug (no 'children') reports exactly one switch."""
    def fake_send_command(host, command, port=9999):
        return {"system": {"get_sysinfo": {"alias": "Office Light", "relay_state": 1}}}

    monkeypatch.setattr("kasa_switch.adapter.send_command", fake_send_command)

    adapter = KasaSwitchAdapter(host="10.0.0.5")
    await adapter.connect()

    assert adapter.is_connected
    assert len(adapter.switches) == 1
    assert adapter.switches[0].name == "Office Light"
    assert adapter.switches[0].state is True
    assert adapter.switches[0].child_id is None


@pytest.mark.requirement("TC-KASA-020")
@pytest.mark.priority("MVP")
async def test_tc_kasa_020_set_switch_single_plug(monkeypatch):
    """KASA-020: toggling a single-plug switch sends a plain set_relay_state command."""
    sent_commands = []

    def fake_send_command(host, command, port=9999):
        sent_commands.append(command)
        if "get_sysinfo" in command.get("system", {}):
            return {"system": {"get_sysinfo": {"alias": "Office Light", "relay_state": 0}}}
        return {"system": {"set_relay_state": {"err_code": 0}}}

    monkeypatch.setattr("kasa_switch.adapter.send_command", fake_send_command)

    adapter = KasaSwitchAdapter(host="10.0.0.5")
    await adapter.connect()
    await adapter.set_switch("Office Light", True)

    assert sent_commands[-1] == {"system": {"set_relay_state": {"state": 1}}}
    assert adapter.switches[0].state is True


# ---------------------------------------------------------------------------
# TC-KASA-030 — multi-outlet power strip
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-KASA-030")
@pytest.mark.priority("MVP")
async def test_tc_kasa_030_connect_power_strip(monkeypatch):
    """KASA-030: an HS300-style strip reports one switch per child, named by its own alias."""
    def fake_send_command(host, command, port=9999):
        return {
            "system": {
                "get_sysinfo": {
                    "alias": "Observatory Strip",
                    "children": [
                        {"id": "AA", "alias": "Camera", "state": 1},
                        {"id": "BB", "alias": "Mount", "state": 0},
                        {"id": "CC", "alias": "Dew Heater", "state": 1},
                    ],
                }
            }
        }

    monkeypatch.setattr("kasa_switch.adapter.send_command", fake_send_command)

    adapter = KasaSwitchAdapter(host="10.0.0.6")
    await adapter.connect()

    names = [s.name for s in adapter.switches]
    assert names == ["Camera", "Mount", "Dew Heater"]
    assert adapter.switches[0].state is True
    assert adapter.switches[1].state is False
    assert adapter.switches[0].child_id == "AA"


@pytest.mark.requirement("TC-KASA-030")
@pytest.mark.priority("MVP")
async def test_tc_kasa_030_set_switch_power_strip_outlet(monkeypatch):
    """KASA-030: toggling one strip outlet scopes the command to its child_id only."""
    sent_commands = []

    def fake_send_command(host, command, port=9999):
        sent_commands.append(command)
        if "get_sysinfo" in command.get("system", {}):
            return {
                "system": {
                    "get_sysinfo": {
                        "alias": "Observatory Strip",
                        "children": [
                            {"id": "AA", "alias": "Camera", "state": 0},
                            {"id": "BB", "alias": "Mount", "state": 0},
                        ],
                    }
                }
            }
        return {"system": {"set_relay_state": {"err_code": 0}}}

    monkeypatch.setattr("kasa_switch.adapter.send_command", fake_send_command)

    adapter = KasaSwitchAdapter(host="10.0.0.6")
    await adapter.connect()
    await adapter.set_switch("Camera", True)

    toggle_cmd = sent_commands[-1]
    assert toggle_cmd["context"]["child_ids"] == ["AA"]
    assert toggle_cmd["system"]["set_relay_state"]["state"] == 1
    # The other outlet is untouched.
    assert adapter.switches[1].state is False


# ---------------------------------------------------------------------------
# TC-KASA-040 — error handling
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-KASA-040")
@pytest.mark.priority("MVP")
async def test_tc_kasa_040_set_switch_unknown_name_raises(monkeypatch):
    """KASA-040: toggling a switch name that doesn't exist on the device raises, rather than silently no-op."""
    def fake_send_command(host, command, port=9999):
        return {"system": {"get_sysinfo": {"alias": "Office Light", "relay_state": 0}}}

    monkeypatch.setattr("kasa_switch.adapter.send_command", fake_send_command)

    adapter = KasaSwitchAdapter(host="10.0.0.5")
    await adapter.connect()

    with pytest.raises(ValueError):
        await adapter.set_switch("Nonexistent", True)
