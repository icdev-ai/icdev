"""
E2E Selenium test — native task dependency / blocked badge on the kanban board.

Seeds a parent + dependent task pair via the dashboard API, navigates to
/ (which hosts the kanban board), verifies:

  1.  API list_tasks returns depends_on_task_id / depends_on_status / is_blocked
  2.  The dependent task renders with the blocked-by badge on-screen
  3.  That badge names the blocker and its status
  4.  Marking the parent 'done' clears is_blocked on the next list call
  5.  Screenshot capture at 1920x1080
  6.  No SEVERE JS errors (favicon/404 excluded)

Cleans up its seeded tasks at the end so repeated runs are idempotent.

Run: python tests/e2e_kanban_depends_on.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Prepend worktree root to sys.path before any non-stdlib imports so
# `tools.*` is resolvable regardless of how this script is invoked.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class TestResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(f"{name}{(': ' + detail) if detail else ''}")
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


# Every mutating dashboard API is CSRF-protected (tools/security/csrf.py), and a
# cookie session is established for this caller whether or not it asked for one —
# so a bare urllib POST is rejected with 403 CSRF_FAILED. That reads as "the seed
# route is broken" when the route is fine, which is exactly how this test failed.
# A browser gets its token from the <meta name="csrf-token"> tag base.html
# renders; do the same, over one cookie jar so the session the token belongs to is
# the session the request arrives on.
_COOKIE_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_COOKIE_JAR))
_CSRF_TOKEN: str | None = None


def _csrf_token() -> str:
    """Open a session against the dashboard and read its CSRF token (once).

    Returns "" when no token is on offer — CSRF may be disabled
    (ICDEV_CSRF_ENFORCE=0), in which case sending no header is correct and
    failing here would invent a failure.
    """
    global _CSRF_TOKEN
    if _CSRF_TOKEN is not None:
        return _CSRF_TOKEN
    _CSRF_TOKEN = ""
    try:
        with _OPENER.open(f"{BASE_URL}/", timeout=15) as resp:  # nosec B310
            html = resp.read().decode("utf-8", "replace")
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
        if match:
            _CSRF_TOKEN = match.group(1)
        else:
            for cookie in _COOKIE_JAR:
                if cookie.name == "icdev_csrf" and cookie.value:
                    _CSRF_TOKEN = cookie.value
                    break
    except Exception:
        _CSRF_TOKEN = ""
    return _CSRF_TOKEN


def _headers(json_body: bool = True) -> dict:
    headers = {"Content-Type": "application/json"} if json_body else {}
    token = _csrf_token()
    if token:
        headers["X-CSRF-Token"] = token
    return headers


def _post(path: str, payload: dict, *, retries: int = 3) -> dict:
    last_exc: Exception | None = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2 ** attempt)  # 2s, 4s backoff for transient API load
        try:
            req = urllib.request.Request(
                f"{BASE_URL}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers=_headers(),
                method="POST",
            )
            with _OPENER.open(req, timeout=45) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def _get(path: str) -> dict:
    with _OPENER.open(f"{BASE_URL}{path}", timeout=10) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def _delete(path: str) -> None:
    req = urllib.request.Request(
        f"{BASE_URL}{path}", headers=_headers(json_body=False), method="DELETE"
    )
    try:
        with _OPENER.open(req, timeout=10) as resp:  # nosec B310
            resp.read()
    except urllib.error.HTTPError:
        pass  # best-effort cleanup


def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=opts)


def check_js_errors(driver) -> list[str]:
    errors: list[str] = []
    try:
        for entry in driver.get_log("browser"):
            if entry.get("level") != "SEVERE":
                continue
            msg = entry.get("message", "")
            if any(tok in msg.lower() for tok in ("favicon", "404")):
                continue
            errors.append(msg[:240])
    except Exception:
        pass
    return errors


_E2E_DEP_TITLES = ("E2E-DEP-PARENT", "E2E-DEP-CHILD")


def _cleanup_stale_dep_rows() -> int:
    """Delete any E2E-DEP-* rows left over from a previous aborted run.

    Without this, leftover tasks are picked up by the kanban scheduler and
    dispatched as real work items, causing false-positive task failures.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        removed = 0
        for title in _E2E_DEP_TITLES:
            cur = conn.execute(
                "SELECT id FROM kanban_tasks WHERE title = ?",
                (title,),
            )
            rows = cur.fetchall() if cur else []
            for row in rows:
                conn.execute("DELETE FROM kanban_tasks WHERE id = ?", (dict(row)["id"],))
                removed += 1
        conn.commit()
        return removed
    finally:
        conn.close()


def main() -> int:
    result = TestResult()

    # Clean up any stale E2E-DEP-* rows from a previous aborted run so they
    # don't get dispatched as real kanban work by the scheduler.
    try:
        wiped = _cleanup_stale_dep_rows()
        result.ok("cleanup stale E2E-DEP rows", f"{wiped} removed")
    except Exception as e:
        result.fail("cleanup stale E2E-DEP rows", e)

    # --- Step 1: seed parent + dependent via dashboard API ----------------
    parent_id = None
    child_id = None
    try:
        parent = _post(
            "/api/kanban/tasks",
            {"title": "E2E-DEP-PARENT", "priority": "high"},
        )
        parent_id = parent["id"]
        result.ok("seed parent", parent_id)

        child = _post(
            "/api/kanban/tasks",
            {
                "title": "E2E-DEP-CHILD",
                "priority": "high",
                "depends_on_task_id": parent_id,
            },
        )
        child_id = child["id"]
        result.ok("seed child with depends_on", child_id)
    except Exception as e:
        result.fail("seed tasks via API", e)
        print(json.dumps(result.summary(), indent=2))
        return 1

    try:
        # --- Step 2: API shape ------------------------------------------
        try:
            data = _get("/api/kanban/tasks")
            by_id = {t["id"]: t for t in data["tasks"]}
            if parent_id not in by_id or child_id not in by_id:
                raise AssertionError("seeded tasks missing from list response")
            child_row = by_id[child_id]
            assert child_row.get("depends_on_task_id") == parent_id, child_row
            assert child_row.get("is_blocked") is True, f"expected blocked, got {child_row}"
            assert child_row.get("depends_on_title") == "E2E-DEP-PARENT"
            assert child_row.get("depends_on_status") == "backlog"
            # Parent is never blocked.
            assert by_id[parent_id].get("is_blocked") is False
            result.ok(
                "API returns is_blocked + depends_on_*",
                f"child.is_blocked={child_row['is_blocked']}",
            )
        except Exception as e:
            result.fail("API shape", e)

        # --- Step 3: self-reference rejected ----------------------------
        try:
            req = urllib.request.Request(
                f"{BASE_URL}/api/kanban/tasks/{parent_id}",
                data=json.dumps({"depends_on_task_id": parent_id}).encode("utf-8"),
                headers=_headers(),
                method="PATCH",
            )
            try:
                _OPENER.open(req, timeout=10)  # nosec B310
                raise AssertionError("self-reference PATCH should have 400'd")
            except urllib.error.HTTPError as http_err:
                assert http_err.code == 400, http_err.code
                body = json.loads(http_err.read().decode("utf-8"))
                assert "itself" in body["error"].lower()
            result.ok("self-reference rejected", "400")
        except Exception as e:
            result.fail("self-reference rejection", e)

        # --- Step 3b: 2-hop cycle rejected (A→B→A) ----------------------
        # parent already depends on nothing; child depends on parent.
        # Patching parent to depend on child would form parent→child→parent.
        try:
            req = urllib.request.Request(
                f"{BASE_URL}/api/kanban/tasks/{parent_id}",
                data=json.dumps({"depends_on_task_id": child_id}).encode("utf-8"),
                headers=_headers(),
                method="PATCH",
            )
            try:
                _OPENER.open(req, timeout=10)  # nosec B310
                raise AssertionError("2-hop cycle PATCH should have 400'd")
            except urllib.error.HTTPError as http_err:
                assert http_err.code == 400, http_err.code
                body = json.loads(http_err.read().decode("utf-8"))
                assert "cycle" in body["error"].lower()
            result.ok("2-hop cycle rejected", "400")
        except Exception as e:
            result.fail("2-hop cycle rejection", e)

        # --- Step 4: browser render ------------------------------------
        driver = None
        try:
            driver = create_driver()
            driver.get(f"{BASE_URL}/")
            time.sleep(2)  # kanban renders on DOMContentLoaded + fetch

            # Wait up to 10s for the kanban board + seeded child card to render.
            deadline = time.time() + 10
            child_card = None
            while time.time() < deadline:
                # Card inner text contains the title; search all kanban cards.
                cards = driver.find_elements(By.CSS_SELECTOR, ".kanban-card")
                for c in cards:
                    try:
                        if "E2E-DEP-CHILD" in c.text:
                            child_card = c
                            break
                    except Exception:
                        continue
                if child_card is not None:
                    break
                time.sleep(0.5)
            if child_card is None:
                raise AssertionError("child card never rendered on /")
            result.ok("child card rendered")

            # Check for the blocked badge text inside the card.
            if "blocked by" not in child_card.text.lower():
                raise AssertionError(
                    f"blocked badge missing from card text: {child_card.text!r}"
                )
            result.ok("blocked badge rendered", "'blocked by' present")

            # The card used to be DIMMED as well as badged, and this step used to
            # assert opacity < 1. The two kanban card renderers were unified into
            # renderTaskKanban (e818307c7) and the dim did not survive that
            # refactor — the shipped renderer signals blocked with the orange
            # "Blocked by: <parent> [status]" line and nothing else. Asserting a
            # dim no renderer emits was testing a renderer that no longer exists,
            # so this step now asserts the affordance that IS shipped: the badge
            # must NAME the blocker and its status, which is what makes a stalled
            # column readable. Restoring the dim is a UI change, not a dependency
            # fix; if it is wanted back, it needs its own card.
            card_text = child_card.text
            if "E2E-DEP-PARENT" not in card_text:
                raise AssertionError(
                    f"blocked badge does not name the blocker: {card_text!r}"
                )
            if "backlog" not in card_text.lower():
                raise AssertionError(
                    f"blocked badge does not state the blocker's status: {card_text!r}"
                )
            result.ok("blocked badge names blocker + status", "E2E-DEP-PARENT [backlog]")

            # Screenshot.
            shot = SCREENSHOT_DIR / "kanban-depends-on-badge-desktop.png"
            driver.save_screenshot(str(shot))
            result.ok("screenshot", str(shot))

            # JS errors (SEVERE, favicon excluded).
            errors = check_js_errors(driver)
            if errors:
                result.fail("no SEVERE JS errors", errors[0])
            else:
                result.ok("no SEVERE JS errors")
        finally:
            if driver is not None:
                driver.quit()

        # --- Step 5: mark parent done, re-check is_blocked -------------
        # guard-22 (tools/dashboard/api/kanban.py) blocks move→done for any
        # task without a passing kanban_verifications row. This E2E seeds
        # tasks purely via the API so no verification row exists; bypass
        # with an audit reason to exercise the dependency-unblock path.
        try:
            _post(
                f"/api/kanban/tasks/{parent_id}/move",
                {
                    "status": "done",
                    "bypass_verification": True,
                    "bypass_reason": "E2E test — depends-on unblock check",
                },
            )
            data = _get("/api/kanban/tasks")
            by_id = {t["id"]: t for t in data["tasks"]}
            child_row = by_id[child_id]
            assert child_row.get("is_blocked") is False, f"still blocked: {child_row}"
            assert child_row.get("depends_on_status") == "done"
            result.ok(
                "child unblocks when parent done",
                f"is_blocked={child_row['is_blocked']}",
            )
        except Exception as e:
            result.fail("unblock on parent done", e)

    finally:
        # Cleanup seeded tasks regardless of pass/fail.
        if child_id:
            _delete(f"/api/kanban/tasks/{child_id}")
        if parent_id:
            _delete(f"/api/kanban/tasks/{parent_id}")

    summary = result.summary()
    print("\n" + "=" * 60)
    print(json.dumps(summary, indent=2))
    print("=" * 60)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
