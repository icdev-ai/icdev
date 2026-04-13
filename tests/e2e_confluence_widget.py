"""Selenium E2E for confluence toggle widget on /orders."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from werkzeug.serving import make_server

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.trading.dashboard.app import app  # noqa: E402

PORT = 5196


class ServerThread(threading.Thread):
    def __init__(self, flask_app, port):
        super().__init__(daemon=True)
        self.srv = make_server("127.0.0.1", port, flask_app)

    def run(self):
        self.srv.serve_forever()

    def shutdown(self):
        self.srv.shutdown()


def main() -> int:
    server = ServerThread(app, PORT)
    server.start()
    time.sleep(1.0)

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(f"http://127.0.0.1:{PORT}/orders")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "confluence-card")))
        WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By.ID, "conf-state-badge").text.strip() not in ("", "…")
        )
        badge = driver.find_element(By.ID, "conf-state-badge").text.strip()
        assert badge in ("ENABLED", "DISABLED", "API ERROR"), f"unexpected badge: {badge!r}"
        assert driver.find_element(By.ID, "conf-toggle-btn").is_displayed()
        assert driver.find_element(By.ID, "conf-probe-ticker").is_displayed()

        screenshot = Path("playwright/screenshots/confluence-widget-desktop.png")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(screenshot))

        severe = [e for e in driver.get_log("browser") if e["level"] == "SEVERE" and "favicon" not in e["message"]]
        if severe:
            print("SEVERE JS errors:", severe)
            return 2

        print(f"OK badge={badge} screenshot={screenshot}")
        return 0
    finally:
        driver.quit()
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
