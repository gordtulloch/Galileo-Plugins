# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Switch profiles for the Wanderer Astro WandererBox Pro V3, Plus V3 and Dew Terminator.

Pure data (WBOX-010 … WBOX-100): no I/O and no INDI import. Galileo core applies a profile with the same
mapping engine it uses for INDI's standard power interface; this module only says which properties of each
driver mean what. Property names are the drivers' own, read from ``wandererbox_pro_v3.cpp``,
``wandererbox_plus_v3.cpp`` and ``wanderer_dew_terminator.cpp`` (docs/plugins/wandererbox/PSD.md §13).

Two facts shape the declarations:

* The Plus V3 driver reports the same default INDI device name as the Pro V3 driver, so both profiles claim
  ``WandererBox Pro V3`` and a connected device is told apart by its property signature — and a property's
  *kind* is part of it, because the Plus V3 has a **switch** called ``DC2`` (its regulator) while the Dew
  Terminator has a **number** called ``DC2`` (a heater). Signatures use only properties a driver always defines
  once connected, never the per-channel PWM/mode properties that come and go with the dew mode.
* The drivers have no on/off property for a dew heater, only a 0-255 PWM, so a heater is switched by writing full
  scale or zero. It is optional: the driver withdraws the PWM property while the heater is in an automatic mode.
"""

from __future__ import annotations

from galileo.core.switches import ProfileChannel, ProfileSensor, SwitchProfile

PRO_V3_DEVICE_NAME = "WandererBox Pro V3"          # also what the Plus V3 driver reports
DEW_TERMINATOR_DEVICE_NAME = "Wanderer Dew Terminator"

_ENABLED = "INDI_ENABLED"
_NATIVE_DUTY_MAX = 255.0                           # the drivers' PWM is 0-255

_USB, _DC, _DEW = "USB", "DC", "Dew heaters"


def _toggle(channel_id: str, label: str, group: str, prop: str) -> ProfileChannel:
    return ProfileChannel(id=channel_id, label=label, group=group, switch=(prop, _ENABLED))


def _regulator(channel_id: str, label: str, switch_prop: str, volts_prop: str, volts_element: str) -> ProfileChannel:
    """The 5-13.2 V adjustable output: an on/off switch and a voltage, on separate properties."""
    return ProfileChannel(id=channel_id, label=label, group=_DC, kind="variable",
                          switch=(switch_prop, _ENABLED), number=(volts_prop, volts_element))


def _heater(channel_id: str, label: str, prop: str, element: str) -> ProfileChannel:
    return ProfileChannel(id=channel_id, label=label, group=_DEW, kind="dew", number=(prop, element),
                          native_max=_NATIVE_DUTY_MAX, on_via_duty=True, optional=True)


def _fixed(channel_id: str, label: str, always_on: bool = False) -> ProfileChannel:
    """A port with no property at all — the hardware cannot switch it."""
    return ProfileChannel(id=channel_id, label=label, group=_DC, display_only=True, always_on=always_on)


def _sensor(sensor_id: str, label: str, unit: str, prop: str, element: str, *, probe: bool = False) -> ProfileSensor:
    # A temperature probe that is not plugged in reads -127 (the Dallas sensor's "absent" value).
    return ProfileSensor(id=sensor_id, label=label, unit=unit, ref=(prop, element), no_reading=(-127.0,) if probe else ())


def _probes(count: int) -> tuple[ProfileSensor, ...]:
    return tuple(_sensor(f"probe{i}", f"Dew probe {i} temperature", "°C", "ENV_Monitor", f"Probe{i}_Temp", probe=True)
                 for i in range(1, count + 1))


def _environment() -> tuple[ProfileSensor, ...]:
    return (
        _sensor("humidity", "Ambient humidity", "%", "ENV_Monitor", "ENV_Humidity"),
        _sensor("ambient", "Ambient temperature", "°C", "ENV_Monitor", "ENV_Temp"),
        _sensor("dew_point", "Dew point", "°C", "ENV_Monitor", "DEW_Point"),
    )


_VOLTAGE = _sensor("voltage", "Input voltage", "V", "POWER_Monitor", "VOLTAGE")
_TOTAL_CURRENT = _sensor("current", "Total current", "A", "POWER_Monitor", "TOTAL_CURRENT")


PRO_V3 = SwitchProfile(
    name="WandererBox Pro V3",
    device_names=(PRO_V3_DEVICE_NAME,),
    signature=frozenset({
        ("USB3.0_1", "switch"), ("USB3.0_2", "switch"), ("USB3.0_3", "switch"),
        ("USB2.0_1-3", "switch"), ("USB2.0_4-6", "switch"),
        ("DC3-4", "switch"), ("DC8-9", "switch"), ("DC10-11", "switch"),
        ("DC34voltageSET", "number"), ("POWER_Monitor", "number"), ("ENV_Monitor", "number"),
    }),
    channels=(
        _toggle("usb3_1", "USB 3.1 #1", _USB, "USB3.0_1"),
        _toggle("usb3_2", "USB 3.1 #2", _USB, "USB3.0_2"),
        _toggle("usb3_3", "USB 3.1 #3", _USB, "USB3.0_3"),
        _toggle("usb2_a", "USB 2.0 group A (ports 1–3)", _USB, "USB2.0_1-3"),
        _toggle("usb2_b", "USB 2.0 group B (ports 4–6)", _USB, "USB2.0_4-6"),
        _regulator("adjustable", "Adjustable 5–13.2 V (2 ports)", "DC3-4", "DC34voltageSET", "DC34SET"),
        _toggle("dc12_a", "12 V group A (2 ports)", _DC, "DC8-9"),
        _toggle("dc12_b", "12 V group B (2 ports)", _DC, "DC10-11"),
        _fixed("dc19", "19 V mini PC"),
        _fixed("dc12_open", "12 V normally open", always_on=True),
        _heater("dew1", "Dew heater 1", "PWM", "DC5"),
        _heater("dew2", "Dew heater 2", "DC6", "DC6"),
        _heater("dew3", "Dew heater 3", "DC7", "DC7"),
    ),
    sensors=(
        _VOLTAGE,
        _TOTAL_CURRENT,
        _sensor("current_19v", "19 V current", "A", "POWER_Monitor", "V19_CURRENT"),
        _sensor("current_regulator", "Adjustable regulator current", "A", "POWER_Monitor", "AR_CURRENT"),
        *_probes(3),
        *_environment(),
    ),
)

PLUS_V3 = SwitchProfile(
    name="WandererBox Plus V3",
    device_names=(PRO_V3_DEVICE_NAME,),
    signature=frozenset({
        ("USB", "switch"), ("DC2", "switch"), ("DC4-6", "switch"), ("DC3_DIFF", "switch"),
        ("DC2voltageSET", "number"), ("POWER_Monitor", "number"), ("ENV_Monitor", "number"),
    }),
    channels=(
        _toggle("usb", "USB (all 5 ports)", _USB, "USB"),
        _regulator("adjustable", "Adjustable 5–13.2 V", "DC2", "DC2voltageSET", "DC2SET"),
        _toggle("dc12", "12 V switched (3 ports)", _DC, "DC4-6"),
        _fixed("dc12_open", "12 V normally open", always_on=True),
        _heater("dew1", "Dew heater", "PWM", "DC3"),
    ),
    sensors=(_VOLTAGE, _TOTAL_CURRENT, *_probes(1), *_environment()),
)

DEW_TERMINATOR = SwitchProfile(
    name="Wanderer Dew Terminator",
    device_names=(DEW_TERMINATOR_DEVICE_NAME,),
    signature=frozenset({
        ("DC1_DIFF", "switch"), ("DC2_DIFF", "switch"), ("DC3_DIFF", "switch"),
        ("POWER_Monitor", "number"), ("ENV_Monitor", "number"),
    }),
    channels=(
        _heater("dew1", "Dew heater / switch 1", "PWM", "DC1"),
        _heater("dew2", "Dew heater / switch 2", "DC2", "DC2"),
        _heater("dew3", "Dew heater / switch 3", "DC3", "DC3"),
    ),
    sensors=(_VOLTAGE, *_probes(3), *_environment()),
)

PROFILES: tuple[SwitchProfile, ...] = (PRO_V3, PLUS_V3, DEW_TERMINATOR)
