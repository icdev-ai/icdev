# CUI // SP-CTI
"""E2E Selenium test — /infra/twin IDC Twin dashboard.

Seeds a test infra_design + snapshot into infra_canvas.db, navigates to
/infra/twin, asserts ≥1 project card is rendered, then drills through to the
canvas page and asserts the resource palette is visible.

If the running server at ICDEV_DASHBOARD_URL does not expose /infra/twin
(ICDEV_IDC_ENABLED may be off), this test spins up a local Flask instance on
port 5055 with IDC enabled and runs against it instead.

Prerequisites:
  - infra_canvas.db initialized (python tools/infra_canvas/db/init_db.py)
  - Optional: Flask dashboard running on http://localhost:5050

Run:
  python tests/e2e_infra_twin.py
  pytest tests/e2e_infra_twin.py -v
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402

_DEFAULT_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = _PROJECT_ROOT / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# ── Test seed data ────────────────────────────────────────────────────────────

_DESIGN_ID = f"e2e-twin-{uuid.uuid4().hex[:8]}"
_DESIGN_NAME = f"E2E IDC Twin Test {_DESIGN_ID[-6:]}"
_SNAP_ID = f"snap-{uuid.uuid4().hex[:8]}"
_ASSESS_ID = f"asmnt-{uuid.uuid4().hex[:8]}"

# aws- and az- prefixes match _CSP_PREFIX_MAP in blueprint.py → "AWS" / "Azure"
_GRAPH_JSON = json.dumps({
    "nodes": [
        {"id": "n1", "type": "aws-ec2", "label": "Web Server", "metadata": {"csp": "aws"}},
        {"id": "n2", "type": "aws-rds", "label": "Primary DB", "metadata": {"csp": "aws"}},
        {"id": "n3", "type": "az-vm", "label": "App Server", "metadata": {"csp": "azure"}},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2"},
        {"id": "e2", "source": "n1", "target": "n3"},
    ],
})

_NOW = datetime.now(timezone.utc).isoformat()


# ── Server helpers ────────────────────────────────────────────────────────────


def _probe_twin_route(base_url: str) -> bool:
    """Return True if the /infra/twin route responds (not 404)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/infra/twin", timeout=4) as resp:  # nosec B310
            return resp.status < 400
    except Exception:
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _find_free_port(start: int = 5056, attempts: int = 20) -> int:
    for port in range(start, start + attempts):
        if _port_free(port):
            return port
    raise RuntimeError(f"No free port found in range {start}–{start + attempts}")


def _start_idc_server(port: int) -> threading.Thread:
    """Spin up a minimal Flask app with IDC enabled on *port* in a daemon thread."""
    os.environ["ICDEV_IDC_ENABLED"] = "true"

    from tools.dashboard.app import create_app  # noqa: E402 — env must be set first

    app = create_app()
    app.config["TESTING"] = False

    def _run():
        app.run(host="127.0.0.1", port=port, use_reloader=False, threaded=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Wait up to 15 s for the /infra/twin route to become accessible
    deadline = time.time() + 15
    while time.time() < deadline:
        if _probe_twin_route(f"http://127.0.0.1:{port}"):
            return t
        time.sleep(0.4)
    raise RuntimeError(f"IDC test server on port {port} did not expose /infra/twin within 15 s")


# ── DB helpers ────────────────────────────────────────────────────────────────


def _seed_db() -> None:
    """Insert test design + snapshot + assessment into infra_canvas.db."""
    from tools.infra_canvas.db.init_db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO infra_designs "
            "(id, name, description, graph_json, classification, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_DESIGN_ID, _DESIGN_NAME, "E2E test design — safe to delete",
             _GRAPH_JSON, "CUI", _NOW, _NOW),
        )
        conn.execute(
            "INSERT OR IGNORE INTO idc_versions "
            "(id, design_id, version_number, graph_json, change_summary, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_SNAP_ID, _DESIGN_ID, 1, _GRAPH_JSON, "Initial E2E snapshot", _NOW),
        )
        conn.execute(
            "INSERT OR IGNORE INTO idc_assessments "
            "(id, design_id, assessment_type, findings_json, score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_ASSESS_ID, _DESIGN_ID, "compliance",
             json.dumps([{"check": "encryption", "detail": "KMS key not configured"}]),
             0.85, _NOW),
        )
        conn.commit()
    finally:
        conn.close()


def _teardown_db() -> None:
    """Remove test rows seeded by this run."""
    from tools.infra_canvas.db.init_db import get_connection

    conn = get_connection()
    try:
        conn.execute("DELETE FROM idc_assessments WHERE id = ?", (_ASSESS_ID,))
        conn.execute("DELETE FROM idc_versions WHERE id = ?", (_SNAP_ID,))
        conn.execute("DELETE FROM infra_designs WHERE id = ?", (_DESIGN_ID,))
        conn.commit()
    finally:
        conn.close()


# ── Test helpers ──────────────────────────────────────────────────────────────


class TestResult:
    def __init__(self):
        self.passed: list[dict] = []
        self.failed: list[dict] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append({"test": name, "detail": detail})
        print(f"  PASS  {name} {detail}")

    def fail(self, name: str, error) -> None:
        self.failed.append({"test": name, "error": str(error)})
        print(f"  FAIL  {name} — {error}")

    def summary(self) -> dict:
        total = len(self.passed) + len(self.failed)
        rate = f"{len(self.passed) / total * 100:.1f}%" if total else "0%"
        return {
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pass_rate": rate,
            "failures": self.failed,
        }


def _screenshot(driver, name: str) -> str:
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        driver.save_screenshot(str(path))
    except Exception:
        pass
    return str(path)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_twin_page_loads(driver, base_url: str, results: TestResult) -> None:
    """Navigate to /infra/twin and assert ≥1 project card is rendered."""
    try:
        driver.get(f"{base_url}/infra/twin")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".section-title"))
        )

        cards = driver.find_elements(By.CSS_SELECTOR, ".twin-card")
        assert len(cards) >= 1, f"Expected ≥1 .twin-card, got {len(cards)}"  # nosec B101

        # Confirm our seeded design is listed
        page_src = driver.page_source
        assert _DESIGN_NAME in page_src, (  # nosec B101
            f"Seeded design '{_DESIGN_NAME}' not found in page"
        )

        # Snapshot version row visible in at least one card
        snap_elements = driver.find_elements(By.CSS_SELECTOR, ".twin-snap")
        assert len(snap_elements) >= 1, "No .twin-snap elements found"  # nosec B101

        _screenshot(driver, "infra-twin-01-load")
        results.ok("twin_page_loads", f"{len(cards)} card(s) rendered")
    except Exception as exc:
        _screenshot(driver, "infra-twin-01-load-error")
        results.fail("twin_page_loads", exc)


def test_drill_through_shows_resources(driver, base_url: str, results: TestResult) -> None:
    """Navigate directly to the canvas page and assert the resource palette loads."""
    try:
        canvas_url = f"{base_url}/infra/canvas/{_DESIGN_ID}"
        driver.get(canvas_url)

        # Canvas page has a server-rendered resource palette sidebar (.dc-sidebar > #palette-tab)
        # Note: .dc-palette is CSS-only; the wrapping element is .dc-sidebar, items are .dc-palette-item
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".dc-palette-item"))
        )

        palette_items = driver.find_elements(By.CSS_SELECTOR, ".dc-palette-item")
        assert len(palette_items) >= 1, (  # nosec B101
            f"Expected ≥1 .dc-palette-item (resource list), got {len(palette_items)}"
        )

        # Design name appears in the canvas page source
        page_src = driver.page_source
        assert _DESIGN_NAME in page_src, "Design name not found on canvas page"  # nosec B101

        # Acceptance screenshot (acceptance criteria: playwright/screenshots/infra-twin.png)
        _screenshot(driver, "infra-twin")
        results.ok(
            "drill_through_shows_resources",
            f"canvas at {canvas_url} — {len(palette_items)} resource type(s) in palette",
        )
    except Exception as exc:
        _screenshot(driver, "infra-twin-error")
        results.fail("drill_through_shows_resources", exc)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    # Resolve which server to use
    base_url = _DEFAULT_URL
    _server_thread = None

    if not _probe_twin_route(base_url):
        print(f"/infra/twin not accessible on {base_url} — starting local IDC server …")
        port = _find_free_port()
        _server_thread = _start_idc_server(port)
        base_url = f"http://127.0.0.1:{port}"
        print(f"IDC test server ready at {base_url}")
    else:
        print(f"Using existing server at {base_url}")

    print("Seeding test data …")
    _seed_db()

    results = TestResult()
    driver = get_driver(headless=True, window_size=(1920, 1080))
    driver.implicitly_wait(5)
    try:
        test_twin_page_loads(driver, base_url, results)
        test_drill_through_shows_resources(driver, base_url, results)
    finally:
        driver.quit()

    print()
    print("Cleaning up test data …")
    _teardown_db()

    print()
    print("=" * 60)
    s = results.summary()
    print(f"Results: {s['passed']}/{s['total']} passed ({s['pass_rate']})")
    if s["failures"]:
        print("Failures:")
        for f in s["failures"]:
            print(f"  - {f['test']}: {f['error']}")
    print("=" * 60)
    print()
    print(json.dumps(s, indent=2))

    return 0 if not s["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
# CUI // SP-CTI
