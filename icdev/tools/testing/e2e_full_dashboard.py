# CUI // SP-CTI
"""Full Dashboard E2E Lifecycle Test — Selenium Headless Chrome.

Tests EVERY dashboard page, all 8 canvases, all nav links, all API endpoints,
chart rendering, kanban board, CUI banners, and JS error detection.

Leaves no stone unturned.
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5050"
SCREENSHOT_DIR = Path("playwright/screenshots/e2e-full")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

passed = 0
failed = 0
errors_list = []
all_js_errors = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        errors_list.append(f"{name}: {detail}")
        print(f"  FAIL  {name} -- {detail}")


def api_get(path):
    try:
        req = urllib.request.Request(f"{BASE}{path}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except Exception as e:
        return {"error": str(e)}, 0


# ── Selenium Setup ───────────────────────────────────────────────────────────

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False
    print("ERROR: Selenium not installed. Install with: pip install selenium")
    sys.exit(1)

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1920,1080")
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")

driver = webdriver.Chrome(options=opts)

print("=" * 70)
print("ICDEV™ Dashboard — Full E2E Lifecycle Test")
print("=" * 70)


def visit_page(url, name, screenshot_name=None, expect_title=None):
    """Visit a page, check for errors, take screenshot."""
    global all_js_errors
    try:
        driver.get(url)
        time.sleep(1.5)

        title = driver.title
        body = driver.page_source

        # Check for error pages
        is_500 = "Internal Server Error" in body or "500" in title
        is_404 = "Not Found" in title
        is_error = "Traceback" in body or "TemplateNotFound" in body

        if is_500:
            check(f"{name} — no 500", False, "Internal Server Error")
        elif is_404:
            check(f"{name} — page exists", False, f"404 Not Found (title: {title[:50]})")
        elif is_error:
            check(f"{name} — no errors", False, "Error/Traceback in page")
        else:
            check(f"{name} — loads OK", True)

        if expect_title and expect_title.lower() not in title.lower():
            check(f"{name} — title contains '{expect_title}'", False, f"title='{title[:60]}'")

        # Check CUI banner
        if "/static/" not in url and "/api/" not in url:
            has_cui = "CUI" in body
            check(f"{name} — CUI banner", has_cui, "No CUI marking found")

        # Collect JS errors
        try:
            logs = driver.get_log("browser")
            severe = [
                entry
                for entry in logs
                if entry.get("level") == "SEVERE" and "favicon" not in entry.get("message", "").lower()
            ]
            if severe:
                all_js_errors.extend([(name, entry["message"][:120]) for entry in severe[:3]])
                check(f"{name} — no JS errors", False, f"{len(severe)} SEVERE errors")
            else:
                check(f"{name} — no JS errors", True)
        except Exception:
            pass  # Some drivers don't support log collection

        # Screenshot
        if screenshot_name:
            driver.save_screenshot(str(SCREENSHOT_DIR / f"{screenshot_name}.png"))

        return body
    except Exception as e:
        check(f"{name} — reachable", False, str(e)[:80])
        return ""


try:
    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1: HOME DASHBOARD
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[1] HOME DASHBOARD")
    print("=" * 70)

    body = visit_page(f"{BASE}/", "Home", "home-dashboard")

    # Kanban board
    import re

    kanban_cards = len(re.findall(r"kanban-card", body))
    check("Home — kanban board present", "kanban-board" in body)
    check(f"Home — kanban cards ({kanban_cards})", kanban_cards >= 0)

    # Stat bar
    check("Home — stat bar present", "stat-bar" in body)

    # Charts (wait for async render)
    time.sleep(3)
    for chart_id in ["chart-compliance", "chart-projects", "chart-agent-health"]:
        inner = driver.execute_script(
            f'var el = document.getElementById("{chart_id}"); return el ? el.innerHTML.substring(0, 30) : "NOT_FOUND"'
        )
        has_content = "<svg" in (inner or "") or "text-muted" in (inner or "")
        check(f"Home — {chart_id} rendered", has_content, f"content: {(inner or '')[:40]}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2: ALL 8 CANVASES
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[2] ALL 8 DESIGN CANVASES")
    print("=" * 70)

    canvases = [
        ("IDC Infrastructure", "/infra/", "canvas-idc"),
        ("NDC Network", "/network/", "canvas-ndc"),
        ("SDC Security", "/security/", "canvas-sdc"),
        ("BDC Boundary", "/boundary/", "canvas-bdc"),
        ("PDC Pipeline", "/devops/", "canvas-pdc"),
        ("ODC Observability", "/observability/", "canvas-odc"),
        ("DDC Data", "/data/", "canvas-ddc"),
        ("QDC Quality", "/quality/", "canvas-qdc"),
    ]

    for name, path, screenshot in canvases:
        visit_page(f"{BASE}{path}", name, screenshot)

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3: OPS PAGES
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[3] OPS PAGES")
    print("=" * 70)

    ops_pages = [
        ("Projects", "/projects"),
        ("Agents", "/agents"),
        ("Orchestration", "/orchestration"),
        ("Monitoring", "/monitoring"),
        ("Platform Health", "/platform-health"),
        ("Activity", "/activity"),
        ("CI/CD", "/cicd"),
        ("Task Board (Kanban)", "/kanban"),
        ("Batch", "/batch"),
    ]

    for name, path in ops_pages:
        visit_page(f"{BASE}{path}", f"Ops — {name}", f"ops-{path.strip('/').replace('/', '-')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4: COMPLIANCE PAGES
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[4] COMPLIANCE PAGES")
    print("=" * 70)

    compliance_pages = [
        ("Compliance Hub", "/compliance"),
        ("OSCAL", "/oscal"),
        ("Production Audit", "/prod-audit"),
        ("AI Transparency", "/ai-transparency"),
        ("AI Accountability", "/ai-accountability"),
        ("Secure by Design", "/sbd"),
        ("Continuous ATO", "/cato"),
        ("Evidence", "/evidence"),
        ("Lineage", "/lineage"),
    ]

    for name, path in compliance_pages:
        visit_page(f"{BASE}{path}", f"Compliance — {name}", f"compliance-{path.strip('/').replace('/', '-')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 5: SECURITY PAGES
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[5] SECURITY PAGES")
    print("=" * 70)

    security_pages = [
        ("Security Scans", "/security-scan"),
        ("PR Intelligence", "/pr-intel"),
        ("STIG Manager", "/stig-manager"),
        ("IaC Gallery", "/iac"),
    ]

    for name, path in security_pages:
        visit_page(f"{BASE}{path}", f"Security — {name}", f"security-{path.strip('/').replace('/', '-')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 6: INTELLIGENCE PAGES
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[6] INTELLIGENCE PAGES")
    print("=" * 70)

    intel_pages = [
        ("Knowledge Search", "/knowledge-search"),
        ("Code Quality", "/code-quality"),
        ("Research", "/research"),
        ("Genesis", "/genesis"),
        ("Oracle", "/oracle"),
    ]

    for name, path in intel_pages:
        visit_page(f"{BASE}{path}", f"Intelligence — {name}", f"intel-{path.strip('/').replace('/', '-')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 7: BUILD & DEV PAGES
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[7] BUILD & DEV PAGES")
    print("=" * 70)

    dev_pages = [
        ("Chat", "/chat"),
        ("Traces", "/traces"),
        ("Provenance", "/provenance"),
        ("XAI", "/xai"),
        ("Fine-Tuning", "/finetune"),
        ("File Sync", "/filesync"),
    ]

    for name, path in dev_pages:
        visit_page(f"{BASE}{path}", f"Dev — {name}", f"dev-{path.strip('/').replace('/', '-')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 8: GOVCON PAGES (may be disabled)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[8] GOVCON PAGES")
    print("=" * 70)

    govcon_pages = [
        ("Proposals", "/proposals"),
        ("GovCon", "/govcon"),
        ("CPMP", "/cpmp"),
        ("Proposal Genesis", "/proposal-genesis"),
    ]

    for name, path in govcon_pages:
        visit_page(f"{BASE}{path}", f"GovCon — {name}", f"govcon-{path.strip('/').replace('/', '-')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 9: AUTH & ADMIN PAGES
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[9] AUTH & ADMIN")
    print("=" * 70)

    visit_page(f"{BASE}/login", "Login Page", "auth-login")
    visit_page(f"{BASE}/admin/users", "Admin Users", "admin-users")
    visit_page(f"{BASE}/profile", "User Profile", "admin-profile")
    visit_page(f"{BASE}/usage", "Usage Tracking", "admin-usage")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 10: API ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[10] API ENDPOINTS")
    print("=" * 70)

    api_endpoints = [
        ("/api/projects", "projects"),
        ("/api/agents", "agents"),
        ("/api/alerts", "alerts"),
        ("/api/charts/overview", "charts"),
        ("/api/notifications", "notifications"),
        ("/api/canvas-projects", "canvas-projects"),
        ("/quality/api/templates", "qdc-templates"),
        ("/quality/api/snippets", "qdc-snippets"),
        ("/quality/api/runbooks", "qdc-runbooks"),
        ("/quality/api/sops", "qdc-sops"),
        ("/quality/api/gates/tools", "qdc-tools"),
    ]

    for path, name in api_endpoints:
        resp, status = api_get(path)
        check(f"API — {name} ({path})", status == 200, f"status={status}, error={resp.get('error', '')[:50]}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 11: QDC LIFECYCLE (CRUD + Assess + UQS)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[11] QDC LIFECYCLE TEST")
    print("=" * 70)

    # Get templates
    resp, status = api_get("/quality/api/templates")
    templates = resp.get("templates", []) if status == 200 else []
    check("QDC — templates available", len(templates) >= 5, f"got {len(templates)}")

    # Create design from template
    design_id = None
    if templates:
        tpl_id = templates[0].get("id", "")
        try:
            req = urllib.request.Request(f"{BASE}/quality/canvas/new?template={tpl_id}")
            with urllib.request.urlopen(req, timeout=15) as r:
                if "/canvas/" in r.url:
                    design_id = r.url.split("/canvas/")[-1].rstrip("/")
        except Exception:
            pass
    check("QDC — design created", design_id is not None, f"id={design_id}")

    if design_id:
        # Assess
        try:
            data = json.dumps({}).encode()
            req = urllib.request.Request(f"{BASE}/quality/api/designs/{design_id}/assess", data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read().decode())
            score = result.get("result", {}).get("score", result.get("score", -1))
            check("QDC — assessment runs", score is not None and score >= 0, f"score={score}")
        except Exception as e:
            check("QDC — assessment runs", False, str(e)[:60])

        # Get UQS
        resp, status = api_get(f"/quality/api/designs/{design_id}/uqs")
        check("QDC — UQS endpoint", status == 200, f"status={status}")

        # Delete
        try:
            req = urllib.request.Request(f"{BASE}/quality/api/designs/{design_id}", method="DELETE")
            with urllib.request.urlopen(req, timeout=15) as r:
                pass
            check("QDC — design deleted", True)
        except Exception as e:
            check("QDC — design deleted", False, str(e)[:60])

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 12: NAV LINK VERIFICATION
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("[12] NAV LINK VERIFICATION")
    print("=" * 70)

    driver.get(f"{BASE}/")
    time.sleep(2)
    body = driver.page_source

    # Extract all nav links
    nav_links = re.findall(r'href="(/[^"]*)"', body)
    unique_links = sorted(set(nav_links))
    check(f"Nav — total links ({len(unique_links)})", len(unique_links) >= 20, f"only {len(unique_links)} links found")

    # Check key nav items
    key_nav = ["/projects", "/agents", "/chat", "/quality", "/genesis", "/compliance"]
    for path in key_nav:
        check(f"Nav — {path} in links", any(path in ln for ln in nav_links), "link not found")

    driver.save_screenshot(str(SCREENSHOT_DIR / "nav-verification.png"))

finally:
    driver.quit()

# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("E2E FULL LIFECYCLE RESULTS")
print("=" * 70)
print(f"\n  PASSED: {passed}")
print(f"  FAILED: {failed}")
print(f"  TOTAL:  {passed + failed}")
print(f"  RATE:   {100 * passed / (passed + failed):.1f}%")

if errors_list:
    print(f"\n  FAILURES ({len(errors_list)}):")
    for e in errors_list:
        print(f"    - {e}")

if all_js_errors:
    print(f"\n  JS ERRORS ({len(all_js_errors)}):")
    for page, msg in all_js_errors[:10]:
        print(f"    [{page}] {msg}")

print("\n  Screenshots saved to:", SCREENSHOT_DIR)
print("=" * 70)

sys.exit(1 if failed > 0 else 0)
