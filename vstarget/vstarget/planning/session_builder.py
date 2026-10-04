# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Turns an observation plan into a Galileo session (VST-060, SES-120, SES-160).

The standalone VSTarget application ended at an iTelescope/ACP ``.txt`` script.
As a Galileo plugin there is no script to write: the plan becomes a session on
Planning > Sessions, authored from the same block types a user would drag in by
hand, so it can then be edited, scheduled and run like any other session.

One plan becomes one session, its targets in RA order - the order the old
script file used, and the order an efficient east-to-west run wants. Each
target contributes:

    Target block
    [PlateSolve] [Autofocus] [GuideStart]      - whichever options are on
    Image block per filter                      - count x interval, binning
    [Dither] between Image blocks
    [GuideStop]

The per-target parameter strings (``script_filters`` and friends) are parallel
comma-separated lists, one value per filter, exactly as the Observation Targets
table edits them.
"""

from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)


class SessionOptions:
    """Which optional blocks the panel's Session Options row adds per target."""

    def __init__(
        self,
        platesolve: bool = True,
        autofocus: bool = False,
        dither: bool = False,
        guiding: bool = False,
    ) -> None:
        self.platesolve = platesolve
        self.autofocus = autofocus
        self.dither = dither
        self.guiding = guiding


def validate_targets(targets: list) -> list[str]:
    """Return human-readable warnings about the plan's capture parameters.

    Filter, count, interval and binning must each supply the same number of
    comma-separated values, since they are read in parallel to build one Image
    block per filter.
    """
    warnings: list[str] = []
    for ot in targets:
        name = ot.aavso.star_name
        parts = {
            "filter": _split(ot.script_filters),
            "count": _split(ot.script_counts),
            "interval": _split(ot.script_intervals),
            "binning": _split(ot.script_binning),
        }
        lengths = {k: len(v) for k, v in parts.items()}
        if len(set(lengths.values())) != 1:
            detail = ", ".join(f"{k}={n}" for k, n in lengths.items())
            warnings.append(
                f"{name}: filter/count/interval/binning must have the same "
                f"number of values ({detail})"
            )
        for key, values in parts.items():
            if not values:
                warnings.append(f"{name}: {key} is empty")
    return warnings


def default_session_name(now: datetime.datetime | None = None) -> str:
    """The name a newly built session gets, e.g. 'Variable Stars 2026-10-03'."""
    # Local time on purpose: the label names the observer's night, not a UTC instant.
    now = now or datetime.datetime.now()  # noqa: DTZ005
    return f"Variable Stars {now:%Y-%m-%d}"


def build_blocks(targets: list, options: SessionOptions | None = None) -> list:
    """Return the session blocks for *targets*, sorted by RA.

    Importing the block types lazily keeps this module importable without a Qt
    application present - ``galileo.ui.sessions`` pulls in PySide6.
    """
    from galileo.ui.sessions import (
        AutofocusBlock,
        DitherBlock,
        GuideStartBlock,
        GuideStopBlock,
        ImageBlock,
        PlateSolveBlock,
        TargetBlock,
    )

    options = options or SessionOptions()
    blocks: list = []

    for ot in sorted(targets, key=lambda t: t.aavso.ra):
        star = ot.aavso
        blocks.append(
            TargetBlock(name=star.star_name, ra_deg=star.ra, dec_deg=star.dec)
        )
        if options.platesolve:
            blocks.append(PlateSolveBlock())
        if options.autofocus:
            blocks.append(AutofocusBlock())
        if options.guiding:
            blocks.append(GuideStartBlock())

        image_blocks = [
            ImageBlock(
                exposure=exposure,
                count=count,
                filter=filter_name,
                binning=binning,
                frame_type="Light",
            )
            for filter_name, count, exposure, binning in _capture_steps(ot)
        ]
        for i, block in enumerate(image_blocks):
            if i and options.dither:
                blocks.append(DitherBlock())
            blocks.append(block)

        if options.guiding:
            blocks.append(GuideStopBlock())

    return blocks


def total_exposure_seconds(targets: list) -> float:
    """Total shutter-open time across the whole plan, in seconds."""
    return sum(target_exposure_seconds(ot) for ot in targets)


def target_exposure_seconds(ot) -> float:
    """Shutter-open time for one target: sum of count x interval over its filters."""
    return sum(count * exposure for _, count, exposure, _ in _capture_steps(ot))


def describe(targets: list, options: SessionOptions | None = None) -> str:
    """A plain-text outline of the session the plan would build, for preview."""
    options = options or SessionOptions()
    lines: list[str] = []
    for block in build_blocks(targets, options):
        text = block.display_text
        # Indent everything that belongs under the Target block it follows.
        lines.append(text if text.startswith("Target:") else f"    {text}")
    return "\n".join(lines)


def _capture_steps(ot) -> list[tuple[str, int, float, int]]:
    """Zip one target's parallel parameter lists into (filter, count, exposure, binning).

    Short lists are tolerated here rather than rejected - ``validate_targets``
    is what reports a ragged plan to the user, and the preview should still
    render something when they choose to proceed anyway.
    """
    filters = _split(ot.script_filters)
    counts = _split(ot.script_counts)
    intervals = _split(ot.script_intervals)
    binnings = _split(ot.script_binning)

    steps: list[tuple[str, int, float, int]] = []
    for i, filter_name in enumerate(filters):
        steps.append((
            filter_name,
            _as_int(counts[i] if i < len(counts) else "", 1),
            _as_float(intervals[i] if i < len(intervals) else "", 0.0),
            _as_int(binnings[i] if i < len(binnings) else "", 1),
        ))
    return steps


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _as_int(value: str, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
