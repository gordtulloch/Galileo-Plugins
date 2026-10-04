# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Running a photometry batch off the UI thread (core ``NFR-PERF-020``).

Measuring a session is seconds of FITS reading and `photutils` work per frame,
so a run of a few nights freezes the panel for minutes if it happens in the
button's own slot. The work is split in two here:

* :func:`build_jobs` turns the panel's selected sessions into self-contained
  jobs. It touches the image library and the plan store, so it stays on the
  calling (UI) thread, where those connections live — the queries are small.
* :func:`run_jobs` does the measuring and nothing else: no database, no Qt,
  just frames in and :class:`PhotometryResult` objects out, reporting progress
  and honouring a cancel check between frames.

``PhotometryThread`` wraps :func:`run_jobs` in a ``QThread``, following the
same lazily-built-class pattern as ``FetchTargetsThread`` in
:mod:`vstarget.planning.aavso_client`, so this module imports without Qt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhotometryJob:
    """One session's worth of measuring: the frames, and what to report them as."""

    label: str
    target: dict
    filter_band: str
    frames: tuple[Path, ...]


@dataclass
class JobPlan:
    """What :func:`build_jobs` resolved, and what it couldn't."""

    jobs: list[PhotometryJob] = field(default_factory=list)
    sessions_without_frames: list[str] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return sum(len(job.frames) for job in self.jobs)


def build_jobs(
    sessions: Sequence,
    *,
    target_override: str = "",
    fallback_band: str = "V",
) -> JobPlan:
    """Resolve *sessions* (from :mod:`vstarget.analysis.library_sessions`) into jobs.

    One selection can span targets, so each session reports under its own
    catalogued target and filter. *target_override* renames a single session's
    target — the case where the catalog spells a star differently from AAVSO —
    and is ignored for a wider selection, where one typed name could not
    describe every session. *fallback_band* covers a session whose filter the
    catalog never recorded.

    Each job carries the target's RA/Dec where the saved observation plan knows
    it, so the aperture lands on the star rather than the middle of the frame.
    """
    from vstarget.analysis.library_sessions import (
        session_frames,
        session_target_coordinates,
    )

    plan = JobPlan()
    coordinates: dict[str, tuple[float, float] | None] = {}

    for session in sessions:
        frames = session_frames(session.session_id)
        if not frames:
            plan.sessions_without_frames.append(f"{session.target} - {session.label}")
            continue

        name = target_override.strip() if (target_override.strip() and len(sessions) == 1) else session.target
        target: dict = {"name": name}
        if name not in coordinates:
            coordinates[name] = session_target_coordinates(name)
        coords = coordinates[name]
        if coords is not None:
            target["ra"], target["dec"] = coords

        plan.jobs.append(
            PhotometryJob(
                label=f"{name} - {session.label}",
                target=target,
                filter_band=session.filter_band or fallback_band,
                frames=tuple(frames),
            )
        )
    return plan


async def run_jobs(
    jobs: Sequence[PhotometryJob],
    *,
    aperture_radius: float | None = None,
    analysis=None,
    progress: Callable[[int, int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list:
    """Measure every frame of every job, in job order, and return the results.

    *progress* is called with ``(frames_done, frames_total, label)`` before each
    frame. *is_cancelled* is checked between frames, so a cancelled run returns
    the results it already has rather than discarding them. *analysis* overrides
    the :class:`~vstarget.analysis.VariableStarAnalysis` instance, which is how
    a test measures without `photutils`.
    """
    from vstarget.analysis import VariableStarAnalysis

    svc = analysis if analysis is not None else VariableStarAnalysis(aperture_radius=aperture_radius)

    total = sum(len(job.frames) for job in jobs)
    measurements: list = []
    for job in jobs:
        for frame in job.frames:
            if is_cancelled is not None and is_cancelled():
                logger.info("Photometry run cancelled after %d of %d frames", len(measurements), total)
                return measurements
            if progress is not None:
                progress(len(measurements), total, job.label)
            measurements.append(
                await svc.run_photometry(frame, job.target, filter_band=job.filter_band)
            )
    if progress is not None:
        progress(len(measurements), total, "")
    return measurements


_PHOTOMETRY_THREAD_CLS = None


def _photometry_thread_cls():
    """Build (once) and return the ``PhotometryThread`` class.

    Defined inside a function so importing this module never requires PySide6.
    """
    global _PHOTOMETRY_THREAD_CLS
    if _PHOTOMETRY_THREAD_CLS is not None:
        return _PHOTOMETRY_THREAD_CLS

    from PySide6.QtCore import QThread, Signal

    class PhotometryThread(QThread):
        """Measures a batch of photometry jobs off the UI thread."""

        progress = Signal(int, int, str)  # frames done, frames total, current job
        finished_ok = Signal(list)        # success (or cancelled): the measurements so far
        error = Signal(str)               # failure: a human-readable message

        def __init__(
            self,
            jobs: Sequence[PhotometryJob],
            aperture_radius: float | None = None,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._jobs = list(jobs)
            self._aperture_radius = aperture_radius
            self._cancelled = False

        def cancel(self) -> None:
            """Ask the run to stop after the frame it is on."""
            self._cancelled = True

        @property
        def cancelled(self) -> bool:
            return self._cancelled

        def run(self) -> None:
            import asyncio

            try:
                # This thread owns no event loop, so the batch gets one of its
                # own for its lifetime rather than one per frame.
                measurements = asyncio.run(
                    run_jobs(
                        self._jobs,
                        aperture_radius=self._aperture_radius,
                        progress=lambda done, total, label: self.progress.emit(done, total, label),
                        is_cancelled=lambda: self._cancelled,
                    )
                )
                self.finished_ok.emit(measurements)
            except Exception as exc:
                logger.exception("Photometry run failed")
                self.error.emit(str(exc))

    _PHOTOMETRY_THREAD_CLS = PhotometryThread
    return PhotometryThread


def __getattr__(name: str):
    """Expose ``PhotometryThread`` without importing Qt at module level (PEP 562)."""
    if name == "PhotometryThread":
        return _photometry_thread_cls()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
