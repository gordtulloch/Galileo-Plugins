# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Credits and licence wording for Skyshot (SKY-110): one place, so the Options page, the download
dialogs, the help and the tests all say the same thing.

What is credited, and why:

* **Skyshot** (the solver program) is Copyright 2026 Adam Jesionkiewicz, Apache License 2.0. This plugin
  contains none of its code; it downloads unmodified builds made by Galileo's CI, which carry Skyshot's
  LICENSE, NOTICE and third-party notices. It runs as a separate program, so it is not combined with
  Galileo's GPL-3.0 code.
* **The star index** is separate from the code. Skyshot's author describes it as intended for
  non-commercial use and as derived from the ASTAP D80 catalogue (ESA Gaia DR3), with terms of its own
  (``LICENSE-DATA.txt`` is installed with each index). This plugin does not redistribute it: it downloads
  it from Skyshot's own site and keeps the licence files with it.
* **This plugin is not Skyshot's, and is not endorsed by its author.**
"""

from __future__ import annotations

SKYSHOT_URL = "https://github.com/ashtree74/skyshot-solver"
SKYSHOT_SITE = "https://skyshot.app"
GAIA_URL = "https://www.cosmos.esa.int/web/gaia/dr3"      # links to the release-specific credit requirements
ASTAP_URL = "https://www.hnsky.org/astap.htm"

PROGRAM_CREDIT = (
    "Skyshot Solver is Copyright 2026 Adam Jesionkiewicz and is licensed under the Apache License 2.0. "
    "This plugin is not part of Skyshot and is not endorsed by its author."
)

DATA_CREDIT = (
    "Skyshot's star indexes are a separate download with their own terms, which their author describes as "
    "intended for non-commercial use. They are derived from the ASTAP D80 star catalogue, which is built "
    "from ESA's Gaia DR3 (Gaia data: ESA/Gaia/DPAC). Their licence and provenance files are kept with the "
    "index (LICENSE-DATA.txt and PROVENANCE.json)."
)

PAGE_CREDIT_HTML = (
    f'{PROGRAM_CREDIT} <a href="{SKYSHOT_URL}">Skyshot on GitHub</a>. '
    f'<br>{DATA_CREDIT} <a href="{ASTAP_URL}">ASTAP</a>, '
    f'<a href="{GAIA_URL}">Gaia DR3 credit requirements</a>.'
)

DOWNLOAD_PROGRAM_NOTICE = (
    "It is an unmodified build of Skyshot Solver (Copyright 2026 Adam Jesionkiewicz, Apache License 2.0), "
    "kept with Skyshot's licence and notices."
)

DOWNLOAD_INDEX_NOTICE = (
    "The index data is separate from the program and has its own terms: its author describes it as "
    "intended for non-commercial use, and it is derived from the ASTAP D80 catalogue (ESA Gaia DR3). "
    "Its licence is installed with it (LICENSE-DATA.txt); please read it before using the index."
)

CITATION = (
    "To cite Skyshot, use its CITATION.cff and name the source revision and data bundle used: see "
    f"{SKYSHOT_URL}."
)
