# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""AAVSO WebObs Extended-format report generation (VST-AN-050).

The Extended format is a header block of ``#KEYWORD=value`` lines followed by
one comma-delimited data line per measurement, each carrying exactly these
fifteen fields in this order:

    NAME, DATE, MAG, MERR, FILT, TRANS, MTYPE, CNAME, CMF, KNAME, KMF,
    AMASS, GROUP, CHART, NOTES

Every field is positional, and an unused one is the literal string ``na`` —
never blank — so a missing value cannot silently shift every field after it
into the wrong column.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vstarget.analysis.photometry import PhotometryResult

#: The fifteen Extended-format data fields, in order.
FIELDS = (
    "NAME", "DATE", "MAG", "MERR", "FILT", "TRANS", "MTYPE",
    "CNAME", "CMF", "KNAME", "KMF", "AMASS", "GROUP", "CHART", "NOTES",
)

_NA = "na"


def generate_aavso_report(
    measurements: list[PhotometryResult], observer_code: str = ""
) -> str:
    """Return a WebObs Extended-format report for *measurements* (VST-AN-050).

    *observer_code* is the submitter's AAVSO observer code; WebObs rejects a
    submission without one, so it is written through to ``#OBSCODE`` rather
    than left blank.
    """
    lines = [
        "#TYPE=EXTENDED",
        f"#OBSCODE={observer_code}",
        "#SOFTWARE=Galileo",
        "#DELIM=,",
        "#DATE=JD",
        "#OBSTYPE=CCD",
        "#",
        "#" + ",".join(FIELDS),
    ]
    lines.extend(_data_line(m) for m in measurements)
    return "\n".join(lines) + "\n"


def _data_line(m: PhotometryResult) -> str:
    """One measurement as a fifteen-field Extended-format record."""
    # The engine measures against an ensemble of comparison stars, so CNAME is
    # ENSEMBLE and CMF is na unless a caller named one specific comparison star.
    comp_name = getattr(m, "comp_star", "") or "ENSEMBLE"

    fields = (
        m.target,
        f"{m.jd:.5f}",
        f"{m.magnitude:.3f}",
        f"{m.uncertainty:.3f}",
        m.filter_band,
        "YES" if getattr(m, "is_transformed", False) else "NO",
        "STD",
        comp_name,
        _NA,
        getattr(m, "check_star", "") or _NA,
        _number(getattr(m, "check_mag", None), ".3f"),
        _number(getattr(m, "airmass", None), ".3f"),
        "0",                                        # GROUP: ungrouped
        getattr(m, "chart_id", "") or _NA,
        getattr(m, "notes", "") or _NA,
    )
    return ",".join(fields)


def _number(value, spec: str) -> str:
    """Format *value*, or ``na`` when it is absent."""
    if value is None or value == "":
        return _NA
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return _NA


def save_aavso_report(
    measurements: list[PhotometryResult],
    output_path: Path | str,
    observer_code: str = "",
) -> None:
    Path(output_path).write_text(
        generate_aavso_report(measurements, observer_code), encoding="utf-8"
    )
