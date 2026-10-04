# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""vstarget — Galileo first-party plugin for variable star planning and analysis (VST, VST-AN).

Installed via Options > Plugins; discovered by Galileo's plugin loader at startup.
"""

from __future__ import annotations

from galileo.plugins import PluginBase
from vstarget.analysis import VariableStarAnalysis
from vstarget.analysis.exposure import ExposureTimeCalculator
from vstarget.analysis.finder_chart import FinderChartRenderer
from vstarget.analysis.photometry import PhotometryResult, StandardFieldObservation
from vstarget.planning import VariableStarPlanner
from vstarget.planning.models import (
    AavsoTarget,
    ObservationPlan,
    TransformationCoefficients,
)


class VSTPlugin(PluginBase):
    """Variable star planning plugin (VST)."""
    name = "VSTPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "primary"
    panel_label = "Variable Stars"

    def activate(self, ctx) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def build_page(self):
        from vstarget.ui import build_vst_page
        return build_vst_page()


class VSTAnalysisPlugin(PluginBase):
    """Variable star analysis plugin (VST-AN)."""
    name = "VSTAnalysisPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "secondary"
    panel_label = "VS Analysis"

    def activate(self, ctx) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def build_page(self):
        from vstarget.ui import build_vst_analysis_page
        return build_vst_analysis_page()


__all__ = [
    "AavsoTarget",
    "ExposureTimeCalculator",
    "FinderChartRenderer",
    "ObservationPlan",
    "PhotometryResult",
    "StandardFieldObservation",
    "TransformationCoefficients",
    "VSTAnalysisPlugin",
    "VSTPlugin",
    "VariableStarAnalysis",
    "VariableStarPlanner",
]
