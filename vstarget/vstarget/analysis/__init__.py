# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Full variable-star analysis service (VST-AN-020 … VST-AN-100)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

# Re-exported so callers can do ``from vstarget.analysis import Foo``
from vstarget.analysis.exposure import ExposureTimeCalculator  # noqa: F401
from vstarget.analysis.finder_chart import FinderChartRenderer  # noqa: F401
from vstarget.analysis.photometry import (  # noqa: F401
    AperturePhotometryEngine,
    PhotometryResult,
    StandardFieldObservation,
)
from vstarget.planning.models import TransformationCoefficients  # noqa: F401

logger = logging.getLogger(__name__)


class VariableStarAnalysis:
    """Coordinates plate-solve, stack, photometry, transform, and report generation."""

    def __init__(
        self,
        aperture_radius: float | None = None,
        annulus_inner: float | None = None,
        annulus_outer: float | None = None,
    ) -> None:
        # The analysis panel's aperture field lands here. A sky annulus that
        # stayed at the 12-20 px default would sit inside an aperture the user
        # widened past 12 px, measuring the star as its own background, so an
        # unspecified annulus scales with the aperture in the same 1.5x/2.5x
        # proportion the engine's own defaults use.
        engine: dict[str, float] = {}
        if aperture_radius is not None:
            radius = float(aperture_radius)
            engine.update(
                aperture_radius=radius,
                annulus_inner=radius * 1.5,
                annulus_outer=radius * 2.5,
            )
        if annulus_inner is not None:
            engine["annulus_inner"] = float(annulus_inner)
        if annulus_outer is not None:
            engine["annulus_outer"] = float(annulus_outer)
        self._photometry = AperturePhotometryEngine(**engine)
        self._solver = None
        self._comparison_stars: list[dict] = []
        self._comparison_chart_id: str = "na"
        self._transformation_coefficients = None

    # --- Comparison stars (VST-EXT-010) ----------------------------------

    async def load_comparison_stars(self, target, filter_band: str = "V",
                                    field_of_view: float = 18.5) -> list[dict]:
        """Fetch *target*'s AAVSO VSP sequence and use it for ensemble photometry.

        Without this the engine has no comparison stars and
        :meth:`run_photometry` can only return the target's instrumental
        magnitude. The chart ID is kept alongside them because an AAVSO report
        has to cite the chart its comparison magnitudes came from; the report
        writer still emits a literal ``na`` there, so nothing reads it yet.
        """
        from vstarget.planning.vsp_client import AavsoVspClient

        chart = await AavsoVspClient().fetch_comparison_stars(
            target, filter_band=filter_band, field_of_view=field_of_view
        )
        self._comparison_stars = chart.stars
        self._comparison_chart_id = chart.chart_id
        return chart.stars

    # --- Plate solving (VST-AN-020) --------------------------------------

    async def solve_image(self, fits_path: Path | str):
        from vstarget.analysis.platesolve import solve_fits
        if self._solver is not None:
            return await self._solver.solve(fits_path)
        return await solve_fits(fits_path)

    # --- Stacking (VST-AN-030) -------------------------------------------

    async def stack(self, frames: list[Path], output_path: Path | str) -> Path:
        from vstarget.analysis.stack import stack_frames
        return await stack_frames(frames, output_path)

    # --- Photometry (VST-AN-040) -----------------------------------------

    async def run_photometry(
        self,
        image_path: Path | str,
        target: dict,
        filter_band: str = "V",
    ):
        from astropy.io import fits
        from astropy.wcs import WCS

        try:
            import numpy as np
            with fits.open(str(image_path)) as hdul:
                data = hdul[0].data.astype(np.float64)
                hdr = hdul[0].header
                wcs = WCS(hdr)

            # Convert target RA/Dec to pixel
            target_ra = target.get("ra", target.get("ra_deg", 0))
            target_dec = target.get("dec", target.get("dec_deg", 0))

            try:
                px, py = wcs.all_world2pix(target_ra, target_dec, 0)
            except Exception:
                px, py = data.shape[1] / 2, data.shape[0] / 2

            target_flux, target_err = self._photometry.measure_star(data, float(px), float(py))

            comp_fluxes, comp_mags = [], []
            for comp in self._comparison_stars:
                try:
                    cx, cy = wcs.all_world2pix(comp["ra"], comp["dec"], 0)
                except Exception:
                    logger.debug("Skipping comparison star with unusable WCS projection: %r", comp, exc_info=True)
                    continue
                f, _ = self._photometry.measure_star(data, float(cx), float(cy))
                comp_fluxes.append(f)
                comp_mags.append(float(comp[f"mag_{filter_band.lower()}"]))

            mag, uncertainty = self._photometry.differential_magnitude(
                target_flux, comp_fluxes, comp_mags
            )

            import datetime
            from astropy.time import Time
            jd = float(Time(hdr.get("DATE-OBS", "2026-01-01"), format="isot").jd)

            return PhotometryResult(
                target=target.get("name", ""),
                jd=jd,
                magnitude=mag,
                uncertainty=uncertainty,
                filter_band=filter_band,
                # The magnitude comes from the whole ensemble, not one star, so
                # the report names ENSEMBLE rather than the first comp star.
                comp_star="ENSEMBLE" if self._comparison_stars else "",
                chart_id=self._comparison_chart_id,
            )
        except Exception as exc:
            logger.exception("Photometry failed: %s", exc)
            return PhotometryResult(target=target.get("name", ""), jd=0, magnitude=99, uncertainty=99, filter_band=filter_band)

    # --- AAVSO report (VST-AN-050) ---------------------------------------

    def export_aavso_report(self, measurements: list, output_path: Path | str,
                            observer_code: str = "") -> None:
        from vstarget.analysis.report import save_aavso_report
        save_aavso_report(measurements, output_path, observer_code)

    # --- Transformation coefficients (VST-AN-060, VST-AN-070) ----------

    async def compute_transformation_coefficients(self, observations: list):
        from vstarget.analysis.transform_generator import compute_transformation_coefficients
        return compute_transformation_coefficients(observations)

    def apply_transformation(self, results):
        """Apply the stored coefficients to *results* — the same-epoch, same-target measurements
        across filters (one ``PhotometryResult`` per filter), not one filter in isolation."""
        from vstarget.analysis.transform_apply import apply_transformation
        return apply_transformation(results, self._transformation_coefficients)
