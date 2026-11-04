# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The WebConnect plugin shell (WCON-010, 020, 040; SDD §3.1).

Master half only. On a node that is a spoke it registers nothing and opens nothing (WCON-020). Disabling gives
Connect Link back and leaves the approved browsers and options in the database, so re-enabling needs no
re-pairing (WCON-040).
"""

from __future__ import annotations

import logging
from typing import Any

from galileo.plugins import PluginBase

from webconnect import PLUGIN, __version__
from webconnect.browsers import DECLARED, BrowserRegistry
from webconnect.delegate import Delegate

logger = logging.getLogger(__name__)

NODECONNECT_NAMESPACE = "plugin.NodeConnect"          # node-local; set once this node designates a Master


class _Leases:
    """The engine's lease broker, looked up when used (it exists only while an engine is running)."""

    @staticmethod
    def on_disconnect(holder: str, reason: str = "disconnected") -> list[str]:
        from galileo.control_surface.service import get_control_surface
        svc = get_control_surface()
        return svc.leases.on_disconnect(holder, reason) if svc is not None else []


def is_spoke() -> bool:
    """A node that has designated a NodeConnect Master is a spoke (WCON-020)."""
    from galileo import settings_store
    return bool(settings_store.get_setting(NODECONNECT_NAMESPACE, "master_address", ""))


class Runtime:
    """The Master half's object graph. Tests build one around fakes; ``build_runtime`` uses core's."""

    def __init__(self, host: Any, registry: BrowserRegistry, delegate: Delegate) -> None:
        self._host, self.browsers, self.delegate = host, registry, delegate
        self._acquired = False

    def start(self) -> None:
        self.stop()
        self._host.acquire(PLUGIN, DECLARED, self._install)
        self._acquired = True

    def stop(self) -> None:
        if self._acquired:
            self._acquired = False
            try:
                self._host.release(PLUGIN)
            except Exception:
                logger.exception("WebConnect could not release Connect Link")

    def _install(self, server) -> None:
        self.delegate.register(server.routes)

    @property
    def running(self) -> bool:
        return self._acquired


def build_runtime(ctx) -> Runtime:
    from galileo.connect_link.host import get_host
    from galileo.control_surface.service import get_control_surface

    host = get_host()
    service = host.service
    registry = BrowserRegistry(service.peers, service.pairing, _Leases())
    return Runtime(host, registry, Delegate(service.peers, get_control_surface))


class WebConnectPlugin(PluginBase):
    name = "WebConnect"                               # the settings namespace is ``plugin.WebConnect``
    version = __version__
    api_version = "1"
    options_label = "WebConnect"

    def __init__(self) -> None:
        self.runtime: Runtime | None = None
        self.inert_reason = ""

    def activate(self, ctx) -> None:
        try:
            if is_spoke():
                self.inert_reason = "WebConnect belongs on the Master. This node is a spoke, so it is switched off here."
                logger.info(self.inert_reason)
                return
            self.inert_reason = ""
            self.runtime = build_runtime(ctx)
            self.runtime.start()
        except Exception:                             # a failing plugin never takes Galileo with it
            logger.exception("WebConnect could not start; it stays inert")
            self.runtime = None

    def deactivate(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None

    def on_enabled_changed(self, enabled: bool) -> None:
        """A disabled plugin is inert: no routes, no listener (PLUG-060, WCON-040)."""
        if self.runtime is None:
            return
        try:
            self.runtime.start() if enabled else self.runtime.stop()
        except Exception:
            logger.exception("WebConnect could not change state")
