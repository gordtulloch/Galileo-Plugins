# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The NodeConnect plugin shell (NCON-010, NCON-310, NCON-530)."""

from __future__ import annotations

import logging

from galileo.plugins import PluginBase

from nodeconnect import __version__
from nodeconnect.runtime import Runtime, build_runtime

logger = logging.getLogger(__name__)


class NodeConnectPlugin(PluginBase):
    # The settings namespace is ``plugin.<name>``; core's registry lists ``plugin.NodeConnect`` as
    # node-local, so this node's Master and credentials never replicate.
    name = "NodeConnect"
    version = __version__
    api_version = "1"

    def __init__(self) -> None:
        self.runtime: Runtime | None = None

    def activate(self, ctx) -> None:
        try:
            self.runtime = build_runtime(ctx)
            self.runtime.start()
        except Exception:                                   # a failing plugin never takes Galileo with it
            logger.exception("NodeConnect could not start; it stays inert")
            self.runtime = None

    def deactivate(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None

    def on_enabled_changed(self, enabled: bool) -> None:
        """A disabled plugin is inert: no thread, no routes (PLUG-060, NCON-310)."""
        if self.runtime is None:
            return
        try:
            if enabled:
                self.runtime.start()
            else:
                self.runtime.stop()
        except Exception:
            logger.exception("NodeConnect could not change state")
