"""Selenium smoke test for FathomDesk election-phase widget.

Spins up the dashboard Flask app on an isolated port via werkzeug server,
loads /scenarios in headless Chrome, asserts the widget rendered and the
API returned a populated payload.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from werkzeug.serving import make_server

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.browser.driver_manager import get_driver  # noqa: E402
from tools.trading.dashboard.app import app  # noqa: E402

PORT = 5199
URL = f"http://127.0.0.1:{PORT}/scenarios"


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

    driver = get_driver()
    try:
        driver.get(URL)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "election-phase-card"))
        )
        WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By.ID, "election-phase-badge").text.strip() not in ("", "—")
        )
        badge = driver.find_element(By.ID, "election-phase-badge").text.strip()
        body = driver.find_element(By.ID, "election-phase-body").text
        assert "Cycle Year" in body, f"body missing cycle text: {body!r}"

        screenshot = Path("playwright/screenshots/election-phase-widget-desktop.png")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(screenshot))

        severe = [e for e in driver.get_log("browser") if e["level"] == "SEVERE" and "favicon" not in e["message"]]
        if severe:
            print("SEVERE JS errors:", severe)
            return 2

        print(f"OK phase={badge} screenshot={screenshot}")
        return 0
    finally:
        driver.quit()
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
