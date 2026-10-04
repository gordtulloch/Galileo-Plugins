#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch
"""Generic plugin builder for the Galileo-Plugins repository.

Scans the repo root for plugin directories (any subdirectory that contains a
``plugin.toml`` manifest), builds a distribution ZIP for each, and updates the
shared ``plugins.json`` index that Galileo's MarketplaceClient reads.

Usage:
    python build_plugins.py                 # build every plugin
    python build_plugins.py vstarget        # build one plugin by directory name

Output for each plugin:
    <plugin_dir>/dist/galileo-plugin-<name>-<version>.zip

``plugins.json`` is written to the repo root and served via raw.githubusercontent.com.
"""

import json
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent
GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/gordtulloch/Galileo-Plugins/main"
)


def _find_plugin_dirs() -> list[Path]:
    """Return all subdirectories of the repo root that contain a plugin.toml."""
    return sorted(p.parent for p in REPO_ROOT.glob("*/plugin.toml"))


def _read_manifest(plugin_dir: Path) -> dict:
    with open(plugin_dir / "plugin.toml", "rb") as f:
        return tomllib.load(f)


def _find_packages(plugin_dir: Path, plugin_name: str) -> list[Path]:
    """Locate the Python package(s) to bundle.

    Convention: a subdirectory named after the plugin that contains
    ``__init__.py``.  Falls back to any ``__init__.py``-bearing sibling
    directory if the named one doesn't exist.
    """
    named = plugin_dir / plugin_name
    if named.is_dir() and (named / "__init__.py").exists():
        return [named]
    return [
        p for p in plugin_dir.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    ]


def build_one(plugin_dir: Path) -> Path | None:
    """Build the ZIP for the plugin in *plugin_dir*.  Returns the ZIP path."""
    manifest = _read_manifest(plugin_dir)
    name = manifest["name"]
    version = manifest["version"]
    zip_name = f"galileo-plugin-{name}-{version}.zip"
    dest = plugin_dir / "dist" / zip_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    packages = _find_packages(plugin_dir, name)
    if not packages:
        print(f"  WARNING: no Python package found in {plugin_dir} — skipping")
        return None

    py_files = [
        f
        for pkg in packages
        for f in pkg.rglob("*.py")
        if "__pycache__" not in f.parts
    ]

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(plugin_dir / "plugin.toml", "plugin.toml")
        for f in sorted(py_files):
            zf.write(f, f.relative_to(plugin_dir).as_posix())

    print(f"  ZIP: {dest.relative_to(REPO_ROOT)}  ({len(py_files)} .py file(s))")
    return dest


def update_index(zip_dest: Path, manifest: dict) -> None:
    """Add or replace this plugin's entry in plugins.json."""
    zip_rel = zip_dest.relative_to(REPO_ROOT).as_posix()
    new_entry = {
        "name": manifest["name"],
        "description": manifest.get("description", ""),
        "version": manifest["version"],
        "tier": manifest.get("tier", "first_party"),
        "author": manifest.get("author", ""),
        "download_url": f"{GITHUB_RAW_BASE}/{zip_rel}",
    }

    index_path = REPO_ROOT / "plugins.json"
    entries: list = []
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            entries = json.load(f)

    entries = [e for e in entries if e.get("name") != new_entry["name"]]
    entries.append(new_entry)

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")


def main(filter_name: str | None = None) -> int:
    plugin_dirs = _find_plugin_dirs()
    if filter_name:
        plugin_dirs = [d for d in plugin_dirs if d.name == filter_name]

    if not plugin_dirs:
        msg = f"Plugin '{filter_name}' not found." if filter_name else "No plugins found."
        print(msg)
        return 1

    built = 0
    for plugin_dir in plugin_dirs:
        manifest = _read_manifest(plugin_dir)
        print(f"[{manifest['name']} v{manifest['version']}]")
        zip_path = build_one(plugin_dir)
        if zip_path:
            update_index(zip_path, manifest)
            built += 1

    print(f"\n{built} plugin(s) built.  Updated: plugins.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
