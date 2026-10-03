# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Shared fixtures for the vstarget plugin test suite."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "requirement(id): RTM test-case ID this test implements")
    config.addinivalue_line("markers", "priority(level): MVP, P2, or P3")


@pytest.fixture
def sample_fits_file(tmp_path):
    """Writes a minimal, valid FITS file and returns its Path."""
    np = pytest.importorskip("numpy")
    fits = pytest.importorskip("astropy.io.fits")
    data = np.zeros((100, 100), dtype=np.float32)
    hdr = fits.Header()
    hdr["OBJECT"] = "R Leo"
    hdr["EXPTIME"] = 120.0
    hdr["FILTER"] = "V"
    hdr["DATE-OBS"] = "2026-09-16T22:00:00.000"
    hdr["CRVAL1"] = 154.0
    hdr["CRVAL2"] = 11.4
    hdr["CRPIX1"] = 50.0
    hdr["CRPIX2"] = 50.0
    hdr["CD1_1"] = -0.000339
    hdr["CD1_2"] = 0.0
    hdr["CD2_1"] = 0.0
    hdr["CD2_2"] = 0.000339
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    p = tmp_path / "rleo_v_001.fits"
    fits.PrimaryHDU(data, header=hdr).writeto(p)
    return p


@pytest.fixture
def sample_fits_repo(tmp_path, sample_fits_file):
    """Creates a small FITS repository tree with 3 FITS frames."""
    np = pytest.importorskip("numpy")
    fits = pytest.importorskip("astropy.io.fits")
    repo = tmp_path / "repo"
    session = repo / "R_Leo" / "2026-09-16" / "V"
    session.mkdir(parents=True)
    for i in range(3):
        data = np.full((100, 100), i * 1000, dtype=np.float32)
        hdr = fits.Header()
        hdr["OBJECT"] = "R Leo"
        hdr["FILTER"] = "V"
        hdr["EXPTIME"] = 120.0
        hdr["DATE-OBS"] = "2026-09-16T22:00:00"
        fits.PrimaryHDU(data, header=hdr).writeto(session / f"rleo_v_{i + 1:03d}.fits")
    return repo
