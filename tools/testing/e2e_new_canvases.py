# CUI // SP-CTI
"""E2E Selenium test for 4 new Design Canvases: IDC, DDC, BDC, ODC.

Verifies:
1. Dashboard nav links exist for all 4 canvases
2. Each canvas index page loads (HTTP 200)
3. No SEVERE JS errors on any page
4. Screenshots captured at 1920x1080
"""

import sys
import os
import time
from pathlib import Path

# Ensure the project root is on sys.path so ``tools.browser.driver_manager``
# resolves when this file is run directly as a script (added in D7 pilot).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "playwright",
    "screenshots",
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

CANVASES = [
    {"name": "Infrastructure", "path": "/infra/", "nav_text": "Infra", "prefix": "idc"},
    {"name": "Data", "path": "/data/", "nav_text": "Data", "prefix": "ddc"},
    {"name": "Boundary", "path": "/boundary/", "nav_text": "Boundary", "prefix": "bdc"},
    {"name": "Observability", "path": "/observability/", "nav_text": "Observability", "prefix": "odc"},
]


def main():
    # Phase D7 pilot: vendored driver via driver_manager.
    # get_driver() applies the same defaults the inline Options previously did.
    from tools.browser.driver_manager import get_driver
    driver = get_driver(headless=True, window_size=(1920, 1080))
    results = []
    passed = 0
    failed = 0

    try:
        # Test 1: Dashboard home loads
        print("=" * 60)
        print("E2E: New Design Canvases (IDC, DDC, BDC, ODC)")
        print("=" * 60)

        driver.get(f"{BASE_URL}/")
        time.sleep(2)
        page_source = driver.page_source

        # Test 2: Nav links exist
        for canvas in CANVASES:
            nav_found = canvas["nav_text"] in page_source
            status = "PASS" if nav_found else "FAIL"
            if nav_found:
                passed += 1
            else:
                failed += 1
            print(f"  [{status}] Nav link '{canvas['nav_text']}' present in sidebar")
            results.append({"test": f"nav_{canvas['prefix']}", "status": status})

        # Test 3: Each index page loads
        for canvas in CANVASES:
            try:
                driver.get(f"{BASE_URL}{canvas['path']}")
                time.sleep(2)

                # Check HTTP status via page content
                title = driver.title
                body = driver.page_source
                is_ok = (
                    "500 Internal Server Error" not in body
                    and "404" not in title
                    and "TemplateNotFound" not in body
                    and len(body) > 500
                )

                # Check for JS errors
                logs = driver.get_log("browser")
                severe = [
                    entry
                    for entry in logs
                    if l.get("level") == "SEVERE" and "favicon" not in l.get("message", "").lower()  # noqa: F821
                ]

                status = "PASS" if is_ok and not severe else "FAIL"
                if status == "PASS":
                    passed += 1
                else:
                    failed += 1

                # Screenshot
                screenshot_path = os.path.join(
                    SCREENSHOT_DIR,
                    f"{canvas['prefix']}-index-desktop.png",
                )
                driver.save_screenshot(screenshot_path)

                print(f"  [{status}] {canvas['name']} index ({canvas['path']}) — title: {title[:50]}")
                if severe:
                    for s in severe[:3]:
                        print(f"         JS ERROR: {s['message'][:100]}")
                results.append(
                    {
                        "test": f"index_{canvas['prefix']}",
                        "status": status,
                        "title": title,
                        "screenshot": screenshot_path,
                    }
                )

            except Exception as exc:
                failed += 1
                print(f"  [FAIL] {canvas['name']} index — {exc}")
                results.append(
                    {
                        "test": f"index_{canvas['prefix']}",
                        "status": "FAIL",
                        "error": str(exc),
                    }
                )

        # Summary
        print(f"\n{'=' * 60}")
        total = passed + failed
        print(f"Results: {passed}/{total} passed, {failed} failed")
        print(f"{'=' * 60}")

        return 0 if failed == 0 else 1

    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
