#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch
"""Build the installable Galileo plugin ZIP for VSTarget.

Usage:
    python build_zip.py

Output: dist/galileo-plugin-vstarget-<version>.zip

The ZIP layout expected by Galileo's PluginManager.install():
    plugin.toml          <- root-level manifest (required)
    vstarget/            <- package tree
        __init__.py
        ui.py
        planning/
            ...
        analysis/
            ...
"""

import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent


def _read_version() -> str:
    with open(ROOT / "plugin.toml", "rb") as f:
        return tomllib.load(f)["version"]


def build(out_dir: Path | None = None) -> Path:
    version = _read_version()
    dest = (out_dir or ROOT / "dist") / f"galileo-plugin-vstarget-{version}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Collect every .py file under vstarget/ (skip __pycache__ and .pyc).
    pkg_root = ROOT / "vstarget"
    py_files = [
        f for f in pkg_root.rglob("*.py")
        if "__pycache__" not in f.parts
    ]

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        # Manifest at archive root.
        zf.write(ROOT / "plugin.toml", "plugin.toml")

        # Package files, preserving the vstarget/ prefix.
        for f in sorted(py_files):
            arcname = f.relative_to(ROOT).as_posix()
            zf.write(f, arcname)

    print(f"Built: {dest}")
    print(f"  plugin.toml  +  {len(py_files)} .py file(s)")
    return dest


if __name__ == "__main__":
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    build(out_dir)
