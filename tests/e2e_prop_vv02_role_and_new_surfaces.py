# CUI // SP-CTI
"""
E2E — role-scoped access + newer role-driven surfaces (prop-vv-02).

Extends tests/e2e_govcon_proposals_cpmp.py (prop-vv-02-d1, which already
covers /govcon, /proposals, /cpmp, /cpmp/deliverables, /cpmp/reports, PTW,
and Language Settings) with the two things that file doesn't:

  1. Genuine per-role access assertions using real dashboard_users rows
     with distinct roles (bd vs developer), not just admin-vs-invalid-key.
     Requires PR #110 (dashboard_users.role CHECK constraint extended to
     bd/capture_mgr/contract_mgr/reviewer) -- without it, create_user(role=
     "bd") itself fails.
  2. The "new surfaces" prop-vv-02 explicitly names that weren't covered
     yet: capture gate (pg_capture_plans), color-team Gold sign-off
     (proposal_reviews.review_type='gold_team'), contract mod / IMS
     milestone / risk register (all three render as sections on the single
     /cpmp/<contract_id> detail page -- confirmed via
     tools/dashboard/app.py::cpmp_detail_page).

Run: python tests/e2e_prop_vv02_role_and_new_surfaces.py
Requires env vars: DASHBOARD_URL, ICDEV_DASHBOARD_API_KEY (admin, for the
DB-backed opp/contract lookups), plus a seeded opportunity + capture plan +
gold-team review + contract + mod + milestone + risk (see this task's PR
for the seed script) and BD_ROLE_KEY / DEV_ROLE_KEY env vars for two
API keys belonging to bd-role and developer-role users respectively.
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

BASE_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_ADMIN_KEY = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
_BD_KEY = os.environ.get("BD_ROLE_KEY", "")
_DEV_KEY = os.environ.get("DEV_ROLE_KEY", "")
_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_KEY}"} if _ADMIN_KEY else {}

BANNER_CSS = "div.design-classification-banner"
CUI_CLASS = "cls-CUI"


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


def _browser_has_cui_banner(driver) -> bool:
    try:
        banners = driver.find_elements(By.CSS_SELECTOR, BANNER_CSS)
        return any(CUI_CLASS in (b.get_attribute("class") or "") for b in banners)
    except Exception:
        return False


def _screenshot(driver, name: str) -> Path:
    path = SCREENSHOT_DIR / f"{name}.png"
    driver.save_screenshot(str(path))
    return path


def _get_first_id(table: str, db_env: dict) -> "str | None":
    try:
        env_backup = dict(os.environ)
        os.environ.update(db_env)
        from tools.db.storage import get_connection
        conn = get_connection()
        row = conn.execute(f"SELECT id FROM {table} LIMIT 1").fetchone()  # nosec B608 -- table is a hardcoded literal at each call site
        conn.close()
        return dict(row)["id"] if row else None
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


# ---------------------------------------------------------------------------
# Suite 1: Real per-role access (bd vs developer)
# ---------------------------------------------------------------------------


def test_bd_role_allowed_on_proposals_denied_on_cpmp(results: TestResult) -> None:
    if not _BD_KEY:
        results.fail("role_bd_setup", "BD_ROLE_KEY not set")
        return
    hdr = {"Authorization": f"Bearer {_BD_KEY}"}
    try:
        r = requests.get(f"{BASE_URL}/proposals", headers=hdr, timeout=10)
        assert r.status_code == 200, f"bd on /proposals -> {r.status_code}"
        results.ok("role_bd_allowed_proposals")
    except Exception as exc:
        results.fail("role_bd_allowed_proposals", exc)

    try:
        r = requests.get(f"{BASE_URL}/cpmp", headers=hdr, timeout=10)
        assert r.status_code == 403, f"bd on /cpmp -> expected 403, got {r.status_code}"
        results.ok("role_bd_denied_cpmp")
    except Exception as exc:
        results.fail("role_bd_denied_cpmp", exc)


def test_developer_role_allowed_on_cpmp_denied_on_proposals(results: TestResult) -> None:
    if not _DEV_KEY:
        results.fail("role_dev_setup", "DEV_ROLE_KEY not set")
        return
    hdr = {"Authorization": f"Bearer {_DEV_KEY}"}
    try:
        r = requests.get(f"{BASE_URL}/cpmp", headers=hdr, timeout=10)
        assert r.status_code == 200, f"developer on /cpmp -> {r.status_code}"
        results.ok("role_dev_allowed_cpmp")
    except Exception as exc:
        results.fail("role_dev_allowed_cpmp", exc)

    try:
        r = requests.get(f"{BASE_URL}/proposals", headers=hdr, timeout=10)
        assert r.status_code == 403, f"developer on /proposals -> expected 403, got {r.status_code}"
        results.ok("role_dev_denied_proposals")
    except Exception as exc:
        results.fail("role_dev_denied_proposals", exc)


# ---------------------------------------------------------------------------
# Suite 2: New surfaces -- capture gate, Gold sign-off, contract detail
# (mods / IMS milestones / risk register all render on one page)
# ---------------------------------------------------------------------------


def test_capture_gate_surface(results: TestResult) -> None:
    """pg_capture_plans / gates, surfaced on the proposal_genesis dashboard."""
    try:
        opp_id = _get_first_id(
            "proposal_opportunities",
            {"ICDEV_DB_PATH": os.environ.get("ICDEV_DB_PATH", ""), "ICDEV_STORAGE_BACKEND": os.environ.get("ICDEV_STORAGE_BACKEND", "")},
        )
        assert opp_id, "no seeded proposal_opportunities row"
        r = requests.get(
            f"{BASE_URL}/api/proposal-genesis/capture-plans",
            headers=_ADMIN_HEADERS, timeout=10,
        )
        assert r.status_code == 200, f"capture-plans -> {r.status_code}"
        data = r.get_json() if hasattr(r, "get_json") else r.json()
        assert data.get("count", 0) >= 1 or data.get("plans"), "capture plan not found in listing"
        results.ok("capture_gate_plan_listed")
    except Exception as exc:
        results.fail("capture_gate_plan_listed", exc)


def test_gold_team_review_surface(results: TestResult) -> None:
    """proposal_reviews.review_type='gold_team' shows on the reviews dashboard."""
    try:
        r = requests.get(f"{BASE_URL}/proposals/reviews-dashboard", headers=_ADMIN_HEADERS, timeout=10)
        assert r.status_code == 200, f"reviews-dashboard -> {r.status_code}"
        assert "gold" in r.text.lower(), "no 'gold' team reference found on reviews dashboard"
        results.ok("gold_team_review_referenced")
    except Exception as exc:
        results.fail("gold_team_review_referenced", exc)


def test_cpmp_contract_detail_browser(results: TestResult, driver: webdriver.Chrome) -> None:
    """/cpmp/<contract_id> -- single page covering mods, IMS milestones, risk register."""
    try:
        contract_id = _get_first_id(
            "cpmp_contracts",
            {"ICDEV_DB_PATH": os.environ.get("ICDEV_DB_PATH", ""), "ICDEV_STORAGE_BACKEND": os.environ.get("ICDEV_STORAGE_BACKEND", "")},
        )
        assert contract_id, "no seeded cpmp_contracts row"
    except Exception as exc:
        results.fail("cpmp_contract_detail_setup", exc)
        return

    try:
        driver.get(f"{BASE_URL}/cpmp/{contract_id}")
        time.sleep(2)
        assert driver.title, "empty title"
        results.ok("browser_cpmp_detail_loads", driver.title[:60])
    except Exception as exc:
        results.fail("browser_cpmp_detail_loads", exc)
        return

    try:
        assert _browser_has_cui_banner(driver), "CUI banner not found on /cpmp/<id>"
        results.ok("browser_cpmp_detail_cui_banner")
    except Exception as exc:
        results.fail("browser_cpmp_detail_cui_banner", exc)

    page_src = driver.page_source.lower()
    for surface, needle in (
        ("contract_mod", "mod"),
        ("ims_milestone", "milestone"),
        ("risk_register", "risk"),
    ):
        try:
            assert needle in page_src, f"'{needle}' not found in page source"
            results.ok(f"browser_cpmp_detail_has_{surface}")
        except Exception as exc:
            results.fail(f"browser_cpmp_detail_has_{surface}", exc)

    try:
        path = _screenshot(driver, "cpmp_contract_detail")
        results.ok("browser_cpmp_detail_screenshot", str(path))
    except Exception as exc:
        results.fail("browser_cpmp_detail_screenshot", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_tests() -> int:
    results = TestResult()

    print("\n=== Suite 1: Real per-role access (bd vs developer) ===")
    test_bd_role_allowed_on_proposals_denied_on_cpmp(results)
    test_developer_role_allowed_on_cpmp_denied_on_proposals(results)

    print("\n=== Suite 2: New surfaces -- capture gate, Gold sign-off, contract detail ===")
    test_capture_gate_surface(results)
    test_gold_team_review_surface(results)

    driver = create_driver()
    try:
        test_cpmp_contract_detail_browser(results, driver)
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
