# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect: configuration sync between a Master and its spoke nodes over Connect Link, and
(not yet built) history and image transfer to a Master.

Installed through Options > Plugins; Galileo's loader imports this module and finds the plugin class.
"""

__version__ = "0.1.0"

from nodeconnect.plugin import NodeConnectPlugin

__all__ = ["NodeConnectPlugin", "__version__"]
