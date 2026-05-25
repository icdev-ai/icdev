"""
E2E Full-Lifecycle test — native task dependency (migration 015).

Exercises the ENTIRE dependency lifecycle end-to-end against the live
dashboard + live DB + real listener code path. Where the existing
`e2e_kanban_depends_on.py` tests the dashboard slice, this test chases
the data through every surface a production task touches:

  Phase A — Setup
    1. Clean up any stale E2E-LC-* rows from a prior aborted run
    2. Create a 3-node chain A → B → C via /api/kanban/tasks with
       depends_on_task_id set on B and C
    3. Backdate updated_at so the listener's 10-minute backlog cooldown
       does not mask the dependency gate under test

  Phase B — Dependency gate at backlog state
    4. Call tools.genesis.reflexes.kanban._get_due_tasks() directly
       (live DB) and assert only A is eligible; B and C are excluded
    5. GET /api/kanban/tasks — assert API returns is_blocked=False for
       A and is_blocked=True for B and C, with correct depends_on_title
       and depends_on_status fields

  Phase C — Parent reaches in_progress (not done)
    6. Move A → in_progress via /move; assert B STILL blocked (the gate
       checks dep.status='done', not just "not backlog")
    7. API confirms B.depends_on_status='in_progress' and is_blocked=True

  Phase D — Parent reaches done
    8. Move A → done
    9. _get_due_tasks() returns B but NOT C
    10. API: B.is_blocked=False, C.is_blocked=True, C.depends_on_status='backlog'

  Phase E — Middle node reaches done
    11. Move B → done
    12. _get_due_tasks() returns C
    13. API: C.is_blocked=False

  Phase F — Scheduled-and-blocked edge case
    14. Create D (scheduled, scheduled_at=past) depending on a fresh
        backlog parent P — verify _get_due_tasks() excludes D even
        though it is "due"

  Phase G — Browser render verification
    15. Drive headless Chrome through the dashboard kanban board
    16. Verify C shows no blocked badge (it's unblocked), a fresh child
        created in this phase still shows the badge
    17. Screenshot capture at 1920x1080
    18. No SEVERE JS errors (favicon/404 excluded)

  Phase H — Cleanup
    19. Delete every seeded E2E-LC-* row

All seeded rows use titles prefixed with E2E-LC- for safe cleanup.

Run: python tests/e2e_kanban_depends_on_full_lifecycle.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests as _requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
_ICDEV_DIR = BASE_DIR / "icdev"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
# Prepend icdev/ so `import tools` resolves via icdev.tools before bare tools/
if str(_ICDEV_DIR) not in sys.path:
    sys.path.insert(0, str(_ICDEV_DIR))

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = BASE_DIR / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

TITLE_PREFIX = "E2E-LC-"  # all seeded tasks share this prefix for cleanup


class TestResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"[OK]   {name}{(': ' + detail) if detail else ''}")

    def fail(self, name: str, err: object) -> None:
        msg = str(err)[:240]
        self.failed.append((name, msg))
        print(f"[FAIL] {name}: {msg}")

    def summary(self) -> dict:
        return {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "total": len(self.passed) + len(self.failed),
            "failures": self.failed,
        }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post(path: str, payload: dict) -> dict:
    resp = _requests.post(f"{BASE_URL}{path}", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _get(path: str) -> dict:
    resp = _requests.get(f"{BASE_URL}{path}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _delete(path: str) -> None:
    try:
        _requests.delete(f"{BASE_URL}{path}", timeout=10)
    except _requests.HTTPError:
        pass  # best-effort


def _list_tasks_by_title() -> dict:
    data = _get("/api/kanban/tasks")
    return {t["title"]: t for t in data["tasks"]}


def _list_tasks_by_id() -> dict:
    data = _get("/api/kanban/tasks")
    return {t["id"]: t for t in data["tasks"]}


# ---------------------------------------------------------------------------
# Direct DB helpers — backdate updated_at, clean up stale E2E rows
# ---------------------------------------------------------------------------


def _backdate_updated_at(task_ids: list, minutes: int = 15) -> None:
    """Push updated_at into the past so the listener's 10-minute
    backlog cooldown does not mask the dependency gate we're testing."""
    from tools.db.storage import get_connection

    past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%d %H:%M:%S+00"
    )
    conn = get_connection()
    try:
        for tid in task_ids:
            conn.execute(
                "UPDATE kanban_tasks SET updated_at = ? WHERE id = ?",
                (past, tid),
            )
        conn.commit()
    finally:
        conn.close()


def _cleanup_stale_e2e_rows() -> int:
    """Delete any E2E-LC-* rows left over from a previous aborted run."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id FROM kanban_tasks WHERE title LIKE ?",
            (f"{TITLE_PREFIX}%",),
        )
        rows = cur.fetchall() if cur else []
        ids = [dict(r)["id"] for r in rows]
        for tid in ids:
            conn.execute("DELETE FROM kanban_tasks WHERE id = ?", (tid,))
        conn.commit()
        return len(ids)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Selenium helpers
# ---------------------------------------------------------------------------


def create_driver():
    """Phase D7 pilot (advanced path) — uses DriverManager for resolution
    but builds Options locally so we can preserve ``goog:loggingPrefs``.

    ``get_driver()`` doesn't expose capability-setting yet; until it does,
    complex tests like this one (which needs ``driver.get_log('browser')``
    to capture JS console errors) go through the lower-level DriverManager
    API. Documented as a D7 finding in tools/browser/README.md.
    """
    from tools.browser.driver_manager import DriverManager

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    mgr = DriverManager.instance()
    driver_path = mgr.resolved_driver_path
    if mgr.resolved_browser == "edge":
        from selenium.webdriver.edge.service import Service as EdgeService
        from selenium.webdriver.edge.options import Options as EdgeOptions

        # Translate Chrome Options to Edge Options (args + caps are compatible)
        edge_opts = EdgeOptions()
        for arg in opts.arguments:
            edge_opts.add_argument(arg)
        edge_opts.set_capability("ms:loggingPrefs", {"browser": "ALL"})
        service = EdgeService(executable_path=driver_path) if driver_path else None
        return (
            webdriver.Edge(service=service, options=edge_opts)
            if service
            else webdriver.Edge(options=edge_opts)
        )
    # Chrome path
    from selenium.webdriver.chrome.service import Service as ChromeService

    service = ChromeService(executable_path=driver_path) if driver_path else None
    return (
        webdriver.Chrome(service=service, options=opts)
        if service
        else webdriver.Chrome(options=opts)
    )


def check_js_errors(driver) -> list:
    errors = []
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


def _wait_for_card(driver, title_substr: str, timeout: float = 10.0):
    """Wait up to `timeout` seconds for a kanban card whose text contains
    `title_substr` to render. Returns the WebElement or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cards = driver.find_elements(By.CSS_SELECTOR, ".kanban-card")
        for c in cards:
            try:
                if title_substr in c.text:
                    return c
            except Exception:
                continue
        time.sleep(0.4)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    result = TestResult()
    seeded_ids: list = []

    # Phase A.1 — wipe stale rows from previous aborted runs
    try:
        wiped = _cleanup_stale_e2e_rows()
        result.ok("cleanup stale rows", f"{wiped} removed")
    except Exception as e:
        result.fail("cleanup stale rows", e)
        print(json.dumps(result.summary(), indent=2))
        return 1

    try:
        # ============================================================
        # Phase A — Setup 3-node chain A → B → C
        # ============================================================
        try:
            a = _post(
                "/api/kanban/tasks",
                {"title": f"{TITLE_PREFIX}A", "priority": "high"},
            )
            a_id = a["id"]
            seeded_ids.append(a_id)

            b = _post(
                "/api/kanban/tasks",
                {
                    "title": f"{TITLE_PREFIX}B",
                    "priority": "high",
                    "depends_on_task_id": a_id,
                },
            )
            b_id = b["id"]
            seeded_ids.append(b_id)

            c = _post(
                "/api/kanban/tasks",
                {
                    "title": f"{TITLE_PREFIX}C",
                    "priority": "high",
                    "depends_on_task_id": b_id,
                },
            )
            c_id = c["id"]
            seeded_ids.append(c_id)

            _backdate_updated_at(seeded_ids, minutes=15)
            result.ok("seed 3-node chain", f"A={a_id}, B={b_id}, C={c_id}")
        except Exception as e:
            result.fail("Phase A — seed chain", e)
            return 1

        # ============================================================
        # Phase B — Dependency gate at backlog state
        # ============================================================
        try:
            from tools.genesis.reflexes.kanban import _get_due_tasks

            due = _get_due_tasks()
            due_ids = {t["id"] for t in due}
            # A must be in the result (eligible); B and C must be absent.
            assert a_id in due_ids, f"A missing from due tasks; got {due_ids}"
            assert b_id not in due_ids, f"B should be blocked; got {due_ids}"
            assert c_id not in due_ids, f"C should be blocked; got {due_ids}"
            result.ok(
                "listener excludes blocked tasks",
                "A eligible, B/C blocked",
            )
        except Exception as e:
            result.fail("Phase B — listener gate at backlog", e)

        try:
            by_id = _list_tasks_by_id()
            assert by_id[a_id]["is_blocked"] is False
            assert by_id[b_id]["is_blocked"] is True
            assert by_id[b_id]["depends_on_task_id"] == a_id
            assert by_id[b_id]["depends_on_title"] == f"{TITLE_PREFIX}A"
            assert by_id[b_id]["depends_on_status"] == "backlog"
            assert by_id[c_id]["is_blocked"] is True
            assert by_id[c_id]["depends_on_status"] == "backlog"
            result.ok("API is_blocked at backlog state")
        except Exception as e:
            result.fail("Phase B — API shape at backlog", e)

        # ============================================================
        # Phase C — Parent reaches in_progress (not done)
        # ============================================================
        try:
            _post(f"/api/kanban/tasks/{a_id}/move", {"status": "in_progress"})
            _backdate_updated_at([b_id, c_id], minutes=15)

            due = _get_due_tasks()
            due_ids = {t["id"] for t in due}
            # Critical: in_progress parent must NOT unblock children —
            # the gate checks dep.status='done' specifically.
            assert b_id not in due_ids, (
                f"B unblocked while parent only in_progress; got {due_ids}"
            )
            result.ok(
                "in_progress parent keeps child blocked",
                "gate is status='done', not !backlog",
            )
        except Exception as e:
            result.fail("Phase C — in_progress parent edge case", e)

        try:
            by_id = _list_tasks_by_id()
            assert by_id[b_id]["is_blocked"] is True
            assert by_id[b_id]["depends_on_status"] == "in_progress"
            result.ok(
                "API surfaces in_progress dep status",
                "B.depends_on_status=in_progress, is_blocked=True",
            )
        except Exception as e:
            result.fail("Phase C — API reflects in_progress state", e)

        # ============================================================
        # Phase D — Parent reaches done
        # ============================================================
        try:
            _post(f"/api/kanban/tasks/{a_id}/move", {"status": "done"})
            _backdate_updated_at([b_id, c_id], minutes=15)

            due = _get_due_tasks()
            due_ids = {t["id"] for t in due}
            assert b_id in due_ids, f"B should be eligible after A done; got {due_ids}"
            assert c_id not in due_ids, f"C still blocked by B; got {due_ids}"
            result.ok("done parent unblocks child", "B eligible, C still blocked")
        except Exception as e:
            result.fail("Phase D — chain unblocks A→B", e)

        try:
            by_id = _list_tasks_by_id()
            assert by_id[b_id]["is_blocked"] is False
            assert by_id[b_id]["depends_on_status"] == "done"
            assert by_id[c_id]["is_blocked"] is True
            assert by_id[c_id]["depends_on_status"] == "backlog"
            result.ok("API shape after A→done")
        except Exception as e:
            result.fail("Phase D — API shape after A→done", e)

        # ============================================================
        # Phase E — Middle node reaches done
        # ============================================================
        try:
            _post(f"/api/kanban/tasks/{b_id}/move", {"status": "done"})
            _backdate_updated_at([c_id], minutes=15)

            due = _get_due_tasks()
            due_ids = {t["id"] for t in due}
            assert c_id in due_ids, f"C should be eligible after B done; got {due_ids}"
            result.ok("chain fully unblocks", "C now eligible")
        except Exception as e:
            result.fail("Phase E — chain unblocks B→C", e)

        try:
            by_id = _list_tasks_by_id()
            assert by_id[c_id]["is_blocked"] is False
            assert by_id[c_id]["depends_on_status"] == "done"
            result.ok("API shape after B→done")
        except Exception as e:
            result.fail("Phase E — API shape after B→done", e)

        # Move C to done so it does not compete for the MAX_AUTO_PROMOTE
        # backlog slots in Phase F, which could crowd out the freshly
        # created P task and cause a spurious assertion failure.
        try:
            _post(f"/api/kanban/tasks/{c_id}/move", {"status": "done"})
            result.ok("Phase E epilogue — C moved to done before Phase F")
        except Exception as e:
            result.fail("Phase E epilogue — move C to done", e)

        # ============================================================
        # Phase F — Scheduled-and-blocked edge case
        # ============================================================
        try:
            past_iso = (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()
            p = _post(
                "/api/kanban/tasks",
                {"title": f"{TITLE_PREFIX}P", "priority": "high"},
            )
            p_id = p["id"]
            seeded_ids.append(p_id)

            d = _post(
                "/api/kanban/tasks",
                {
                    "title": f"{TITLE_PREFIX}D",
                    "priority": "high",
                    "status": "scheduled",
                    "scheduled_at": past_iso,
                    "depends_on_task_id": p_id,
                },
            )
            d_id = d["id"]
            seeded_ids.append(d_id)
            _backdate_updated_at([p_id, d_id], minutes=15)

            due = _get_due_tasks()
            due_ids = {t["id"] for t in due}
            # P is in backlog, unblocked → should appear.
            # D is scheduled, due, but blocked by P → should NOT appear.
            assert p_id in due_ids, f"P missing; got {due_ids}"
            assert d_id not in due_ids, (
                f"D blocked scheduled task leaked past listener gate; got {due_ids}"
            )
            result.ok("scheduled + blocked task is gated", "D excluded")
        except Exception as e:
            result.fail("Phase F — scheduled+blocked edge case", e)

        # ============================================================
        # Phase G — Browser render verification
        # ============================================================
        # Create a fresh blocked child E (depends on P, still backlog) so
        # we can verify the badge renders at this exact moment in time
        # without relying on cached screenshots from earlier.
        driver = None
        try:
            e = _post(
                "/api/kanban/tasks",
                {
                    "title": f"{TITLE_PREFIX}E",
                    "priority": "high",
                    "depends_on_task_id": p_id,
                },
            )
            e_id = e["id"]
            seeded_ids.append(e_id)

            driver = create_driver()
            driver.get(f"{BASE_URL}/")
            time.sleep(2)  # wait for initial fetch

            # E must render with the blocked badge (P is still in backlog).
            card_e = _wait_for_card(driver, f"{TITLE_PREFIX}E", timeout=10)
            if card_e is None:
                raise AssertionError(f"{TITLE_PREFIX}E card never rendered")
            if "blocked by" not in card_e.text.lower():
                raise AssertionError(
                    f"{TITLE_PREFIX}E missing blocked badge: {card_e.text!r}"
                )
            opacity_e = float(card_e.value_of_css_property("opacity"))
            if opacity_e >= 1.0:
                raise AssertionError(f"E not dimmed: opacity={opacity_e}")
            result.ok(
                "blocked child renders with badge + dimmed",
                f"opacity={opacity_e:.2f}",
            )

            # P (the parent, still backlog) must render WITHOUT a badge.
            card_p = _wait_for_card(driver, f"{TITLE_PREFIX}P", timeout=5)
            if card_p is None:
                raise AssertionError(f"{TITLE_PREFIX}P card never rendered")
            if "blocked by" in card_p.text.lower():
                raise AssertionError(
                    f"{TITLE_PREFIX}P should NOT be blocked: {card_p.text!r}"
                )
            opacity_p = float(card_p.value_of_css_property("opacity"))
            if opacity_p < 1.0:
                raise AssertionError(f"P should not be dimmed: opacity={opacity_p}")
            result.ok("unblocked parent renders without badge")

            # Now progress P → done and reload; E should drop the badge.
            _post(f"/api/kanban/tasks/{p_id}/move", {"status": "done"})
            time.sleep(0.5)  # let the SSE broadcast settle
            driver.refresh()
            time.sleep(2)

            card_e2 = _wait_for_card(driver, f"{TITLE_PREFIX}E", timeout=10)
            if card_e2 is None:
                raise AssertionError(f"{TITLE_PREFIX}E missing after refresh")
            if "blocked by" in card_e2.text.lower():
                raise AssertionError(
                    f"{TITLE_PREFIX}E still shows badge after P done: {card_e2.text!r}"
                )
            opacity_e2 = float(card_e2.value_of_css_property("opacity"))
            if opacity_e2 < 1.0:
                raise AssertionError(
                    f"E still dimmed after P done: opacity={opacity_e2}"
                )
            result.ok(
                "badge clears when dependency done",
                f"opacity={opacity_e2:.2f}",
            )

            # Screenshot at the final state.
            shot = SCREENSHOT_DIR / "kanban-depends-on-full-lifecycle-desktop.png"
            driver.save_screenshot(str(shot))
            result.ok("screenshot", str(shot))

            errors = check_js_errors(driver)
            if errors:
                result.fail("no SEVERE JS errors", errors[0])
            else:
                result.ok("no SEVERE JS errors")
        except Exception as e:
            result.fail("Phase G — browser render verification", e)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    finally:
        # ============================================================
        # Phase H — Cleanup
        # ============================================================
        cleanup_ok = 0
        for tid in seeded_ids:
            try:
                _delete(f"/api/kanban/tasks/{tid}")
                cleanup_ok += 1
            except Exception:
                pass
        # Belt-and-braces: also wipe any rows we might have missed
        try:
            extra = _cleanup_stale_e2e_rows()
            if extra:
                cleanup_ok += extra
        except Exception:
            pass
        print(f"\n[cleanup] removed {cleanup_ok} task(s)")

    summary = result.summary()
    print("\n" + "=" * 70)
    print(json.dumps(summary, indent=2))
    print("=" * 70)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
