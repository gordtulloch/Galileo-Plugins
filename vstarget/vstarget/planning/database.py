# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""SQLite persistence for observation plans (adapted from VSTarget planning/database.py)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vstarget.planning.models import ObservationPlan

logger = logging.getLogger(__name__)


class PlanDatabase:
    """Persists observation plans to a JSON file (simple, no external ORM needed)."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, plans: list[ObservationPlan]) -> None:
        data = [
            {
                "target_name": p.target_name,
                "ra_deg": p.ra_deg,
                "dec_deg": p.dec_deg,
                "filter_configs": [asdict(fc) for fc in p.filter_configs],
            }
            for p in plans
        ]
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self) -> list[ObservationPlan]:
        from vstarget.planning.models import ObservationPlan, FilterConfig
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text("utf-8"))
            plans = []
            for d in data:
                plan = ObservationPlan(
                    target_name=d["target_name"],
                    ra_deg=d.get("ra_deg", 0.0),
                    dec_deg=d.get("dec_deg", 0.0),
                )
                for fc in d.get("filter_configs", []):
                    plan.filter_configs.append(FilterConfig(**fc))
                plans.append(plan)
            return plans
        except Exception:
            logger.exception("Failed to load observation plans from %s", self._path)
            return []


# ---------------------------------------------------------------------------
# Observation-plan store (restored from the standalone VSTarget app)
# ---------------------------------------------------------------------------
#
# `PlanDatabase` above persists the planner service's `ObservationPlan` objects.
# `PlanStore` below persists what the planning panel's Observation Targets table
# holds — `ObservingTarget` rows, in their user-arranged order — so the plan is
# still there on the next run ("N target(s) restored from previous session").

_PLAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_targets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    -- AAVSO star data
    star_name        TEXT    NOT NULL,
    ra               REAL    NOT NULL,
    dec              REAL    NOT NULL,
    var_type         TEXT    NOT NULL DEFAULT '',
    min_mag          REAL,
    min_mag_band     TEXT    NOT NULL DEFAULT '',
    max_mag          REAL,
    max_mag_band     TEXT    NOT NULL DEFAULT '',
    period           REAL,
    obs_cadence      REAL,
    obs_mode         TEXT    NOT NULL DEFAULT '',
    obs_section      TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    aavso_filters    TEXT    NOT NULL DEFAULT '',
    other_info       TEXT    NOT NULL DEFAULT '',
    last_data_point  INTEGER,
    priority         INTEGER NOT NULL DEFAULT 0,      -- boolean
    constellation    TEXT    NOT NULL DEFAULT '',
    solar_conjunction INTEGER NOT NULL DEFAULT 0,     -- boolean
    -- Per-target capture parameters
    script_filters   TEXT    NOT NULL DEFAULT 'V,B,I',
    script_counts    TEXT    NOT NULL DEFAULT '4,4,4',
    script_intervals TEXT    NOT NULL DEFAULT '30,30,30',
    script_binning   TEXT    NOT NULL DEFAULT '1,1,1'
);
"""


def default_plan_db_path() -> Path:
    """Where the plan database lives.

    Under Galileo's own per-user data directory (`galileo.platform`, NFR-PORT-010)
    so the plugin never invents its own OS-specific path; falls back to the
    plugin package directory only if Galileo isn't importable.
    """
    try:
        from galileo.platform import get_data_dir
        base = Path(get_data_dir()) / "vstarget"
    except Exception:  # noqa: BLE001 - standalone/dev use without Galileo installed
        base = Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "vstarget_plan.db"


class PlanStore:
    """SQLite persistence for the Observation Targets table.

    One connection is held open for the object's lifetime with
    ``check_same_thread=False``, so in-memory databases work in tests and WAL
    mode is used efficiently in normal use.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        import sqlite3
        self._path = str(path) if path is not None else str(default_plan_db_path())
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_PLAN_SCHEMA)

    def close(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self._conn.close()

    def __del__(self) -> None:
        self.close()

    @property
    def path(self) -> str:
        return self._path

    def save_plan(self, targets: list) -> None:
        """Replace the whole saved plan with *targets*, preserving their order."""
        with self._conn:
            self._conn.execute("DELETE FROM plan_targets")
            self._conn.executemany(
                """
                INSERT INTO plan_targets (
                    sort_order, star_name, ra, dec, var_type,
                    min_mag, min_mag_band, max_mag, max_mag_band,
                    period, obs_cadence, obs_mode, obs_section,
                    aavso_filters, other_info, last_data_point,
                    priority, constellation, solar_conjunction,
                    script_filters, script_counts, script_intervals, script_binning
                ) VALUES (
                    :sort_order, :star_name, :ra, :dec, :var_type,
                    :min_mag, :min_mag_band, :max_mag, :max_mag_band,
                    :period, :obs_cadence, :obs_mode, :obs_section,
                    :aavso_filters, :other_info, :last_data_point,
                    :priority, :constellation, :solar_conjunction,
                    :script_filters, :script_counts, :script_intervals, :script_binning
                )
                """,
                [_plan_row(i, ot) for i, ot in enumerate(targets)],
            )

    def load_plan(self) -> list:
        """Every saved target, in its saved order."""
        rows = self._conn.execute("SELECT * FROM plan_targets ORDER BY sort_order").fetchall()
        return [_plan_target(r) for r in rows]

    def target_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM plan_targets").fetchone()[0]


def _plan_row(sort_order: int, ot) -> dict:
    t = ot.aavso
    return {
        "sort_order": sort_order,
        "star_name": t.star_name,
        "ra": t.ra,
        "dec": t.dec,
        "var_type": t.var_type or "",
        "min_mag": t.min_mag,
        "min_mag_band": t.min_mag_band or "",
        "max_mag": t.max_mag,
        "max_mag_band": t.max_mag_band or "",
        "period": t.period,
        "obs_cadence": t.obs_cadence,
        "obs_mode": t.obs_mode or "",
        "obs_section": json.dumps(t.obs_section or []),
        "aavso_filters": t.filters or "",
        "other_info": t.other_info or "",
        "last_data_point": t.last_data_point,
        "priority": int(bool(t.priority)),
        "constellation": t.constellation or "",
        "solar_conjunction": int(bool(t.solar_conjunction)),
        "script_filters": ot.script_filters,
        "script_counts": ot.script_counts,
        "script_intervals": ot.script_intervals,
        "script_binning": ot.script_binning,
    }


def _plan_target(row):
    from vstarget.planning.models import AAVSOTarget, ObservingTarget
    return ObservingTarget(
        aavso=AAVSOTarget(
            star_name=row["star_name"],
            ra=row["ra"],
            dec=row["dec"],
            var_type=row["var_type"] or "",
            min_mag=row["min_mag"],
            min_mag_band=row["min_mag_band"] or "",
            max_mag=row["max_mag"],
            max_mag_band=row["max_mag_band"] or "",
            period=row["period"],
            obs_cadence=row["obs_cadence"],
            obs_mode=row["obs_mode"] or "",
            obs_section=json.loads(row["obs_section"] or "[]"),
            filters=row["aavso_filters"] or "",
            other_info=row["other_info"] or "",
            last_data_point=row["last_data_point"],
            priority=bool(row["priority"]),
            constellation=row["constellation"] or "",
            solar_conjunction=bool(row["solar_conjunction"]),
        ),
        script_filters=row["script_filters"],
        script_counts=row["script_counts"],
        script_intervals=row["script_intervals"],
        script_binning=row["script_binning"],
    )
