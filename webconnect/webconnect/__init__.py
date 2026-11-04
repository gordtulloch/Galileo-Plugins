# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""WebConnect: browser control of a running Galileo observatory over Connect Link.

Two halves (SDD ADR-WCON-001): the Master half in this package runs inside Galileo and owns every decision
(identity, role, lease, confirmation, audit); the web half (``webconnect.web``) is a separate process that only
presents pages and asks.
"""

__version__ = "0.1.0"

PLUGIN = "webconnect"
API = f"/{PLUGIN}/v1"

from webconnect.plugin import WebConnectPlugin

__all__ = ["API", "PLUGIN", "WebConnectPlugin", "__version__"]
