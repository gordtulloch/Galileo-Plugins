#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch
"""Convenience wrapper: delegates to the repo-level build_plugins.py.

Run from within the webconnect/ directory:
    python build_zip.py          # builds only this plugin
"""

import subprocess
import sys
from pathlib import Path

BUILDER = Path(__file__).parent.parent / "build_plugins.py"

sys.exit(subprocess.call([sys.executable, str(BUILDER), "webconnect"]))
