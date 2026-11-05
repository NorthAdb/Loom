"""Every demo script must run at tip: the demos are the reproducible story.

The demo scripts live outside pytest's and ruff's view of imports, so a
package move once broke every one of them while the suite stayed green.
This guard runs each keyless demo as a subprocess and fails if any exits
non-zero. Browser demos run their real headless chromium only when the
availability probe passes (the same skip rule as the transport tests).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Every demo is covered: KEYLESS is derived from the scripts directory, so a
# new demo joins the smoke set automatically and an omitted demo cannot hide.
# A browser demo must be listed in BROWSER to be skipped without chromium.
BROWSER = ["demo_gate4_browser.py"]
_keyless = sorted(path.name for path in (REPO / "scripts").glob("demo_*.py"))
KEYLESS = [name for name in _keyless if name not in BROWSER]


def _run(script: str) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO,
        env={**os.environ, "LOOM_MEMORY_ENABLED": "false"},
    )
    assert result.returncode == 0, f"{script} failed at tip:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"


@pytest.mark.parametrize("script", KEYLESS)
def test_keyless_demo_runs_at_tip(script):
    _run(script)


@pytest.mark.parametrize("script", BROWSER)
def test_browser_demo_runs_at_tip_when_chromium_is_available(script):
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
    except Exception:  # noqa: BLE001 - same availability rule as the transport tests
        pytest.skip("chromium is not installed")
    _run(script)
