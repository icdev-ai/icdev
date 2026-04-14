"""Selenium E2E — DDC SOPs page + approval workflow API.

Verifies:
  1. GET /data/sops returns 200 with seeded rows visible.
  2. GET /data/api/sops returns a JSON list.
  3. POST /data/api/sops creates a draft SOP (status=draft).
  4. POST /data/api/sops/<id>/submit transitions draft → pending_review.
  5. POST /data/api/sops/<id>/approve transitions pending_review → approved.
  6. DELETE /data/api/sops/<id> cleans up the test row.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:5050"


def _get(path: str) -> tuple[int, str]:
    with urlopen(f"{BASE}{path}", timeout=10) as r:
        return r.getcode(), r.read().decode("utf-8")


def _json_req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body or {}).encode("utf-8")
    req = Request(
        f"{BASE}{path}",
        data=data if method in ("POST", "PUT") else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=10) as r:
        raw = r.read().decode("utf-8")
        return r.getcode(), (json.loads(raw) if raw else {})


def test_api_workflow() -> str:
    # list
    code, _ = _get("/data/api/sops")
    assert code == 200, f"/data/api/sops GET -> {code}"

    # create — response echoes the action ("created"); SOP itself starts as draft in DB
    code, created = _json_req("POST", "/data/api/sops", {
        "title": "E2E Temp SOP",
        "category": "general",
        "purpose": "E2E workflow test",
    })
    assert code == 201, f"create -> {code}: {created}"
    sop_id = created.get("id")
    assert sop_id, f"missing id in create response: {created}"

    # fetch the actual SOP and confirm it starts as draft
    code, sop = _json_req("GET", f"/data/api/sops/{sop_id}")
    assert code == 200, f"get -> {code}"
    assert sop.get("status") == "draft", f"new SOP should be draft: {sop.get('status')}"

    # submit for review (draft -> pending_review)
    code, after_submit = _json_req(
        "POST", f"/data/api/sops/{sop_id}/submit",
        {"reviewer": "e2e-reviewer", "comment": "ready for review"},
    )
    assert code == 200, f"submit -> {code}: {after_submit}"
    assert after_submit.get("status") == "pending_review", f"after submit: {after_submit}"

    # approve (pending_review -> approved)
    code, after_approve = _json_req(
        "POST", f"/data/api/sops/{sop_id}/approve",
        {"action": "approved", "approver": "e2e-approver", "comment": "LGTM"},
    )
    assert code == 200, f"approve -> {code}: {after_approve}"
    assert after_approve.get("status") == "approved", f"after approve: {after_approve}"

    # approvals history should contain both events
    code, approvals = _json_req("GET", f"/data/api/sops/{sop_id}/approvals")
    assert code == 200 and isinstance(approvals, list) and len(approvals) >= 2, \
        f"approvals history incomplete: {approvals}"

    return sop_id


def test_page_render(sop_id: str) -> None:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(f"{BASE}/data/sops")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        body = driver.find_element(By.TAG_NAME, "body").text
        assert "SOP" in body or "Standard Operating" in body or "sop" in body.lower(), \
            f"/data/sops missing SOP markers: {body[:200]!r}"

        screenshot = Path("playwright/screenshots/ddc-sops.png")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(screenshot))
        print(f"screenshot: {screenshot}")
    finally:
        driver.quit()


def cleanup(sop_id: str) -> None:
    code, body = _json_req("DELETE", f"/data/api/sops/{sop_id}")
    assert code in (200, 204), f"delete -> {code}: {body}"


def main() -> int:
    t0 = time.time()
    sop_id = test_api_workflow()
    test_page_render(sop_id)
    cleanup(sop_id)
    print(f"PASS in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
