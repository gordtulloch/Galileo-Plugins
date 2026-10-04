# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Settings dialog behind the planning panel's Settings button.

Covers what the planning panel itself needs: the AAVSO API credentials used to
download targets, the site the observability filter is evaluated for, and the
capture defaults new plan targets inherit. The standalone application's SFTP
and SeeStar tabs are deliberately absent - image retrieval and device control
are Galileo's own, not this panel's.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vstarget.planning.models import TELESCOPE_PRESETS


class VstSettingsDialog(QDialog):
    """Tabbed settings for the variable-star planning panel."""

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("VSTarget Settings")
        self.setMinimumWidth(480)
        self._build_ui()
        self._load()

    # --- Construction -----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_api_tab(), "API")
        tabs.addTab(self._build_telescope_tab(), "Telescope")
        tabs.addTab(self._build_defaults_tab(), "Capture Defaults")
        tabs.addTab(self._build_analysis_tab(), "Analysis")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_api_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        self._api_key_edit.setPlaceholderText("Paste your AAVSO API key here")

        show_btn = QPushButton("Show")
        show_btn.setCheckable(True)
        show_btn.setFixedWidth(60)
        show_btn.toggled.connect(
            lambda on: self._api_key_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )

        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key_edit)
        key_row.addWidget(show_btn)
        form.addRow("API Key:", key_row)

        self._observer_code_edit = QLineEdit()
        self._observer_code_edit.setPlaceholderText("e.g. TGOR")
        form.addRow("AAVSO Observer Code:", self._observer_code_edit)

        info = QLabel(
            'Don\'t have a key? '
            '<a href="https://targettool.aavso.org/TargetTool/default/user/register">'
            'Register at AAVSO Target Tool</a>'
        )
        info.setOpenExternalLinks(True)
        info.setTextFormat(Qt.RichText)
        form.addRow("", info)
        return tab

    def _build_telescope_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(list(TELESCOPE_PRESETS.keys()))
        self._preset_combo.currentTextChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset_combo, 1)
        layout.addLayout(preset_row)

        box = QGroupBox("Location")
        form = QFormLayout(box)

        self._lat_spin = QDoubleSpinBox()
        self._lat_spin.setRange(-90.0, 90.0)
        self._lat_spin.setDecimals(4)
        self._lat_spin.setSuffix("°")
        self._lat_spin.setToolTip("North positive, South negative")

        self._lon_spin = QDoubleSpinBox()
        self._lon_spin.setRange(-180.0, 180.0)
        self._lon_spin.setDecimals(4)
        self._lon_spin.setSuffix("°")
        self._lon_spin.setToolTip("East positive, West negative")

        self._alt_spin = QDoubleSpinBox()
        self._alt_spin.setRange(0.0, 90.0)
        self._alt_spin.setDecimals(1)
        self._alt_spin.setSuffix("°")
        self._alt_spin.setToolTip(
            "Minimum altitude above the horizon for a target to count as observable"
        )

        self._sun_spin = QDoubleSpinBox()
        self._sun_spin.setRange(-18.0, 0.0)
        self._sun_spin.setDecimals(1)
        self._sun_spin.setSuffix("°")
        self._sun_spin.setToolTip("Sun altitude defining dusk/dawn (typically -5° to -12°)")

        form.addRow("Latitude:", self._lat_spin)
        form.addRow("Longitude:", self._lon_spin)
        form.addRow("Min target altitude:", self._alt_spin)
        form.addRow("Sun altitude (dusk/dawn):", self._sun_spin)
        layout.addWidget(box)
        layout.addStretch()
        return tab

    def _build_defaults_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._def_filter = QLineEdit()
        self._def_count = QLineEdit()
        self._def_interval = QLineEdit()
        self._def_binning = QLineEdit()

        form.addRow("Filter(s):", self._def_filter)
        form.addRow("Count(s):", self._def_count)
        form.addRow("Interval(s) [s]:", self._def_interval)
        form.addRow("Binning:", self._def_binning)

        hint = QLabel(
            "Use comma-separated values for multi-filter setups.\n"
            "All four fields must have the same number of values.\n"
            "Example:  V,B,I  /  4,4,4  /  30,30,30  /  1,1,1"
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", hint)
        return tab

    def _build_analysis_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._workdir_edit = QLineEdit()
        self._workdir_edit.setPlaceholderText("Folder of FITS images for analysis")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_workdir)

        row = QHBoxLayout()
        row.addWidget(self._workdir_edit)
        row.addWidget(browse_btn)
        form.addRow("Working directory:", row)

        hint = QLabel(
            "Shown in the planning panel's status bar and used as the starting "
            "folder for variable-star analysis."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", hint)
        return tab

    # --- Behaviour --------------------------------------------------------

    def _browse_workdir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select working directory", self._workdir_edit.text()
        )
        if folder:
            self._workdir_edit.setText(folder)

    def _on_preset_changed(self, name: str) -> None:
        preset = TELESCOPE_PRESETS.get(name)
        if not preset or name == "Custom":
            return
        self._lat_spin.setValue(preset["latitude"])
        self._lon_spin.setValue(preset["longitude"])
        self._alt_spin.setValue(preset["target_altitude"])
        self._sun_spin.setValue(preset["sun_altitude"])

    def _load(self) -> None:
        s = self.settings
        self._api_key_edit.setText(s.api_key)
        self._observer_code_edit.setText(s.observer_code)

        name = s.telescope_name
        if name in TELESCOPE_PRESETS:
            self._preset_combo.setCurrentText(name)
        else:
            self._preset_combo.setCurrentText("Custom")
        # Set the spins after the combo so a preset's signal doesn't clobber
        # a custom site the user previously saved.
        self._lat_spin.setValue(s.latitude)
        self._lon_spin.setValue(s.longitude)
        self._alt_spin.setValue(s.target_altitude)
        self._sun_spin.setValue(s.sun_altitude)

        self._def_filter.setText(s.default_filters)
        self._def_count.setText(s.default_counts)
        self._def_interval.setText(s.default_intervals)
        self._def_binning.setText(s.default_binning)
        self._workdir_edit.setText(s.analysis_working_dir)

    def _save_and_accept(self) -> None:
        s = self.settings
        s.api_key = self._api_key_edit.text().strip()
        s.observer_code = self._observer_code_edit.text().strip()

        s.telescope_name = self._preset_combo.currentText()
        s.latitude = self._lat_spin.value()
        s.longitude = self._lon_spin.value()
        s.target_altitude = self._alt_spin.value()
        s.sun_altitude = self._sun_spin.value()

        s.default_filters = self._def_filter.text().strip()
        s.default_counts = self._def_count.text().strip()
        s.default_intervals = self._def_interval.text().strip()
        s.default_binning = self._def_binning.text().strip()
        s.analysis_working_dir = self._workdir_edit.text().strip()
        s.sync()
        self.accept()
