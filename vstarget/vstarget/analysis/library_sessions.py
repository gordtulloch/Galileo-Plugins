# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Library-backed input selection for the analysis panel (``VST-AN-100``).

The analysis panel used to take a folder of FITS files. Everything it needs is
already catalogued by ``galileo.library``: the frames live in the repository,
grouped into sessions that carry the target, date, filter and telescope, and a
target is designated a variable star from the Images tab's *Add Variable Star*
context menu (the ``VariableStars`` table). This module is the read side of
that — it turns those two tables into the session rows the panel lists and the
frame paths photometry runs over, so the Qt page holds no queries of its own.

Kept free of Qt so it is testable against a migrated database alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# AstroFiler-era catalogs spell the light-frame type both ways, so matching is
# done on the upper-cased column rather than one literal.
_LIGHT_TYPES = ("LIGHT", "LIGHT FRAME")


class LibraryUnavailable(RuntimeError):
    """Raised when the Galileo library database isn't importable or isn't open."""


@dataclass(frozen=True)
class LibrarySession:
    """One catalogued imaging session of a designated variable-star target."""

    session_id: str
    target: str
    date: str
    filter_band: str
    telescope: str
    instrument: str
    exposure: str
    frame_count: int

    @property
    def label(self) -> str:
        """How the session reads in a one-line summary."""
        parts = [self.date or "unknown date"]
        if self.filter_band:
            parts.append(self.filter_band)
        parts.append(f"{self.frame_count} frame{'' if self.frame_count == 1 else 's'}")
        return " · ".join(parts)


def _models():
    """The library models, or :class:`LibraryUnavailable` if there's no open database."""
    try:
        from galileo.library.models import VariableStars, fitsFile, fitsSession
        from galileo.library.models.base import db
    except Exception as exc:  # standalone use without Galileo installed
        raise LibraryUnavailable(
            "Galileo's image library is not available in this installation."
        ) from exc
    if db.database is None:
        raise LibraryUnavailable(
            "Galileo's image library database is not open — open the Library tab first."
        )
    return VariableStars, fitsSession, fitsFile


def variable_star_targets() -> list[str]:
    """Every target designated a variable star, in name order.

    These are the names the Images tab's *Add Variable Star* action writes.
    """
    VariableStars, _, _ = _models()
    names = [
        (row.target_name or "").strip()
        for row in VariableStars.select().order_by(VariableStars.target_name)
    ]
    return [n for n in names if n]


def list_variable_star_sessions(target: str | None = None) -> list[LibrarySession]:
    """Every catalogued session belonging to a designated variable-star target.

    Restricted to *target* when given. Object names are matched case- and
    whitespace-insensitively, because a FITS ``OBJECT`` value won't always
    match the capitalisation of the name designated in the Images tab, and
    every session reports under the designated spelling so one star doesn't
    split into two groups in the panel. Sessions come back newest first within
    each target; a session holding no light frames is dropped, since there'd be
    nothing to measure.
    """
    import peewee as pw

    _, fitsSession, fitsFile = _models()

    canonical = {n.lower(): n for n in variable_star_targets()}
    if target:
        # Narrowing never widens: an undesignated target has no sessions here,
        # whether or not the library holds images of it.
        canonical = {k: v for k, v in canonical.items() if k == target.strip().lower()}
    if not canonical:
        return []

    object_key = pw.fn.LOWER(pw.fn.TRIM(fitsSession.fitsSessionObjectName))
    rows = fitsSession.select().where(object_key.in_(sorted(canonical)))

    sessions: list[LibrarySession] = []
    for row in rows:
        count = _light_frame_query(fitsFile, str(row.fitsSessionId)).count()
        if not count:
            continue
        key = (row.fitsSessionObjectName or "").strip().lower()
        sessions.append(
            LibrarySession(
                session_id=str(row.fitsSessionId),
                target=canonical.get(key, (row.fitsSessionObjectName or "").strip()),
                date=str(row.fitsSessionDate) if row.fitsSessionDate else "",
                filter_band=(row.fitsSessionFilter or "").strip(),
                telescope=(row.fitsSessionTelescope or "").strip(),
                instrument=(row.fitsSessionImager or "").strip(),
                exposure=(row.fitsSessionExposure or "").strip(),
                frame_count=count,
            )
        )
    # Ordered here rather than in SQL, because the sort key is the designated
    # name, not the session's own spelling of it. Two stable passes: newest
    # first (undated last), then grouped by target without disturbing that.
    sessions.sort(key=lambda s: s.date or "")
    sessions.reverse()
    sessions.sort(key=lambda s: s.target.lower())
    return sessions


def _light_frame_query(fitsFile, session_id: str):
    """Light frames of *session_id* that haven't been soft-deleted."""
    import peewee as pw

    return fitsFile.select().where(
        (fitsFile.fitsFileSession == session_id)
        # A NULL soft-delete flag is an undeleted frame, which `!= 1` would miss.
        & (fitsFile.fitsFileSoftDelete.is_null() | (fitsFile.fitsFileSoftDelete == 0))
        & (pw.fn.UPPER(pw.fn.TRIM(fitsFile.fitsFileType)).in_(list(_LIGHT_TYPES)))
    )


def session_frames(session_id: str, *, prefer_calibrated: bool = True) -> list[Path]:
    """The FITS files of *session_id* to run photometry over.

    Calibrated frames are used when the session has any, since photometry on
    uncalibrated frames carries the bias/dark/flat signature into the
    magnitudes; a session that was never calibrated falls back to its raw
    lights rather than returning nothing. Rows whose file is missing from disk
    are skipped — the catalog outlives a deleted file.
    """
    _, _, fitsFile = _models()

    rows = list(_light_frame_query(fitsFile, str(session_id)))
    if prefer_calibrated:
        calibrated = [r for r in rows if r.fitsFileCalibrated == 1]
        if calibrated:
            rows = calibrated

    paths: list[Path] = []
    for row in rows:
        name = (row.fitsFileName or "").strip()
        if not name:
            continue
        path = Path(name)
        if not path.exists():
            logger.warning("Catalogued frame is missing from disk, skipping: %s", path)
            continue
        paths.append(path)
    return sorted(paths)


def session_target_coordinates(target: str) -> tuple[float, float] | None:
    """*target*'s RA/Dec in degrees from the saved observation plan, if it's in one.

    Photometry needs the star's sky position to place the aperture; the plan
    store already holds it for anything planned through the planning panel, so
    the panel doesn't have to ask the user to retype coordinates. Returns
    ``None`` when the target isn't in the plan — the caller then falls back to
    a name lookup.
    """
    try:
        from vstarget.planning.database import PlanStore

        store = PlanStore()
        try:
            key = target.strip().lower()
            for saved in store.load_plan():
                if (saved.aavso.star_name or "").strip().lower() == key:
                    return float(saved.aavso.ra), float(saved.aavso.dec)
        finally:
            store.close()
    except Exception:  # a missing plan store must not block an analysis run
        logger.debug("Could not read target coordinates from the plan store", exc_info=True)
    return None
