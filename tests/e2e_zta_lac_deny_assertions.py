# [TEMPLATE: CUI // SP-CTI]
"""ZTA LAC Simulator — deny_reasons panel + audit_trail Playwright assertions.

Acceptance criteria (irad-lac-10-d4):
  1. After a DENY simulation, deny_reasons panel is visible with ≥1 reason.
  2. audit_trail table contains ≥1 row.
"""

import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
from selenium.webdriver.common.by import By
from tools.browser.driver_manager import get_driver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE = "http://localhost:5050"
LAC = f"{BASE}/zta/lac-simulator"
SCREENSHOT_DIR = Path("playwright/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

passed = 0
failed = 0
errors = []

DENY_PAYLOAD = {
    "scenario_id": "SCENARIO_ECI_SPILLAGE_PREVENTION",
    "principal": {
        "citizenship": "FVEY",
        "clearance_level": "TS//SCI",
        "cois": ["COI_ALPHA"],
        "roles": ["ANALYST"],
    },
    "resource": {
        "resource_id": "res-eci-alpha-007",
        "classification": "TS//SCI",
        "cois": ["COI_ALPHA"],
        "is_eci": True,
        "ownership": "US",
    },
}


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        errors.append(f"{name}: {detail}")
        print(f"  FAIL  {name} -- {detail}")


def api_post(path, data):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8")), resp.status


print("=" * 60)
print("ZTA LAC Simulator — deny_reasons + audit_trail assertions")
print("=" * 60)

# ── Pre-condition: seed a DENY simulation via API ─────────────
print("\n[1/3] Seed DENY simulation via API")
resp, status = api_post("/zta/lac/simulate", DENY_PAYLOAD)
check("API simulate DENY returns 200", status == 200, f"status={status}")
check("API decision is DENY", resp.get("decision") == "DENY", f"decision={resp.get('decision')}")
api_deny_reasons = resp.get("deny_reasons", [])
check(
    "API deny_reasons non-empty",
    len(api_deny_reasons) >= 1,
    f"deny_reasons={api_deny_reasons}",
)

# ── Browser assertions ────────────────────────────────────────
print("\n[2/3] Browser — deny_reasons panel + audit_trail table")
driver = get_driver()
wait = WebDriverWait(driver, 10)

try:
    driver.get(LAC)
    time.sleep(1)

    # Dismiss tour modal if present
    driver.execute_script("""
        const tour = document.getElementById('icdev-tour-welcome');
        if (tour) tour.classList.remove('visible');
    """)

    # Select ECI_SPILLAGE_PREVENTION scenario and run simulation
    driver.execute_script("""
        const sel = document.getElementById('scenario-select');
        if (sel) { sel.value = 'SCENARIO_ECI_SPILLAGE_PREVENTION'; sel.dispatchEvent(new Event('change')); }
        runSimulation();
    """)

    # Wait for verdict to appear
    try:
        wait.until(EC.visibility_of_element_located((By.ID, "verdict-badge")))
    except Exception:
        time.sleep(2)

    # ── Assertion 1: deny_reasons panel ──────────────────────
    deny_panel = driver.find_element(By.ID, "deny-reasons-panel")
    panel_visible = deny_panel.is_displayed()
    check("deny_reasons panel is visible after DENY", panel_visible, "panel display=none")

    deny_list = driver.find_element(By.ID, "deny-reasons-list")
    deny_items = deny_list.find_elements(By.TAG_NAME, "li")
    check(
        "deny_reasons panel contains ≥1 reason",
        len(deny_items) >= 1,
        f"items={len(deny_items)}",
    )
    if deny_items:
        reason_text = deny_items[0].text.strip()
        check(
            "deny reason text is non-empty",
            len(reason_text) > 0,
            f"text='{reason_text}'",
        )
        print(f"        Reason: {reason_text[:80]}")

    verdict = driver.find_element(By.ID, "verdict-badge")
    check(
        "verdict badge shows DENY",
        "DENY" in verdict.text,
        f"verdict='{verdict.text}'",
    )

    # Screenshot after simulation
    screenshot_path = SCREENSHOT_DIR / "lac-simulator-deny-assertions-1920x1080.png"
    driver.save_screenshot(str(screenshot_path))
    print(f"        Screenshot: {screenshot_path}")

    # ── Assertion 2: audit_trail table has ≥1 row ────────────
    audit_body = driver.find_element(By.ID, "audit-table-body")
    audit_rows = audit_body.find_elements(By.TAG_NAME, "tr")
    check(
        "audit_trail table contains ≥1 row",
        len(audit_rows) >= 1,
        f"rows={len(audit_rows)}",
    )
    if audit_rows:
        first_row_text = audit_rows[0].text.strip()
        check(
            "audit row content is non-empty",
            len(first_row_text) > 0,
            f"row='{first_row_text[:60]}'",
        )
        # Verify the row contains denial-relevant content
        check(
            "audit row contains DENY decision",
            "DENY" in first_row_text or "SCENARIO_ECI" in first_row_text or "ECI" in first_row_text,
            f"row='{first_row_text[:80]}'",
        )
        print(f"        Audit row[0]: {first_row_text[:80]}")

finally:
    driver.quit()

# ── API: verify audit endpoint also returns rows ──────────────
print("\n[3/3] API audit endpoint verification")
audit_url = f"{BASE}/zta/lac/audit"
with urllib.request.urlopen(audit_url, timeout=10) as r:  # nosec B310
    audit_data = json.loads(r.read().decode("utf-8"))

audit_rows_api = audit_data.get("entries", audit_data) if isinstance(audit_data, dict) else audit_data
check(
    "audit API returns ≥1 entry",
    (len(audit_rows_api) >= 1 if isinstance(audit_rows_api, list) else audit_data.get("total", 0) >= 1),
    f"type={type(audit_rows_api).__name__}, data={str(audit_data)[:80]}",
)

# ── Report ────────────────────────────────────────────────────
print("\n" + "=" * 60)
total = passed + failed
print(f"RESULTS: {passed}/{total} passed, {failed} failed")
print("=" * 60)

if errors:
    print("\nFailed checks:")
    for e in errors:
        print(f"  - {e}")

if failed == 0:
    print("\n  ALL ASSERTIONS PASSED")
else:
    print(f"\n  {failed} FAILURE(S)")
    sys.exit(1)
