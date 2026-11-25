# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The Skyshot plate-solver backend (SKY-010, SKY-020).

Skyshot (github.com/ashtree74/skyshot-solver) is a blind solver: it needs no position, scale or
rotation hint, so the search hint Galileo passes is ignored. It is run as a program (``skyshot solve``)
and its result is read from the WCS file it writes, not from its JSON report, whose layout upstream
does not yet document; the JSON is only consulted for a reason when a solve fails.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import shutil
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from galileo.platesolve import SolverBackend, SolveResult, SolverParams

from skyshot_solver import engine
from skyshot_solver.settings import SkyshotSettings

logger = logging.getLogger(__name__)

KEY = "skyshot"
LABEL = "Skyshot (blind, local)"

# Skyshot's solve budget excludes process start-up, image decoding and mapping the index (upstream
# docs), so the process is given this much extra before it is killed.
_STARTUP_ALLOWANCE_S = 90.0
_EXE_NAMES = ("skyshot.exe", "skyshot") if sys.platform == "win32" else ("skyshot",)


def find_executable(data_root: Path | None = None) -> str:
    """The Skyshot program to run, looked for in fixed places and in this order: one placed by hand in
    the plugin's ``program`` folder under *data_root*, the one this plugin downloaded under *data_root*,
    then PATH and cargo's install folder (for a program installed another way). Empty when none is
    found."""
    if data_root is not None:
        for name in _EXE_NAMES:
            placed = engine.program_dir(data_root) / name
            if placed.is_file():
                return str(placed)
        downloaded = engine.installed_executable(data_root)
        if downloaded is not None:
            return str(downloaded)
    for name in _EXE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for name in _EXE_NAMES:
        candidate = Path.home() / ".cargo" / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return ""


@dataclass
class _Frame:
    width: int
    height: int


def _frame_size(path: Path) -> _Frame | None:
    """Pixel size of the first image in the FITS file *path*, from its headers only."""
    from astropy.io import fits
    try:
        with fits.open(path, memmap=True) as hdul:
            for hdu in hdul:
                h = hdu.header
                w = h.get("ZNAXIS1", h.get("NAXIS1", 0))
                ht = h.get("ZNAXIS2", h.get("NAXIS2", 0))
                if w and ht:
                    return _Frame(int(w), int(ht))
    except Exception:
        logger.debug("Could not read the frame size of %s", path, exc_info=True)
    return None


def _read_header(path: Path):
    """The header Skyshot wrote: a FITS file, or failing that plain 80-column cards."""
    from astropy.io import fits
    try:
        return fits.getheader(path)
    except Exception:  # noqa: BLE001 -- reported to the user as the failure reason
        text = path.read_text(encoding="latin-1")
        return fits.Header.fromstring(text, sep="\n") if "\n" in text.strip() else fits.Header.fromstring(text)


def result_from_wcs(wcs_path: Path, frame: _Frame | None) -> SolveResult:
    """Turn Skyshot's WCS into a :class:`SolveResult` giving the position of the *image centre*.

    Unlike astrometry.net's ``--crpix-center``, Skyshot's reference pixel is not promised to be the
    centre, so the centre is worked out through the WCS rather than read from ``CRVAL``."""
    try:
        from astropy.wcs import WCS
        header = _read_header(wcs_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")     # a header-only file has no image, which astropy warns about
            wcs = WCS(header, naxis=2)
        if not wcs.has_celestial:
            return SolveResult(success=False, failure_reason="Skyshot's solution has no sky coordinates")
        crpix1, crpix2 = (float(header.get("CRPIX1", 0.0)), float(header.get("CRPIX2", 0.0)))
        if frame is not None:
            cx, cy = (frame.width + 1) / 2.0, (frame.height + 1) / 2.0     # FITS 1-based pixel centre
        else:
            cx, cy = crpix1, crpix2             # frame size unknown: the reference point is the best we have
        ra, dec = (float(v) for v in wcs.wcs_pix2world([[cx, cy]], 1)[0])
        matrix = wcs.pixel_scale_matrix            # degrees per pixel, PC and CDELT combined
        cd11, cd12, cd21, cd22 = (float(matrix[0][0]), float(matrix[0][1]), float(matrix[1][0]), float(matrix[1][1]))
        near_centre = abs(cx - crpix1) < 1.0 and abs(cy - crpix2) < 1.0
        return SolveResult(
            success=True, ra_deg=ra % 360.0, dec_deg=dec,
            rotation_deg=math.degrees(math.atan2(cd12, cd22)),
            scale_arcsec_px=math.hypot(cd11, cd21) * 3600.0,
            # The CD matrix is only offered when it is referenced to the centre (callers assume so).
            cd=(cd11, cd12, cd21, cd22) if near_centre else None,
        )
    except Exception as exc:  # noqa: BLE001 -- reported to the user as the failure reason
        return SolveResult(success=False, failure_reason=f"Could not read Skyshot's solution: {exc}")


def _json_reason(stdout: bytes) -> str:
    """``solution.status`` from Skyshot's JSON report, when it can be read; else empty."""
    try:
        report: Any = json.loads(stdout.decode("utf-8", "replace"))
        status = report["solution"]["status"]
        return str(status) if status else ""
    except (ValueError, KeyError, TypeError):
        return ""


class SkyshotBackend(SolverBackend):
    """Runs ``skyshot solve`` on the frame against the installed index."""

    key = KEY
    label = LABEL
    offline = True

    def __init__(self, settings: SkyshotSettings) -> None:
        self._settings = settings

    # -- readiness ------------------------------------------------------------------------

    def executable(self) -> str:
        return find_executable(self._settings.data_root)

    def index(self) -> Path | None:
        return self._settings.resolved_index()

    def is_available(self) -> bool:
        return bool(self.executable()) and self.index() is not None

    def missing_message(self) -> str:
        if not self.executable():
            if engine.build_for(engine.current_target()) is not None:
                return ("Solving with Skyshot needs the skyshot program. Download it under "
                        "Options > Skyshot (Download Skyshot program).")
            return ("Solving with Skyshot needs the skyshot program. No prebuilt one is published for "
                    "this computer: build it from github.com/ashtree74/skyshot-solver "
                    "(cargo build --release --locked) and put it in "
                    f"{engine.program_dir(self._settings.data_root)} (Options > Skyshot has an Open folder button).")
        return "Solving with Skyshot needs its star index. Download it under Options > Skyshot."

    # -- solving --------------------------------------------------------------------------

    def command(self, fits_path: Path, wcs_path: Path, index: Path, exe: str) -> list[str]:
        cmd = [exe, "solve", str(fits_path), "--index", str(index), "--wcs", str(wcs_path),
               "--deadline-ms", str(int(self._settings.deadline_s * 1000))]
        if self._settings.mode != "auto":
            cmd += ["--mode", self._settings.mode]
        return cmd

    async def solve(self, fits_path: Path, hint: tuple[float, float] | None, params: SolverParams) -> SolveResult:
        exe, index = self.executable(), self.index()
        if not exe or index is None:
            return SolveResult(success=False, failure_reason=self.missing_message())
        from galileo.platesolve_solvers import _start_process
        with tempfile.TemporaryDirectory(prefix="galileo_skyshot_") as outdir:
            wcs_path = Path(outdir) / "solution.wcs"        # Skyshot refuses to overwrite, so it must not exist
            cmd = self.command(fits_path, wcs_path, index, exe)
            try:
                proc = await _start_process(cmd)
            except Exception as exc:  # noqa: BLE001 -- reported to the user as the failure reason
                return SolveResult(success=False, failure_reason=f"Could not start Skyshot: {exc}")
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._settings.deadline_s + _STARTUP_ALLOWANCE_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return SolveResult(success=False, failure_reason="Skyshot timed out")
            except asyncio.CancelledError:
                proc.kill()
                raise
            if proc.returncode == 0 and wcs_path.is_file():
                frame = await asyncio.to_thread(_frame_size, fits_path)
                return await asyncio.to_thread(result_from_wcs, wcs_path, frame)
            tail = stderr.decode("utf-8", "replace").strip()[-200:]
            if proc.returncode == 1:
                status = _json_reason(stdout)
                return SolveResult(success=False, failure_reason=f"No solution found{f' ({status})' if status else ''}")
            reason = tail or f"Skyshot exited with code {proc.returncode}"
            logger.info("skyshot failed (%s): %s", reason, " ".join(cmd))
            return SolveResult(success=False, failure_reason=reason)


async def inspect_index(exe: str, index: Path) -> tuple[bool, str]:
    """Run ``skyshot inspect`` on *index*; ``(ok, text)`` where text is its output or the error."""
    from galileo.platesolve_solvers import _start_process
    try:
        proc = await _start_process([exe, "inspect", str(index)])
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except Exception as exc:  # noqa: BLE001 -- reported to the user as the failure reason
        return False, str(exc)
    text = (stdout or b"").decode("utf-8", "replace").strip() or (stderr or b"").decode("utf-8", "replace").strip()
    return proc.returncode == 0, text[-600:]
