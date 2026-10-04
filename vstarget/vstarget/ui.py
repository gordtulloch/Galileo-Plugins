# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Qt panel widgets for the VSTarget plugin's two science sub-panels."""

from __future__ import annotations


def build_vst_page() -> object:
    """Return the Variable Star Planning panel widget.

    The panel itself lives in :mod:`vstarget.planning.page`; this stays the
    entry point ``VSTPlugin.build_page()`` calls, and imports lazily so the
    module is importable without Qt present.
    """
    from vstarget.planning.page import VSTPlanningPage
    return VSTPlanningPage()


def build_vst_analysis_page() -> object:
    """Return the Variable Star Analysis panel widget.

    Input images come from Galileo's own image library (``VST-AN-100``): the
    panel lists every catalogued session of every target designated a variable
    star in Library > Images, and photometry runs over the frames those
    sessions hold. Nothing is browsed for on disk — the library already knows
    where the frames are, which filter each session used, and whether it has
    been calibrated.
    """
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
        QGroupBox, QFileDialog, QFormLayout, QComboBox,
        QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView,
        QApplication,
    )
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtGui import QDoubleValidator

    page = QWidget()
    page.setObjectName("VSTAnalysisPage")
    outer = QVBoxLayout(page)
    outer.setContentsMargins(16, 12, 16, 12)
    outer.setSpacing(8)

    title = QLabel("Variable Star Analysis")
    title.setObjectName("PageTitle")
    outer.addWidget(title)

    # --- Input images: library sessions of designated variable stars ---
    input_box = QGroupBox("Input Images")
    input_layout = QVBoxLayout(input_box)
    input_layout.setContentsMargins(8, 8, 8, 8)
    input_layout.setSpacing(6)

    header_row = QHBoxLayout()
    header_label = QLabel("Variable-star sessions in the image library:")
    refresh_btn = QPushButton("Refresh")
    refresh_btn.setToolTip("Re-read the image library for newly designated targets or sessions")
    header_row.addWidget(header_label, 1)
    header_row.addWidget(refresh_btn)
    input_layout.addLayout(header_row)

    session_tree = QTreeWidget()
    session_tree.setObjectName("VSTAnalysisSessionTree")
    session_tree.setColumnCount(6)
    session_tree.setHeaderLabels(
        ["Target / Session", "Date", "Filter", "Frames", "Telescope", "Exposure"]
    )
    session_tree.setRootIsDecorated(True)
    session_tree.setUniformRowHeights(True)
    session_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    session_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    session_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    session_tree.setMinimumHeight(160)
    input_layout.addWidget(session_tree)

    selection_label = QLabel("")
    selection_label.setObjectName("VSTAnalysisSelectionLabel")
    selection_label.setWordWrap(True)
    input_layout.addWidget(selection_label)

    outer.addWidget(input_box)

    # --- Analysis parameters ---
    params_box = QGroupBox("Analysis Parameters")
    params_form = QFormLayout(params_box)
    params_form.setContentsMargins(8, 8, 8, 8)

    target_edit = QLineEdit()
    target_edit.setPlaceholderText("Filled from the selected session; override for the AAVSO name or AUID")
    params_form.addRow("Target star:", target_edit)

    filter_combo = QComboBox()
    filter_combo.addItems(["V", "B", "R", "I", "U"])
    filter_combo.setToolTip("Used only for a session whose catalogued filter is blank")
    params_form.addRow("Filter band:", filter_combo)

    aperture_edit = QLineEdit("8")
    aperture_edit.setValidator(QDoubleValidator(0.5, 200.0, 2, page))
    aperture_edit.setToolTip(
        "Measuring aperture, in pixels. The sky annulus scales with it (1.5x to 2.5x the radius)."
    )
    params_form.addRow("Aperture radius (px):", aperture_edit)

    outer.addWidget(params_box)

    # --- Run / results ---
    run_row = QHBoxLayout()
    run_btn = QPushButton("Run Photometry")
    stop_btn = QPushButton("Stop")
    stop_btn.setEnabled(False)
    stop_btn.setToolTip("Stop after the frame being measured, keeping the results so far")
    export_btn = QPushButton("Export AAVSO Report...")
    progress_label = QLabel("")
    progress_label.setObjectName("VSTAnalysisProgressLabel")
    run_row.addWidget(run_btn)
    run_row.addWidget(stop_btn)
    run_row.addWidget(export_btn)
    run_row.addWidget(progress_label, 1)
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
    # The running PhotometryThread, if any. A dict because the nested
    # handlers below rebind it and `nonlocal` can't reach a function-local
    # name from a nested closure defined later in the same scope.
    _thread: dict = {"worker": None}

    # --- Session list -----------------------------------------------------

    def _refresh_sessions() -> None:
        """Rebuild the session tree from the library, grouped by target."""
        from vstarget.analysis.library_sessions import (
            LibraryUnavailable,
            list_variable_star_sessions,
        )

        session_tree.clear()
        try:
            sessions = list_variable_star_sessions()
        except LibraryUnavailable as exc:
            selection_label.setText(str(exc))
            return

        if not sessions:
            selection_label.setText(
                "No variable-star sessions found. Designate a target in Library > Images - "
                "right-click the target and choose Add Variable Star - then press Refresh."
            )
            return

        by_target: dict[str, list] = {}
        for session in sessions:
            by_target.setdefault(session.target, []).append(session)

        for target, rows in by_target.items():
            frames = sum(r.frame_count for r in rows)
            plural = "" if len(rows) == 1 else "s"
            parent = QTreeWidgetItem(
                [target, f"{len(rows)} session{plural}", "", str(frames), "", ""]
            )
            parent.setToolTip(0, "Select the target to use all of its sessions")
            session_tree.addTopLevelItem(parent)
            for row in rows:
                child = QTreeWidgetItem([
                    row.session_id[:8],
                    row.date,
                    row.filter_band,
                    str(row.frame_count),
                    row.telescope or row.instrument,
                    row.exposure,
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, row)
                child.setToolTip(0, f"Session {row.session_id}")
                parent.addChild(child)
            parent.setExpanded(True)

        for column in range(1, session_tree.columnCount()):
            session_tree.resizeColumnToContents(column)
        _update_selection_label()

    def _selected_sessions() -> list:
        """The selected sessions in tree order; a selected target means all of its sessions."""
        picked: dict[str, object] = {}
        for i in range(session_tree.topLevelItemCount()):
            parent = session_tree.topLevelItem(i)
            whole_target = parent.isSelected()
            for j in range(parent.childCount()):
                child = parent.child(j)
                if whole_target or child.isSelected():
                    row = child.data(0, Qt.ItemDataRole.UserRole)
                    if row is not None:
                        picked[row.session_id] = row
        return list(picked.values())

    def _update_selection_label() -> None:
        selected = _selected_sessions()
        if not selected:
            selection_label.setText("Select one or more sessions to analyse.")
            return
        frames = sum(s.frame_count for s in selected)
        targets = sorted({s.target for s in selected})
        selection_label.setText(
            f"{len(selected)} session(s), {frames} frame(s) selected - {', '.join(targets)}"
        )
        # Fill the parameter fields from a single-session selection so the common
        # case needs no typing. A wider selection leaves them alone, since each
        # session carries its own target and filter into the run.
        if len(selected) == 1:
            session = selected[0]
            target_edit.setText(session.target)
            index = filter_combo.findText(session.filter_band, Qt.MatchFlag.MatchFixedString)
            if index >= 0:
                filter_combo.setCurrentIndex(index)

    # --- Photometry -------------------------------------------------------

    def _aperture_radius() -> float | None:
        """The aperture field as a number, or ``None`` if it can't be read as one."""
        try:
            radius = float(aperture_edit.text().strip())
        except ValueError:
            return None
        return radius if radius > 0 else None

    def _set_running(running: bool) -> None:
        run_btn.setEnabled(not running)
        stop_btn.setEnabled(running)
        export_btn.setEnabled(not running)
        session_tree.setEnabled(not running)
        refresh_btn.setEnabled(not running)

    def _show_progress(done: int, total: int, label: str) -> None:
        if total and done < total:
            progress_label.setText(f"Measuring frame {done + 1} of {total} - {label}")
        else:
            progress_label.setText("")

    def _fill_results() -> None:
        results_table.setRowCount(0)
        for m in _measurements:
            r = results_table.rowCount()
            results_table.insertRow(r)
            results_table.setItem(r, 0, QTableWidgetItem(m.target))
            results_table.setItem(r, 1, QTableWidgetItem(f"{m.jd:.5f}"))
            results_table.setItem(r, 2, QTableWidgetItem(f"{m.magnitude:.3f}"))
            results_table.setItem(r, 3, QTableWidgetItem(f"{m.uncertainty:.3f}"))
            results_table.setItem(r, 4, QTableWidgetItem(m.filter_band))

    def _run_photometry() -> None:
        from PySide6.QtWidgets import QMessageBox

        from vstarget.analysis.library_sessions import LibraryUnavailable
        from vstarget.analysis.runner import PhotometryThread, build_jobs

        if _thread.get("worker") is not None:
            return  # a run is already going; Stop ends it

        selected = _selected_sessions()
        if not selected:
            QMessageBox.information(page, "VSTarget", "Select at least one library session first.")
            return

        radius = _aperture_radius()
        if radius is None:
            QMessageBox.information(
                page, "VSTarget", "Aperture radius must be a positive number of pixels."
            )
            aperture_edit.setFocus()
            return

        # Resolving the frames reads the library and the plan store, so it stays
        # here on the UI thread, where their connections live; the measuring,
        # which is the slow part, goes to a worker (core NFR-PERF-020).
        try:
            plan = build_jobs(
                selected,
                target_override=target_edit.text(),
                fallback_band=filter_combo.currentText(),
            )
        except LibraryUnavailable as exc:
            QMessageBox.warning(page, "Image library unavailable", str(exc))
            return

        if not plan.jobs:
            progress_label.setText("")
            if plan.sessions_without_frames:
                QMessageBox.information(
                    page,
                    "VSTarget",
                    "No frames found on disk for:\n" + "\n".join(plan.sessions_without_frames),
                )
            else:
                QMessageBox.information(
                    page, "VSTarget", "The selected sessions hold no frames to measure."
                )
            return

        worker = PhotometryThread(plan.jobs, aperture_radius=radius, parent=page)
        _thread["worker"] = worker

        def _done(measurements: list) -> None:
            _measurements.clear()
            _measurements.extend(measurements)
            _fill_results()
            _finish()
            if worker.cancelled:
                progress_label.setText(
                    f"Stopped after {len(measurements)} of {plan.frame_count} frame(s)."
                )
            else:
                progress_label.setText(f"Measured {len(measurements)} frame(s).")
            if plan.sessions_without_frames:
                QMessageBox.information(
                    page,
                    "VSTarget",
                    "No frames found on disk for:\n" + "\n".join(plan.sessions_without_frames),
                )

        def _failed(message: str) -> None:
            _finish()
            progress_label.setText("")
            QMessageBox.warning(page, "Photometry failed", message)

        def _finish() -> None:
            _set_running(False)
            _thread["worker"] = None
            worker.deleteLater()

        worker.progress.connect(_show_progress)
        worker.finished_ok.connect(_done)
        worker.error.connect(_failed)

        _set_running(True)
        progress_label.setText(f"Measuring {plan.frame_count} frame(s)...")
        worker.start()

    def _stop_photometry() -> None:
        worker = _thread.get("worker")
        if worker is not None:
            worker.cancel()
            progress_label.setText("Stopping after the current frame...")

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
            from vstarget.settings import VstSettings
            svc = VariableStarAnalysis()
            # WebObs rejects a submission with no observer code, so carry the
            # one configured in the planning panel's Settings dialog.
            svc.export_aavso_report(
                _measurements, path, observer_code=VstSettings().observer_code
            )
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(page, "Export failed", str(exc))

    refresh_btn.clicked.connect(_refresh_sessions)
    session_tree.itemSelectionChanged.connect(_update_selection_label)
    run_btn.clicked.connect(_run_photometry)
    stop_btn.clicked.connect(_stop_photometry)
    export_btn.clicked.connect(_export_report)

    def _abort_run() -> None:
        """Stop a run that is still going when the panel or the app goes away.

        A ``QThread`` deleted while still running takes the process with it, so
        the worker is cancelled and waited for — it checks between frames, so
        the wait is one frame long, with a bounded timeout in case a frame
        wedges in a library call.
        """
        worker = _thread.get("worker")
        if worker is None:
            return
        worker.cancel()
        worker.wait(10_000)

    class _PanelEvents(QObject):
        """Re-reads the library when the panel is shown; stops a run when it closes.

        A target designated in Library > Images while this panel sits in the
        background should be there when the user switches to it, without
        having to press Refresh. The queries are cheap, and the selection is
        rebuilt rather than preserved — the panel is only being opened.
        """

        def eventFilter(self, watched: object, event: object) -> bool:
            if event.type() == QEvent.Type.Show:
                _refresh_sessions()
            elif event.type() == QEvent.Type.Close:
                _abort_run()
            return False

    panel_events = _PanelEvents(page)
    page.installEventFilter(panel_events)
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(_abort_run)

    # Exposed so the host (and a test) can drive the list without reaching into
    # the widget tree, and to keep the filter alive with the page.
    page.refresh_sessions = _refresh_sessions  # type: ignore[attr-defined]
    page.selected_sessions = _selected_sessions  # type: ignore[attr-defined]
    page.session_tree = session_tree  # type: ignore[attr-defined]
    page.abort_run = _abort_run  # type: ignore[attr-defined]
    page._panel_events = panel_events  # type: ignore[attr-defined]

    _refresh_sessions()

    return page
