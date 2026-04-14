"""Selenium E2E — Oracle Insights widget on home dashboard.

Verifies:
  1. GET /api/oracle/summary returns well-formed JSON (4 stats + severity).
  2. Home page renders the #tile-oracle card with data populated.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:5050"


def check_api() -> dict:
    with urlopen(f"{BASE}/api/oracle/summary", timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    required = {"total_24h", "high_conf_pending", "unresolved_convergence",
                "median_horizon_days", "severity"}
    missing = required - set(data.keys())
    assert not missing, f"/api/oracle/summary missing keys: {missing}"
    sev = data["severity"]
    for k in ("critical", "high", "medium", "low"):
        assert k in sev, f"severity missing {k!r}"
    return data


def check_widget(data: dict) -> None:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(f"{BASE}/")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "tile-oracle"))
        )
        # Wait for the async fetch to populate the tile.
        WebDriverWait(driver, 15).until(
            lambda d: d.find_element(By.ID, "tile-oracle").text.strip()
            not in ("", "Loading…", "Loading...")
        )
        text = driver.find_element(By.ID, "tile-oracle").text
        assert "Total 24h" in text, f"widget missing Total 24h label: {text!r}"
        assert "High-conf pending" in text, f"widget missing pending label: {text!r}"
        assert str(data["total_24h"]) in text, (
            f"widget total {data['total_24h']} not rendered: {text!r}"
        )

        screenshot = Path("playwright/screenshots/oracle-insights-widget.png")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(screenshot))
        print(f"screenshot: {screenshot}")
    finally:
        driver.quit()


def main() -> int:
    t0 = time.time()
    data = check_api()
    print(f"api OK: {json.dumps(data)[:200]}")
    check_widget(data)
    print(f"PASS in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
