# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The MobileConnect plugin shell. Master node only: on a spoke it registers nothing and opens nothing.

Disabling it gives Connect Link back and leaves the pairings in the database, so re-enabling needs no re-pairing.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from galileo.plugins import PluginBase

from mobileconnect import PLUGIN, __version__
from mobileconnect.clients import DECLARED, ClientRegistry
from mobileconnect.events import EventPump
from mobileconnect.routes import Routes

logger = logging.getLogger(__name__)

NODECONNECT_NAMESPACE = "plugin.NodeConnect"          # node-local; set once this node designates a Master


def is_spoke() -> bool:
    """A node that has designated a NodeConnect Master is a spoke. Apps talk to the Master."""
    from galileo import settings_store
    return bool(settings_store.get_setting(NODECONNECT_NAMESPACE, "master_address", ""))


class Runtime:
    """The plugin's object graph. Tests build one around fakes; ``build_runtime`` uses core's."""

    def __init__(self, host: Any, peers: Any, clients: ClientRegistry, surface: Any, node_name: Any) -> None:
        self._host, self._peers, self.clients, self._surface = host, peers, clients, surface
        self._node_name = node_name
        self._server: Any = None
        self._acquired = False
        self.pump = EventPump(surface, self._describe, self._broadcast, self._watching)
        self.routes = Routes(peers, surface, node_name, self.pump, self._publish)

    # -- what the pump and routes need from the listener ---------------------------------------
    def _describe(self, holder: str) -> tuple[str | None, str | None]:
        return self.routes.describe_holder(holder)

    def _broadcast(self, data: dict[str, Any]) -> None:
        if self._server is not None:
            self._server.streams.broadcast(PLUGIN, data)

    def _publish(self, peer_id: str, data: dict[str, Any]) -> int | None:
        return self._server.streams.publish(peer_id, data) if self._server is not None else None

    def _watching(self) -> bool:
        """Sampling is for apps: nobody connected, nothing read."""
        if self._server is None:
            return False
        return any(self._server.streams.connected(p.peer_id) for p in self._peers.list(PLUGIN))

    # -- lifecycle -----------------------------------------------------------------------------
    def install(self, server: Any) -> None:
        """Connect Link calls this on its own loop, now and after every listener restart."""
        self._server = server
        self.routes.register(server.routes)
        self.clients.set_stream_closer(server.streams.close_peer)
        self.pump.start()

    def start(self) -> None:
        if is_spoke():
            logger.info("MobileConnect stays off: this node is a spoke, and apps talk to its Master")
            return
        self.stop()
        self._host.acquire(PLUGIN, DECLARED, self.install)
        self._acquired = True

    def stop(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        try:
            self._host.call(self.pump.stop())
            self._host.release(PLUGIN)
        except Exception:
            logger.exception("MobileConnect could not release Connect Link")
        self._server = None
        self.clients.set_stream_closer(None)

    @property
    def running(self) -> bool:
        return self._acquired


def build_runtime(ctx: Any) -> Runtime:
    from galileo.connect_link.host import get_host
    from galileo.control_surface.service import get_control_surface

    host = get_host()
    service = host.service

    def leases() -> Any:
        svc = get_control_surface()
        return svc.leases if svc is not None else None

    clients = ClientRegistry(service.peers, service.pairing, leases, service.certs.fingerprint)
    return Runtime(host, service.peers, clients, get_control_surface, lambda: socket.gethostname().split(".")[0])


class MobileConnectPlugin(PluginBase):
    name = "MobileConnect"                            # the settings namespace is ``plugin.MobileConnect``
    version = __version__
    api_version = "1"
    options_label = "MobileConnect"

    def __init__(self) -> None:
        self.runtime: Runtime | None = None

    def activate(self, ctx: Any) -> None:
        try:
            self.runtime = build_runtime(ctx)
            self.runtime.start()
        except Exception:                             # a failing plugin never takes Galileo with it
            logger.exception("MobileConnect could not start; it stays inert")
            self.runtime = None

    def deactivate(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None

    def on_enabled_changed(self, enabled: bool) -> None:
        if self.runtime is None:
            return
        try:
            if enabled:
                self.runtime.start()
            else:
                self.runtime.stop()
        except Exception:
            logger.exception("MobileConnect could not change state")

    def build_options_page(self) -> Any:
        if self.runtime is None:
            return None
        from mobileconnect.ui import MobileConnectOptions
        return MobileConnectOptions(self.runtime)
