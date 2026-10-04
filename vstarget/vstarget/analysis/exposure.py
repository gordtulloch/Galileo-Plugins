# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Exposure-time calculator (adapted from VSTarget analysis/exposure.py)."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)


class ExposureTimeCalculator:
    """Calibrate per-telescope/filter throughput and suggest exposure times (VST-AN-080)."""

    def __init__(
        self,
        telescope_aperture_mm: float = 200.0,
        focal_length_mm: float = 1000.0,
        pixel_size_um: float = 5.86,
        qe: float = 0.7,
    ) -> None:
        self.aperture_mm = telescope_aperture_mm
        self.focal_length_mm = focal_length_mm
        self.pixel_size_um = pixel_size_um
        self.qe = qe

    def compute(
        self,
        target_magnitude: float,
        filter_band: str = "V",
        target_snr: float = 100.0,
        sky_brightness_mag_arcsec2: float = 20.0,
    ) -> float:
        """Return the required exposure time in seconds (VST-AN-080)."""
        # Simple signal-to-noise estimate:
        # S ∝ flux × aperture_area × QE × t
        # N ≈ sqrt(S + sky × aperture_px × t)
        # SNR = S / N → solve for t.

        area_cm2 = math.pi * (self.aperture_mm / 20) ** 2  # convert mm to cm
        # Star flux (photons/cm²/s) approximated from magnitude
        zp_flux = 1e6  # photons/cm²/s at mag 0 (rough V-band approximation)
        star_flux = zp_flux * 10 ** (-0.4 * target_magnitude)
        signal_rate = star_flux * area_cm2 * self.qe

        if signal_rate <= 0:
            return 3600.0

        # Approximate t for target SNR: t = (SNR² × N²) / S² — simplified
        t = (target_snr ** 2) / signal_rate
        return max(1.0, min(t, 3600.0))


# ---------------------------------------------------------------------------
# Calibrated exposure suggestion (restored from the standalone VSTarget app)
# ---------------------------------------------------------------------------
#
# `ExposureTimeCalculator` above is the analytic calculator.  What follows is
# the empirical path the planning panel's "Suggest Exposures from Calibration"
# button uses: an exposure sized from a throughput model measured on the user's
# own plate-solved images of the field, per telescope and filter.
#
# The exposure is sized so the target reaches a requested S/N at its *faint*
# end (min_mag), subject to a hard cap: the brightest usable comparison star
# must never saturate, because ensemble photometry dies without comps.  If the
# target's *bright* end (max_mag) would saturate at the chosen exposure a
# warning is attached rather than shortening the exposure - the star may well
# not be in outburst.
#
# The S/N model is photon statistics in ADU assuming gain 1 e-/ADU and no read
# noise; both simplifications are absorbed into the empirical calibration
# closely enough for planning.

# Standard exposure steps (seconds)
EXPOSURE_LADDER: tuple[int, ...] = (10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 600)

DEFAULT_TARGET_SNR = 50.0
DEFAULT_SATURATION_ADU = 55_000.0   # conservative for 16-bit cameras
DEFAULT_COMP_BRIGHT_MAG = 11.0      # brightest comp star that must stay linear
DEFAULT_MAX_EXPOSURE = 300.0


@dataclass
class ExposureCalibration:
    """Empirical throughput model for one telescope + filter combination."""

    telescope: str
    filter_band: str
    zeropoint: float          # mag giving 1 ADU/s total aperture flux
    sky_rate: float           # ADU/s per pixel
    peak_frac: float          # peak-pixel fraction of aperture flux
    aperture_radius: float
    n_stars: int
    zp_rms: float             # scatter of per-star zeropoint estimates (mag)
    source_image: str = ""
    date: str = ""

    def flux_rate(self, mag: float) -> float:
        """Total aperture flux in ADU/s for a star of magnitude *mag*."""
        return 10.0 ** (0.4 * (self.zeropoint - mag))

    def snr(self, mag: float, exposure_s: float) -> float:
        """Estimated S/N for *mag* at *exposure_s* (gain 1, no read noise)."""
        signal = self.flux_rate(mag) * exposure_s
        npix = math.pi * self.aperture_radius ** 2
        noise = math.sqrt(signal + npix * self.sky_rate * exposure_s)
        return signal / noise if noise > 0 else 0.0

    def exposure_for_snr(self, mag: float, target_snr: float) -> float:
        """Exposure (s) needed for *mag* to reach *target_snr*."""
        rate = self.flux_rate(mag)
        npix = math.pi * self.aperture_radius ** 2
        return target_snr ** 2 * (rate + npix * self.sky_rate) / rate ** 2

    def saturation_exposure(
        self, mag: float, saturation_adu: float = DEFAULT_SATURATION_ADU
    ) -> float:
        """Longest exposure (s) before a star of *mag* saturates its peak pixel."""
        peak_rate = self.peak_frac * self.flux_rate(mag) + self.sky_rate
        return saturation_adu / peak_rate if peak_rate > 0 else math.inf

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ExposureCalibration:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def normalize_telescope(name: str) -> str:
    """Reduce a telescope description to a short code ('T5').

    Handles 'T05', 'T5 - NMS...', and header values like 'iTelescope 5' (the
    TELESCOP keyword iTelescope writes has no 'T' prefix).
    """
    name = name or ""
    m = re.search(r"\bT0*(\d+)\b", name, re.IGNORECASE)
    if not m:
        m = re.search(r"itelescope\D*0*(\d+)", name, re.IGNORECASE)
    return f"T{m.group(1)}" if m else name.strip() or "UNKNOWN"


@dataclass
class ExposureSuggestion:
    """Result of one exposure suggestion."""

    seconds: int
    expected_snr: float
    capped_by_comp: bool          # comp-star saturation limited the exposure
    target_may_saturate: bool     # bright end (max_mag) would saturate
    warnings: list[str] = field(default_factory=list)


def suggest_exposure(
    calib: ExposureCalibration,
    faint_mag: float,
    bright_mag: float | None = None,
    target_snr: float = DEFAULT_TARGET_SNR,
    comp_bright_mag: float = DEFAULT_COMP_BRIGHT_MAG,
    saturation_adu: float = DEFAULT_SATURATION_ADU,
    max_exposure: float = DEFAULT_MAX_EXPOSURE,
    ladder: tuple[int, ...] = EXPOSURE_LADDER,
) -> ExposureSuggestion:
    """Suggest an exposure sized for *faint_mag* at *target_snr*.

    Hard constraints, never exceeded: the brightest comparison star
    (*comp_bright_mag*) must not saturate, and *max_exposure* stands.
    Soft constraint, warning only: the target at *bright_mag* should not
    saturate - a variable may be observed anywhere between its two extremes.
    """
    warnings: list[str] = []

    t_snr = calib.exposure_for_snr(faint_mag, target_snr)
    t_comp = calib.saturation_exposure(comp_bright_mag, saturation_adu)
    cap = min(t_comp, max_exposure)

    capped = t_snr > cap
    if t_snr > t_comp and t_comp <= max_exposure:
        warnings.append(
            f"S/N {target_snr:.0f} at mag {faint_mag:.1f} needs {t_snr:.0f}s but "
            f"comparison stars (mag <= {comp_bright_mag:.1f}) saturate beyond "
            f"{t_comp:.0f}s - consider more exposures (Count) instead."
        )
    elif capped:
        warnings.append(
            f"S/N {target_snr:.0f} at mag {faint_mag:.1f} needs {t_snr:.0f}s; "
            f"clamped to the {max_exposure:.0f}s maximum - consider more "
            f"exposures (Count) instead."
        )

    # Snap to the ladder without ever exceeding the cap
    allowed = [s for s in ladder if s <= cap] or [max(1, int(cap))]
    at_least_snr = [s for s in allowed if s >= t_snr]
    seconds = min(at_least_snr) if at_least_snr else max(allowed)

    target_sat = False
    if bright_mag is not None:
        t_target_sat = calib.saturation_exposure(bright_mag, saturation_adu)
        if seconds > t_target_sat:
            target_sat = True
            warnings.append(
                f"Target at its bright end (mag {bright_mag:.1f}) saturates beyond "
                f"{t_target_sat:.0f}s - check recent brightness before using {seconds}s."
            )

    return ExposureSuggestion(
        seconds=seconds,
        expected_snr=calib.snr(faint_mag, seconds),
        capped_by_comp=capped,
        target_may_saturate=target_sat,
        warnings=warnings,
    )
