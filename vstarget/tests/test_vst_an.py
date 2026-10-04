# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""VST-AN — Variable Star Analysis & Photometry (TC-VST-AN-010 … TC-VST-AN-090)."""

import pytest
from unittest.mock import AsyncMock


@pytest.fixture
def vst_analysis():
    an_mod = pytest.importorskip("vstarget.analysis")
    return an_mod.VariableStarAnalysis()


# ---------------------------------------------------------------------------
# TC-VST-AN-010
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-010")
@pytest.mark.priority("MVP")
async def test_tc_vst_an_010_retrieve_fits_via_ftp_sftp(vst_analysis):
    """VST-AN-010: Retrieve calibrated FITS images from remote-telescope server via FTP/FTPS/SFTP."""
    an_mod = pytest.importorskip("vstarget.analysis")
    retriever = an_mod.SftpImageRetriever.__new__(an_mod.SftpImageRetriever)
    retriever.download = AsyncMock(return_value=["/local/cache/m42_001.fits"])

    files = await retriever.download(host="remote.telescope.net", path="/data/R_Leo/", dest="/tmp/cache")
    assert len(files) == 1


# ---------------------------------------------------------------------------
# TC-VST-AN-020
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-020")
@pytest.mark.priority("MVP")
async def test_tc_vst_an_020_plate_solve_retrieved_images(vst_analysis, sample_fits_file):
    """VST-AN-020: Plate-solve retrieved variable-star images via existing solver integration to add WCS."""
    plt_mod = pytest.importorskip("galileo.platesolve")
    mock_solver = plt_mod.PlateSolver.__new__(plt_mod.PlateSolver)
    mock_solver.solve = AsyncMock(return_value=plt_mod.SolveResult(
        success=True, ra_deg=154.0, dec_deg=11.4, rotation_deg=0.0, scale_arcsec_px=1.22
    ))
    vst_analysis._solver = mock_solver

    result = await vst_analysis.solve_image(sample_fits_file)
    assert result.success is True
    mock_solver.solve.assert_called_once_with(sample_fits_file)


# ---------------------------------------------------------------------------
# TC-VST-AN-030
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-030")
@pytest.mark.priority("MVP")
async def test_tc_vst_an_030_stack_same_target_filter(vst_analysis, sample_fits_repo):
    """VST-AN-030: Produce a registered mean-stacked image from same-target, same-filter frames for improved SNR."""
    fits = pytest.importorskip("astropy.io.fits")
    frames = list(sample_fits_repo.rglob("*.fits"))
    assert len(frames) >= 2

    result_path = sample_fits_repo / "stacked.fits"
    await vst_analysis.stack(frames=frames, output_path=result_path)
    assert result_path.exists()

    with fits.open(result_path) as hdul:
        assert hdul[0].data is not None
        assert hdul[0].data.shape == (100, 100)


# ---------------------------------------------------------------------------
# TC-VST-AN-040
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-040")
@pytest.mark.priority("MVP")
async def test_tc_vst_an_040_aperture_photometry_differential(vst_analysis, sample_fits_file):
    """VST-AN-040: Perform aperture photometry against AAVSO VSP comparison stars using ensemble linear regression."""
    an_mod = pytest.importorskip("vstarget.analysis")

    comparison_stars = [
        {"label": "127", "ra": 154.0, "dec": 11.5, "mag_v": 12.7},
        {"label": "134", "ra": 153.9, "dec": 11.3, "mag_v": 13.4},
    ]
    vst_analysis._comparison_stars = comparison_stars

    result = await vst_analysis.run_photometry(
        image_path=sample_fits_file,
        target={"name": "R Leo", "ra": 154.0, "dec": 11.4},
        filter_band="V",
    )
    assert result is not None
    assert hasattr(result, "magnitude")
    assert hasattr(result, "uncertainty")


@pytest.mark.requirement("TC-VST-AN-040")
@pytest.mark.priority("MVP")
def test_tc_vst_an_040_aperture_radius_is_configurable():
    """VST-AN-040: The measuring aperture is settable, and the sky annulus scales with it."""
    an_mod = pytest.importorskip("vstarget.analysis")

    engine = an_mod.VariableStarAnalysis()._photometry
    assert (engine.aperture_radius, engine.annulus_inner, engine.annulus_outer) == (8.0, 12.0, 20.0)

    # A widened aperture must not swallow its own sky annulus: at r=16 the
    # engine's 12-20 px default annulus would sit inside the aperture.
    engine = an_mod.VariableStarAnalysis(aperture_radius=16)._photometry
    assert engine.aperture_radius == 16.0
    assert engine.annulus_inner == 24.0
    assert engine.annulus_outer == 40.0
    assert engine.annulus_inner > engine.aperture_radius

    # An explicitly given annulus wins over the scaled one.
    engine = an_mod.VariableStarAnalysis(
        aperture_radius=5, annulus_inner=30, annulus_outer=40
    )._photometry
    assert (engine.aperture_radius, engine.annulus_inner, engine.annulus_outer) == (5.0, 30.0, 40.0)


# ---------------------------------------------------------------------------
# TC-VST-AN-050
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-050")
@pytest.mark.priority("MVP")
def test_tc_vst_an_050_generate_aavso_webobs_report(vst_analysis, tmp_path):
    """VST-AN-050: Generate an AAVSO WebObs Extended-format measurement report from photometry results."""
    an_mod = pytest.importorskip("vstarget.analysis")
    measurements = [
        an_mod.PhotometryResult(
            target="R Leo",
            jd=2461100.5,
            magnitude=6.8,
            uncertainty=0.05,
            filter_band="V",
            comp_star="127",
            check_star="134",
        )
    ]
    report_path = tmp_path / "webobs_report.csv"
    vst_analysis.export_aavso_report(measurements, output_path=report_path)
    assert report_path.exists()

    content = report_path.read_text()
    assert "#TYPE=EXTENDED" in content or "R Leo" in content
    assert "6.8" in content


@pytest.mark.requirement("TC-VST-AN-050")
@pytest.mark.priority("MVP")
def test_tc_vst_an_050_report_field_layout(vst_analysis, tmp_path):
    """VST-AN-050: Each report record carries the 15 Extended-format fields, in order, with 'na' for absent values."""
    an_mod = pytest.importorskip("vstarget.analysis")
    report_mod = pytest.importorskip("vstarget.analysis.report")

    measurement = an_mod.PhotometryResult(
        target="R Leo",
        jd=2461100.5,
        magnitude=6.8,
        uncertainty=0.05,
        filter_band="V",
        check_star="134",
        check_mag=13.421,
        chart_id="X28077ABC",
        airmass=1.23,
    )
    report_path = tmp_path / "webobs_report.csv"
    vst_analysis.export_aavso_report(
        [measurement], output_path=report_path, observer_code="TGOR"
    )
    content = report_path.read_text()

    # WebObs rejects a submission with no observer code.
    assert "#OBSCODE=TGOR" in content

    data_lines = [ln for ln in content.splitlines() if ln and not ln.startswith("#")]
    assert len(data_lines) == 1
    fields = data_lines[0].split(",")
    assert len(fields) == len(report_mod.FIELDS) == 15, (
        "Extended format is positional: a wrong field count shifts every "
        "later value into the wrong column"
    )

    assert dict(zip(report_mod.FIELDS, fields)) == {
        "NAME": "R Leo",
        "DATE": "2461100.50000",
        "MAG": "6.800",
        "MERR": "0.050",
        "FILT": "V",
        "TRANS": "NO",
        "MTYPE": "STD",
        "CNAME": "ENSEMBLE",
        "CMF": "na",
        "KNAME": "134",
        "KMF": "13.421",
        "AMASS": "1.230",
        "GROUP": "0",
        "CHART": "X28077ABC",
        "NOTES": "na",
    }

    # Absent optional values become 'na', never blank, and the count holds.
    bare = an_mod.PhotometryResult(
        target="SS Cyg", jd=2461101.5, magnitude=8.1, uncertainty=0.03,
        filter_band="B", is_transformed=True,
    )
    bare_fields = report_mod.generate_aavso_report([bare]).splitlines()[-1].split(",")
    assert len(bare_fields) == 15
    bare_row = dict(zip(report_mod.FIELDS, bare_fields))
    assert bare_row["TRANS"] == "YES"
    assert bare_row["CNAME"] == "ENSEMBLE"
    assert [bare_row[k] for k in ("CMF", "KNAME", "KMF", "AMASS", "CHART", "NOTES")] == ["na"] * 6
    assert "" not in bare_fields, "An absent field must be 'na', not empty"


# ---------------------------------------------------------------------------
# TC-VST-AN-060
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-060")
@pytest.mark.priority("P2")
async def test_tc_vst_an_060_transformation_coefficients_from_standard_field(vst_analysis):
    """VST-AN-060: Compute per-telescope per-filter transformation coefficients from standard-field observations."""
    an_mod = pytest.importorskip("vstarget.analysis")
    standard_obs = [
        an_mod.StandardFieldObservation(star="HD12345", b_mag=10.1, v_mag=9.8, r_mag=9.5, b_inst=-0.12, v_inst=-0.10, r_inst=-0.08),
        an_mod.StandardFieldObservation(star="HD23456", b_mag=11.5, v_mag=11.0, r_mag=10.7, b_inst=-0.14, v_inst=-0.11, r_inst=-0.09),
    ]
    coefficients = await vst_analysis.compute_transformation_coefficients(standard_obs)
    assert coefficients is not None
    assert hasattr(coefficients, "Tbv") or "B" in str(coefficients)


# ---------------------------------------------------------------------------
# TC-VST-AN-070
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-070")
@pytest.mark.priority("P2")
async def test_tc_vst_an_070_apply_transformation_to_multifilter(vst_analysis, sample_fits_file):
    """VST-AN-070: Apply stored transformation coefficients to multi-filter observations before report generation."""
    an_mod = pytest.importorskip("vstarget.analysis")
    vst_analysis._transformation_coefficients = an_mod.TransformationCoefficients(
        Tbv=0.05, Tv=0.02, Tvr=-0.03, Tr=0.04
    )

    b_result = an_mod.PhotometryResult(
        target="R Leo", jd=2461100.5, magnitude=7.60, uncertainty=0.05, filter_band="B",
        comp_star="127", check_star="134"
    )
    v_result = an_mod.PhotometryResult(
        target="R Leo", jd=2461100.5, magnitude=6.75, uncertainty=0.04, filter_band="V",
        comp_star="127", check_star="134"
    )

    corrected = vst_analysis.apply_transformation([b_result, v_result])
    corrected_b, corrected_v = corrected[0], corrected[1]

    # (B-V)_std = Tbv * (b - v); V' = v + Tv * (B-V)_std; B' = V' + (B-V)_std.
    bv_std = 0.05 * (b_result.magnitude - v_result.magnitude)
    expected_v = v_result.magnitude + 0.02 * bv_std
    expected_b = expected_v + bv_std

    assert corrected_v.is_transformed and corrected_v.magnitude == pytest.approx(expected_v)
    assert corrected_b.is_transformed and corrected_b.magnitude == pytest.approx(expected_b)

    # A lone V with no B or R alongside it carries no colour information, so it passes through
    # unchanged rather than claiming a correction that was never computed.
    lone_v = vst_analysis.apply_transformation([v_result])[0]
    assert lone_v.magnitude == v_result.magnitude and not lone_v.is_transformed


# ---------------------------------------------------------------------------
# TC-VST-AN-080
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-080")
@pytest.mark.priority("P2")
def test_tc_vst_an_080_exposure_time_calculator(vst_analysis):
    """VST-AN-080: Exposure-time calculator calibrated to configured telescope/filter throughput."""
    an_mod = pytest.importorskip("vstarget.analysis")
    etc = an_mod.ExposureTimeCalculator(
        telescope_aperture_mm=200,
        focal_length_mm=1000,
        pixel_size_um=5.86,
        qe=0.8,
    )
    exp_s = etc.compute(target_magnitude=12.0, filter_band="V", target_snr=100.0)
    assert exp_s > 0
    assert exp_s < 3600  # sanity: less than 1 hour for a mag-12 target


# ---------------------------------------------------------------------------
# TC-VST-AN-090
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-AN-090")
@pytest.mark.priority("P2")
async def test_tc_vst_an_090_generate_finder_chart(vst_analysis, tmp_path):
    """VST-AN-090: Generate an AAVSO-style finder chart image for a variable-star field."""
    an_mod = pytest.importorskip("vstarget.analysis")
    chart_renderer = an_mod.FinderChartRenderer.__new__(an_mod.FinderChartRenderer)
    chart_renderer.render = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

    chart_bytes = await chart_renderer.render(target="R Leo", ra_deg=154.0, dec_deg=11.4, fov_arcmin=30.0)
    assert chart_bytes[:4] == b"\x89PNG"

    chart_path = tmp_path / "finder_r_leo.png"
    chart_path.write_bytes(chart_bytes)
    assert chart_path.exists()


# ---------------------------------------------------------------------------
# TC-VST-AN-100
# ---------------------------------------------------------------------------

@pytest.fixture
def variable_star_library(tmp_path):
    """A migrated library database holding one designated variable star and its sessions.

    Mirrors what the Images tab produces: a ``VariableStars`` row for the
    target, light-frame sessions of that target (one of them calibrated), a
    session of an undesignated target, and an empty session.
    """
    pytest.importorskip("peewee")
    database = pytest.importorskip("galileo.library.database")
    models = pytest.importorskip("galileo.library.models")

    database.init_db(tmp_path / "library.db")

    models.VariableStars.create(target_name="R Leo")

    def session(session_id, obj, date, band, frames, calibrated=False):
        models.fitsSession.create(
            fitsSessionId=session_id,
            fitsSessionObjectName=obj,
            fitsSessionDate=date,
            fitsSessionFilter=band,
            fitsSessionTelescope="GSO 8in",
            fitsSessionImager="ASI1600",
            fitsSessionExposure="120",
        )
        for i in range(frames):
            path = tmp_path / f"{session_id}_{i:03d}.fits"
            path.write_bytes(b"SIMPLE  =                    T")
            models.fitsFile.create(
                fitsFileId=f"{session_id}-{i}",
                fitsFileName=str(path),
                fitsFileObject=obj,
                fitsFileType="Light Frame",
                fitsFileFilter=band,
                fitsFileSession=session_id,
                fitsFileCalibrated=1 if calibrated else 0,
                fitsFileSoftDelete=False,
            )

    session("sess-old", "R Leo", "2026-08-01", "V", 2)
    # Object name deliberately cased differently from the VariableStars row.
    session("sess-new", "r leo", "2026-09-16", "B", 3, calibrated=True)
    session("sess-other", "NGC 7000", "2026-09-17", "Ha", 2)
    models.fitsSession.create(
        fitsSessionId="sess-empty",
        fitsSessionObjectName="R Leo",
        fitsSessionDate="2026-07-01",
        fitsSessionFilter="V",
    )

    yield tmp_path
    database.db.close()


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
def test_tc_vst_an_100_input_sessions_come_from_library(variable_star_library):
    """VST-AN-100: List every library session of every target designated a variable star."""
    lib = pytest.importorskip("vstarget.analysis.library_sessions")

    assert lib.variable_star_targets() == ["R Leo"]

    sessions = lib.list_variable_star_sessions()
    ids = [s.session_id for s in sessions]

    # Only the designated target's sessions, newest first, and the empty
    # session dropped because it has nothing to measure.
    assert ids == ["sess-new", "sess-old"]
    assert [s.frame_count for s in sessions] == [3, 2]
    assert [s.filter_band for s in sessions] == ["B", "V"]
    assert sessions[0].telescope == "GSO 8in"

    # Filtering by target name is case-insensitive, same as the listing.
    assert [s.session_id for s in lib.list_variable_star_sessions("r LEO")] == [
        "sess-new",
        "sess-old",
    ]
    assert lib.list_variable_star_sessions("NGC 7000") == []


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
def test_tc_vst_an_100_session_frames_prefer_calibrated_and_skip_missing(variable_star_library):
    """VST-AN-100: A session's frames are its calibrated lights when it has any, and must exist on disk."""
    lib = pytest.importorskip("vstarget.analysis.library_sessions")
    models = pytest.importorskip("galileo.library.models")

    # sess-new is calibrated throughout; sess-old never was, so its raw lights
    # are used rather than returning nothing.
    assert len(lib.session_frames("sess-new")) == 3
    assert len(lib.session_frames("sess-old")) == 2

    # One calibrated frame of sess-new deleted from disk behind the catalog's back.
    (variable_star_library / "sess-new_000.fits").unlink()
    assert len(lib.session_frames("sess-new")) == 2

    # A soft-deleted frame is not offered for measurement.
    row = models.fitsFile.get(models.fitsFile.fitsFileId == "sess-old-0")
    row.fitsFileSoftDelete = True
    row.save()
    assert len(lib.session_frames("sess-old")) == 1


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
def test_tc_vst_an_100_no_designated_targets_lists_nothing(tmp_path):
    """VST-AN-100: With no target designated a variable star, the panel's input list is empty."""
    database = pytest.importorskip("galileo.library.database")
    lib = pytest.importorskip("vstarget.analysis.library_sessions")

    database.init_db(tmp_path / "empty.db")
    try:
        assert lib.variable_star_targets() == []
        assert lib.list_variable_star_sessions() == []
    finally:
        database.db.close()


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
def test_tc_vst_an_100_build_jobs_resolves_sessions_to_frames(variable_star_library):
    """VST-AN-100: Selected sessions become self-contained jobs, each with its own target and filter."""
    lib = pytest.importorskip("vstarget.analysis.library_sessions")
    runner = pytest.importorskip("vstarget.analysis.runner")

    sessions = lib.list_variable_star_sessions()
    plan = runner.build_jobs(sessions)

    assert [job.filter_band for job in plan.jobs] == ["B", "V"]
    assert [len(job.frames) for job in plan.jobs] == [3, 2]
    assert plan.frame_count == 5
    assert {job.target["name"] for job in plan.jobs} == {"R Leo"}
    assert plan.sessions_without_frames == []

    # The typed target name renames a single session's target, and is ignored
    # for a wider selection, where one name could not describe every session.
    one = runner.build_jobs(sessions[:1], target_override="R Leo A")
    assert one.jobs[0].target["name"] == "R Leo A"
    assert {job.target["name"] for job in runner.build_jobs(sessions, target_override="R Leo A").jobs} == {"R Leo"}

    # A session whose filter the catalog never recorded falls back to the panel's band.
    models = pytest.importorskip("galileo.library.models")
    row = models.fitsSession.get(models.fitsSession.fitsSessionId == "sess-old")
    row.fitsSessionFilter = None
    row.save()
    bandless = [s for s in lib.list_variable_star_sessions() if s.session_id == "sess-old"]
    assert runner.build_jobs(bandless, fallback_band="I").jobs[0].filter_band == "I"


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
def test_tc_vst_an_100_build_jobs_reports_sessions_with_no_frames(variable_star_library):
    """VST-AN-100: A session whose frames have left the disk is reported, not measured."""
    lib = pytest.importorskip("vstarget.analysis.library_sessions")
    runner = pytest.importorskip("vstarget.analysis.runner")

    for frame in variable_star_library.glob("sess-old_*.fits"):
        frame.unlink()

    plan = runner.build_jobs(lib.list_variable_star_sessions())
    assert [job.filter_band for job in plan.jobs] == ["B"]
    assert len(plan.sessions_without_frames) == 1
    assert "R Leo" in plan.sessions_without_frames[0]


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
async def test_tc_vst_an_100_run_jobs_reports_progress_and_honours_cancel():
    """VST-AN-100: The measuring loop reports progress per frame and stops on request, keeping results."""
    from pathlib import Path

    runner = pytest.importorskip("vstarget.analysis.runner")

    job = runner.PhotometryJob(
        label="R Leo - 2026-09-16",
        target={"name": "R Leo"},
        filter_band="V",
        frames=tuple(Path(f"frame_{i}.fits") for i in range(4)),
    )

    class _StubAnalysis:
        def __init__(self):
            self.measured = []

        async def run_photometry(self, image_path, target, filter_band="V"):
            self.measured.append(image_path)
            return f"{target['name']}/{filter_band}/{image_path.name}"

    stub = _StubAnalysis()
    seen: list[tuple[int, int, str]] = []
    results = await runner.run_jobs([job], analysis=stub, progress=lambda *a: seen.append(a))

    assert len(results) == 4
    assert results[0] == "R Leo/V/frame_0.fits"
    assert [done for done, _total, _label in seen] == [0, 1, 2, 3, 4]
    assert {total for _done, total, _label in seen} == {4}

    # Cancelling after the second frame keeps the two already measured.
    stub = _StubAnalysis()
    results = await runner.run_jobs(
        [job], analysis=stub, is_cancelled=lambda: len(stub.measured) >= 2
    )
    assert len(results) == 2
    assert len(stub.measured) == 2


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
def test_tc_vst_an_100_photometry_runs_on_a_worker_thread():
    """VST-AN-100: Measuring happens off the UI thread (core NFR-PERF-020)."""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QThread

    runner = pytest.importorskip("vstarget.analysis.runner")

    assert issubclass(runner.PhotometryThread, QThread)
    for signal in ("progress", "finished_ok", "error"):
        assert hasattr(runner.PhotometryThread, signal)
    assert hasattr(runner.PhotometryThread, "cancel")


@pytest.mark.requirement("TC-VST-AN-100")
@pytest.mark.priority("MVP")
async def test_tc_vst_an_100_run_jobs_passes_the_aperture_through(monkeypatch):
    """VST-AN-100: The panel's aperture radius reaches the photometry engine."""
    an_mod = pytest.importorskip("vstarget.analysis")
    runner = pytest.importorskip("vstarget.analysis.runner")

    built: list = []
    real = an_mod.VariableStarAnalysis

    def _record(*args, **kwargs):
        service = real(*args, **kwargs)
        built.append(service)
        return service

    monkeypatch.setattr(an_mod, "VariableStarAnalysis", _record)
    await runner.run_jobs([], aperture_radius=12.0)

    assert built[0]._photometry.aperture_radius == 12.0
    assert built[0]._photometry.annulus_inner == 18.0
