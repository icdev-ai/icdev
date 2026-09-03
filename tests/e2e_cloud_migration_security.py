#!/usr/bin/env python3
# CUI // SP-CTI
"""
E2E Test: Cloud Migration Security Dashboard Pages (11 pages)
=============================================================
Tests all 11 new dashboard pages added for cloud migration security capabilities:

Batch 1 (from article):
  /security-scan, /migration, /sbd, /pr-intel, /iac

Batch 2 (additional recommendations):
  /boundary/cato-health (was /cato), /control-inheritance, /migration-cost, /compliance-debt, /stig-manager, /ato-package

Validates: page loads, HTTP 200, no JS errors, API endpoints respond, navbar links present.
"""

import json
import sys
import time
import urllib.request
import urllib.error

# Reconfigure stdout for UTF-8 safety on Windows
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:5050"
SCREENSHOT_DIR = "playwright/screenshots"

# All 11 new pages
PAGES = [
    # Batch 1: Article capabilities
    {"path": "/security-scan", "title": "Security Scan", "api": "/api/security-scan/stats"},
    {"path": "/migration", "title": "Migration Tracker", "api": "/api/migration/stats"},
    {"path": "/sbd", "title": "Secure by Design", "api": "/api/sbd/stats"},
    {"path": "/pr-intel", "title": "PR Intelligence", "api": "/api/pr-intel/stats"},
    {"path": "/iac", "title": "IaC Gallery", "api": "/api/iac/stats"},
    # Batch 2: Additional recommendations
    {"path": "/boundary/cato-health", "title": "Continuous ATO", "api": "/api/cato/health"},
    {"path": "/control-inheritance", "title": "Control Inheritance", "api": "/api/control-inheritance/csps"},
    {"path": "/migration-cost", "title": "Migration Cost", "api": "/api/migration-cost/portfolio"},
    {"path": "/compliance-debt", "title": "Compliance Debt", "api": "/api/compliance-debt/summary"},
    {"path": "/security/stig-manager", "title": "STIG Manager", "api": "/api/stig-manager/stats"},
    {"path": "/ato-package", "title": "ATO Package", "api": "/api/ato-package/status?project_id=test"},
]

# Additional API endpoints to test
EXTRA_APIS = [
    "/api/security-scan/findings",
    "/api/security-scan/sbom",
    "/api/security-scan/trends",
    "/api/migration/assessments",
    "/api/migration/plans",
    "/api/sbd/domains",
    "/api/sbd/exceptions",
    "/api/pr-intel/reports",
    "/api/pr-intel/drift",
    "/api/iac/artifacts",
    "/api/iac/stig-coverage",
    "/api/iac/templates",
    "/api/cato/evidence",
    "/api/cato/controls",
    "/api/cato/certifications",
    "/api/cato/timeline",
    "/api/control-inheritance/model",
    "/api/control-inheritance/summary",
    "/api/migration-cost/estimate",
    "/api/migration-cost/roi",
    "/api/compliance-debt/burndown",
    "/api/compliance-debt/poams",
    "/api/compliance-debt/sla",
    "/api/compliance-debt/expiring-atos",
    "/api/stig-manager/benchmarks",
    "/api/stig-manager/findings",
    "/api/stig-manager/coverage",
    "/api/stig-manager/cat1",
    "/api/ato-package/checklist?project_id=test",
    "/api/ato-package/controls-summary?project_id=test",
    "/api/ato-package/poam-summary?project_id=test",
]


def fetch_url(url, timeout=10):
    """Fetch a URL and return (status_code, body, error)."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, None
    except urllib.error.HTTPError as e:
        return e.code, "", str(e)
    except Exception as e:
        return 0, "", str(e)


def test_page_loads():
    """Test all 11 pages return HTTP 200."""
    results = []
    for page in PAGES:
        url = f"{BASE_URL}{page['path']}"
        status, body, err = fetch_url(url)
        ok = status == 200
        results.append(
            {
                "page": page["path"],
                "status": status,
                "ok": ok,
                "has_content": len(body) > 500 if ok else False,
                "error": err,
            }
        )
        symbol = "PASS" if ok else "FAIL"
        print(f"  [{symbol}] {page['path']} -> {status} ({len(body)} bytes)")
    return results


def test_api_endpoints():
    """Test all API endpoints return valid JSON."""
    results = []
    # Primary APIs from pages
    apis = [p["api"] for p in PAGES] + EXTRA_APIS
    for api in apis:
        url = f"{BASE_URL}{api}"
        status, body, err = fetch_url(url)
        is_json = False
        if status == 200 and body:
            try:
                json.loads(body)
                is_json = True
            except json.JSONDecodeError:
                pass
        ok = status == 200 and is_json
        results.append(
            {
                "api": api,
                "status": status,
                "ok": ok,
                "is_json": is_json,
                "error": err,
            }
        )
        symbol = "PASS" if ok else "FAIL"
        print(f"  [{symbol}] {api} -> {status} {'(JSON)' if is_json else '(not JSON)'}")
    return results


def test_navbar_links():
    """Test that navbar contains links to all new pages."""
    url = f"{BASE_URL}/"
    status, body, err = fetch_url(url)
    if status != 200:
        print(f"  [FAIL] Could not load homepage: {err}")
        return []

    results = []
    for page in PAGES:
        href = f'href="{page["path"]}"'
        found = href in body
        results.append(
            {
                "page": page["path"],
                "in_navbar": found,
            }
        )
        symbol = "PASS" if found else "FAIL"
        print(f"  [{symbol}] Navbar link: {page['path']}")
    return results


def test_selenium_screenshots():
    """Take screenshots of all 11 pages using Selenium headless Chrome."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from pathlib import Path
    except ImportError:
        print("  [SKIP] Selenium not installed")
        return []

    Path(SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")

    results = []
    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as e:
        print(f"  [SKIP] Chrome driver not available: {e}")
        return []

    try:
        for page in PAGES:
            url = f"{BASE_URL}{page['path']}"
            slug = page["path"].strip("/").replace("/", "-")
            screenshot_path = f"{SCREENSHOT_DIR}/cloud-migration-{slug}-desktop.png"
            try:
                driver.get(url)
                time.sleep(1.5)  # Allow JS to load data

                # Check for JS errors (exclude favicon 404)
                logs = driver.get_log("browser")
                severe_errors = [
                    log for log in logs if log["level"] == "SEVERE" and "favicon" not in log.get("message", "").lower()
                ]

                driver.save_screenshot(screenshot_path)
                ok = len(severe_errors) == 0
                results.append(
                    {
                        "page": page["path"],
                        "screenshot": screenshot_path,
                        "js_errors": len(severe_errors),
                        "error_details": [e["message"] for e in severe_errors[:3]],
                        "ok": ok,
                    }
                )
                symbol = "PASS" if ok else "WARN"
                print(f"  [{symbol}] Screenshot: {slug} ({len(severe_errors)} JS errors)")
                if severe_errors:
                    for err in severe_errors[:2]:
                        print(f"         JS: {err['message'][:120]}")
            except Exception as e:
                results.append(
                    {
                        "page": page["path"],
                        "screenshot": None,
                        "js_errors": -1,
                        "ok": False,
                        "error": str(e),
                    }
                )
                print(f"  [FAIL] Screenshot {slug}: {e}")
    finally:
        driver.quit()

    return results


def main():
    print("=" * 70)
    print("E2E: Cloud Migration Security Dashboard Pages (11 pages)")
    print("=" * 70)

    print("\n--- 1. Page Load Tests ---")
    page_results = test_page_loads()

    print("\n--- 2. API Endpoint Tests ---")
    api_results = test_api_endpoints()

    print("\n--- 3. Navbar Link Tests ---")
    nav_results = test_navbar_links()

    print("\n--- 4. Selenium Screenshots ---")
    screenshot_results = test_selenium_screenshots()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    page_pass = sum(1 for r in page_results if r["ok"])
    api_pass = sum(1 for r in api_results if r["ok"])
    nav_pass = sum(1 for r in nav_results if r.get("in_navbar"))
    screenshot_pass = sum(1 for r in screenshot_results if r.get("ok"))

    print(f"  Pages:       {page_pass}/{len(page_results)} passed")
    print(f"  APIs:        {api_pass}/{len(api_results)} passed")
    print(f"  Navbar:      {nav_pass}/{len(nav_results)} passed")
    print(f"  Screenshots: {screenshot_pass}/{len(screenshot_results)} passed")

    total = page_pass + api_pass + nav_pass + screenshot_pass
    total_tests = len(page_results) + len(api_results) + len(nav_results) + len(screenshot_results)
    print(f"\n  TOTAL: {total}/{total_tests} passed")

    # Collect failures for autofix
    failures = []
    for r in page_results:
        if not r["ok"]:
            failures.append(f"Page {r['page']} returned {r['status']}")
    for r in api_results:
        if not r["ok"]:
            failures.append(f"API {r['api']} returned {r['status']}")
    for r in nav_results:
        if not r.get("in_navbar"):
            failures.append(f"Navbar missing link to {r['page']}")

    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for f in failures:
            print(f"    - {f}")

    # Exit code
    critical_failures = sum(1 for r in page_results if not r["ok"])
    return 1 if critical_failures > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
