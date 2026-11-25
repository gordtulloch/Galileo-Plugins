# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The plugin's settings and fixed locations (SKY-070, SKY-120).

Settings live in Galileo's database under ``plugin.SkyshotSolverPlugin``. Where things are kept is not
a setting: everything the plugin downloads goes under one folder inside Galileo's own data folder, so
nobody has to be asked for a path::

    <Galileo data folder>/skyshot/
        engine/<version>-<commit>/…       the downloaded Skyshot program
        <data release>/index, index-deep  the downloaded star indexes
        program/                          a Skyshot program you built yourself (used first)
        download-cache/                   partial downloads
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyshot_solver import data, engine

MODES = ("auto", "standard", "deep")
DEADLINE_RANGE_S = (1, 600)
DEFAULT_DEADLINE_S = 30


class SkyshotSettings:
    """Thin typed view over a ``PluginSettings`` (``ctx.settings()``): every read falls back to a
    default and bad stored values are clamped, so the backend never sees nonsense. *root* replaces
    the plugin's folder (tests only)."""

    def __init__(self, store: Any, root: Path | None = None) -> None:
        self._store = store
        self._root = root

    @property
    def data_root(self) -> Path:
        """The one folder everything is kept under (see the module docstring)."""
        if self._root is not None:
            return self._root
        from galileo.platform import get_data_dir
        return get_data_dir() / "skyshot"

    @property
    def program_dir(self) -> Path:
        """Where to put a Skyshot program built by hand; used before the downloaded one."""
        return engine.program_dir(self.data_root)

    @property
    def mode(self) -> str:
        value = str(self._store.get("mode", "auto"))
        return value if value in MODES else "auto"

    @mode.setter
    def mode(self, value: str) -> None:
        self._store.set("mode", value if value in MODES else "auto")

    @property
    def deadline_s(self) -> int:
        try:
            value = int(self._store.get("deadline_s", DEFAULT_DEADLINE_S))
        except (TypeError, ValueError):
            return DEFAULT_DEADLINE_S
        return max(DEADLINE_RANGE_S[0], min(DEADLINE_RANGE_S[1], value))

    @deadline_s.setter
    def deadline_s(self, value: int) -> None:
        self._store.set("deadline_s", int(max(DEADLINE_RANGE_S[0], min(DEADLINE_RANGE_S[1], value))))

    def resolved_index(self) -> Path | None:
        """The index folder to solve against, or ``None`` when none is downloaded."""
        return data.find_index(self.data_root)
