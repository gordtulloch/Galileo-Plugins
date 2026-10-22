# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""allskycam — Galileo first-party plugin that shows the latest image from an indi-allsky all-sky
camera, as a still and as an animation of the last few images (ASC-010 … ASC-090).

Installed via Options > Plugins; discovered by Galileo's plugin loader at startup. It has its own
top-level sidebar section (``panel_level = "top"``) and an optional dockable animation pane.
"""

from __future__ import annotations

import logging
from typing import Any

from galileo.plugins import PluginBase

logger = logging.getLogger(__name__)


def _draw_icon(painter, rect) -> None:
    """The sidebar icon: a fisheye lens with a horizon line and a few stars, drawn with the pen the
    nav bar already set (so it follows the theme colour)."""
    from PySide6.QtCore import QPointF, QRectF

    cx, cy = rect.center().x(), rect.center().y()
    r = min(rect.width(), rect.height()) / 2
    painter.drawEllipse(QPointF(cx, cy), r, r)
    # horizon: an arc across the lower part of the dome
    painter.drawArc(QRectF(cx - r * 0.95, cy - r * 0.15, r * 1.9, r * 1.3), 0, 180 * 16)
    for dx, dy in ((-0.38, -0.45), (0.1, -0.62), (0.45, -0.25), (-0.1, -0.12)):
        painter.drawPoint(QPointF(cx + dx * r, cy + dy * r))
        painter.drawEllipse(QPointF(cx + dx * r, cy + dy * r), r * 0.04, r * 0.04)


class AllSkyCamPlugin(PluginBase):
    """Owns the shared fetch service and hands out the page and the dock pane."""

    name = "AllSkyCamPlugin"
    version = "1.0.0"
    api_version = "1"
    panel_level = "top"
    panel_label = "AllSkyCam"
    dock_label = "AllSkyCam"
    options_label = "AllSkyCam"

    def __init__(self) -> None:
        self._ctx: Any = None
        self._service: Any = None

    # --- lifecycle -----------------------------------------------------------------------

    def activate(self, ctx) -> None:
        self._ctx = ctx
        self._ensure_service()

    def deactivate(self) -> None:
        if self._service is not None:
            self._service.stop()

    def on_enabled_changed(self, enabled: bool) -> None:
        """Disabled means inert (PLUG-060): no fetching, no pane."""
        service = self._ensure_service()
        if service is None:
            return
        if enabled:
            service.start()
            self._ctx.set_dock_visible(service.config.show_dock)
        else:
            service.stop()
            self._ctx.set_dock_visible(False)

    def _ensure_service(self):
        """The one shared service, created on first use. Needs a running Qt application; returns
        ``None`` when there is none (a headless import), so activation never fails for lack of one."""
        if self._service is not None:
            return self._service
        from PySide6.QtCore import QCoreApplication
        if QCoreApplication.instance() is None:
            return None
        from allskycam.service import AllSkyService
        service = AllSkyService(self._ctx.settings(), site_provider=self._ctx.site_location)
        service.config_changed.connect(self._sync_dock)
        service.start()
        self._service = service
        return service

    def _sync_dock(self) -> None:
        self._ctx.set_dock_visible(self._service.config.show_dock)

    # --- views ---------------------------------------------------------------------------

    def build_options_page(self):
        service = self._ensure_service()
        if service is None:
            return None
        from allskycam.ui import SettingsPage
        return SettingsPage(service)

    def build_page(self):
        service = self._ensure_service()
        if service is None:
            return None
        from allskycam.ui import AllSkyCamPage
        return AllSkyCamPage(service)

    def panel_icon_painter(self):
        return _draw_icon

    def build_dock_widget(self):
        service = self._ensure_service()
        if service is None:
            return None
        from allskycam.ui import DockPane
        return DockPane(service)

    def dock_initially_visible(self) -> bool:
        service = self._ensure_service()
        return bool(service and service.config.show_dock)

    def on_dock_visibility_changed(self, visible: bool) -> None:
        if self._service is not None:
            self._service.set_show_dock(visible)


__all__ = ["AllSkyCamPlugin"]
