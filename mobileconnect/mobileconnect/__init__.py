# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""MobileConnect: the server side of the Galileo MobileConnect apps, over Connect Link.

The wire contract lives with the apps, in the Galileo-MobileConnect repository (``api/openapi.yaml``,
``api/events.schema.json``). Every decision (lease, confirmation, idempotency, safety, audit) is core's shared
``Gateway``; this plugin translates between it and that contract.
"""

__version__ = "0.1.0"

PLUGIN = "mobileconnect"
API = f"/{PLUGIN}/v1"

from mobileconnect.plugin import MobileConnectPlugin  # noqa: E402

__all__ = ["API", "PLUGIN", "MobileConnectPlugin", "__version__"]
