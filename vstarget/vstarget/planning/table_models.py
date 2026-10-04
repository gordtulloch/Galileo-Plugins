# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Qt item models behind the planning panel's two tables (VST-010 … VST-060).

``VariableListModel`` + ``VariableFilterProxy`` drive the read-only AAVSO
Variable List on the left; ``TargetsModel`` drives the editable Observation
Targets table on the right, whose last four columns the user edits in place.
"""

from __future__ import annotations

from typing import ClassVar, cast

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QBrush, QColor

from vstarget.planning.models import SECTION_CODES, AAVSOTarget, ObservingTarget

# Columns whose values must sort numerically rather than as text.
_NUMERIC_VAR_COLUMNS = (0, 3, 4, 6, 7, 9)


class VariableListModel(QAbstractTableModel):
    """Read-only model over the downloaded AAVSO target list."""

    HEADERS: ClassVar[list[str]] = [
        "★", "Name", "Type", "Max Mag", "Min Mag",
        "Filters", "RA (h)", "Dec (°)", "Const.", "Cadence (d)",
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[AAVSOTarget] = []

    # --- Public API -------------------------------------------------------

    def set_targets(self, targets: list[AAVSOTarget]) -> None:
        self.beginResetModel()
        self._data = targets
        self.endResetModel()

    def target_at(self, row: int) -> AAVSOTarget | None:
        return self._data[row] if 0 <= row < len(self._data) else None

    # --- QAbstractTableModel ---------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._data)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        t = self._data[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            return self._display(t, col)

        if role == Qt.ForegroundRole:
            if t.solar_conjunction:
                return QBrush(QColor("#999999"))
            if t.priority:
                return QBrush(QColor("#b87820"))

        if role == Qt.ToolTipRole:
            return self._tooltip(t)

        # Raw values so the proxy can sort numerically
        if role == Qt.UserRole:
            return self._sort_value(t, col) if col in _NUMERIC_VAR_COLUMNS \
                else self._display(t, col)

        return None

    # --- Rendering helpers ------------------------------------------------

    def _display(self, t: AAVSOTarget, col: int):
        if col == 0:
            return "★" if t.priority else ""
        if col == 1:
            return t.star_name
        if col == 2:
            return t.var_type
        if col == 3:
            return _mag(t.max_mag, t.max_mag_band)
        if col == 4:
            return _mag(t.min_mag, t.min_mag_band)
        if col == 5:
            return t.filters
        if col == 6:
            return f"{t.ra_hours:.4f}"
        if col == 7:
            return f"{t.dec:+.4f}"
        if col == 8:
            return t.constellation
        if col == 9:
            return f"{t.obs_cadence:.1f}" if t.obs_cadence is not None else ""
        return None

    def _sort_value(self, t: AAVSOTarget, col: int):
        if col == 0:
            return 0 if t.priority else 1          # priorities sort first
        if col == 3:
            return t.max_mag if t.max_mag is not None else 99.0
        if col == 4:
            return t.min_mag if t.min_mag is not None else 99.0
        if col == 6:
            return t.ra_hours
        if col == 7:
            return t.dec
        if col == 9:
            return t.obs_cadence if t.obs_cadence is not None else 9999.0
        return None

    def _tooltip(self, t: AAVSOTarget) -> str:
        parts: list[str] = [f"<b>{t.star_name}</b> ({t.var_type})"]
        if t.period is not None:
            parts.append(f"Period: {t.period:.4f} d")
        if t.obs_cadence is not None:
            parts.append(f"Cadence: {t.obs_cadence:.1f} d")
        if t.obs_mode:
            parts.append(f"Mode: {t.obs_mode}")
        if t.obs_section:
            parts.append(f"Section: {', '.join(t.obs_section)}")
        if t.solar_conjunction:
            parts.append("<i>⚠ Near solar conjunction – may not be observable</i>")
        if t.other_info:
            parts.append(t.other_info)
        return "<br>".join(parts)


class VariableFilterProxy(QSortFilterProxyModel):
    """Filters the variable list by section, free text, and the two flag checkboxes."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active_sections: set[str] = set()   # empty -> show all sections
        self._search_text: str = ""
        self._priority_only: bool = False
        self._hide_solar_conj: bool = False
        self.setSortRole(Qt.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def set_active_sections(self, codes: set[str]) -> None:
        self._active_sections = codes
        self.invalidateFilter()

    def set_search_text(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self.invalidateFilter()

    def set_priority_only(self, on: bool) -> None:
        self._priority_only = on
        self.invalidateFilter()

    def set_hide_solar_conj(self, on: bool) -> None:
        self._hide_solar_conj = on
        self.invalidateFilter()

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        model = cast(VariableListModel, self.sourceModel())
        t = model.target_at(source_row)
        if t is None:
            return False

        if self._priority_only and not t.priority:
            return False
        if self._hide_solar_conj and t.solar_conjunction:
            return False

        if self._active_sections:
            target_codes = {SECTION_CODES[s] for s in t.obs_section if s in SECTION_CODES}
            if not (self._active_sections & target_codes):
                return False

        if self._search_text:
            haystack = f"{t.star_name} {t.var_type} {t.constellation}".lower()
            if self._search_text not in haystack:
                return False

        return True

    def lessThan(
        self,
        left: QModelIndex | QPersistentModelIndex,
        right: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        model = cast(VariableListModel, self.sourceModel())
        if left.column() in _NUMERIC_VAR_COLUMNS:
            vl = model.data(left, Qt.UserRole)
            vr = model.data(right, Qt.UserRole)
            if vl is not None and vr is not None:
                try:
                    return float(vl) < float(vr)
                except (TypeError, ValueError):
                    pass
        return super().lessThan(left, right)


class TargetsModel(QAbstractTableModel):
    """Editable model for the observation plan.

    Sorting physically reorders the list rather than going through a proxy,
    because the row order is the session's target order.
    """

    HEADERS: ClassVar[list[str]] = [
        "Name", "RA (h)", "Dec (°)", "Const.",
        "Filter ✎", "Count ✎", "Interval (s) ✎", "Binning ✎",
    ]
    _EDITABLE: ClassVar[set[int]] = {4, 5, 6, 7}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._targets: list[ObservingTarget] = []

    # --- Public API -------------------------------------------------------

    def set_targets(self, targets: list[ObservingTarget]) -> None:
        self.beginResetModel()
        self._targets = list(targets)
        self.endResetModel()

    def get_targets(self) -> list[ObservingTarget]:
        return list(self._targets)

    def add_target(self, ot: ObservingTarget) -> bool:
        """Add *ot*; returns False if that star is already in the plan."""
        if any(e.aavso.star_name == ot.aavso.star_name for e in self._targets):
            return False
        row = len(self._targets)
        self.beginInsertRows(QModelIndex(), row, row)
        self._targets.append(ot)
        self.endInsertRows()
        return True

    def remove_rows_by_index(self, rows: list[int]) -> None:
        for row in sorted(rows, reverse=True):
            if 0 <= row < len(self._targets):
                self.beginRemoveRows(QModelIndex(), row, row)
                self._targets.pop(row)
                self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._targets.clear()
        self.endResetModel()

    def sort_by_ra(self) -> None:
        self.beginResetModel()
        self._targets.sort(key=lambda t: t.aavso.ra)
        self.endResetModel()

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        key_funcs = {
            0: lambda t: t.aavso.star_name.lower(),
            1: lambda t: t.aavso.ra,
            2: lambda t: t.aavso.dec,
            3: lambda t: t.aavso.constellation.lower(),
            4: lambda t: t.script_filters.lower(),
            5: lambda t: t.script_counts.lower(),
            6: lambda t: t.script_intervals.lower(),
            7: lambda t: t.script_binning.lower(),
        }
        key = key_funcs.get(column)
        if key is None:
            return
        self.beginResetModel()
        self._targets.sort(key=key, reverse=order == Qt.DescendingOrder)
        self.endResetModel()

    def move_up(self, row: int) -> None:
        if row <= 0:
            return
        self.beginResetModel()
        self._targets[row - 1], self._targets[row] = self._targets[row], self._targets[row - 1]
        self.endResetModel()

    def move_down(self, row: int) -> None:
        if row >= len(self._targets) - 1:
            return
        self.beginResetModel()
        self._targets[row], self._targets[row + 1] = self._targets[row + 1], self._targets[row]
        self.endResetModel()

    def target_at(self, row: int) -> ObservingTarget | None:
        return self._targets[row] if 0 <= row < len(self._targets) else None

    # --- QAbstractTableModel ---------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._targets)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.HEADERS[section]
            if role == Qt.ToolTipRole and section in self._EDITABLE:
                return "Double-click or press F2 to edit"
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        f = super().flags(index)
        if index.column() in self._EDITABLE:
            f |= Qt.ItemIsEditable
        return f

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        ot = self._targets[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0:
                return ot.aavso.star_name
            if col == 1:
                return f"{ot.aavso.ra_hours:.6f}"
            if col == 2:
                return f"{ot.aavso.dec:+.6f}"
            if col == 3:
                return ot.aavso.constellation
            if col == 4:
                return ot.script_filters
            if col == 5:
                return ot.script_counts
            if col == 6:
                return ot.script_intervals
            if col == 7:
                return ot.script_binning

        elif role == Qt.BackgroundRole:
            if col in self._EDITABLE:
                return QBrush(QColor("#fdfaed"))   # soft cream tint for editable cells

        elif role == Qt.ToolTipRole:
            if col <= 3:
                t = ot.aavso
                return (
                    f"{t.var_type}  |  "
                    f"Max: {t.max_mag} {t.max_mag_band}  "
                    f"Min: {t.min_mag} {t.min_mag_band}"
                )
            return "Double-click or press F2 to edit"

        return None

    def setData(
        self, index: QModelIndex | QPersistentModelIndex, value, role: int = Qt.EditRole
    ) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False
        ot = self._targets[index.row()]
        val = str(value).strip()
        col = index.column()
        if col == 4:
            ot.script_filters = val
        elif col == 5:
            ot.script_counts = val
        elif col == 6:
            ot.script_intervals = val
        elif col == 7:
            ot.script_binning = val
        else:
            return False
        self.dataChanged.emit(index, index, [role])
        return True


def _mag(value: float | None, band: str) -> str:
    if value is None:
        return ""
    return f"{value:.1f}{f' {band}' if band else ''}"
