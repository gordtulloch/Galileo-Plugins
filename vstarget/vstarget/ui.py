# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Qt panel widgets for the VSTarget plugin's two science sub-panels."""
"""Test

from __future__ import annotations


def build_vst_page() -> object:
    """Return the Variable Star Planning panel widget."""
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
        QSplitter, QGroupBox, QFormLayout, QComboBox,
    )
    from PySide6.QtCore import Qt

    _planner = None

    def _get_planner():
        nonlocal _planner
        if _planner is None:
            from vstarget.planning.planner import VariableStarPlanner
            _planner = VariableStarPlanner()
        return _planner

    page = QWidget()
    page.setObjectName("VSTPage")
    outer = QVBoxLayout(page)
    outer.setContentsMargins(16, 12, 16, 12)
    outer.setSpacing(8)

    title = QLabel("Variable Star Planning")
    title.setObjectName("PageTitle")
    outer.addWidget(title)

    splitter = QSplitter(Qt.Orientation.Vertical)

    # --- Target download ---
    search_box = QGroupBox("AAVSO Target Download")
    search_layout = QVBoxLayout(search_box)
    search_layout.setContentsMargins(8, 8, 8, 8)
    search_layout.setSpacing(6)

    row = QHBoxLayout()
    row.addWidget(QLabel("Observer section:"))
    section_edit = QLineEdit()
    section_edit.setPlaceholderText("leave blank for all sections")
    row.addWidget(section_edit, 1)
    fetch_btn = QPushButton("Download from AAVSO")
    row.addWidget(fetch_btn)
    search_layout.addLayout(row)

    status_label = QLabel("")
    status_label.setObjectName("SubtleLabel")
    search_layout.addWidget(status_label)

    targets_table = QTableWidget(0, 5)
    targets_table.setHorizontalHeaderLabels(["Star", "Type", "RA (°)", "Dec (°)", "Priority"])
    targets_table.horizontalHeader().setStretchLastSection(True)
    targets_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    targets_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    search_layout.addWidget(targets_table, 1)

    splitter.addWidget(search_box)

    # --- Observation plan ---
    plan_box = QGroupBox("Observation Plan")
    plan_form_widget = QWidget()
    plan_form = QFormLayout(plan_form_widget)
    plan_form.setContentsMargins(8, 8, 8, 8)

    filter_combo = QComboBox()
    filter_combo.addItems(["V", "B", "R", "I", "U", "B+V", "All BVRI"])
    plan_form.addRow("Filter(s):", filter_combo)

    exposure_edit = QLineEdit()
    exposure_edit.setPlaceholderText("seconds (e.g. 60)")
    plan_form.addRow("Exposure time:", exposure_edit)

    repeats_edit = QLineEdit("3")
    plan_form.addRow("Repeats per filter:", repeats_edit)

    plan_vbox = QVBoxLayout(plan_box)
    plan_vbox.setContentsMargins(8, 8, 8, 8)
    plan_vbox.addWidget(plan_form_widget)

    btn_row = QHBoxLayout()
    add_seq_btn = QPushButton("Add to Session")
    export_btn = QPushButton("Export ACP Script…")
    btn_row.addWidget(add_seq_btn)
    btn_row.addWidget(export_btn)
    btn_row.addStretch(1)
    plan_vbox.addLayout(btn_row)

    splitter.addWidget(plan_box)
    outer.addWidget(splitter, 1)

    def _populate_table(targets) -> None:
        targets_table.setRowCount(0)
        for t in targets:
            r = targets_table.rowCount()
            targets_table.insertRow(r)
            targets_table.setItem(r, 0, QTableWidgetItem(getattr(t, "name", "")))
            targets_table.setItem(r, 1, QTableWidgetItem(getattr(t, "type_code", "")))
            ra = getattr(t, "ra_deg", None)
            dec = getattr(t, "dec_deg", None)
            targets_table.setItem(r, 2, QTableWidgetItem(f"{ra:.4f}" if ra is not None else ""))
            targets_table.setItem(r, 3, QTableWidgetItem(f"{dec:.4f}" if dec is not None else ""))
            prio = getattr(t, "priority", "")
            targets_table.setItem(r, 4, QTableWidgetItem(str(prio)))
        status_label.setText(f"{len(targets)} target(s) loaded.")

    def _fetch() -> None:
        import asyncio
        section = section_edit.text().strip()
        status_label.setText("Downloading…")
        QApplication_processEvents()
        try:
            planner = _get_planner()
            asyncio.run(planner.sync_from_aavso(section=section))
            targets = planner.get_targets(section=section)
            _populate_table(targets)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            status_label.setText("Download failed.")
            QMessageBox.warning(page, "AAVSO download failed", str(exc))

    def _export_script() -> None:
        import asyncio
        from pathlib import Path
        sel = targets_table.selectedItems()
        if not sel:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(page, "VSTarget", "Select a target in the table first.")
            return
        row_idx = targets_table.currentRow()
        star_name = targets_table.item(row_idx, 0).text()
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(page, "Save ACP Script", f"{star_name}.txt", "ACP scripts (*.txt);;All files (*)")
        if not path:
            return
        try:
            planner = _get_planner()
            targets = planner.get_targets()
            target = next((t for t in targets if t.name == star_name), None)
            if target is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(page, "VSTarget", "Target not found in planner.")
                return
            exp = float(exposure_edit.text() or "60")
            repeats = int(repeats_edit.text() or "3")
            filters_str = filter_combo.currentText()
            filter_names = [f.strip() for f in filters_str.replace("+", ",").split(",")]
            from vstarget.planning.models import ObservationPlan
            plan = ObservationPlan(target_name=target.name, ra_deg=target.ra_deg, dec_deg=target.dec_deg)
            for fn in filter_names:
                plan.add_filter_config(fn, exp, repeats)
            from vstarget.planning.script_exporter import export_acp_script
            export_acp_script([plan], Path(path))
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(page, "Export failed", str(exc))

    # processEvents helper for the status label
    def QApplication_processEvents():
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass

    fetch_btn.clicked.connect(_fetch)
    export_btn.clicked.connect(_export_script)

    return page


def build_vst_analysis_page() -> object:
    """Return the Variable Star Analysis panel widget."""
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
        QGroupBox, QFileDialog, QFormLayout, QComboBox,
    )
    from PySide6.QtCore import Qt

    page = QWidget()
    page.setObjectName("VSTAnalysisPage")
    outer = QVBoxLayout(page)
    outer.setContentsMargins(16, 12, 16, 12)
    outer.setSpacing(8)

    title = QLabel("Variable Star Analysis")
    title.setObjectName("PageTitle")
    outer.addWidget(title)

    # --- Input files ---
    input_box = QGroupBox("Input Images")
    input_layout = QVBoxLayout(input_box)
    input_layout.setContentsMargins(8, 8, 8, 8)
    input_layout.setSpacing(6)

    row = QHBoxLayout()
    path_edit = QLineEdit()
    path_edit.setPlaceholderText("Folder of FITS files…")
    path_edit.setReadOnly(True)
    browse_btn = QPushButton("Browse…")
    row.addWidget(path_edit, 1)
    row.addWidget(browse_btn)
    input_layout.addLayout(row)
    outer.addWidget(input_box)

    # --- Analysis parameters ---
    params_box = QGroupBox("Analysis Parameters")
    params_form = QFormLayout(params_box)
    params_form.setContentsMargins(8, 8, 8, 8)

    target_edit = QLineEdit()
    target_edit.setPlaceholderText("Star name or AUID")
    params_form.addRow("Target star:", target_edit)

    filter_combo = QComboBox()
    filter_combo.addItems(["V", "B", "R", "I", "U"])
    params_form.addRow("Filter band:", filter_combo)

    aperture_edit = QLineEdit("8")
    params_form.addRow("Aperture radius (px):", aperture_edit)

    outer.addWidget(params_box)

    # --- Run / results ---
    run_row = QHBoxLayout()
    run_btn = QPushButton("Run Photometry")
    export_btn = QPushButton("Export AAVSO Report…")
    run_row.addWidget(run_btn)
    run_row.addWidget(export_btn)
    run_row.addStretch(1)
    outer.addLayout(run_row)

    results_label = QLabel("Results")
    results_label.setObjectName("SectionLabel")
    outer.addWidget(results_label)

    results_table = QTableWidget(0, 5)
    results_table.setHorizontalHeaderLabels(["Target", "JD", "Magnitude", "Uncertainty", "Filter"])
    results_table.horizontalHeader().setStretchLastSection(True)
    results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    outer.addWidget(results_table, 1)

    _measurements: list = []

    def _browse() -> None:
        folder = QFileDialog.getExistingDirectory(page, "Select FITS image folder")
        if folder:
            path_edit.setText(folder)

    def _run_photometry() -> None:
        import asyncio
        from pathlib import Path
        folder = path_edit.text().strip()
        if not folder:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(page, "VSTarget", "Choose an image folder first.")
            return
        target = target_edit.text().strip() or "unknown"
        band = filter_combo.currentText()
        try:
            from vstarget.analysis import VariableStarAnalysis
            svc = VariableStarAnalysis()
            fits_files = sorted(
                list(Path(folder).glob("*.fits")) + list(Path(folder).glob("*.fit"))
            )
            if not fits_files:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(page, "VSTarget", "No FITS files found in that folder.")
                return

            _measurements.clear()
            for f in fits_files:
                result = asyncio.run(svc.run_photometry(f, {"name": target}, filter_band=band))
                _measurements.append(result)

            results_table.setRowCount(0)
            for m in _measurements:
                r = results_table.rowCount()
                results_table.insertRow(r)
                results_table.setItem(r, 0, QTableWidgetItem(m.target))
                results_table.setItem(r, 1, QTableWidgetItem(f"{m.jd:.5f}"))
                results_table.setItem(r, 2, QTableWidgetItem(f"{m.magnitude:.3f}"))
                results_table.setItem(r, 3, QTableWidgetItem(f"{m.uncertainty:.3f}"))
                results_table.setItem(r, 4, QTableWidgetItem(m.filter_band))
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(page, "Photometry failed", str(exc))

    def _export_report() -> None:
        if not _measurements:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(page, "VSTarget", "Run photometry first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            page, "Save AAVSO Report", "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            from vstarget.analysis import VariableStarAnalysis
            svc = VariableStarAnalysis()
            svc.export_aavso_report(_measurements, path)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(page, "Export failed", str(exc))

    browse_btn.clicked.connect(_browse)
    run_btn.clicked.connect(_run_photometry)
    export_btn.clicked.connect(_export_report)

    return page
