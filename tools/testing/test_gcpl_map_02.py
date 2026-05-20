#!/usr/bin/env python3
"""Test: gcpl-map-02 — /govcon/capabilities — CUI banner present.

Renders the capabilities page with CUI banner enabled and verifies the
cui-banner div appears in the HTML response from the base template.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# Must be set before importing config/app so module-level constants pick it up.
os.environ["ICDEV_CUI_BANNER_ENABLED"] = "true"
os.environ.setdefault("ICDEV_CUI_BANNER_TOP", "CUI // CONTROLLED UNCLASSIFIED INFORMATION")
os.environ.setdefault("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))
os.environ.setdefault("ICDEV_GOVCON_ENABLED", "true")

# Reload config so the env vars above take effect (module may already be cached).
import importlib
import tools.dashboard.config as _cfg
_cfg.CUI_BANNER_ENABLED = True
_cfg.CUI_BANNER_TOP = os.environ["ICDEV_CUI_BANNER_TOP"]

from tools.dashboard.app import create_app  # noqa: E402


def run():
    failures = []

    # Force the app's context processor to see the patched constants.
    import tools.dashboard.app as _app_mod
    _app_mod.CUI_BANNER_ENABLED = True
    _app_mod.CUI_BANNER_TOP = os.environ["ICDEV_CUI_BANNER_TOP"]

    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        # Test 1: page returns 200
        resp = client.get("/govcon/capabilities")
        if resp.status_code != 200:
            failures.append(
                f"Test 1 FAIL: GET /govcon/capabilities returned {resp.status_code}, expected 200"
            )
        else:
            print("PASS  Test 1: /govcon/capabilities returns 200")

        # Test 2: cui-banner div present in HTML
        html = resp.data.decode("utf-8", errors="replace")
        if 'class="cui-banner"' not in html:
            failures.append(
                "Test 2 FAIL: cui-banner div not found in /govcon/capabilities HTML"
            )
        else:
            print("PASS  Test 2: cui-banner div present in rendered HTML")

        # Test 3: CUI marking text present
        if "CUI" not in html:
            failures.append(
                "Test 3 FAIL: 'CUI' text not found in /govcon/capabilities HTML"
            )
        else:
            print("PASS  Test 3: CUI text present in rendered HTML")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("\nAll 3 tests passed — /govcon/capabilities CUI banner is present.")
        sys.exit(0)


if __name__ == "__main__":
    run()
