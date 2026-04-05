# CUI // SP-CTI
"""Quality Design Canvas — Full Lifecycle E2E Test.

Tests: index page, design CRUD, template loading, assessment,
snippets, runbooks, SOPs, versioning, export.
Uses Selenium headless Chrome (Playwright MCP no longer working).
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

passed = 0
failed = 0
errors = []

BASE = "http://localhost:5050"
QUALITY_URL = f"{BASE}/quality"
SCREENSHOT_DIR = Path("playwright/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        errors.append(f"{name}: {detail}")
        print(f"  FAIL  {name} -- {detail}")


def api_get(path, base=None):
    url = (base or BASE) + path
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except Exception as e:
        return {"error": str(e)}, 0


def api_post(path, data=None, base=None):
    url = (base or BASE) + path
    body = json.dumps(data or {}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except Exception as e:
        return {"error": str(e)}, 0


def api_put(path, data=None, base=None):
    url = (base or BASE) + path
    body = json.dumps(data or {}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="PUT")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except Exception as e:
        return {"error": str(e)}, 0


def api_delete(path, base=None):
    url = (base or BASE) + path
    try:
        req = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except Exception as e:
        return {"error": str(e)}, 0


# ── Selenium setup ───────────────────────────────────────────────────────────

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

print("=" * 60)
print("Quality Design Canvas (QDC) — Full Lifecycle E2E Test")
print("=" * 60)

# ── [1] API Health ───────────────────────────────────────────────────────────
print("\n[1] API Health Check")
resp, status = api_get("/quality/")
check("QDC index reachable", status in (200, 302), f"status={status}")

# ── [2] Templates ────────────────────────────────────────────────────────────
print("\n[2] Templates API")
resp, status = api_get("/quality/api/templates")
templates = resp.get("templates", []) if status == 200 else []
check("Templates API returns 200", status == 200, f"status={status}")
check("5 templates seeded", len(templates) == 5, f"got {len(templates)}")

# ── [3] Snippets ─────────────────────────────────────────────────────────────
print("\n[3] Snippets API")
resp, status = api_get("/quality/api/snippets")
snippets = resp.get("snippets", []) if status == 200 else []
check("Snippets API returns 200", status == 200, f"status={status}")
check("8 snippets seeded", len(snippets) == 8, f"got {len(snippets)}")

# ── [4] Runbooks ─────────────────────────────────────────────────────────────
print("\n[4] Runbooks API")
resp, status = api_get("/quality/api/runbooks")
runbooks = resp.get("runbooks", []) if status == 200 else []
check("Runbooks API returns 200", status == 200, f"status={status}")
check("6 runbooks seeded", len(runbooks) == 6, f"got {len(runbooks)}")

# ── [5] SOPs ─────────────────────────────────────────────────────────────────
print("\n[5] SOPs API")
resp, status = api_get("/quality/api/sops")
sops = resp.get("sops", []) if status == 200 else []
check("SOPs API returns 200", status == 200, f"status={status}")
check("4 SOPs seeded", len(sops) == 4, f"got {len(sops)}")

# ── [6] Design CRUD ──────────────────────────────────────────────────────────
print("\n[6] Design CRUD")

# Create via new canvas endpoint (redirects)
resp, status = api_get("/quality/api/templates")
tpl_id = templates[0]["id"] if templates else None

# Create design directly via save
design_id = None
if tpl_id:
    # Navigate to new canvas with template — capture redirect
    try:
        req = urllib.request.Request(f"{BASE}/quality/canvas/new?template={tpl_id}")
        with urllib.request.urlopen(req, timeout=15) as r:
            # Parse design_id from redirected URL
            final_url = r.url
            if "/canvas/" in final_url:
                design_id = final_url.split("/canvas/")[-1].rstrip("/")
    except Exception:
        pass

check("Design created from template", design_id is not None, f"id={design_id}")

if design_id:
    # Get design
    resp, status = api_get(f"/quality/api/designs/{design_id}")
    check("GET design", status == 200, f"status={status}")

    # Save design
    resp, status = api_put(
        f"/quality/api/designs/{design_id}",
        {
            "name": "E2E Test Design",
            "graph_json": json.dumps(
                {
                    "nodes": [
                        {"id": "n-test1", "type": "gate-sast", "label": "SAST", "x": 100, "y": 100},
                        {"id": "n-test2", "type": "gate-unit", "label": "Unit", "x": 300, "y": 100},
                    ],
                    "edges": [{"id": "e-test1", "source": "n-test1", "target": "n-test2"}],
                }
            ),
        },
    )
    check("PUT save design", status == 200, f"status={status}")

    # ── [7] Assessment ─────────────────────────────────────────────────────
    print("\n[7] Assessment")
    resp, status = api_post(f"/quality/api/designs/{design_id}/assess")
    check("POST assess", status == 200, f"status={status}")
    if status == 200:
        score = resp.get("score", 0)
        uqs = resp.get("uqs_score", 0)
        findings = resp.get("findings", [])
        check("Assessment returns score", score > 0, f"score={score}")
        check("Assessment returns findings", isinstance(findings, list), f"type={type(findings)}")

    # ── [8] UQS ────────────────────────────────────────────────────────────
    print("\n[8] UQS")
    resp, status = api_get(f"/quality/api/designs/{design_id}/uqs")
    check("GET UQS", status == 200, f"status={status}")

    # ── [9] Gates ──────────────────────────────────────────────────────────
    print("\n[9] Gates")
    resp, status = api_get(f"/quality/api/designs/{design_id}/gates")
    check("GET gates", status == 200, f"status={status}")

    # ── [10] Versions ──────────────────────────────────────────────────────
    print("\n[10] Versions")
    resp, status = api_post(f"/quality/api/versions/{design_id}")
    check("POST create version", status in (200, 201), f"status={status}")
    resp, status = api_get(f"/quality/api/versions/{design_id}")
    check("GET versions", status == 200, f"status={status}")

    # ── [11] Export ────────────────────────────────────────────────────────
    print("\n[11] Export")
    resp, status = api_post(f"/quality/api/export/{design_id}/json")
    check("POST export JSON", status == 200, f"status={status}")

    # ── [12] Cross-Canvas ──────────────────────────────────────────────────
    print("\n[12] Cross-Canvas")
    resp, status = api_get("/quality/api/cross-canvas")
    check("GET cross-canvas", status == 200, f"status={status}")

    # ── [13] Delete design ─────────────────────────────────────────────────
    print("\n[13] Cleanup")
    resp, status = api_delete(f"/quality/api/designs/{design_id}")
    check("DELETE design", status == 200, f"status={status}")

# ── [14] Selenium Browser Tests ──────────────────────────────────────────────
if HAS_SELENIUM:
    print("\n[14] Selenium Browser Tests")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    driver = None
    try:
        driver = webdriver.Chrome(options=opts)

        # Index page
        driver.get(f"{BASE}/quality/")
        time.sleep(1)
        check("Index page loads", "Quality" in driver.title or "ICDEV" in driver.title, f"title={driver.title}")
        driver.save_screenshot(str(SCREENSHOT_DIR / "qdc-index-desktop.png"))

        # Check for CUI banner
        body = driver.page_source
        check("CUI banner present", "CUI" in body, "No CUI text found")

        # Templates page
        driver.get(f"{BASE}/quality/templates")
        time.sleep(1)
        check("Templates page loads", "Template" in driver.page_source, "No template content")
        driver.save_screenshot(str(SCREENSHOT_DIR / "qdc-templates-desktop.png"))

        # Runbooks page
        driver.get(f"{BASE}/quality/runbooks")
        time.sleep(1)
        check("Runbooks page loads", "Runbook" in driver.page_source, "No runbook content")
        driver.save_screenshot(str(SCREENSHOT_DIR / "qdc-runbooks-desktop.png"))

        # SOPs page
        driver.get(f"{BASE}/quality/sops")
        time.sleep(1)
        check("SOPs page loads", "SOP" in driver.page_source, "No SOP content")
        driver.save_screenshot(str(SCREENSHOT_DIR / "qdc-sops-desktop.png"))

        # Check console errors (exclude favicon 404)
        logs = driver.get_log("browser")
        severe = [
            entry
            for entry in logs
            if entry.get("level") == "SEVERE" and "favicon" not in entry.get("message", "").lower()
        ]
        check("No SEVERE console errors", len(severe) == 0, f"{len(severe)} errors: {severe[:3]}")

    except Exception as e:
        check("Selenium available", False, str(e))
    finally:
        if driver:
            driver.quit()
else:
    print("\n[14] Selenium not installed — skipping browser tests")

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailures:")
    for e in errors:
        print(f"  - {e}")
print("=" * 60)

sys.exit(1 if failed > 0 else 0)
