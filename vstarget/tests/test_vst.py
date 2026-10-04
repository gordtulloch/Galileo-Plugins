# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""VST / VST-EXT — Variable Star Target Planning (TC-VST-010 … TC-VST-090, TC-VST-EXT-010).

VST-EXT-010 is this plugin's own external-interface requirement (docs/plugins/vstarget/SRS.md
Section 1.3's plugin-local `EXT` sub-prefix), relocated here from core's former EXT-100.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def vst_planner():
    vst_mod = pytest.importorskip("vstarget.planning")
    return vst_mod.VariableStarPlanner()


# ---------------------------------------------------------------------------
# TC-VST-EXT-010
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-EXT-010")
@pytest.mark.priority("MVP")
def test_tc_vst_ext_010_aavso_target_tool_and_vsp_apis():
    """VST-EXT-010: Retrieve variable-star target data from the AAVSO Target Tool API and comparison-star data from the AAVSO VSP API."""
    vst = pytest.importorskip("vstarget.planning")

    target_client = vst.AavsoTargetToolClient.__new__(vst.AavsoTargetToolClient)
    assert hasattr(target_client, "fetch_targets"), "Must expose fetch_targets(section)"

    vsp_client = vst.AavsoVspClient.__new__(vst.AavsoVspClient)
    assert hasattr(vsp_client, "fetch_comparison_stars"), "Must expose fetch_comparison_stars(target)"


@pytest.mark.requirement("TC-VST-EXT-010")
@pytest.mark.priority("MVP")
def test_tc_vst_ext_010_vsp_chart_parsed_for_photometry():
    """VST-EXT-010: VSP comparison-star data is parsed into the shape ensemble photometry consumes."""
    vsp = pytest.importorskip("vstarget.planning.vsp_client")

    # One usable V star, one with no V magnitude, one with unusable coordinates.
    payload = {
        "chartid": "X28077ABC",
        "photometry": [
            {
                "auid": "000-BCT-123", "label": "118",
                "ra": "08:53:44.67", "dec": "+57:48:40.6",
                "bands": [
                    {"band": "V", "mag": 11.8, "error": 0.03},
                    {"band": "B", "mag": 12.4, "error": 0.05},
                ],
            },
            {
                "auid": "000-BCT-456", "label": "125",
                "ra": "08:54:01.20", "dec": "+57:50:12.0",
                "bands": [{"band": "B", "mag": 12.5, "error": 0.04}],
            },
            {
                "auid": "000-BCT-789", "label": "131",
                "ra": "", "dec": "",
                "bands": [{"band": "V", "mag": 13.1, "error": 0.04}],
            },
        ],
    }

    chart = vsp.parse_chart(payload, filter_band="V")
    assert chart.chart_id == "X28077ABC"
    assert len(chart.stars) == 1, "Stars lacking the band, or with bad coordinates, are dropped"

    star = chart.stars[0]
    assert star["auid"] == "000-BCT-123"
    assert star["label"] == "118"
    # run_photometry indexes comparison stars by ra/dec in degrees and mag_<band>.
    assert star["mag_v"] == 11.8
    assert abs(star["ra"] - 133.436125) < 1e-6, "RA is sexagesimal hours -> degrees"
    assert abs(star["dec"] - 57.811278) < 1e-6
    assert star["error"] == 0.03

    # The B ensemble sees the other star instead.
    b_chart = vsp.parse_chart(payload, filter_band="B")
    assert {s["label"] for s in b_chart.stars} == {"118", "125"}

    # A target's coordinates are read from whichever representation is handed over.
    models = pytest.importorskip("vstarget.planning.models")
    assert vsp.target_coords({"ra": 154.0, "dec": 11.4}) == (154.0, 11.4)
    assert vsp.target_coords({"ra_deg": 154.0, "dec_deg": 11.4}) == (154.0, 11.4)
    assert vsp.target_coords(
        models.AAVSOTarget(star_name="R Leo", ra=154.0, dec=11.4)
    ) == (154.0, 11.4)


@pytest.mark.requirement("TC-VST-EXT-010")
@pytest.mark.priority("MVP")
async def test_tc_vst_ext_010_vsp_failure_yields_empty_chart(monkeypatch):
    """VST-EXT-010: A failed VSP lookup degrades to an empty chart, not an exception."""
    vsp = pytest.importorskip("vstarget.planning.vsp_client")

    client = vsp.AavsoVspClient()
    def boom(*_args, **_kwargs):
        raise RuntimeError("VSP unreachable")
    monkeypatch.setattr(client, "_get_chart", boom)

    chart = await client.fetch_comparison_stars({"ra": 154.0, "dec": 11.4})
    assert chart.stars == []
    assert chart.chart_id == "na"


# ---------------------------------------------------------------------------
# TC-VST-010
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-010")
@pytest.mark.priority("MVP")
async def test_tc_vst_010_sync_from_aavso_target_tool(vst_planner):
    """VST-010: Sync variable-star targets from AAVSO Target Tool API; filterable by observing section."""
    vst_mod = pytest.importorskip("vstarget.planning")
    vst_planner._aavso_client = vst_mod.AavsoTargetToolClient.__new__(vst_mod.AavsoTargetToolClient)
    vst_planner._aavso_client.fetch_targets = AsyncMock(return_value=[
        {"name": "Z UMa", "section": "LPV", "ra": 152.6, "dec": 57.9},
        {"name": "SS Cyg", "section": "Cataclysmic", "ra": 325.7, "dec": 43.6},
    ])

    await vst_planner.sync_from_aavso(section="LPV")
    targets = vst_planner.get_targets(section="LPV")
    assert any(t.name == "Z UMa" for t in targets)
    assert not any(t.name == "SS Cyg" for t in targets)


# ---------------------------------------------------------------------------
# TC-VST-020
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-020")
@pytest.mark.priority("MVP")
def test_tc_vst_020_sortable_searchable_target_list(vst_planner):
    """VST-020: Sortable, searchable variable-star target list with priority indication and solar-conjunction warnings."""
    r_leo = MagicMock(priority=2, solar_conjunction=False)
    r_leo.name = "R Leo"
    chi_cyg = MagicMock(priority=1, solar_conjunction=True)
    chi_cyg.name = "Chi Cyg"
    mira = MagicMock(priority=1, solar_conjunction=False)
    mira.name = "Mira"
    vst_planner._targets = [r_leo, chi_cyg, mira]

    sorted_targets = vst_planner.sorted_targets(key="priority")
    assert sorted_targets[0].priority <= sorted_targets[-1].priority

    conjunctions = [t for t in vst_planner._targets if t.solar_conjunction]
    assert any(t.name == "Chi Cyg" for t in conjunctions)


# ---------------------------------------------------------------------------
# TC-VST-030
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-030")
@pytest.mark.priority("MVP")
def test_tc_vst_030_filter_to_observable_tonight(vst_planner):
    """VST-030: Filter variable-star target list to targets observable from configured location tonight."""
    sky_mod = pytest.importorskip("galileo.planning.sky_atlas")
    loc = sky_mod.ObservingLocation(name="Home", latitude=51.5, longitude=-1.0, elevation_m=100, timezone="UTC")
    vst_planner.set_location(loc)

    vst_planner._targets = [
        MagicMock(name="R Leo", ra_deg=154.0, dec_deg=11.4),
        MagicMock(name="Mira", ra_deg=34.8, dec_deg=-2.9),
    ]

    observable = vst_planner.get_observable_tonight(date="2026-09-16")
    assert isinstance(observable, list)
    for t in observable:
        assert hasattr(t, "name")


# ---------------------------------------------------------------------------
# TC-VST-040
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-040")
@pytest.mark.priority("P2")
def test_tc_vst_040_import_target_list_from_file(vst_planner, tmp_path):
    """VST-040: Support manual import of a variable-star target list from a delimited text file."""
    csv_file = tmp_path / "targets.csv"
    csv_file.write_text("name,ra,dec\nR Leo,154.0,11.4\nMira,34.8,-2.9\n")

    vst_planner.import_from_file(csv_file)
    targets = vst_planner.get_targets()
    assert any(t.name == "R Leo" for t in targets)
    assert any(t.name == "Mira" for t in targets)


# ---------------------------------------------------------------------------
# TC-VST-050
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-050")
@pytest.mark.priority("MVP")
def test_tc_vst_050_observation_plan_editor(vst_planner):
    """VST-050: Observation-plan editor with per-target filter, exposure count, interval, and binning."""
    vst_mod = pytest.importorskip("vstarget.planning")
    plan = vst_mod.ObservationPlan(target_name="R Leo")
    plan.add_filter_config(filter_name="V", exposure_s=60.0, count=3, interval_s=120.0, binning=1)
    plan.add_filter_config(filter_name="B", exposure_s=90.0, count=3, interval_s=120.0, binning=1)

    assert len(plan.filter_configs) == 2
    assert plan.filter_configs[0].filter_name == "V"
    assert plan.filter_configs[1].exposure_s == 90.0


# ---------------------------------------------------------------------------
# TC-VST-060
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-060")
@pytest.mark.priority("MVP")
def test_tc_vst_060_generate_acp_observing_script(vst_planner, tmp_path):
    """VST-060: Generate an ACP-compatible observing script with targets ordered by right ascension."""
    vst_mod = pytest.importorskip("vstarget.planning")
    plans = [
        vst_mod.ObservationPlan(target_name="Mira", ra_deg=34.8),
        vst_mod.ObservationPlan(target_name="R Leo", ra_deg=154.0),
    ]

    script_path = tmp_path / "obs_plan.acp"
    vst_planner.export_acp_script(plans, output_path=script_path)
    assert script_path.exists()

    content = script_path.read_text()
    # ACP scripts start with #TARGET
    assert "#TARGET" in content or "Mira" in content
    mira_pos = content.find("Mira")
    rleo_pos = content.find("R Leo")
    assert mira_pos < rleo_pos, "Targets must be ordered by RA (Mira RA < R Leo RA)"


# ---------------------------------------------------------------------------
# TC-VST-070
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-070")
@pytest.mark.priority("MVP")
def test_tc_vst_070_persist_plans_across_restarts(vst_planner, tmp_path):
    """VST-070: Persist observation plans across application restarts."""
    vst_mod = pytest.importorskip("vstarget.planning")
    plan = vst_mod.ObservationPlan(target_name="R Leo", ra_deg=154.0)
    vst_planner.set_persistence(tmp_path / "plans.db")
    vst_planner.save_plan(plan)

    vst_mod2 = pytest.importorskip("vstarget.planning")
    planner2 = vst_mod2.VariableStarPlanner()
    planner2.set_persistence(tmp_path / "plans.db")
    loaded = planner2.load_plans()
    assert any(p.target_name == "R Leo" for p in loaded)


# ---------------------------------------------------------------------------
# TC-VST-080
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-080")
@pytest.mark.priority("MVP")
async def test_tc_vst_080_simbad_coordinate_fallback(vst_planner):
    """VST-080: Look up target coordinates via Simbad when not already in synced AAVSO catalog data."""
    vst_mod = pytest.importorskip("vstarget.planning")
    vst_planner._simbad = vst_mod.SimbadClient.__new__(vst_mod.SimbadClient)
    vst_planner._simbad.lookup = AsyncMock(return_value={"ra_deg": 154.0, "dec_deg": 11.4, "magnitude_v": 8.5})

    coords = await vst_planner.resolve_target("R Leo")
    assert abs(coords["ra_deg"] - 154.0) < 0.1
    vst_planner._simbad.lookup.assert_called_with("R Leo")


# ---------------------------------------------------------------------------
# TC-VST-090
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-090")
@pytest.mark.priority("MVP")
async def test_tc_vst_090_submit_target_to_scheduler_from_vst_ui(vst_planner):
    """VST-090: Submit a variable-star target with observation plan directly to SCHED job queue from VST UI."""
    sched_mod = pytest.importorskip("galileo.scheduler")
    vst_mod = pytest.importorskip("vstarget.planning")

    mock_scheduler = MagicMock()
    mock_scheduler.add_job = MagicMock()
    vst_planner._scheduler = mock_scheduler

    plan = vst_mod.ObservationPlan(target_name="R Leo", ra_deg=154.0, dec_deg=11.4)
    await vst_planner.submit_to_scheduler(plan)
    mock_scheduler.add_job.assert_called_once()
    submitted_job = mock_scheduler.add_job.call_args[0][0]
    assert "R Leo" in str(submitted_job)


# ---------------------------------------------------------------------------
# TC-VST-100
# ---------------------------------------------------------------------------

@pytest.mark.requirement("TC-VST-100")
@pytest.mark.priority("MVP")
def test_tc_vst_100_build_session_from_observation_plan():
    """VST-100: Create a session from the observation plan, targets ordered by RA, one Image block per filter."""
    pytest.importorskip("galileo.ui.sessions")
    builder = pytest.importorskip("vstarget.planning.session_builder")
    models = pytest.importorskip("vstarget.planning.models")
    from galileo.ui.sessions import ImageBlock, PlateSolveBlock, TargetBlock

    def target(name, ra_deg, filters, counts, intervals, binning):
        return models.ObservingTarget(
            aavso=models.AAVSOTarget(star_name=name, ra=ra_deg, dec=0.0),
            script_filters=filters,
            script_counts=counts,
            script_intervals=intervals,
            script_binning=binning,
        )

    # Deliberately out of RA order, to prove the builder sorts them.
    targets = [
        target("R Leo", 154.0, "V,B", "4,4", "30,60", "1,1"),
        target("Mira", 34.8, "V", "3", "20", "2"),
    ]

    blocks = builder.build_blocks(
        targets, builder.SessionOptions(platesolve=True, autofocus=False,
                                        dither=False, guiding=False)
    )

    target_blocks = [b for b in blocks if isinstance(b, TargetBlock)]
    assert [b.name for b in target_blocks] == ["Mira", "R Leo"], "Targets must be RA-ordered"

    # Mira: Target, PlateSolve, one Image. R Leo: Target, PlateSolve, two Images.
    assert [type(b).__name__ for b in blocks] == [
        "TargetBlock", "PlateSolveBlock", "ImageBlock",
        "TargetBlock", "PlateSolveBlock", "ImageBlock", "ImageBlock",
    ]

    mira_image = blocks[2]
    assert isinstance(mira_image, ImageBlock)
    assert (mira_image.filter, mira_image.count, mira_image.exposure, mira_image.binning) == (
        "V", 3, 20.0, 2,
    )

    rleo_images = [b for b in blocks[4:] if isinstance(b, ImageBlock)]
    assert [(b.filter, b.count, b.exposure) for b in rleo_images] == [
        ("V", 4, 30.0), ("B", 4, 60.0),
    ]
    assert isinstance(blocks[4], PlateSolveBlock)

    # Optional blocks are off unless asked for.
    plain = builder.build_blocks(targets, builder.SessionOptions(platesolve=False))
    assert not any(isinstance(b, PlateSolveBlock) for b in plain)

    # A ragged plan is reported rather than silently mis-zipped.
    ragged = [target("T CrB", 239.9, "V,B", "4", "15,15", "1,1")]
    assert builder.validate_targets(ragged), "Mismatched parameter counts must warn"
    assert not builder.validate_targets(targets)
