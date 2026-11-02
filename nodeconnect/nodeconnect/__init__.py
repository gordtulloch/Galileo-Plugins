# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""NodeConnect: node-to-node configuration sync and history/image transfer to a Master.

Work in progress. The logic modules (roles, filtering and merge, arbitration, tombstones,
compatibility) are written and tested against core's replication facilities; the plugin shell,
sync engine, apply step and transfer worker are not built yet, which is why the folder has no
``plugin.toml`` and is not packaged.
"""

__version__ = "0.1.0"
