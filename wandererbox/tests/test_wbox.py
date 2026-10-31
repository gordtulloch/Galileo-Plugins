# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""WBOX — Wanderer switch profiles (TC-WBOX-010 … TC-WBOX-100).

The profiles are data, so these tests apply them with Galileo core's own mapping engine to recorded property
sets of the three drivers (``tests/recorded.py``) and check what the Switches page would be given. They need a
Galileo with switch-profile support (``EQP-SW-080``, ``PLUG-210``).
"""

import ast
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("galileo.core.switches")

import recorded
from galileo.adapters.indi import switch_map
from galileo.core.switches import flat_switches, resolve_profile
from galileo.plugins import PluginManager

import wandererbox
from wandererbox import DEW_TERMINATOR, PLUS_V3, PRO_V3, WandererBoxPlugin
from wandererbox.profiles import (
    DEW_TERMINATOR_DEVICE_NAME,
    PRO_V3_DEVICE_NAME,
)

PACKAGE = Path(wandererbox.__file__).parent


@pytest.fixture
def registered(clean_profile_registry):
    """The three profiles registered with core, as the plugin does."""
    for profile in (PRO_V3, PLUS_V3, DEW_TERMINATOR):
        clean_profile_registry.register(profile)
    return clean_profile_registry


def panel(device, props):
    return switch_map.build_panel(device, props).panel


def defined(props):
    return {(p.name, p.kind) for p in props.values()}


def channels(p):
    return {c.id: c for g in p.groups for c in g.channels}


def by_group(p):
    return {g.name: [c.id for c in g.channels] for g in p.groups}


# ---------------------------------------------------------------------------
# TC-WBOX-010 — registers exactly three profiles and nothing else
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-WBOX-010")
@pytest.mark.priority("MVP")
def test_tc_wbox_010_registers_three_profiles_and_nothing_else():
    """WBOX-010: the plugin registers the Pro V3, Plus V3 and Dew Terminator profiles through core, and no
    device backend, panel, settings page, dock pane or session block."""
    ctx = MagicMock()
    ctx.register_switch_profile.return_value = True
    WandererBoxPlugin().activate(ctx)
    assert [c.args[0].name for c in ctx.register_switch_profile.call_args_list] == [
        "WandererBox Pro V3", "WandererBox Plus V3", "Wanderer Dew Terminator"]
    assert [c[0] for c in ctx.method_calls] == ["register_switch_profile"] * 3      # nothing else was called

    plugin = WandererBoxPlugin()
    assert plugin.build_page() is None
    assert not (plugin.panel_label or plugin.dock_label or plugin.options_label or plugin.dashboard_label)


@pytest.mark.requirement("TC-WBOX-010")
@pytest.mark.priority("MVP")
def test_tc_wbox_010_an_older_galileo_gets_a_warning_not_a_failure(caplog):
    """WBOX-010: on a Galileo without switch-profile support the plugin warns instead of failing to load."""
    class OldContext:      # has no register_switch_profile
        pass

    with caplog.at_level(logging.WARNING):
        WandererBoxPlugin().activate(OldContext())
    assert "switch-profile support" in caplog.text


@pytest.mark.requirement("TC-WBOX-010")
@pytest.mark.priority("MVP")
def test_tc_wbox_010_core_accepts_all_three_profiles(clean_profile_registry):
    """WBOX-010: loaded into a real PluginManager, all three profiles pass core's validation."""
    manager = PluginManager()
    manager.load(WandererBoxPlugin)
    assert not manager.is_faulted("WandererBoxPlugin")
    assert {p.name for p in clean_profile_registry.profiles()} == {
        "WandererBox Pro V3", "WandererBox Plus V3", "Wanderer Dew Terminator"}
    for profile in (PRO_V3, PLUS_V3, DEW_TERMINATOR):
        profile.validate()


# ---------------------------------------------------------------------------
# TC-WBOX-020 / TC-WBOX-090 — identification by signature
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-WBOX-020")
@pytest.mark.priority("MVP")
def test_tc_wbox_020_device_names_are_claimed_as_documented():
    """WBOX-020: the Pro V3 and Plus V3 profiles both claim the name the Pro V3 driver (and the Plus V3
    driver) report; the Dew Terminator claims its own."""
    assert PRO_V3.device_names == PLUS_V3.device_names == ("WandererBox Pro V3",)
    assert DEW_TERMINATOR.device_names == ("Wanderer Dew Terminator",)
    assert PRO_V3.signature and PLUS_V3.signature and DEW_TERMINATOR.signature


@pytest.mark.requirement("TC-WBOX-020")
@pytest.mark.priority("MVP")
def test_tc_wbox_020_signatures_use_only_properties_always_defined_when_connected():
    """WBOX-020: a signature never relies on the per-channel PWM/mode properties (they come and go with the dew
    mode) or on Calibrate_DEVICE (firmware dependent), so a model is recognised whatever mode it is in."""
    volatile = {"PWM", "DC1", "DC5", "DC6", "DC7", "Calibrate_DEVICE",
                "DC5_DIFF_SET", "DC5_CONST_SET", "DC3_DIFF_SET", "DC3_CONST_SET"}
    heater_numbers = {"DC1", "DC2", "DC3"}              # the Dew Terminator's PWM numbers
    for profile in (PRO_V3, PLUS_V3, DEW_TERMINATOR):
        names = {n for n, _kind in profile.signature}
        assert not names & volatile, profile.name
        assert not {(n, k) for n, k in profile.signature if k == "number" and n in heater_numbers}, profile.name


@pytest.mark.requirement("TC-WBOX-090")
@pytest.mark.priority("MVP")
@pytest.mark.parametrize(("device", "build", "expected"), [
    (PRO_V3_DEVICE_NAME, recorded.pro_v3, "WandererBox Pro V3"),
    (PRO_V3_DEVICE_NAME, recorded.plus_v3, "WandererBox Plus V3"),      # same INDI name as the Pro V3
    (DEW_TERMINATOR_DEVICE_NAME, recorded.dew_terminator, "Wanderer Dew Terminator"),
])
def test_tc_wbox_090_each_models_property_set_resolves_to_its_own_profile(registered, device, build, expected):
    """WBOX-090: given a model's complete property set the intended profile is the one resolved, including the
    Plus V3 resolving to the Plus profile despite sharing the Pro V3's device name."""
    resolution = resolve_profile(device, defined(build()))
    assert resolution.profile is not None and resolution.profile.name == expected
    assert not resolution.ambiguous


@pytest.mark.requirement("TC-WBOX-090")
@pytest.mark.priority("MVP")
@pytest.mark.parametrize(("build", "expected"), [
    (recorded.pro_v3, "WandererBox Pro V3"),
    (recorded.plus_v3, "WandererBox Plus V3"),
    (recorded.dew_terminator, "Wanderer Dew Terminator"),
])
def test_tc_wbox_090_no_other_profiles_signature_is_satisfied_by_a_models_properties(build, expected):
    """WBOX-090: stronger than "the right one wins" — a model's complete property set satisfies *only* its own
    profile's signature, so no resolution ever depends on a tie-break between two profiles."""
    satisfied = {p.name for p in (PRO_V3, PLUS_V3, DEW_TERMINATOR) if p.satisfied_by(defined(build()))}
    assert satisfied == {expected}


@pytest.mark.requirement("TC-WBOX-090")
@pytest.mark.priority("MVP")
def test_tc_wbox_090_the_resolution_holds_in_every_dew_mode(registered):
    """WBOX-090: a model is still recognised when its drivers have withdrawn the PWM properties (automatic modes)."""
    assert resolve_profile(PRO_V3_DEVICE_NAME, defined(recorded.pro_v3((False, False, False)))).profile is PRO_V3
    assert resolve_profile(PRO_V3_DEVICE_NAME, defined(recorded.plus_v3(manual=False))).profile is PLUS_V3
    dt = recorded.dew_terminator((False, False, False))
    assert resolve_profile(DEW_TERMINATOR_DEVICE_NAME, defined(dt)).profile is DEW_TERMINATOR


@pytest.mark.requirement("TC-WBOX-090")
@pytest.mark.priority("MVP")
def test_tc_wbox_090_a_switch_dc2_is_not_a_number_dc2(registered):
    """WBOX-090: the Dew Terminator's DC2 is a number and the Plus V3's a switch; neither is taken for the other,
    and neither profile applies to a model it does not describe."""
    assert resolve_profile(PRO_V3_DEVICE_NAME, defined(recorded.dew_terminator())).profile is None
    assert resolve_profile(DEW_TERMINATOR_DEVICE_NAME, defined(recorded.plus_v3())).profile is None
    assert resolve_profile(PRO_V3_DEVICE_NAME, defined(recorded.dew_terminator())).ambiguous == []


# ---------------------------------------------------------------------------
# TC-WBOX-030 / 040 / 050 — what each model presents
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-WBOX-030")
@pytest.mark.priority("MVP")
def test_tc_wbox_030_pro_v3_channels(registered):
    """WBOX-030: three USB 3.1 ports individually, two USB 2.0 groups, the adjustable pair with its voltage,
    two 12 V groups and three dew heaters, with the vendor's names."""
    p = panel(PRO_V3_DEVICE_NAME, recorded.pro_v3())
    assert p.recognised and p.profile == "WandererBox Pro V3"
    assert by_group(p) == {
        "USB": ["usb3_1", "usb3_2", "usb3_3", "usb2_a", "usb2_b"],
        "DC": ["adjustable", "dc12_a", "dc12_b", "dc19", "dc12_open"],
        "Dew heaters": ["dew1", "dew2", "dew3"],
    }
    c = channels(p)
    assert [c[i].label for i in ("usb3_1", "usb3_2", "usb3_3")] == ["USB 3.1 #1", "USB 3.1 #2", "USB 3.1 #3"]
    assert c["usb2_a"].label == "USB 2.0 group A (ports 1–3)" and c["usb2_b"].label == "USB 2.0 group B (ports 4–6)"
    assert (c["usb3_1"].on, c["usb3_2"].on, c["usb2_a"].on) == (True, False, True)   # states come from the driver
    assert c["dc12_a"].label == "12 V group A (2 ports)" and c["dc12_b"].label == "12 V group B (2 ports)"

    adj = c["adjustable"]
    assert (adj.kind, adj.label, adj.on, adj.volts, adj.min, adj.max) == (
        "variable", "Adjustable 5–13.2 V (2 ports)", False, 12.0, 5.0, 13.2)
    assert [c[i].label for i in ("dew1", "dew2", "dew3")] == ["Dew heater 1", "Dew heater 2", "Dew heater 3"]


@pytest.mark.requirement("TC-WBOX-040")
@pytest.mark.priority("MVP")
def test_tc_wbox_040_plus_v3_channels(registered):
    """WBOX-040: all USB ports as one toggle, the adjustable port with its voltage, the three 12 V ports as one
    toggle and one dew heater, with the vendor's names."""
    p = panel(PRO_V3_DEVICE_NAME, recorded.plus_v3())
    assert p.profile == "WandererBox Plus V3"
    assert by_group(p) == {"USB": ["usb"], "DC": ["adjustable", "dc12", "dc12_open"], "Dew heaters": ["dew1"]}
    c = channels(p)
    assert c["usb"].label == "USB (all 5 ports)" and c["usb"].on is True
    assert c["dc12"].label == "12 V switched (3 ports)" and c["dc12"].on is True
    assert (c["adjustable"].kind, c["adjustable"].volts, c["adjustable"].min, c["adjustable"].max) == ("variable", 9.0, 5.0, 13.2)
    assert c["dew1"].label == "Dew heater"


@pytest.mark.requirement("TC-WBOX-050")
@pytest.mark.priority("MVP")
def test_tc_wbox_050_dew_terminator_channels(registered):
    """WBOX-050: three dew heaters, each usable as a heater or a plain switch, and none of the groups the
    device does not have."""
    p = panel(DEW_TERMINATOR_DEVICE_NAME, recorded.dew_terminator())
    assert p.profile == "Wanderer Dew Terminator"
    assert by_group(p) == {"Dew heaters": ["dew1", "dew2", "dew3"]}
    heaters = [channels(p)[i] for i in ("dew1", "dew2", "dew3")]
    assert all(h.kind == "dew" and h.switchable for h in heaters)
    assert [flat.name for flat in flat_switches(p)] == ["dew1", "dew2", "dew3"]       # usable as plain switches


# ---------------------------------------------------------------------------
# TC-WBOX-060 — dew heaters
# ---------------------------------------------------------------------------

class RecordingClient:
    def __init__(self):
        self.numbers, self.switches = [], []

    def send_number(self, device, name, values):
        self.numbers.append((name, values))

    def send_switch(self, device, name, values):
        self.switches.append((name, values))


def write(device, props, channel, **kwargs):
    client = RecordingClient()
    mapped = switch_map.build_panel(device, props)
    switch_map.apply_write(client, device, mapped, props, channel, **kwargs)
    return client


@pytest.mark.requirement("TC-WBOX-060")
@pytest.mark.priority("MVP")
def test_tc_wbox_060_duty_is_zero_to_one_hundred_on_a_zero_to_255_driver(registered):
    """WBOX-060: the drivers' 0-255 PWM is shown as 0-100 %, and written back on the native scale."""
    props = recorded.pro_v3()
    heater = channels(panel(PRO_V3_DEVICE_NAME, props))["dew1"]
    assert heater.duty == pytest.approx(128 / 255 * 100) and (heater.min, heater.max) == (0.0, 100.0)

    assert write(PRO_V3_DEVICE_NAME, props, "dew1", duty=40).numbers == [("PWM", {"DC5": 102.0})]
    assert write(PRO_V3_DEVICE_NAME, props, "dew2", duty=100).numbers == [("DC6", {"DC6": 255.0})]


@pytest.mark.requirement("TC-WBOX-060")
@pytest.mark.priority("MVP")
def test_tc_wbox_060_on_and_off_write_full_scale_and_zero(registered):
    """WBOX-060: the drivers have no on/off property for a heater, so on writes full scale and off writes zero,
    and a heater reads as on when its duty is above zero."""
    props = recorded.dew_terminator()
    assert write(DEW_TERMINATOR_DEVICE_NAME, props, "dew1", on=True).numbers == [("PWM", {"DC1": 255.0})]
    assert write(DEW_TERMINATOR_DEVICE_NAME, props, "dew2", on=False).numbers == [("DC2", {"DC2": 0.0})]
    assert write(DEW_TERMINATOR_DEVICE_NAME, props, "dew3", on=True).switches == []        # no switch property written

    assert channels(panel(DEW_TERMINATOR_DEVICE_NAME, props))["dew1"].on is False          # duty 0
    props["PWM"].elements["DC1"].value = 50.0
    assert channels(panel(DEW_TERMINATOR_DEVICE_NAME, props))["dew1"].on is True


@pytest.mark.requirement("TC-WBOX-060")
@pytest.mark.priority("MVP")
def test_tc_wbox_060_a_heater_in_an_automatic_mode_is_disabled_not_broken(registered):
    """WBOX-060: when a driver withdraws a heater's PWM (it is in a dew-point-difference or constant-temperature
    mode) that heater's control is disabled with a reason; its other heaters and the rest of the box are unaffected."""
    props = recorded.pro_v3((False, True, True))                  # dew heater 1 in an automatic mode
    p = panel(PRO_V3_DEVICE_NAME, props)
    c = channels(p)
    assert c["dew1"].available is False and "PWM" in c["dew1"].note and "optional" in c["dew1"].note
    assert c["dew2"].available and c["dew3"].available and c["usb3_1"].available
    assert p.recognised and p.profile == "WandererBox Pro V3"
    with pytest.raises(Exception, match="PWM"):
        write(PRO_V3_DEVICE_NAME, props, "dew1", duty=10)
    # The mode selector and setpoints stay reachable, with the driver's own labels, in Other.
    assert "DC5_DIFF" in {pr.name for g in p.other for pr in g.properties}


# ---------------------------------------------------------------------------
# TC-WBOX-070 — display-only ports
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-WBOX-070")
@pytest.mark.priority("MVP")
def test_tc_wbox_070_fixed_ports_are_shown_but_not_switchable(registered):
    """WBOX-070: the 12 V normally-open port (Pro and Plus) and the 19 V mini-PC port (Pro) are display-only:
    named, no toggle, and absent from the flat switch list."""
    pro = panel(PRO_V3_DEVICE_NAME, recorded.pro_v3())
    plus = panel(PRO_V3_DEVICE_NAME, recorded.plus_v3())
    for p, fixed in ((pro, ("dc19", "dc12_open")), (plus, ("dc12_open",))):
        c = channels(p)
        for cid in fixed:
            assert c[cid].switchable is False and c[cid].writable is False
        assert not set(fixed) & {s.name for s in flat_switches(p)}
    assert channels(pro)["dc19"].label == "19 V mini PC" and channels(pro)["dc19"].on is None
    assert channels(pro)["dc12_open"].label == "12 V normally open" and channels(pro)["dc12_open"].on is True
    assert "dc19" not in channels(plus)                                # the Plus has no 19 V port

    with pytest.raises(Exception, match="no switch channel"):
        write(PRO_V3_DEVICE_NAME, recorded.pro_v3(), "dc19", on=False)    # and cannot be written


# ---------------------------------------------------------------------------
# TC-WBOX-080 — readings
# ---------------------------------------------------------------------------

def readings(p):
    return {s.id: (s.label, s.value, s.unit) for s in p.sensors}


@pytest.mark.requirement("TC-WBOX-080")
@pytest.mark.priority("MVP")
def test_tc_wbox_080_pro_v3_readings(registered):
    """WBOX-080: input voltage, total / 19 V / regulator currents, three probes, humidity, temperature, dew point."""
    r = readings(panel(PRO_V3_DEVICE_NAME, recorded.pro_v3()))
    assert r["voltage"] == ("Input voltage", 12.1, "V")
    assert r["current"][1:] == (3.2, "A") and r["current_19v"][1:] == (1.1, "A") and r["current_regulator"][1:] == (0.4, "A")
    assert r["probe1"][1:] == (5.2, "°C") and r["probe3"][1:] == (6.0, "°C")
    assert r["humidity"][1:] == (70.0, "%") and r["ambient"][1:] == (7.5, "°C") and r["dew_point"][1:] == (2.1, "°C")


@pytest.mark.requirement("TC-WBOX-080")
@pytest.mark.priority("MVP")
def test_tc_wbox_080_a_missing_probe_is_no_reading_not_minus_127(registered):
    """WBOX-080: a probe that is not plugged in (the driver reports -127) is "no reading", not -127 °C."""
    r = readings(panel(PRO_V3_DEVICE_NAME, recorded.pro_v3()))
    assert r["probe2"][1] is None
    assert r["probe1"][1] == 5.2                                       # a real reading is untouched
    dt = readings(panel(DEW_TERMINATOR_DEVICE_NAME, recorded.dew_terminator()))
    assert dt["probe3"][1] is None and dt["probe1"][1] == 3.0


@pytest.mark.requirement("TC-WBOX-080")
@pytest.mark.priority("MVP")
def test_tc_wbox_080_each_model_shows_only_the_sensors_it_has(registered):
    """WBOX-080: the Plus V3 has one probe and a total current; the Dew Terminator has three probes and no current."""
    plus = readings(panel(PRO_V3_DEVICE_NAME, recorded.plus_v3()))
    assert set(plus) == {"voltage", "current", "probe1", "humidity", "ambient", "dew_point"}
    dt = readings(panel(DEW_TERMINATOR_DEVICE_NAME, recorded.dew_terminator()))
    assert set(dt) == {"voltage", "probe1", "probe2", "probe3", "humidity", "ambient", "dew_point"}
    assert not {"current", "current_19v", "current_regulator"} & set(dt)


# ---------------------------------------------------------------------------
# TC-WBOX-100 — data-only, inert when disabled, nothing else claimed
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-WBOX-100")
@pytest.mark.priority("MVP")
def test_tc_wbox_100_the_package_has_no_device_code():
    """WBOX-100: the plugin holds no INDI connection and writes no device-protocol code — nothing in the
    package imports the adapters, a socket, or Qt."""
    forbidden = ("galileo.adapters", "socket", "PySide6", "serial", "asyncio", "threading")
    for source in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            imported = []
            if isinstance(node, ast.Import):
                imported = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for name in imported:
                assert not name.startswith(forbidden), f"{source.name} imports {name}"


@pytest.mark.requirement("TC-WBOX-100")
@pytest.mark.priority("MVP")
def test_tc_wbox_100_disabling_or_removing_the_plugin_withdraws_the_profiles(clean_profile_registry):
    """WBOX-100 / PLUG-060 / PLUG-120: while disabled the profiles are not offered, so the device is a plain
    unrecognised one reachable through Aux; enabling restores them; unloading drops them."""
    manager = PluginManager()
    manager.load(WandererBoxPlugin)
    assert resolve_profile(PRO_V3_DEVICE_NAME, defined(recorded.pro_v3())).profile is PRO_V3

    manager.disable("WandererBoxPlugin")
    assert resolve_profile(PRO_V3_DEVICE_NAME, defined(recorded.pro_v3())).profile is None
    unmapped = panel(PRO_V3_DEVICE_NAME, recorded.pro_v3())
    assert not unmapped.recognised and unmapped.groups == []
    assert "USB3.0_1" in {pr.name for g in unmapped.other for pr in g.properties}      # still reachable

    manager.enable("WandererBoxPlugin")
    assert resolve_profile(PRO_V3_DEVICE_NAME, defined(recorded.pro_v3())).profile is PRO_V3
    manager.unload("WandererBoxPlugin")
    assert clean_profile_registry.profiles() == []


@pytest.mark.requirement("TC-WBOX-100")
@pytest.mark.priority("MVP")
def test_tc_wbox_100_only_the_two_documented_names_are_claimed(registered):
    """WBOX-100: no profile claims any other device name, so a WandererBox Lite V3 (which has no control
    interface) or any other Wanderer product is not presented as supported."""
    assert registered.claimed_device_names() == {"WandererBox Pro V3", "Wanderer Dew Terminator"}
    for other in ("WandererBox Lite V3", "WandererBox Plus V3", "Wanderer Cover V4", "WandererRotator Mini"):
        assert registered.profiles_for_name(other) == []
        assert not panel(other, recorded.pro_v3()).profile
