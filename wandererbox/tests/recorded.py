# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Property sets of the three Wanderer INDI drivers, as a client sees them once connected.

Transcribed from the driver sources (``wandererbox_pro_v3.cpp``, ``wandererbox_plus_v3.cpp``,
``wanderer_dew_terminator.cpp``; docs/plugins/wandererbox/PSD.md §13): property names, kinds, element names,
ranges and the dew mode / PWM properties each driver defines. They are *not* captured from a running driver —
that remains to be done against real hardware — so a driver that differs from its source would not be caught here.
"""

from __future__ import annotations

from galileo.adapters.indi_client import IndiElement, IndiProperty


def sw(name, elements, rule="OneOfMany", perm="rw", group="Main Control"):
    return IndiProperty("Dev", name, "switch", group=group, perm=perm, rule=rule,
                        elements={k: IndiElement(k, k, v) for k, v in elements.items()})


def num(name, elements, perm="rw", step=1.0, group="Main Control"):
    """elements: name -> (value, min, max)."""
    return IndiProperty("Dev", name, "number", group=group, perm=perm,
                        elements={k: IndiElement(k, k, v, lo, hi, step) for k, (v, lo, hi) in elements.items()})


def snap(*props):
    return {p.name: p for p in props}


def on_off(name, on=False):
    return sw(name, {"INDI_ENABLED": on, "INDI_DISABLED": not on})


def dew_modes(name):
    return sw(name, {"Manual": True, "DPD_Mode": False, "CT_Mode": False}, group="Dew")


def common():
    return [
        IndiProperty("Dev", "DRIVER_INFO", "text", perm="ro", elements={"DRIVER_NAME": IndiElement("DRIVER_NAME", "n", "x")}),
        sw("CONNECTION", {"CONNECT": True, "DISCONNECT": False}),
    ]


def environment(probes):
    elements = {f"Probe{i}_Temp": (v, 0, 999) for i, v in enumerate(probes, start=1)}
    elements.update({"ENV_Humidity": (70.0, 0, 999), "ENV_Temp": (7.5, 0, 999), "DEW_Point": (2.1, 0, 999)})
    return num("ENV_Monitor", elements, perm="ro", group="Sensors")


def pro_v3(manual=(True, True, True)):
    """``manual``: for each dew channel, whether its PWM property is defined (Manual mode) or withdrawn."""
    props = [
        *common(),
        num("POWER_Monitor", {"VOLTAGE": (12.1, 0, 999), "TOTAL_CURRENT": (3.2, 0, 999),
                              "V19_CURRENT": (1.1, 0, 999), "AR_CURRENT": (0.4, 0, 999)}, perm="ro"),
        on_off("USB3.0_1", True), on_off("USB3.0_2"), on_off("USB3.0_3"),
        on_off("USB2.0_1-3", True), on_off("USB2.0_4-6"),
        num("DC34voltageSET", {"DC34SET": (12.0, 5, 13.2)}, step=0.1), on_off("DC3-4"),
        on_off("DC8-9"), on_off("DC10-11"),
        dew_modes("DC5_DIFF"), dew_modes("DC6_DIFF"), dew_modes("DC7_DIFF"),
        environment([5.2, -127.0, 6.0]),
        sw("Calibrate_DEVICE", {"Calibrate": False}, rule="AtMostOne"),
        num("WEATHER_PARAMETERS", {"WEATHER_TEMPERATURE": (7.5, -15, 35)}, perm="ro", group="Environment"),
    ]
    for present, prop, element in zip(manual, ("PWM", "DC6", "DC7"), ("DC5", "DC6", "DC7"), strict=True):
        if present:
            props.append(num(prop, {element: (128.0, 0, 255)}, step=5.0, group="Dew"))
    return snap(*props)


def plus_v3(manual=True):
    props = [
        *common(),
        num("POWER_Monitor", {"VOLTAGE": (12.1, 0, 999), "TOTAL_CURRENT": (2.0, 0, 999)}, perm="ro"),
        on_off("USB", True), on_off("DC2"), num("DC2voltageSET", {"DC2SET": (9.0, 5, 13.2)}, step=0.1),
        on_off("DC4-6", True), dew_modes("DC3_DIFF"),
        environment([4.4]),
        sw("Calibrate_DEVICE", {"Calibrate": False}, rule="AtMostOne"),
    ]
    if manual:
        props.append(num("PWM", {"DC3": (0.0, 0, 255)}, step=5.0, group="Dew"))
    return snap(*props)


def dew_terminator(manual=(True, True, True)):
    props = [
        *common(),
        num("POWER_Monitor", {"VOLTAGE": (12.3, 0, 999)}, perm="ro"),
        dew_modes("DC1_DIFF"), dew_modes("DC2_DIFF"), dew_modes("DC3_DIFF"),
        environment([3.0, 3.5, -127.0]),
    ]
    for present, prop, element in zip(manual, ("PWM", "DC2", "DC3"), ("DC1", "DC2", "DC3"), strict=True):
        if present:
            props.append(num(prop, {element: (0.0, 0, 255)}, step=5.0, group="Dew"))
    return snap(*props)
