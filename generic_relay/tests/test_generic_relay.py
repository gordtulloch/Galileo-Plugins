# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""RELAY — Generic Internet Relay switch plugin (TC-RELAY-010 … TC-RELAY-060)."""

import pytest

from generic_relay.adapter import DEFAULT_URL_TEMPLATE, GenericRelayAdapter


class _FakeResponse:
    def __init__(self, status_ok: bool = True) -> None:
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("HTTP error")


# ---------------------------------------------------------------------------
# TC-RELAY-010 — default URL template matches the reference example exactly
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-RELAY-010")
@pytest.mark.priority("MVP")
def test_tc_relay_010_default_template_matches_reference_example():
    """RELAY-010: relay 1 on/off reproduces the documented
    http://10.0.0.101/30000/01 and http://10.0.0.101/30000/00 URLs exactly."""
    adapter = GenericRelayAdapter(host="10.0.0.101", port_count=1)

    on_url = adapter.build_url(relay_index_0based=0, state=1)
    off_url = adapter.build_url(relay_index_0based=0, state=0)

    assert on_url == "http://10.0.0.101/30000/01"
    assert off_url == "http://10.0.0.101/30000/00"


@pytest.mark.requirement("TC-RELAY-010")
@pytest.mark.priority("MVP")
def test_tc_relay_010_relay_index_is_zero_based_in_url():
    """RELAY-010: relay 2 (UI-facing "Relay 2") uses port index 1 in the URL."""
    adapter = GenericRelayAdapter(host="10.0.0.101", port_count=4)
    assert adapter.build_url(relay_index_0based=1, state=1) == "http://10.0.0.101/30000/11"
    assert adapter.build_url(relay_index_0based=3, state=0) == "http://10.0.0.101/30000/30"


# ---------------------------------------------------------------------------
# TC-RELAY-020 — configuration (host, port count, custom template)
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-RELAY-020")
@pytest.mark.priority("MVP")
def test_tc_relay_020_port_count_creates_that_many_switches():
    """RELAY-020: the adapter exposes one switch per configured relay, named 'Relay N'."""
    adapter = GenericRelayAdapter(host="10.0.0.101", port_count=8)
    assert [s.name for s in adapter.switches] == [f"Relay {n}" for n in range(1, 9)]
    assert all(s.state is False for s in adapter.switches)


@pytest.mark.requirement("TC-RELAY-020")
@pytest.mark.priority("MVP")
def test_tc_relay_020_custom_url_template_is_respected():
    """RELAY-020: a board with a different URL scheme is supported via its own template."""
    adapter = GenericRelayAdapter(
        host="192.168.1.9",
        port_count=2,
        url_template="http://{ip}/api/relay/{port:02d}?state={state}",
    )
    assert adapter.build_url(0, 1) == "http://192.168.1.9/api/relay/00?state=1"
    assert adapter.build_url(1, 0) == "http://192.168.1.9/api/relay/01?state=0"


# ---------------------------------------------------------------------------
# TC-RELAY-030 — toggling a relay
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-RELAY-030")
@pytest.mark.priority("MVP")
async def test_tc_relay_030_set_switch_issues_correct_get(monkeypatch):
    """RELAY-030: toggling a relay performs an HTTP GET against the templated URL
    and updates the adapter's tracked state on success."""
    requested_urls = []

    def fake_get(url, timeout=None):
        requested_urls.append(url)
        return _FakeResponse(status_ok=True)

    monkeypatch.setattr("generic_relay.adapter.requests.get", fake_get)

    adapter = GenericRelayAdapter(host="10.0.0.101", port_count=2)
    await adapter.set_switch("Relay 1", True)

    assert requested_urls == ["http://10.0.0.101/30000/01"]
    assert adapter.switches[0].state is True
    assert adapter.switches[1].state is False

    await adapter.set_switch("Relay 1", False)
    assert requested_urls[-1] == "http://10.0.0.101/30000/00"
    assert adapter.switches[0].state is False


@pytest.mark.requirement("TC-RELAY-030")
@pytest.mark.priority("MVP")
async def test_tc_relay_030_second_relay_uses_its_own_index(monkeypatch):
    """RELAY-030: relay 2's URL encodes port index 1, not 0."""
    requested_urls = []

    def fake_get(url, timeout=None):
        requested_urls.append(url)
        return _FakeResponse()

    monkeypatch.setattr("generic_relay.adapter.requests.get", fake_get)

    adapter = GenericRelayAdapter(host="10.0.0.101", port_count=2)
    await adapter.set_switch("Relay 2", True)

    assert requested_urls == ["http://10.0.0.101/30000/11"]
    assert adapter.switches[1].state is True
    assert adapter.switches[0].state is False


# ---------------------------------------------------------------------------
# TC-RELAY-040 — error handling
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-RELAY-040")
@pytest.mark.priority("MVP")
async def test_tc_relay_040_unknown_switch_name_raises(monkeypatch):
    """RELAY-040: toggling a name that doesn't match a configured relay raises."""
    adapter = GenericRelayAdapter(host="10.0.0.101", port_count=2)
    with pytest.raises(ValueError):
        await adapter.set_switch("Relay 99", True)


@pytest.mark.requirement("TC-RELAY-040")
@pytest.mark.priority("MVP")
async def test_tc_relay_040_http_error_propagates_and_state_unchanged(monkeypatch):
    """RELAY-040: an HTTP failure (e.g. unreachable board) raises rather than
    silently reporting success, and the tracked state is left as it was."""
    monkeypatch.setattr(
        "generic_relay.adapter.requests.get",
        lambda url, timeout=None: _FakeResponse(status_ok=False),
    )

    adapter = GenericRelayAdapter(host="10.0.0.101", port_count=1)
    with pytest.raises(RuntimeError):
        await adapter.set_switch("Relay 1", True)

    assert adapter.switches[0].state is False
