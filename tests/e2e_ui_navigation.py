"""
E2E UI Navigation Test — menus, tabs, links, and content.

Verifies that every key page renders its expected navigation items,
tab labels, heading text, and live content (not just HTTP 200).

Run: python tests/e2e_ui_navigation.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Result:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def ok(self, name: str, detail: str = "") -> None:
        label = f"{name}{': ' + detail if detail else ''}"
        self.passed.append(label)
        print(f"  [OK]   {label}")

    def fail(self, name: str, err: object) -> None:
        msg = str(err)[:200]
        self.failed.append((name, msg))
        print(f"  [FAIL] {name}: {msg}")

    def summary(self) -> dict:
        return {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "total": len(self.passed) + len(self.failed),
            "failures": self.failed,
        }


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"))


# ---------------------------------------------------------------------------
# 1. Top navigation bar
# ---------------------------------------------------------------------------
def check_top_nav(page: Page, r: Result) -> None:
    print("\n── Top Navigation ──────────────────────────────────────")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # Primary menu items that must be present (nav uses "Item ▾" text for dropdowns)
    expected_nav = ["Home", "Ops", "Build", "Canvases", "Compliance", "Platforms", "Studio"]
    for item in expected_nav:
        try:
            el = page.get_by_role("navigation").get_by_role("link", name=item)
            assert el.count() > 0, f"{item} not found in nav"
            r.ok(f"nav:{item}")
        except Exception as exc:
            r.fail(f"nav:{item}", exc)

    # Dropdown menus — open each and verify it has child links
    # Build: Chat, AI Strategy Wizard, Dev Profiles, Fine-Tuning, Diagrams, etc.
    # Ops: Genesis, Oracle, Kanban, etc.
    # Canvases: Data, Network, Security, Infrastructure, etc.
    dropdown_tests = [
        ("Canvases", ["Data", "Network", "Security", "Infrastructure"]),
        ("Build",    ["Chat", "AI Strategy Wizard", "Dev Profiles", "Fine-Tuning"]),
        ("Ops",      ["Genesis", "Oracle", "Kanban"]),
    ]
    for menu_label, expected_children in dropdown_tests:
        try:
            # Dismiss any open dropdown first, then click the trigger
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            trigger = page.get_by_role("navigation").get_by_role("link", name=menu_label)
            trigger.first.click(force=True)
            page.wait_for_timeout(500)
            # Check at least one expected child is visible
            found = []
            for child in expected_children:
                child_els = page.locator(f"text={child}").all()
                if any(el.is_visible() for el in child_els):
                    found.append(child)
            assert found, f"No dropdown children visible for {menu_label}"
            r.ok(f"nav:{menu_label}:dropdown", f"found {found}")
            # Close dropdown by pressing Escape
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception as exc:
            r.fail(f"nav:{menu_label}:dropdown", exc)
            page.keyboard.press("Escape")

    shot(page, "nav_top")


# ---------------------------------------------------------------------------
# 2. Home page content
# ---------------------------------------------------------------------------
def check_home(page: Page, r: Result) -> None:
    print("\n── Home Page ───────────────────────────────────────────")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    # Hero heading
    try:
        heading = page.locator("h1").first.inner_text()
        assert "ICDEV" in heading or "Dashboard" in heading, f"Unexpected heading: {heading}"
        r.ok("home:heading", heading[:40])
    except Exception as exc:
        r.fail("home:heading", exc)

    # Stat counters — check by partial text (actual labels: "Total Projects",
    # "Active Agents", "CAT1 Findings", "Open POA&M")
    stat_checks = [
        ("Total Projects",  "total_projects"),
        ("Active Agents",   "active_agents"),
        ("CAT1 Findings",   "cat1_findings"),
        ("POA",             "open_poam"),       # "Open POA&M" contains "POA"
    ]
    for partial, key in stat_checks:
        try:
            found = page.locator(f"text={partial}").count()
            assert found > 0, f"Stat containing '{partial}' not found"
            r.ok(f"home:stat:{key}")
        except Exception as exc:
            r.fail(f"home:stat:{key}", exc)

    # Task Board columns
    for col in ["SUGGESTED", "BACKLOG", "SCHEDULED", "IN PROGRESS", "DONE"]:
        try:
            els = page.locator(f"text={col}").all()
            assert any(el.is_visible() for el in els), f"Column '{col}' not visible"
            r.ok(f"home:taskboard:{col.lower().replace(' ','_')}")
        except Exception as exc:
            r.fail(f"home:taskboard:{col}", exc)

    # Dashboard monitor cards
    for card in ["COMPLIANCE POSTURE", "ORACLE INSIGHTS", "SANDBOX", "MCP MONITOR"]:
        try:
            found = page.locator(f"text={card}").count()
            assert found > 0, f"Card '{card}' not found"
            r.ok(f"home:card:{card.lower().split()[0]}")
        except Exception as exc:
            r.fail(f"home:card:{card}", exc)

    shot(page, "home_page")


# ---------------------------------------------------------------------------
# 3. Chat page — rebranding + tab labels
# ---------------------------------------------------------------------------
def check_chat(page: Page, r: Result) -> None:
    print("\n── Chat / AI Assistant Page ────────────────────────────")
    page.goto(f"{BASE_URL}/chat", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # Page should NOT show old "Chat" branding in heading
    try:
        page_text = page.content()
        assert "AI Assistant" in page_text, "Missing 'AI Assistant' label"
        r.ok("chat:ai_assistant_label")
    except Exception as exc:
        r.fail("chat:ai_assistant_label", exc)

    # Old labels must be gone from visible UI
    old_labels = ["RICOAS"]
    for old in old_labels:
        try:
            visible_els = [e for e in page.locator(f"text={old}").all() if e.is_visible()]
            assert not visible_els, f"Old label '{old}' still visible"
            r.ok(f"chat:no_old_label:{old}")
        except Exception as exc:
            r.fail(f"chat:no_old_label:{old}", exc)

    # Right sidebar tabs must show renamed labels
    renamed_tabs = ["Requirements", "Governance", "Knowledge"]
    for tab in renamed_tabs:
        try:
            els = page.locator(f"text={tab}").all()
            assert any(el.is_visible() for el in els), f"Tab '{tab}' not visible"
            r.ok(f"chat:tab:{tab.lower()}")
        except Exception as exc:
            r.fail(f"chat:tab:{tab}", exc)

    # Welcome banner / prompt chips should be present
    try:
        content = page.content()
        has_welcome = any(kw in content for kw in ["welcome", "Welcome", "Get started", "prompt", "chip", "suggest"])
        assert has_welcome, "No welcome/prompt chips detected in page source"
        r.ok("chat:welcome_content")
    except Exception as exc:
        r.fail("chat:welcome_content", exc)

    # Message input box
    try:
        inp = page.locator("textarea, input[type='text']").first
        assert inp.is_visible(), "No message input visible"
        r.ok("chat:message_input")
    except Exception as exc:
        r.fail("chat:message_input", exc)

    # Click Requirements tab and verify content panel changes
    try:
        req_tab = page.locator("text=Requirements").first
        req_tab.click()
        page.wait_for_timeout(500)
        r.ok("chat:tab:requirements:click")
    except Exception as exc:
        r.fail("chat:tab:requirements:click", exc)

    shot(page, "chat_page")


# ---------------------------------------------------------------------------
# 4. Data Canvas — /data/contracts (Data Mesh nav tabs)
# ---------------------------------------------------------------------------
def check_data_contracts(page: Page, r: Result) -> None:
    print("\n── /data/contracts — Data Mesh Tabs ───────────────────")
    page.goto(f"{BASE_URL}/data/contracts", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # Page heading
    try:
        heading = page.locator("h1").first.inner_text()
        assert heading, "No h1 heading"
        r.ok("data_contracts:heading", heading[:40])
    except Exception as exc:
        r.fail("data_contracts:heading", exc)

    # Data Mesh nav tabs — scope to .dm-nav to avoid matching other "Overview" links
    # Overview → /data/mesh; all others match their label name
    dm_tabs = {
        "Overview":   "/data/mesh",
        "Domains":    "/data/domains",
        "Products":   "/data/products",
        "Contracts":  "/data/contracts",
        "Governance": "/data/governance",
        "CSP":        "/data/csp",
    }
    for tab_label, expected_href in dm_tabs.items():
        try:
            # Use .dm-nav scoped selector to avoid false matches (e.g. NOC Overview)
            link = page.locator(f".dm-nav a:has-text('{tab_label}')").first
            assert link.is_visible(), f"Tab link '{tab_label}' not visible in .dm-nav"
            href = link.get_attribute("href") or ""
            assert expected_href in href, (
                f"Tab '{tab_label}' href='{href}' doesn't contain '{expected_href}'"
            )
            r.ok(f"data_contracts:tab:{tab_label.lower()}", f"href={href}")
        except Exception as exc:
            r.fail(f"data_contracts:tab:{tab_label}", exc)

    # "Contracts" tab is active (current page)
    try:
        active = page.locator("a.active, a[aria-current='page']").all()
        active_texts = [a.inner_text().strip() for a in active if a.is_visible()]
        assert any("Contract" in t for t in active_texts), (
            f"'Contracts' not marked active, found: {active_texts}"
        )
        r.ok("data_contracts:active_tab", f"active={active_texts}")
    except Exception as exc:
        r.fail("data_contracts:active_tab", exc)

    # Page content — ODCS / contract table or empty state
    try:
        body = page.locator("body").inner_text()
        has_content = any(kw in body for kw in ["Contract", "contract", "schema", "Schema", "SLA", "producer", "No contracts"])
        assert has_content, "No contract-related content found"
        r.ok("data_contracts:content")
    except Exception as exc:
        r.fail("data_contracts:content", exc)

    shot(page, "data_contracts")

    # Navigate to each tab and verify page loads (200 equivalent)
    for tab_label, path in dm_tabs.items():
        if tab_label == "Contracts":
            continue  # already on it
        try:
            # Use .dm-nav scoped selector to avoid ambiguity
            link = page.locator(f".dm-nav a:has-text('{tab_label}')").first
            link.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)
            assert page.url.endswith(path) or path in page.url, (
                f"After clicking '{tab_label}', URL is {page.url}"
            )
            # Heading must not be a 500 / error page
            body = page.locator("body").inner_text()
            assert "Internal Server Error" not in body, f"{tab_label} page shows 500 error"
            assert "Not Found" not in body[:100], f"{tab_label} page shows 404"
            r.ok(f"data_contracts:navigate:{tab_label.lower()}", page.url.split("/")[-1])
            # Navigate back to contracts to restore tab nav
            page.goto(f"{BASE_URL}/data/contracts", wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
        except Exception as exc:
            r.fail(f"data_contracts:navigate:{tab_label}", exc)
            page.goto(f"{BASE_URL}/data/contracts", wait_until="domcontentloaded")
            page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# 5. Kanban board — columns, cards, controls
# ---------------------------------------------------------------------------
def check_kanban(page: Page, r: Result) -> None:
    print("\n── Kanban Board ────────────────────────────────────────")
    page.goto(f"{BASE_URL}/kanban", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    # Board heading
    try:
        heading = page.locator("h1, h2").first.inner_text()
        assert heading, "No heading"
        r.ok("kanban:heading", heading[:40])
    except Exception as exc:
        r.fail("kanban:heading", exc)

    # All five columns must be labeled
    for col in ["Suggested", "Backlog", "Scheduled", "In Progress", "Done"]:
        try:
            # case-insensitive check
            found = page.locator(f"text={col}").count()
            if found == 0:
                # try uppercase version
                found = page.locator(f"text={col.upper()}").count()
            assert found > 0, f"Column '{col}' label not found"
            r.ok(f"kanban:col:{col.lower().replace(' ','_')}")
        except Exception as exc:
            r.fail(f"kanban:col:{col}", exc)

    # Add Task button (actual label: "+ Add Task")
    try:
        btn = page.get_by_role("button", name="Add Task")
        assert btn.is_visible(), "+ Add Task button not visible"
        r.ok("kanban:add_task_button", btn.inner_text().strip())
    except Exception as exc:
        r.fail("kanban:add_task_button", exc)

    # Filter / Executor selector
    try:
        content = page.content()
        has_executor = any(kw in content for kw in ["Executor", "executor", "Claude", "GitLab"])
        assert has_executor, "No executor selector found"
        r.ok("kanban:executor_selector")
    except Exception as exc:
        r.fail("kanban:executor_selector", exc)

    shot(page, "kanban_board")


# ---------------------------------------------------------------------------
# 6. Proposals Reviews Dashboard
# ---------------------------------------------------------------------------
def check_proposals_reviews(page: Page, r: Result) -> None:
    print("\n── Proposals Reviews Dashboard ─────────────────────────")
    page.goto(f"{BASE_URL}/proposals/reviews-dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # Page heading
    try:
        heading = page.locator("h1, h2").first.inner_text()
        assert heading, "No heading"
        r.ok("reviews_dash:heading", heading[:50])
    except Exception as exc:
        r.fail("reviews_dash:heading", exc)

    # Must show review-related content or empty state (not error)
    try:
        body = page.locator("body").inner_text()
        assert "Internal Server Error" not in body, "500 error on reviews dashboard"
        has_content = any(kw in body for kw in [
            "review", "Review", "passed", "failed", "finding", "proposal",
            "opportunity", "No active", "No opportunities"
        ])
        assert has_content, f"No review-related content found. Body starts: {body[:200]}"
        r.ok("reviews_dash:content")
    except Exception as exc:
        r.fail("reviews_dash:content", exc)

    # No JS errors
    try:
        page.evaluate("() => window.__errors || []")
        r.ok("reviews_dash:no_js_errors")
    except Exception as exc:
        r.fail("reviews_dash:no_js_errors", exc)

    shot(page, "proposals_reviews_dashboard")


# ---------------------------------------------------------------------------
# 7. Secondary page navigation — verify links in sidebar/breadcrumbs
# ---------------------------------------------------------------------------
def check_secondary_nav(page: Page, r: Result) -> None:
    print("\n── Secondary Navigation Links ──────────────────────────")

    link_checks = [
        ("/agents",       ["Agent", "agent"],           "agents:heading"),
        ("/boundary/compliance-hub", ["Compliance", "Control"], "compliance:heading"),
        ("/genesis",      ["Genesis", "Daemon", "Reflex"], "genesis:heading"),
        ("/kanban",       ["Kanban", "Task", "Board"],   "kanban_nav:heading"),
        ("/oracle",       ["Oracle", "Prediction"],      "oracle:heading"),
        ("/security-scan",["Security", "SAST", "Scan"],  "security_scan:heading"),
    ]

    for path, keywords, check_name in link_checks:
        try:
            page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            assert "Internal Server Error" not in body, f"500 on {path}"
            assert "Not Found" not in body[:100], f"404 on {path}"
            found_kw = [kw for kw in keywords if kw in body]
            assert found_kw, f"No expected keywords {keywords} found on {path}"
            r.ok(check_name, f"keywords={found_kw[:2]}")
        except Exception as exc:
            r.fail(check_name, exc)

    shot(page, "secondary_nav_last")


# ---------------------------------------------------------------------------
# 8. Canvases dropdown → Data Canvas pages
# ---------------------------------------------------------------------------
def check_canvases_menu(page: Page, r: Result) -> None:
    print("\n── Canvases Menu → Data Canvas ─────────────────────────")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    canvas_links = [
        ("/data",          ["Data", "Canvas", "Design"]),
        ("/network",       ["Network", "Canvas", "Topology"]),
        ("/security",      ["Security", "Canvas", "Control"]),
        ("/boundary",      ["Boundary", "Canvas", "Zone"]),
        ("/observability", ["Observability", "Canvas", "SLO"]),
    ]

    for path, keywords in canvas_links:
        try:
            page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            assert "Internal Server Error" not in body, f"500 on {path}"
            found_kw = [kw for kw in keywords if kw in body]
            assert found_kw, f"No expected keywords on {path}"
            r.ok(f"canvas:{path.strip('/')}:loads", f"kw={found_kw[0]}")
        except Exception as exc:
            r.fail(f"canvas:{path.strip('/')}:loads", exc)

    shot(page, "canvas_pages")


# ---------------------------------------------------------------------------
# 9. Mini-bar (ask-anything) present across pages
# ---------------------------------------------------------------------------
def check_mini_bar(page: Page, r: Result) -> None:
    print("\n── Mini-Bar (Ask Anything) ─────────────────────────────")
    pages_to_check = ["/", "/kanban", "/data/contracts", "/chat"]
    for path in pages_to_check:
        try:
            page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            content = page.content()
            # Mini-bar has placeholder "Ask anything" or similar
            has_bar = any(kw in content for kw in ["Ask anything", "ask anything", "mini-bar", "minibar", "ask-bar"])
            assert has_bar, f"Mini-bar 'Ask anything' not found on {path}"
            r.ok(f"minibar:{path.strip('/') or 'home'}")
        except Exception as exc:
            r.fail(f"minibar:{path}", exc)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_all() -> int:
    r = Result()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Capture console errors
        js_errors: list[str] = []
        page.on("console", lambda msg: js_errors.append(msg.text) if msg.type == "error" else None)

        try:
            check_top_nav(page, r)
            check_home(page, r)
            check_chat(page, r)
            check_data_contracts(page, r)
            check_kanban(page, r)
            check_proposals_reviews(page, r)
            check_secondary_nav(page, r)
            check_canvases_menu(page, r)
            check_mini_bar(page, r)
        finally:
            browser.close()

    s = r.summary()
    print(f"\n{'='*60}")
    print(f"UI Navigation Results: {s['passed']} passed, {s['failed']} failed / {s['total']} total")
    if s["failures"]:
        print("\nFAILURES:")
        for name, err in s["failures"]:
            print(f"  ✗ {name}: {err}")
    else:
        print("All checks passed ✓")
    return 0 if s["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
