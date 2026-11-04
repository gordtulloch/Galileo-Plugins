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
from webconnect.config import WebConnectConfig
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


def stored_options(settings) -> dict:
    """The options the person has saved; anything unset keeps its default."""
    return {k: settings.get(k) for k in WebConnectConfig.field_names() if settings.get(k) is not None}


def is_spoke() -> bool:
    """A node that has designated a NodeConnect Master is a spoke (WCON-020)."""
    from galileo import settings_store
    return bool(settings_store.get_setting(NODECONNECT_NAMESPACE, "master_address", ""))


class Runtime:
    """The Master half's object graph. Tests build one around fakes; ``build_runtime`` uses core's."""

    def __init__(self, host: Any, registry: BrowserRegistry, delegate: Delegate, supervisor: Any = None,
                 settings: Any = None, audit: Any = None) -> None:
        self._host, self.browsers, self.delegate = host, registry, delegate
        self.supervisor = supervisor                  # runs the web process; None in tests that need only the Master half
        self.settings, self.audit = settings, audit   # the plugin's saved options, and Connect Link's audit log
        self._acquired = False

    def start(self) -> None:
        self.stop()
        self._host.acquire(PLUGIN, DECLARED, self._install)
        self._acquired = True
        if self.supervisor is not None:
            self.supervisor.start()

    def stop(self) -> None:
        """The web process first (no listener left to answer a browser), then Connect Link."""
        if self.supervisor is not None:
            self.supervisor.stop()
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

    def options(self) -> WebConnectConfig:
        return WebConnectConfig.from_dict(stored_options(self.settings) if self.settings is not None else None)

    def save_options(self, new: WebConnectConfig) -> None:
        """Persist the options, and restart the web process if it is running so a changed port or address applies."""
        if self.settings is None:
            return
        for key, value in new.to_dict().items():
            self.settings.set(key, value)
        if self.supervisor is not None and self.running:
            self.supervisor.stop()
            self.supervisor.start()


def build_runtime(ctx) -> Runtime:
    from galileo.connect_link.host import get_host
    from galileo.control_surface.service import get_control_surface

    host = get_host()
    service = host.service
    registry = BrowserRegistry(service.peers, service.pairing, _Leases())
    return Runtime(host, registry, Delegate(service.peers, get_control_surface),
                   _build_supervisor(ctx, host, service, registry), settings=ctx.settings(), audit=service.audit)


def _build_supervisor(ctx, host, service, registry):
    from pathlib import Path

    from galileo.connect_link.secrets_store import KeyringSecretStore

    from webconnect.config import WebConnectConfig
    from webconnect.launch import make_config_provider
    from webconnect.supervisor import Supervisor, child_command
    from webconnect.tls import CertificateStore

    settings = ctx.settings()

    def cfg() -> WebConnectConfig:
        return WebConnectConfig.from_dict(stored_options(settings))

    certs = CertificateStore(KeyringSecretStore(), settings)
    provide = make_config_provider(
        cfg, certs, registry.mint_service_credential,
        link_address=lambda: f"https://127.0.0.1:{host.settings().port}",
        link_fingerprint=service.certs.fingerprint)
    plugin_dir = Path(__file__).resolve().parents[1]
    deps = plugin_dir / ".deps"
    return Supervisor(child_command(plugin_dir, deps if deps.is_dir() else None), provide)


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
            if WebConnectConfig.from_dict(stored_options(ctx.settings())).start_with_galileo:
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

    def build_options_page(self):
        if self.runtime is None:
            return None
        from webconnect.ui import WebConnectOptions
        return WebConnectOptions(self.runtime)
