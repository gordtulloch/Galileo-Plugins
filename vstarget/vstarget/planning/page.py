# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The Variable Star Planning panel (VST-010 … VST-060, VST-090).

Restores the standalone VSTarget application's planner window as a Galileo
plugin panel: the AAVSO Variable List on the left, the Observation Targets plan
on the right, and a status strip along the bottom. The window's menu bar is
deliberately not reproduced - Galileo owns the application chrome, and the
analysis entries it carried live in the VS Analysis panel instead.

The one behavioural change from the standalone app: the plan is turned into a
Galileo session on Planning > Sessions rather than exported as an iTelescope
ACP script. See :mod:`vstarget.planning.session_builder`.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse

from PySide6.QtCore import QModelIndex, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from vstarget.planning.models import SECTION_CODES, ObservingTarget
from vstarget.planning.table_models import (
    TargetsModel,
    VariableFilterProxy,
    VariableListModel,
)

logger = logging.getLogger(__name__)


class SessionPreviewDialog(QDialog):
    """Read-only outline of the session the plan would build."""

    def __init__(self, outline: str, on_create, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session Preview")
        self.resize(640, 540)
        self._on_create = on_create

        layout = QVBoxLayout(self)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(outline)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.Monospace)
        text.setFont(font)
        layout.addWidget(text)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Save).setText("Create Session")
        btns.accepted.connect(self._create)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _create(self) -> None:
        self.accept()
        self._on_create()


class VSTPlanningPage(QWidget):
    """Variable Star Planning panel."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("VSTPage")

        from vstarget.planning.database import PlanStore
        from vstarget.settings import VstSettings

        self.settings = VstSettings()
        self._fetch_thread = None
        self._loading_plan = False      # suppress autosave while restoring
        self._summary_count = 0
        self._summary_sec = 0.0

        try:
            self._store = PlanStore()
        except Exception:
            logger.exception("Could not open the VSTarget plan database")
            self._store = None

        self._build()
        self._restore_plan()

    # -- Construction ------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_variable_panel())
        splitter.addWidget(self._build_targets_panel())
        splitter.setStretchFactor(0, 55)
        splitter.setStretchFactor(1, 45)
        outer.addWidget(splitter, 1)
        outer.addWidget(self._build_status_bar())

    # -- Left panel: AAVSO Variable List -----------------------------------

    def _build_variable_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 3, 6)

        title_row = QHBoxLayout()
        title_row.addWidget(_bold(QLabel("AAVSO Variable List")))
        title_row.addStretch()

        settings_btn = QPushButton("⚙ Settings…")
        settings_btn.setToolTip("Configure the AAVSO API key and telescope location")
        settings_btn.clicked.connect(self._open_settings)
        title_row.addWidget(settings_btn)

        self._download_btn = QPushButton("⬇  Download")
        self._download_btn.setToolTip("Fetch targets from the AAVSO Target Tool API")
        self._download_btn.clicked.connect(self._download_targets)
        title_row.addWidget(self._download_btn)
        layout.addLayout(title_row)

        layout.addWidget(self._build_sections_box())
        layout.addLayout(self._build_filter_row())
        layout.addLayout(self._build_search_row())
        layout.addWidget(self._build_variable_table())
        layout.addLayout(self._build_add_row())
        return panel

    def _build_sections_box(self) -> QWidget:
        box = QGroupBox("Sections  (used for download and display filter)")
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(2)

        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel("Select:"))
        all_btn = _link_button("All", lambda: self._set_all_sections(True))
        none_btn = _link_button("None", lambda: self._set_all_sections(False))
        quick_row.addWidget(all_btn)
        quick_row.addWidget(none_btn)
        quick_row.addStretch()
        box_layout.addLayout(quick_row)

        grid = QGridLayout()
        grid.setSpacing(2)
        saved = set(self.settings.selected_sections)
        self._section_cbs: dict[str, QCheckBox] = {}
        for i, (display_name, code) in enumerate(SECTION_CODES.items()):
            cb = QCheckBox(display_name)
            cb.setChecked(code in saved)
            cb.stateChanged.connect(self._on_section_changed)
            self._section_cbs[code] = cb
            grid.addWidget(cb, i // 2, i % 2)
        box_layout.addLayout(grid)
        return box

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._observable_cb = QCheckBox("Observable only (next night)")
        self._observable_cb.setToolTip(
            "When downloading, the API returns only targets visible from the "
            "configured telescope location during the next nighttime period."
        )
        row.addWidget(self._observable_cb)

        self._priority_only_cb = QCheckBox("Priority only")
        self._priority_only_cb.stateChanged.connect(
            lambda s: self._var_proxy.set_priority_only(s == Qt.Checked.value)
        )
        row.addWidget(self._priority_only_cb)

        self._hide_solar_cb = QCheckBox("Hide solar conj.")
        self._hide_solar_cb.stateChanged.connect(
            lambda s: self._var_proxy.set_hide_solar_conj(s == Qt.Checked.value)
        )
        row.addWidget(self._hide_solar_cb)
        row.addStretch()
        return row

    def _build_search_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by name, type, or constellation…")
        self._search_edit.textChanged.connect(self._on_search_changed)
        row.addWidget(self._search_edit)
        return row

    def _build_variable_table(self) -> QWidget:
        self._var_model = VariableListModel()
        self._var_proxy = VariableFilterProxy()
        self._var_proxy.set_active_sections(
            {c for c, cb in self._section_cbs.items() if cb.isChecked()}
        )
        self._var_proxy.setSourceModel(self._var_model)

        table = QTableView()
        table.setModel(self._var_proxy)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.doubleClicked.connect(self._add_selected_targets)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_variable_context_menu)
        table.sortByColumn(6, Qt.AscendingOrder)

        for col, width in [(0, 24), (1, 130), (2, 90), (3, 75), (4, 75),
                           (5, 70), (6, 80), (7, 80), (8, 46)]:
            table.setColumnWidth(col, width)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)

        self._var_table = table
        return table

    def _build_add_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._count_label = QLabel("No targets downloaded")
        self._count_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self._count_label)

        add_all_btn = QPushButton("Add All Visible →")
        add_all_btn.setToolTip("Add every currently visible target to the observation plan")
        add_all_btn.clicked.connect(self._add_all_visible)
        add_sel_btn = QPushButton("Add Selected →")
        add_sel_btn.setToolTip("Add the selected rows to the observation plan (or double-click)")
        add_sel_btn.clicked.connect(self._add_selected_targets)
        row.addWidget(add_all_btn)
        row.addWidget(add_sel_btn)
        return row

    # -- Right panel: Observation Targets ----------------------------------

    def _build_targets_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(3, 6, 6, 6)

        layout.addWidget(_bold(QLabel("Observation Targets  (session built sorted by RA)")))
        layout.addLayout(self._build_target_toolbar())
        layout.addWidget(self._build_targets_table())

        hint = QLabel(
            "✎  Click a row to select, then double-click or press F2 / type to edit "
            "Filter · Count · Interval · Binning"
        )
        hint.setStyleSheet("color: gray; font-size: 11px; padding: 1px 0;")
        layout.addWidget(hint)

        self._tgt_summary_lbl = QLabel("0 targets  │  Total exposure: —")
        self._tgt_summary_lbl.setStyleSheet("font-size: 12px; padding: 2px 0;")
        layout.addWidget(self._tgt_summary_lbl)

        layout.addWidget(self._build_session_options())
        return panel

    def _build_target_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()

        up_btn = QPushButton("▲")
        up_btn.setFixedWidth(34)
        up_btn.setToolTip("Move selected target up")
        up_btn.clicked.connect(self._move_target_up)

        down_btn = QPushButton("▼")
        down_btn.setFixedWidth(34)
        down_btn.setToolTip("Move selected target down")
        down_btn.clicked.connect(self._move_target_down)

        remove_btn = QPushButton("✕ Remove")
        remove_btn.setToolTip("Remove selected targets from the plan")
        remove_btn.clicked.connect(self._remove_selected_targets)

        clear_btn = QPushButton("🗑 Clear All")
        clear_btn.setToolTip("Remove all targets from the plan")
        clear_btn.clicked.connect(self._clear_targets)

        for w in (up_btn, down_btn, remove_btn, clear_btn):
            row.addWidget(w)
        row.addStretch()
        return row

    def _build_targets_table(self) -> QWidget:
        self._tgt_model = TargetsModel()

        table = QTableView()
        table.setModel(self._tgt_model)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        # Editable by double-click, F2, or simply typing
        table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        table.doubleClicked.connect(self._on_target_double_clicked)

        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_target_header_clicked)
        # Sort state is tracked here rather than read back from the header,
        # whose indicator defaults to column 0 / ascending and would confuse
        # the first toggle.
        self._tgt_sort_col = -1
        self._tgt_sort_order = Qt.AscendingOrder

        # Delete removes rows; WidgetShortcut keeps it from firing while a cell
        # editor has focus.
        delete_sc = QShortcut(QKeySequence(Qt.Key_Delete), table)
        delete_sc.setContext(Qt.WidgetShortcut)
        delete_sc.activated.connect(self._remove_selected_targets)

        for col, width in [(0, 130), (1, 80), (2, 80), (3, 46), (4, 100), (5, 65), (6, 85)]:
            table.setColumnWidth(col, width)

        self._tgt_table = table
        self._connect_plan_signals()
        return table

    def _build_session_options(self) -> QWidget:
        box = QGroupBox("Session Options")
        vbox = QVBoxLayout(box)

        blocks_row = QHBoxLayout()
        blocks_row.addWidget(QLabel("Blocks:"))
        self._chk_platesolve = _option_cb(
            "Plate solve", self.settings.block_platesolve,
            "Plate-solve after slewing to each target",
        )
        self._chk_autofocus = _option_cb(
            "Autofocus", self.settings.block_autofocus,
            "Run an autofocus routine on each target before imaging",
        )
        self._chk_dither = _option_cb(
            "Dither", self.settings.block_dither,
            "Dither between each filter's exposures",
        )
        self._chk_guiding = _option_cb(
            "Guiding", self.settings.block_guiding,
            "Start guiding on each target and stop when its exposures finish",
        )
        for cb in (self._chk_platesolve, self._chk_autofocus,
                   self._chk_dither, self._chk_guiding):
            cb.toggled.connect(self._save_block_options)
            blocks_row.addWidget(cb)
        blocks_row.addStretch()
        vbox.addLayout(blocks_row)

        vbox.addWidget(self._build_defaults_box())

        action_row = QHBoxLayout()
        action_row.addStretch()
        preview_btn = QPushButton("📄 Preview Session")
        preview_btn.setToolTip("See the blocks this plan would create before creating them")
        preview_btn.clicked.connect(self._preview_session)
        create_btn = QPushButton("💾 Create Session")
        create_btn.setToolTip("Create a session for this plan on Planning > Sessions")
        create_btn.clicked.connect(self._create_session)
        action_row.addWidget(preview_btn)
        action_row.addWidget(create_btn)
        vbox.addLayout(action_row)
        return box

    def _build_defaults_box(self) -> QWidget:
        box = QGroupBox("New Target Defaults")
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        self._def_filter = QLineEdit(self.settings.default_filters)
        self._def_count = QLineEdit(self.settings.default_counts)
        self._def_interval = QLineEdit(self.settings.default_intervals)
        self._def_binning = QLineEdit(self.settings.default_binning)
        for le in (self._def_filter, self._def_count, self._def_interval, self._def_binning):
            le.setMaximumWidth(120)
            le.editingFinished.connect(self._save_defaults)

        grid.addWidget(QLabel("Filter:"), 0, 0)
        grid.addWidget(self._def_filter, 0, 1)
        grid.addWidget(QLabel("Count:"), 0, 2)
        grid.addWidget(self._def_count, 0, 3)
        grid.addWidget(QLabel("Interval (s):"), 1, 0)
        grid.addWidget(self._def_interval, 1, 1)
        grid.addWidget(QLabel("Binning:"), 1, 2)
        grid.addWidget(self._def_binning, 1, 3)

        apply_btn = QPushButton("Apply to All Targets in Plan")
        apply_btn.setToolTip("Overwrite every target's capture parameters with these defaults")
        apply_btn.clicked.connect(self._apply_defaults_to_all)
        grid.addWidget(apply_btn, 2, 0, 1, 4)

        suggest_btn = QPushButton("📏 Suggest Exposures from Calibration")
        suggest_btn.setToolTip(
            "Size each target's Interval for its faint end (min mag) using the\n"
            "stored exposure calibration, capped so comparison stars never saturate"
        )
        suggest_btn.clicked.connect(self._suggest_exposures)
        grid.addWidget(suggest_btn, 3, 0, 1, 4)

        box.setLayout(grid)
        return box

    # -- Status strip ------------------------------------------------------

    def _build_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("VSTStatusBar")
        bar.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 2, 6, 2)

        self._status_lbl = QLabel("")
        row.addWidget(self._status_lbl, 1)

        self._db_lbl = QLabel("✓ DB" if self._store is not None else "⚠ DB")
        self._db_lbl.setToolTip(
            f"Plan database: {self._store.path}" if self._store is not None
            else "The plan database could not be opened — the plan will not be saved."
        )
        self._db_lbl.setStyleSheet("color: gray; font-size: 11px; padding: 0 6px;")
        row.addWidget(self._db_lbl)

        self._workdir_lbl = QLabel()
        self._workdir_lbl.setStyleSheet("color: gray; font-size: 11px; padding: 0 6px;")
        row.addWidget(self._workdir_lbl)
        self._refresh_workdir_label()
        return bar

    def _show_status(self, message: str) -> None:
        self._status_lbl.setText(message)

    def _refresh_workdir_label(self) -> None:
        workdir = self.settings.analysis_working_dir
        if workdir:
            self._workdir_lbl.setText(f"📁 {os.path.basename(os.path.normpath(workdir))}")
            self._workdir_lbl.setToolTip(f"Working directory: {workdir}")
        else:
            self._workdir_lbl.setText("")
            self._workdir_lbl.setToolTip("")

    # -- Plan restore / autosave -------------------------------------------

    def _connect_plan_signals(self) -> None:
        model = self._tgt_model
        for signal in (model.dataChanged, model.rowsInserted,
                       model.rowsRemoved, model.modelReset):
            signal.connect(self._autosave_plan)
        # The summary counter updates incrementally where it can;
        # rowsAboutToBeRemoved still sees the rows, so their exposure can be
        # subtracted before they go.
        model.rowsAboutToBeRemoved.connect(self._on_targets_about_to_remove)
        model.rowsInserted.connect(self._on_targets_inserted)
        model.dataChanged.connect(self._recalc_target_summary)
        model.modelReset.connect(self._recalc_target_summary)

    def _restore_plan(self) -> None:
        saved = []
        if self._store is not None:
            try:
                saved = self._store.load_plan()
            except Exception:
                logger.exception("Could not restore the saved observation plan")
        self._loading_plan = True
        if saved:
            self._tgt_model.set_targets(saved)
        self._loading_plan = False
        self._recalc_target_summary()

        message = f"Ready  |  Telescope: {self.settings.telescope_name}"
        if saved:
            message += f"  |  {len(saved)} target(s) restored from previous session"
        self._show_status(message)

    def _autosave_plan(self, *_args) -> None:
        if self._loading_plan or self._store is None:
            return
        try:
            self._store.save_plan(self._tgt_model.get_targets())
        except Exception as exc:  # noqa: BLE001 - a failed autosave must not break editing
            self._show_status(f"⚠ Plan save failed: {exc}")

    # -- Settings ----------------------------------------------------------

    def _open_settings(self) -> None:
        from vstarget.settings_dialog import VstSettingsDialog

        if VstSettingsDialog(self.settings, self).exec():
            self._def_filter.setText(self.settings.default_filters)
            self._def_count.setText(self.settings.default_counts)
            self._def_interval.setText(self.settings.default_intervals)
            self._def_binning.setText(self.settings.default_binning)
            self._refresh_workdir_label()
            self._show_status(
                f"Settings saved  |  Telescope: {self.settings.telescope_name}"
            )

    def _save_defaults(self) -> None:
        self.settings.default_filters = self._def_filter.text().strip()
        self.settings.default_counts = self._def_count.text().strip()
        self.settings.default_intervals = self._def_interval.text().strip()
        self.settings.default_binning = self._def_binning.text().strip()

    def _save_block_options(self) -> None:
        self.settings.block_platesolve = self._chk_platesolve.isChecked()
        self.settings.block_autofocus = self._chk_autofocus.isChecked()
        self.settings.block_dither = self._chk_dither.isChecked()
        self.settings.block_guiding = self._chk_guiding.isChecked()

    # -- Download ----------------------------------------------------------

    def _download_targets(self) -> None:
        if not self.settings.api_key:
            QMessageBox.warning(
                self, "No API Key",
                "Please configure your AAVSO API key in Settings before downloading.",
            )
            self._open_settings()
            return

        sections = [c for c, cb in self._section_cbs.items() if cb.isChecked()]
        if not sections:
            QMessageBox.information(
                self, "No Sections Selected",
                "Please check at least one section to download.",
            )
            return

        from vstarget.planning.aavso_client import FetchTargetsThread

        self._download_btn.setEnabled(False)
        self._download_btn.setText("⬇  Downloading…")
        self._show_status("Connecting to AAVSO Target Tool API…")

        self._fetch_thread = FetchTargetsThread(
            api_key=self.settings.api_key,
            sections=sections,
            observable=self._observable_cb.isChecked(),
            latitude=self.settings.latitude,
            longitude=self.settings.longitude,
            target_altitude=self.settings.target_altitude,
            sun_altitude=self.settings.sun_altitude,
            parent=self,
        )
        self._fetch_thread.finished.connect(self._on_targets_downloaded)
        self._fetch_thread.error.connect(self._on_download_error)
        self._fetch_thread.status.connect(self._show_status)
        self._fetch_thread.start()

        self.settings.selected_sections = sections

    def _on_targets_downloaded(self, targets: list) -> None:
        self._var_model.set_targets(targets)
        self._download_btn.setEnabled(True)
        self._download_btn.setText("⬇  Download")
        self._update_count_label()
        self._show_status(
            f"Downloaded {len(targets)} targets  |  "
            f"Showing {self._var_proxy.rowCount()} after filters"
        )

    def _on_download_error(self, message: str) -> None:
        self._download_btn.setEnabled(True)
        self._download_btn.setText("⬇  Download")
        self._show_status("Download failed")
        QMessageBox.critical(self, "Download Error", message)

    # -- Variable list filters ---------------------------------------------

    def _on_section_changed(self) -> None:
        self._var_proxy.set_active_sections(
            {c for c, cb in self._section_cbs.items() if cb.isChecked()}
        )
        self.settings.selected_sections = [
            c for c, cb in self._section_cbs.items() if cb.isChecked()
        ]
        self._update_count_label()

    def _set_all_sections(self, checked: bool) -> None:
        for cb in self._section_cbs.values():
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._on_section_changed()

    def _on_search_changed(self, text: str) -> None:
        self._var_proxy.set_search_text(text)
        self._update_count_label()

    def _update_count_label(self) -> None:
        total = self._var_model.rowCount()
        shown = self._var_proxy.rowCount()
        if total == 0:
            self._count_label.setText("No targets downloaded")
        elif total == shown:
            self._count_label.setText(f"{total} targets")
        else:
            self._count_label.setText(f"{shown} of {total} shown")

    # -- Adding targets ----------------------------------------------------

    def _make_observing_target(self, t) -> ObservingTarget:
        return ObservingTarget(
            aavso=t,
            script_filters=self._def_filter.text().strip() or self.settings.default_filters,
            script_counts=self._def_count.text().strip() or self.settings.default_counts,
            script_intervals=self._def_interval.text().strip() or self.settings.default_intervals,
            script_binning=self._def_binning.text().strip() or self.settings.default_binning,
        )

    def _add_selected_targets(self) -> None:
        added = 0
        for proxy_idx in self._var_table.selectionModel().selectedRows():
            src_idx = self._var_proxy.mapToSource(proxy_idx)
            t = self._var_model.target_at(src_idx.row())
            if t and self._tgt_model.add_target(self._make_observing_target(t)):
                added += 1
        if added:
            self._show_status(
                f"Added {added} target(s)  |  {self._tgt_model.rowCount()} in plan"
            )

    def _add_all_visible(self) -> None:
        added = 0
        for row in range(self._var_proxy.rowCount()):
            src_idx = self._var_proxy.mapToSource(self._var_proxy.index(row, 0))
            t = self._var_model.target_at(src_idx.row())
            if t and self._tgt_model.add_target(self._make_observing_target(t)):
                added += 1
        self._show_status(
            f"Added {added} target(s)  |  {self._tgt_model.rowCount()} in plan"
        )

    def _show_variable_context_menu(self, pos) -> None:
        proxy_idx = self._var_table.indexAt(pos)
        if not proxy_idx.isValid():
            return
        src_idx = self._var_proxy.mapToSource(proxy_idx)
        t = self._var_model.target_at(src_idx.row())
        if t is None:
            return

        menu = QMenu(self)
        add_act = menu.addAction(f"Add '{t.star_name}' to Plan")
        add_act.triggered.connect(self._add_selected_targets)
        menu.addSeparator()

        encoded_name = urllib.parse.quote(t.star_name)
        vsx_url = QUrl(
            f"https://www.aavso.org/vsx/index.php?view=results.get&ident={encoded_name}"
        )
        menu.addAction("Open in AAVSO VSX…").triggered.connect(
            lambda: QDesktopServices.openUrl(vsx_url)
        )
        webobs_url = QUrl(f"https://www.aavso.org/apps/webobs/results/?star={encoded_name}")
        menu.addAction("Open Recent Observations (WebObs)…").triggered.connect(
            lambda: QDesktopServices.openUrl(webobs_url)
        )

        # Campaign / Alert Notice links carried in other_info as [[Text url]]
        if t.other_info:
            notice_links = re.findall(r"\[\[([^\]]*?)\s+(https?://\S+?)\]\]", t.other_info)
            if notice_links:
                menu.addSeparator()
                for description, url in notice_links:
                    label = description.strip() or "Campaign Notice"
                    act = menu.addAction(f"Open: {label}…")
                    captured = QUrl(url)
                    act.triggered.connect(
                        lambda checked=False, u=captured: QDesktopServices.openUrl(u)
                    )

        menu.exec(self._var_table.viewport().mapToGlobal(pos))

    # -- Managing the plan --------------------------------------------------

    def _on_target_double_clicked(self, index: QModelIndex) -> None:
        """Double-clicking a read-only cell starts editing the Filter column instead."""
        if not (index.flags() & Qt.ItemIsEditable):
            editable_idx = self._tgt_model.index(index.row(), min(TargetsModel._EDITABLE))
            self._tgt_table.setCurrentIndex(editable_idx)
            self._tgt_table.edit(editable_idx)

    def _remove_selected_targets(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._tgt_table.selectionModel().selectedRows()},
            reverse=True,
        )
        self._tgt_model.remove_rows_by_index(rows)
        self._show_status(f"{self._tgt_model.rowCount()} targets in plan")

    def _clear_targets(self) -> None:
        if self._tgt_model.rowCount() == 0:
            return
        reply = QMessageBox.question(
            self, "Clear Plan",
            "Remove all targets from the observation plan?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._tgt_model.clear()
            self._show_status("Observation plan cleared")

    def _on_target_header_clicked(self, col: int) -> None:
        """Toggle the sort direction and physically reorder the plan."""
        if self._tgt_sort_col == col:
            self._tgt_sort_order = (
                Qt.DescendingOrder if self._tgt_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._tgt_sort_col = col
            self._tgt_sort_order = Qt.AscendingOrder
        self._tgt_table.horizontalHeader().setSortIndicator(col, self._tgt_sort_order)
        self._tgt_model.sort(col, self._tgt_sort_order)

        col_name = TargetsModel.HEADERS[col].replace(" ✎", "")
        direction = (
            "A→Z / low→high" if self._tgt_sort_order == Qt.AscendingOrder
            else "Z→A / high→low"
        )
        self._show_status(f"Sorted by {col_name} ({direction})")

    def _move_target_up(self) -> None:
        rows = [idx.row() for idx in self._tgt_table.selectionModel().selectedRows()]
        if len(rows) != 1:
            return
        row = rows[0]
        self._tgt_model.move_up(row)
        self._select_target_row(max(0, row - 1))

    def _move_target_down(self) -> None:
        rows = [idx.row() for idx in self._tgt_table.selectionModel().selectedRows()]
        if len(rows) != 1:
            return
        row = rows[0]
        count = self._tgt_model.rowCount()
        self._tgt_model.move_down(row)
        self._select_target_row(min(row + 1, count - 1))

    def _select_target_row(self, row: int) -> None:
        self._tgt_table.selectRow(row)
        self._tgt_table.scrollTo(self._tgt_model.index(row, 0))
        self._tgt_table.setFocus()

    def _apply_defaults_to_all(self) -> None:
        targets = self._tgt_model.get_targets()
        if not targets:
            return
        for ot in targets:
            ot.script_filters = self._def_filter.text().strip()
            ot.script_counts = self._def_count.text().strip()
            ot.script_intervals = self._def_interval.text().strip()
            ot.script_binning = self._def_binning.text().strip()
        self._tgt_model.set_targets(targets)
        self._show_status(f"Applied defaults to all {len(targets)} targets")

    def _suggest_exposures(self) -> None:
        """Fill each target's Interval from the stored exposure calibration.

        Sized for the target's faint end (min mag) at the default S/N and
        hard-capped so comparison stars never saturate; warns when the bright
        end could saturate.
        """
        from vstarget.analysis.exposure import (
            ExposureCalibration,
            normalize_telescope,
            suggest_exposure,
        )

        targets = self._tgt_model.get_targets()
        if not targets:
            QMessageBox.information(
                self, "Suggest Exposures", "Add targets to the observation plan first."
            )
            return

        telescope = normalize_telescope(self.settings.telescope_name)
        calibs = {
            d.get("filter_band", ""): ExposureCalibration.from_dict(d)
            for d in self.settings.exposure_calibrations()
            if d.get("telescope") == telescope
        }
        if not calibs:
            QMessageBox.information(
                self, "Suggest Exposures",
                f"No exposure calibration stored for {telescope}.\n\n"
                "Calibrate a plate-solved image from this telescope in the "
                "VS Analysis panel first.",
            )
            return

        updated = 0
        skipped: list[str] = []
        warnings: list[str] = []
        for ot in targets:
            name = ot.aavso.star_name
            faint = ot.aavso.min_mag if ot.aavso.min_mag is not None else ot.aavso.max_mag
            if faint is None:
                skipped.append(f"{name}: no magnitude data")
                continue

            bands = [b.strip() for b in ot.script_filters.split(",") if b.strip()]
            old_intervals = [iv.strip() for iv in ot.script_intervals.split(",")]
            new_intervals: list[str] = []
            missing: set[str] = set()
            changed = False

            for i, band in enumerate(bands):
                calib = calibs.get(band)
                if calib is None:
                    # Keep whatever was there for an uncalibrated filter
                    new_intervals.append(old_intervals[i] if i < len(old_intervals) else "30")
                    missing.add(band)
                    continue
                suggestion = suggest_exposure(
                    calib, faint_mag=faint, bright_mag=ot.aavso.max_mag
                )
                new_intervals.append(str(suggestion.seconds))
                changed = True
                warnings.extend(f"{name} ({band}): {w}" for w in suggestion.warnings)

            if missing:
                skipped.append(
                    f"{name}: no {telescope} calibration for {', '.join(sorted(missing))}"
                )
            if changed:
                ot.script_intervals = ",".join(new_intervals)
                updated += 1

        self._tgt_model.set_targets(targets)
        self._show_status(f"Suggested exposures for {updated} target(s)")

        summary = [f"Updated intervals for {updated} of {len(targets)} target(s)."]
        if skipped:
            summary.append("\nSkipped / partial:")
            summary.extend(f"• {s}" for s in skipped[:10])
            if len(skipped) > 10:
                summary.append(f"…and {len(skipped) - 10} more")
        if warnings:
            summary.append("\nWarnings:")
            summary.extend(f"⚠ {w}" for w in warnings[:10])
            if len(warnings) > 10:
                summary.append(f"…and {len(warnings) - 10} more")
        QMessageBox.information(self, "Suggest Exposures", "\n".join(summary))

    # -- Session creation ---------------------------------------------------

    def _session_options(self):
        from vstarget.planning.session_builder import SessionOptions
        return SessionOptions(
            platesolve=self._chk_platesolve.isChecked(),
            autofocus=self._chk_autofocus.isChecked(),
            dither=self._chk_dither.isChecked(),
            guiding=self._chk_guiding.isChecked(),
        )

    def _check_ready(self) -> bool:
        """True when there are targets and validation passes (or the user accepts it)."""
        from vstarget.planning.session_builder import validate_targets

        targets = self._tgt_model.get_targets()
        if not targets:
            QMessageBox.information(
                self, "No Targets",
                "Add targets to the observation plan before creating a session.",
            )
            return False

        warnings = validate_targets(targets)
        if warnings:
            reply = QMessageBox.warning(
                self, "Validation Warnings",
                "The following validation issues were found:\n\n"
                + "\n".join(f"• {w}" for w in warnings)
                + "\n\nProceed anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            return reply == QMessageBox.Yes
        return True

    def _preview_session(self) -> None:
        from vstarget.planning.session_builder import describe

        if not self._check_ready():
            return
        outline = describe(self._tgt_model.get_targets(), self._session_options())
        SessionPreviewDialog(outline, self._create_session, self).exec()

    def _create_session(self) -> None:
        from vstarget.planning.session_builder import build_blocks, default_session_name

        if not self._check_ready():
            return

        try:
            blocks = build_blocks(self._tgt_model.get_targets(), self._session_options())
        except Exception as exc:
            logger.exception("Could not build session blocks")
            QMessageBox.warning(self, "Create Session", f"Could not build the session:\n{exc}")
            return
        if not blocks:
            QMessageBox.information(
                self, "Create Session", "This plan produced no session blocks."
            )
            return

        name = default_session_name()
        try:
            where = self._add_session(name, blocks)
        except Exception as exc:
            logger.exception("Could not create the session")
            QMessageBox.warning(self, "Create Session", f"Could not create the session:\n{exc}")
            return

        target_count = self._tgt_model.rowCount()
        self._show_status(
            f"Created session {name!r} with {target_count} target(s)  |  {where}"
        )
        QMessageBox.information(
            self, "Create Session",
            f"Created session {name!r} with {target_count} target(s) "
            f"and {len(blocks)} block(s).\n\n{where}",
        )

    def _add_session(self, name: str, blocks: list) -> str:
        """Add the session to the live Sessions screen, or save it to disk.

        Returns a short human-readable note on where the session ended up.
        """
        sessions_page = self._find_sessions_page()

        if sessions_page is not None:
            # Create through the host's own entry point so the new region gets
            # the active Pier and that Pier's scheduler, then append the rest
            # of the blocks to the Target block it starts with.
            first = blocks[0]
            sessions_page.create_session_for_target(first.name, first.ra_deg, first.dec_deg)
            region = sessions_page.screen.visible_sessions[-1]
            region.name = name
            for block in blocks[1:]:
                region.insert_block(block)
            region.save()
            sessions_page.reload()
            return "It is now on Planning > Sessions."

        # No host window to attach to (standalone use): persist it so the
        # Sessions screen picks it up.
        from galileo.ui.sessions import SessionRegion

        region = SessionRegion(name=name)
        for block in blocks:
            region.insert_block(block)
        region.save()
        return f"Saved to {region._store_path()}"

    def _find_sessions_page(self):
        """The host application's Sessions page, if this panel is inside one."""
        window = self.window()
        if window is None:
            return None
        try:
            from galileo.ui.sessions import SessionsPageWidget
            return window.findChild(SessionsPageWidget)
        except Exception:
            logger.exception("Could not locate the Sessions page")
            return None

    # -- Exposure summary ---------------------------------------------------

    @staticmethod
    def _exposure_for_target(ot) -> float:
        from vstarget.planning.session_builder import target_exposure_seconds
        return target_exposure_seconds(ot)

    def _on_targets_about_to_remove(self, _parent, first: int, last: int) -> None:
        for row in range(first, last + 1):
            ot = self._tgt_model.target_at(row)
            if ot:
                self._summary_sec -= self._exposure_for_target(ot)
                self._summary_count -= 1
        self._refresh_summary_label()

    def _on_targets_inserted(self, _parent, first: int, last: int) -> None:
        for row in range(first, last + 1):
            ot = self._tgt_model.target_at(row)
            if ot:
                self._summary_sec += self._exposure_for_target(ot)
                self._summary_count += 1
        self._refresh_summary_label()

    def _recalc_target_summary(self, *_args) -> None:
        """Full recalculation — after a sort, clear, load, or cell edit."""
        targets = self._tgt_model.get_targets()
        self._summary_count = len(targets)
        self._summary_sec = sum(self._exposure_for_target(ot) for ot in targets)
        self._refresh_summary_label()

    def _refresh_summary_label(self) -> None:
        n = self._summary_count
        total_sec = max(0.0, self._summary_sec)   # guard against float drift
        star_str = f"{n} target{'s' if n != 1 else ''}"
        if total_sec <= 0:
            exp_str = "—"
        elif total_sec < 60:
            exp_str = f"{total_sec:.0f} s"
        elif total_sec < 3600:
            exp_str = f"{total_sec / 60:.1f} min"
        else:
            exp_str = f"{int(total_sec // 3600)}h {int((total_sec % 3600) // 60):02d}m"
        self._tgt_summary_lbl.setText(f"{star_str}  │  Total exposure: {exp_str}")


# --- Small widget helpers ---------------------------------------------------

def _bold(label: QLabel) -> QLabel:
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


def _link_button(text: str, on_click) -> QPushButton:
    btn = QPushButton(text)
    btn.setFlat(True)
    btn.setStyleSheet(
        "QPushButton { color: #0078d4; border: none; padding: 0 4px; }"
        "QPushButton:hover { text-decoration: underline; }"
    )
    btn.clicked.connect(on_click)
    return btn


def _option_cb(text: str, checked: bool, tooltip: str) -> QCheckBox:
    cb = QCheckBox(text)
    cb.setChecked(checked)
    cb.setToolTip(tooltip)
    return cb
