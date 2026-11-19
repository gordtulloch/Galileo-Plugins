# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The plugin's settings, kept in Galileo's database under ``plugin.SkyshotSolverPlugin`` (SKY-070)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyshot_solver import data

MODES = ("auto", "standard", "deep")
DEADLINE_RANGE_S = (1, 600)
DEFAULT_DEADLINE_S = 30


class SkyshotSettings:
    """Thin typed view over a ``PluginSettings`` (``ctx.settings()``): every read falls back to a
    default and bad stored values are clamped, so the backend never sees nonsense."""

    def __init__(self, store: Any) -> None:
        self._store = store

    # The skyshot program; blank means "look for it".
    @property
    def executable(self) -> str:
        return str(self._store.get("executable", "") or "").strip()

    @executable.setter
    def executable(self, value: str) -> None:
        self._store.set("executable", value.strip())

    # Where downloaded indexes are kept; blank means the default folder under Galileo's data folder.
    @property
    def data_root(self) -> Path:
        configured = str(self._store.get("data_root", "") or "").strip()
        if configured:
            return Path(configured)
        from galileo.platform import get_data_dir
        return get_data_dir() / "skyshot"

    @property
    def data_root_text(self) -> str:
        return str(self._store.get("data_root", "") or "").strip()

    @data_root_text.setter
    def data_root_text(self, value: str) -> None:
        self._store.set("data_root", value.strip())

    # A index folder chosen by hand (for one built or installed outside the plugin); blank means the downloaded one.
    @property
    def index_override(self) -> str:
        return str(self._store.get("index_path", "") or "").strip()

    @index_override.setter
    def index_override(self, value: str) -> None:
        self._store.set("index_path", value.strip())

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
        """The index folder to solve against, or ``None`` when there is none."""
        if self.index_override:
            path = Path(self.index_override)
            return path if path.is_dir() else None
        return data.index_path(self.data_root)
