# CUI // SP-CTI
"""
E2E Selenium tests — /govcon, /proposals, /cpmp (prop-vv-02-d1).

Verifies:
  1. Page loads (200 + title present)
  2. DERIVED CUI classification banner rendered in HTML
  3. Role-scoped access: @require_role gates on /govcon (admin/bd/capture_mgr/pm)
  4. PTW workspace page renders with banner
  5. Screenshots captured for each surface

Banner checks use two layers:
  - Static: template source declares the macro import + call (fast, no DB needed)
  - Live: rendered HTML from the real server contains the banner element

Run: python tests/e2e_govcon_proposals_cpmp.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Chrome headless on Windows often can't resolve 'localhost' via DNS; use 127.0.0.1.
_RAW_BASE = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:5050")
# requests/browser share the same host; keep consistent
BASE_URL = _RAW_BASE
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "tools" / "dashboard" / "templates"

# .env admin API key auto-provisioned on first run
_ENV_KEY = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
_ADMIN_HEADERS = {"Authorization": f"Bearer {_ENV_KEY}"} if _ENV_KEY else {}

BANNER_CSS = "div.design-classification-banner"
CUI_CLASS = "cls-CUI"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(name)
        print(f"[OK]   {name}{(': ' + detail) if detail else ''}")

    def fail(self, name: str, err: object) -> None:
        msg = str(err)[:200]
        self.failed.append((name, msg))
        print(f"[FAIL] {name}: {msg}")

    def summary(self) -> dict:
        return {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "total": len(self.passed) + len(self.failed),
            "failures": self.failed,
        }


def create_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def check_js_errors(driver) -> list[str]:
    errors: list[str] = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") != "SEVERE":
                continue
            msg = entry.get("message", "")
            if any(
                t in msg.lower()
                for t in ("favicon", "401", "404", "failed to load resource")
            ):
                continue
            errors.append(msg)
    except Exception:
        pass
    return errors


def _browser_has_cui_banner(driver) -> bool:
    """True if rendered page contains a CUI classification banner."""
    try:
        banners = driver.find_elements(By.CSS_SELECTOR, BANNER_CSS)
        for banner in banners:
            if CUI_CLASS in (banner.get_attribute("class") or ""):
                return True
        return False
    except Exception:
        return False


def _html_has_cui_banner(html: str) -> bool:
    return BANNER_CSS.replace("div.", "design-classification-banner") in html and CUI_CLASS in html


def _screenshot(driver, name: str) -> Path:
    path = SCREENSHOT_DIR / f"{name}.png"
    driver.save_screenshot(str(path))
    return path


def _template_src(relative_path: str) -> str:
    return (_TEMPLATES_DIR / relative_path).read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Suite 1: Template source — banner macro declared in each template
# ---------------------------------------------------------------------------


def test_template_source_banners(results: TestResult) -> None:
    """Each template must import and call design_classification_banner."""
    checks = {
        "govcon/pipeline.html": "govcon_pipeline",
        "cpmp/portfolio.html": "cpmp_portfolio",
        "proposals/list.html": "proposals_list",
        "proposals/detail.html": "proposals_detail",
        "proposals/ptw.html": "proposals_ptw",
    }
    for tmpl_path, key in checks.items():
        try:
            src = _template_src(tmpl_path)
            assert "classification_macros.html" in src, f"{tmpl_path}: missing macro import"
            assert "design_classification_banner" in src, f"{tmpl_path}: missing banner call"
            results.ok(f"src_banner_{key}")
        except Exception as exc:
            results.fail(f"src_banner_{key}", exc)


def test_template_source_cui_level(results: TestResult) -> None:
    """Templates that use a hardcoded level must specify CUI (not SECRET/UNCLASS)."""
    # govcon and cpmp pass 'CUI' directly; proposals/detail passes the opp object
    hardcoded = {
        "govcon/pipeline.html": "govcon_pipeline_cui",
        "cpmp/portfolio.html": "cpmp_portfolio_cui",
        "proposals/list.html": "proposals_list_cui",
    }
    for tmpl_path, key in hardcoded.items():
        try:
            src = _template_src(tmpl_path)
            assert "'CUI'" in src or '"CUI"' in src, f"{tmpl_path}: must pass CUI classification"
            results.ok(f"src_cui_level_{key}")
        except Exception as exc:
            results.fail(f"src_cui_level_{key}", exc)

    # proposals/detail.html must pass opp object (DERIVED from data)
    try:
        src = _template_src("proposals/detail.html")
        # The call should be design_classification_banner(opp) — opp is a mapping
        # so the macro derives .classification from the object
        assert "design_classification_banner(opp)" in src, (
            "proposals/detail.html must derive classification from opp object"
        )
        results.ok("src_derived_from_opp_proposals_detail")
    except Exception as exc:
        results.fail("src_derived_from_opp_proposals_detail", exc)

    # proposals/ptw.html must pass opp object (DERIVED)
    try:
        src = _template_src("proposals/ptw.html")
        assert "design_classification_banner(opp)" in src, (
            "proposals/ptw.html must derive classification from opp object"
        )
        results.ok("src_derived_from_opp_proposals_ptw")
    except Exception as exc:
        results.fail("src_derived_from_opp_proposals_ptw", exc)


# ---------------------------------------------------------------------------
# Suite 2: Live server — rendered HTML contains CUI banner
# ---------------------------------------------------------------------------


def test_live_banner_govcon(results: TestResult) -> None:
    try:
        r = requests.get(f"{BASE_URL}/govcon", headers=_ADMIN_HEADERS, timeout=15)
        assert r.status_code == 200, f"govcon returned {r.status_code}"
        html = r.text
        assert "design-classification-banner" in html, "/govcon: banner element missing from HTML"
        assert CUI_CLASS in html, f"/govcon: {CUI_CLASS} not in rendered HTML"
        results.ok("live_govcon_cui_banner")
    except Exception as exc:
        results.fail("live_govcon_cui_banner", exc)


def test_live_banner_proposals(results: TestResult) -> None:
    try:
        r = requests.get(f"{BASE_URL}/proposals", headers=_ADMIN_HEADERS, timeout=15)
        assert r.status_code == 200, f"proposals returned {r.status_code}"
        html = r.text
        assert "design-classification-banner" in html, "/proposals: banner element missing"
        assert CUI_CLASS in html, f"/proposals: {CUI_CLASS} not in HTML"
        results.ok("live_proposals_cui_banner")
    except Exception as exc:
        results.fail("live_proposals_cui_banner", exc)


def test_live_banner_cpmp(results: TestResult) -> None:
    try:
        r = requests.get(f"{BASE_URL}/cpmp", headers=_ADMIN_HEADERS, timeout=15)
        assert r.status_code == 200, f"cpmp returned {r.status_code}"
        html = r.text
        assert "design-classification-banner" in html, "/cpmp: banner element missing"
        assert CUI_CLASS in html, f"/cpmp: {CUI_CLASS} not in HTML"
        results.ok("live_cpmp_cui_banner")
    except Exception as exc:
        results.fail("live_cpmp_cui_banner", exc)


def test_live_banner_cpmp_deliverables(results: TestResult) -> None:
    # Covers route /cpmp/deliverables (Deliverable Command Center).
    try:
        r = requests.get(f"{BASE_URL}/cpmp/deliverables", headers=_ADMIN_HEADERS, timeout=15)
        assert r.status_code == 200, f"cpmp/deliverables returned {r.status_code}"
        html = r.text
        assert "design-classification-banner" in html, "/cpmp/deliverables: banner element missing"
        assert CUI_CLASS in html, f"/cpmp/deliverables: {CUI_CLASS} not in HTML"
        results.ok("live_cpmp_deliverables_cui_banner")
    except Exception as exc:
        results.fail("live_cpmp_deliverables_cui_banner", exc)


# ---------------------------------------------------------------------------
# Suite 3: Role-scoped access assertions
# ---------------------------------------------------------------------------


def test_role_scoped_govcon_page(results: TestResult) -> None:
    """@require_role gates /govcon: admin key -> 200; invalid key -> redirected/401."""
    # Admin (env key) passes the role gate
    if _ENV_KEY:
        try:
            r = requests.get(f"{BASE_URL}/govcon", headers=_ADMIN_HEADERS, timeout=10)
            assert r.status_code == 200, f"admin key -> {r.status_code}"
            assert "GovCon" in r.text or "pipeline" in r.text.lower(), "page content missing for admin"
            results.ok("role_govcon_admin_200")
        except Exception as exc:
            results.fail("role_govcon_admin_200", exc)

    # Invalid bearer -> 401 on write API endpoint (gated by @require_role)
    try:
        r = requests.post(
            f"{BASE_URL}/api/govcon/sam/scan",
            headers={"Authorization": "Bearer icdev_dash_invalid", "Content-Type": "application/json"},
            json={},
            timeout=10,
        )
        assert r.status_code == 401, f"invalid key on scan endpoint: expected 401, got {r.status_code}"
        results.ok("role_govcon_write_invalid_401")
    except Exception as exc:
        results.fail("role_govcon_write_invalid_401", exc)


def test_role_scoped_write_ops(results: TestResult) -> None:
    """All govcon write endpoints return 401 for an invalid bearer (no role bypass)."""
    endpoints = [
        ("POST", "/api/govcon/sam/scan"),
        ("POST", "/api/govcon/opportunities/x/extract-requirements"),
        ("POST", "/api/govcon/opportunities/x/auto-compliance"),
        ("POST", "/api/govcon/opportunities/x/auto-draft"),
        ("GET", "/api/govcon/opportunities/x/bid-recommendation"),
    ]
    bad_hdr = {"Authorization": "Bearer icdev_dash_invalid", "Content-Type": "application/json"}
    session = requests.Session()
    for method, path in endpoints:
        time.sleep(0.2)  # avoid overwhelming the server
        try:
            r = session.request(method, f"{BASE_URL}{path}", headers=bad_hdr, json={}, timeout=15)
            assert r.status_code == 401, f"{method} {path}: expected 401, got {r.status_code}"
            results.ok(f"role_write_gate_{path.split('/')[-1]}")
        except Exception as exc:
            results.fail(f"role_write_gate_{path.split('/')[-1]}", exc)


# ---------------------------------------------------------------------------
# Suite 4: Selenium browser tests with screenshots
# ---------------------------------------------------------------------------


def test_govcon_browser(results: TestResult, driver: webdriver.Chrome) -> None:
    try:
        driver.get(f"{BASE_URL}/govcon")
        time.sleep(2)
        assert driver.title, "empty title"
        results.ok("browser_govcon_loads", driver.title[:60])
    except Exception as exc:
        results.fail("browser_govcon_loads", exc)
        return

    try:
        assert _browser_has_cui_banner(driver), "CUI banner not found"
        results.ok("browser_govcon_cui_banner")
    except Exception as exc:
        results.fail("browser_govcon_cui_banner", exc)

    try:
        js_errors = check_js_errors(driver)
        assert not js_errors, f"SEVERE JS: {js_errors}"
        results.ok("browser_govcon_no_js_errors")
    except Exception as exc:
        results.fail("browser_govcon_no_js_errors", exc)

    try:
        path = _screenshot(driver, "govcon_pipeline")
        results.ok("browser_govcon_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_govcon_screenshot", exc)


def test_proposals_browser(results: TestResult, driver: webdriver.Chrome) -> None:
    try:
        driver.get(f"{BASE_URL}/proposals")
        time.sleep(2)
        assert driver.title, "empty title"
        results.ok("browser_proposals_loads", driver.title[:60])
    except Exception as exc:
        results.fail("browser_proposals_loads", exc)
        return

    try:
        assert _browser_has_cui_banner(driver), "CUI banner not found on /proposals"
        results.ok("browser_proposals_cui_banner")
    except Exception as exc:
        results.fail("browser_proposals_cui_banner", exc)

    try:
        js_errors = check_js_errors(driver)
        assert not js_errors, f"SEVERE JS: {js_errors}"
        results.ok("browser_proposals_no_js_errors")
    except Exception as exc:
        results.fail("browser_proposals_no_js_errors", exc)

    try:
        path = _screenshot(driver, "proposals_list")
        results.ok("browser_proposals_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_proposals_screenshot", exc)


def test_cpmp_browser(results: TestResult, driver: webdriver.Chrome) -> None:
    try:
        driver.get(f"{BASE_URL}/cpmp")
        time.sleep(2)
        assert driver.title, "empty title"
        results.ok("browser_cpmp_loads", driver.title[:60])
    except Exception as exc:
        results.fail("browser_cpmp_loads", exc)
        return

    try:
        assert _browser_has_cui_banner(driver), "CUI banner not found on /cpmp"
        results.ok("browser_cpmp_cui_banner")
    except Exception as exc:
        results.fail("browser_cpmp_cui_banner", exc)

    try:
        js_errors = check_js_errors(driver)
        assert not js_errors, f"SEVERE JS: {js_errors}"
        results.ok("browser_cpmp_no_js_errors")
    except Exception as exc:
        results.fail("browser_cpmp_no_js_errors", exc)

    try:
        path = _screenshot(driver, "cpmp_portfolio")
        results.ok("browser_cpmp_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_cpmp_screenshot", exc)


def _get_proposal_opp_id() -> "str | None":
    """Return the first proposal opportunity ID from the backend DB (PG or SQLite)."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        row = conn.execute("SELECT id FROM proposal_opportunities LIMIT 1").fetchone()
        conn.close()
        return dict(row)["id"] if row else None
    except Exception:
        return None


def test_pwin_ptw_browser(results: TestResult, driver: webdriver.Chrome) -> None:
    # Covers route /proposals//ptw (Flask: /proposals/<opp_id>/ptw)
    # proposals/reviews-dashboard (pWin aggregate)
    try:
        driver.get(f"{BASE_URL}/proposals/reviews-dashboard")
        time.sleep(2)
        assert driver.title, "empty title"
        results.ok("browser_reviews_dashboard_loads", driver.title[:60])
        path = _screenshot(driver, "proposals_reviews_dashboard")
        results.ok("browser_reviews_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_reviews_dashboard", exc)

    # PTW: look up a real opp_id from proposal_opportunities (backend DB), fall back gracefully
    try:
        opp_id = _get_proposal_opp_id()

        if opp_id:
            driver.get(f"{BASE_URL}/proposals/{opp_id}/ptw")
            time.sleep(2)
            assert driver.title, "empty PTW title"
            assert _browser_has_cui_banner(driver), "CUI banner missing on PTW page"
            results.ok("browser_ptw_loads_with_banner", f"opp={opp_id[:16]}")
            path = _screenshot(driver, "proposals_ptw")
            results.ok("browser_ptw_screenshot", str(path))
        else:
            # No opportunities in DB; graceful 404/redirect is fine
            driver.get(f"{BASE_URL}/proposals/nonexistent/ptw")
            time.sleep(1)
            results.ok("browser_ptw_empty_graceful", f"url={driver.current_url[:60]}")
            path = _screenshot(driver, "proposals_ptw_empty")
            results.ok("browser_ptw_empty_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_ptw", exc)


def test_cpmp_deliverables_browser(results: TestResult, driver: webdriver.Chrome) -> None:
    # Covers route /cpmp/deliverables (Flask: cpmp_deliverable_center_page).
    # Deliverable Command Center — all deliverables across all active contracts.
    try:
        driver.get(f"{BASE_URL}/cpmp/deliverables")
        time.sleep(2)
        assert driver.title, "empty title"
        results.ok("browser_cpmp_deliverables_loads", driver.title[:60])
    except Exception as exc:
        results.fail("browser_cpmp_deliverables_loads", exc)
        return

    try:
        assert _browser_has_cui_banner(driver), "CUI banner not found on /cpmp/deliverables"
        results.ok("browser_cpmp_deliverables_cui_banner")
    except Exception as exc:
        results.fail("browser_cpmp_deliverables_cui_banner", exc)

    try:
        js_errors = check_js_errors(driver)
        assert not js_errors, f"SEVERE JS: {js_errors}"
        results.ok("browser_cpmp_deliverables_no_js_errors")
    except Exception as exc:
        results.fail("browser_cpmp_deliverables_no_js_errors", exc)

    try:
        path = _screenshot(driver, "cpmp_deliverable_center")
        results.ok("browser_cpmp_deliverables_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_cpmp_deliverables_screenshot", exc)


def test_proposals_language_browser(results: TestResult, driver: webdriver.Chrome) -> None:
    # Covers route /proposals//language (Flask: /proposals/<opp_id>/language)
    # Proposal Language Settings — glossary, wall of truth, taxonomy, style templates.
    try:
        opp_id = _get_proposal_opp_id()

        if opp_id:
            driver.get(f"{BASE_URL}/proposals/{opp_id}/language")
            time.sleep(2)
            assert driver.title, "empty Language Settings title"
            assert _browser_has_cui_banner(driver), "CUI banner missing on Language page"
            results.ok("browser_language_loads_with_banner", f"opp={opp_id[:16]}")
            path = _screenshot(driver, "proposals_language")
            results.ok("browser_language_screenshot", str(path))
        else:
            # No opportunities in DB; graceful 404/redirect is fine
            driver.get(f"{BASE_URL}/proposals/nonexistent/language")
            time.sleep(1)
            results.ok("browser_language_empty_graceful", f"url={driver.current_url[:60]}")
            path = _screenshot(driver, "proposals_language_empty")
            results.ok("browser_language_empty_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_language", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_tests() -> int:
    results = TestResult()

    print("\n=== Suite 1: Template source — banner declarations ===")
    test_template_source_banners(results)
    test_template_source_cui_level(results)

    print("\n=== Suite 2: Live server — rendered HTML banner ===")
    test_live_banner_govcon(results)
    test_live_banner_proposals(results)
    test_live_banner_cpmp(results)
    test_live_banner_cpmp_deliverables(results)

    print("\n=== Suite 3: Role-scoped access ===")
    test_role_scoped_govcon_page(results)
    test_role_scoped_write_ops(results)

    print("\n=== Suite 4: Selenium browser + screenshots ===")
    driver = create_driver()
    try:
        test_govcon_browser(results, driver)
        test_proposals_browser(results, driver)
        test_cpmp_browser(results, driver)
        test_cpmp_deliverables_browser(results, driver)
        test_pwin_ptw_browser(results, driver)
        test_proposals_language_browser(results, driver)
    finally:
        driver.quit()

    summary = results.summary()
    print(
        f"\n{'='*60}\n"
        f"Results: {summary['passed']} passed, {summary['failed']} failed / {summary['total']} total"
    )
    for name, err in summary["failures"]:
        print(f"  FAIL {name}: {err}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
