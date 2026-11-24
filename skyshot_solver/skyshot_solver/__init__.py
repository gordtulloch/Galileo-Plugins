# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""skyshot_solver — Galileo first-party plugin adding the Skyshot blind plate solver (SKY-010 … SKY-070).

Skyshot (github.com/ashtree74/skyshot-solver) is an optional solver: it appears beside ASTAP and the
others under Options > Solve and on the Solve screen once this plugin is installed. Its page under
Options > Skyshot sets the program path and downloads the star index.
"""

from __future__ import annotations

from galileo.plugins import PluginBase


class SkyshotSolverPlugin(PluginBase):
    """Registers the Skyshot solver backend and its Options page."""

    name = "SkyshotSolverPlugin"
    version = "0.1.1"
    api_version = "1"
    options_label = "Skyshot"

    def __init__(self) -> None:
        self._ctx = None

    def activate(self, ctx) -> None:
        from skyshot_solver.backend import SkyshotBackend
        from skyshot_solver.settings import SkyshotSettings
        self._ctx = ctx
        ctx.register_solver_backend(SkyshotBackend(SkyshotSettings(ctx.settings())))

    def deactivate(self) -> None:
        pass

    def build_options_page(self):
        if self._ctx is None:
            return None
        from skyshot_solver.settings import SkyshotSettings
        from skyshot_solver.ui import SettingsPage
        return SettingsPage(SkyshotSettings(self._ctx.settings()))


__all__ = ["SkyshotSolverPlugin"]
