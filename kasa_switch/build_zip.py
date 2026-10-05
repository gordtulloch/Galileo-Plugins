#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch
"""Convenience wrapper — delegates to the repo-level build_plugins.py.

Run from within the kasa_switch/ directory:
    python build_zip.py          # builds only this plugin
    python build_zip.py <dir>    # custom ZIP output directory (passed through)

The actual build logic lives in ../build_plugins.py so all plugins share
the same packaging code.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BUILDER = REPO_ROOT / "build_plugins.py"

args = [sys.executable, str(BUILDER), "kasa_switch"]
if len(sys.argv) > 1:
    print(f"Note: custom output directory '{sys.argv[1]}' is not supported via the shim; "
          f"run 'python build_plugins.py kasa_switch' from the repo root instead.")

sys.exit(subprocess.call(args))
