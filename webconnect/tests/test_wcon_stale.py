# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The page when the event stream is down (WCON-780). Runs the real ``pier.js`` under Node against a small fake DOM
and EventSource (``stale_harness.mjs``); skipped where Node is not installed."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "webconnect" / "web" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
@pytest.mark.requirement("TC-WCON-780")
@pytest.mark.priority("MVP")
def test_tc_wcon_780_a_down_stream_marks_state_stale_and_disables_all_but_emergency():
    """WCON-780: while the stream is down the page says its state is stale and disables Pier-affecting
    operations except the emergency ones, which stay attemptable; everything returns when the stream does."""
    done = subprocess.run(["node", str(HERE / "stale_harness.mjs"), str(STATIC)], capture_output=True, text=True,
                          timeout=60, check=True)
    r = json.loads(done.stdout.strip().splitlines()[-1])
    assert r["liveHidden"] is True and r["live"] == {"slew": False, "abort": False}
    assert r["downShown"] is True and "out of date" in r["downText"] and "Last update" in r["downText"]
    assert r["down"] == {"slew": True, "abort": False}            # emergency stays attemptable
    assert r["backHidden"] is True and r["back"] == {"slew": False, "abort": False}
