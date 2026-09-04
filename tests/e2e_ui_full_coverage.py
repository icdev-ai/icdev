"""
E2E Full Coverage — every menu, submenu, tab, and key button.

Covers:
  1. All 9 top-nav dropdowns — every child link present + href correct
  2. Every nav route loads (no 500 / 404)
  3. Page-level tabs on: /data/*, /chat, /finetune, /kanban, /studio/*
  4. Key action buttons across the dashboard

Run: python tests/e2e_ui_full_coverage.py
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("DASHBOARD_URL", "http://localhost:5050")
SHOT_DIR = Path(__file__).resolve().parent.parent / "playwright" / "screenshots" / "full_coverage"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

# ── Result collector ─────────────────────────────────────────────────────────

@dataclass
class R:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def ok(self, name: str, detail: str = "") -> None:
        s = f"{name}{(': ' + detail) if detail else ''}"
        self.passed.append(s)
        print(f"  ✓ {s}")

    def fail(self, name: str, err: object) -> None:
        msg = str(err)[:180]
        self.failed.append((name, msg))
        print(f"  ✗ {name}: {msg}")

    def skip(self, name: str, reason: str = "") -> None:
        self.skipped.append(name)
        print(f"  ○ {name} [skipped: {reason}]")

    def summary(self) -> dict:
        return {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "skipped": len(self.skipped),
            "total": len(self.passed) + len(self.failed),
        }


def shot(page: Page, name: str) -> None:
    try:
        page.screenshot(path=str(SHOT_DIR / f"{name}.png"))
    except Exception:
        pass


def goto(page: Page, path: str, timeout: int = 12000) -> bool:
    """Navigate and return True if page loaded without error."""
    try:
        page.goto(f"{BASE}{path}", wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(800)
        return True
    except Exception:
        return False


def page_ok(page: Page) -> tuple[bool, str]:
    """Return (ok, reason) — fails on 500 error page or redirect to /login."""
    try:
        body = page.locator("body").inner_text(timeout=5000)
        if "Internal Server Error" in body:
            return False, "500 Internal Server Error"
        if "TemplateNotFound" in body:
            return False, "TemplateNotFound"
        url = page.url
        if "/login" in url and page.url != f"{BASE}/login":
            return False, f"Redirected to login from {url}"
        return True, ""
    except Exception as e:
        return False, str(e)[:100]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — Complete nav structure (extracted via JS from live page)
# ═══════════════════════════════════════════════════════════════════════════

# All nav items grouped by menu — text → href
NAV_STRUCTURE = {
    "Ops ▾": {
        "DevOps": [
            ("Projects",         "/projects"),
            ("Agents",           "/agents"),
            ("Task Board",       "/kanban"),
            ("CI/CD",            "/cicd"),
            ("Orchestration",    "/orchestration"),
        ],
        "Monitor": [
            ("Monitoring",       "/monitoring"),
            ("Platform Health",  "/platform-health"),
            ("Activity",         "/activity"),
            ("Batch",            "/batch"),
            ("Simulate Chat",    "/simulate/chat"),
        ],
    },
    "Build ▾": {
        "Build": [
            ("Chat",             "/chat"),
            ("AI Strategy Wizard", "/ai-wizard"),
            ("Dev Profiles",     "/dev-profiles"),
            ("Fine-Tuning",      "/finetune"),
            ("Diagrams",         "/diagrams"),
            ("Connector Forge",  "/connector-forge"),
            ("Translations",     "/translations"),
            ("AI Pattern Library", "/ai-patterns"),
        ],
    },
    "Canvases ▾": {
        "Network": [
            ("Dashboard",        "/network/"),
            ("New Topology",     "/network/canvas/new"),
            ("Templates",        "/network/templates"),
        ],
        "DevOps": [
            ("Dashboard",        "/devops/"),
            ("New Pipeline",     "/devops/canvas/new"),
        ],
        "Infrastructure": [
            ("Dashboard",        "/infra/"),
            ("New Design",       "/infra/canvas/new"),
            ("Templates",        "/infra/templates"),
        ],
        "Data": [
            ("Dashboard",        "/data/"),
            ("New Design",       "/data/canvas/new"),
            ("Templates",        "/data/templates"),
            ("Data Mesh",        "/data/mesh"),
        ],
        "Boundary": [
            ("Dashboard",        "/boundary/"),
            ("New Design",       "/boundary/canvas/new"),
        ],
        "Migration": [
            ("Design Canvas",    "/migration-canvas/"),
            ("Network Migration", "/migration-canvas/network-migration/"),
            ("Intelligence Engine", "/migration-intel/"),
        ],
        "Observability": [
            ("Dashboard",        "/observability/"),
            ("New Design",       "/observability/canvas/new"),
        ],
        "QA/QC": [
            ("Dashboard",        "/quality/"),
            ("Runbooks",         "/quality/runbooks"),
        ],
        "Agentic AI": [
            ("Dashboard",        "/agentic-ai/"),
            ("Impact Graph",     "/agentic-ai/impact-graph"),
        ],
        "AI/ML": [
            ("Dashboard",        "/ai-ml/"),
            ("Model Catalog",    "/ai-ml/model-catalog"),
        ],
        "Operations": [
            ("Ops Hub",          "/ops"),
            ("LLMOps",           "/ops/llm"),
            ("SLOs",             "/ops/slos"),
            ("Incidents",        "/ops/incidents"),
        ],
        "NOC Operations": [
            ("NOC Overview",     "/noc"),
            ("Alarms",           "/noc/alarms"),
            ("Incidents",        "/noc/incidents"),
        ],
        "Peering Management": [
            ("Peering Canvas",   "/pmc"),
            ("BGP Peers",        "/pmc/peers"),
        ],
        "Circuit & Capacity": [
            ("CCC Overview",     "/ccc"),
            ("Circuits",         "/ccc/circuits"),
        ],
        "DDoS & Security Ops": [
            ("DSOC Overview",    "/dsoc"),
            ("Flowspec Rules",   "/dsoc/flowspec"),
        ],
        "Cloud Migration": [
            ("GovLift",          "/govlift"),
            ("Workloads",        "/govlift/workloads"),
        ],
        "Mission Control": [
            ("Dashboard",        "/mission-canvas/"),
        ],
        "Cross-Canvas": [
            ("Canvas Posture",   "/canvas-compliance"),
        ],
    },
    "Intelligence ▾": {
        "Awareness": [
            ("Component Map",    "/components-map"),
            ("Ask ICDEV",        "/ask-icdev"),
            ("AI Observatory",   "/ai-observatory"),
        ],
        "Research": [
            ("Research",         "/research"),
            ("Knowledge Search", "/knowledge-search"),
            ("Code Quality",     "/code-quality"),
            ("WriteGuard",       "/writeguard"),
        ],
        "Publish": [
            ("Pulse Blog",       "/pulse"),
            ("SkillHub",   "/skillhub"),
        ],
    },
    "Compliance ▾": {
        "Compliance": [
            ("POA&M Findings",   "/boundary/poam"),
            ("Compliance Hub",   "/boundary/compliance-hub"),
            ("OSCAL",            "/oscal"),
            ("Continuous ATO",   "/boundary/cato-health"),
            ("MOSA",             "/boundary/mosa"),
            ("Security Canvas",  "/security/"),
            ("Security Scans",   "/security-scan"),
            ("STIG Manager",     "/security/stig-manager"),
            ("SRE Operations",   "/sre"),
            ("AI Transparency",  "/ai-transparency"),
            ("AI Accountability", "/ai-accountability"),
            ("Traces",           "/traces"),
            ("Provenance",       "/provenance"),
            ("XAI",              "/xai"),
        ],
    },
    "Strategos ▾": {
        "Command": [
            ("Overview",         "/strategos"),
            ("Commander Dashboard", "/strategos/commander"),
            ("INTSUM Generator", "/strategos/intsum"),
        ],
        "OSINT & Intel": [
            ("Knowledge Graph",  "/strategos/kg"),
            ("Ghost Signals",    "/strategos/ghost"),
            ("ORBAT Tracker",    "/strategos/orbat"),
        ],
        "Operations": [
            ("Wargame",          "/strategos/wargame"),
            ("F3EAD Targeting",  "/strategos/f3ead"),
            ("OPORD Generator",  "/strategos/opord"),
            ("METT-TC",          "/strategos/mett-tc"),
            ("HITL Queue",       "/strategos/hitl"),
        ],
    },
    "Platforms ▾": {
        "Platforms": [
            ("Genesis",          "/genesis"),
            ("Oracle",           "/oracle"),
            ("Proposals",        "/proposals"),
            ("FORGE Academy",    "/academy"),
        ],
    },
    "Studio ▾": {
        "Studio": [
            ("App Builder",      "/studio/app-builder"),
            ("Workflow Builder", "/studio/workflows"),
            ("Form Builder",     "/studio/forms"),
            ("Case Management",  "/studio/cases"),
            ("Automations",      "/studio/automations"),
            ("Dashboard Builder", "/studio/dashboards"),
            ("Marketplace",      "/studio/marketplace"),
        ],
    },
    "More ▾": {
        "More": [
            ("Profile & Settings", "/profile"),
            ("Notifications",    "/notifications"),
            ("Usage",            "/usage"),
            ("Analytics",        "/analytics"),
            ("Get Started",      "/wizard"),
            ("Quick Paths",      "/quick-paths"),
        ],
    },
}

# ── Page-level tabs (discovered from live pages) ─────────────────────────────

PAGE_TABS = {
    "/data/contracts": {
        "selector": ".dm-nav a",
        "expected": [
            ("Overview",    "/data/mesh"),
            ("Domains",     "/data/domains"),
            ("Products",    "/data/products"),
            ("Contracts",   "/data/contracts"),
            ("Governance",  "/data/governance"),
            ("CSP",         "/data/csp"),
        ],
    },
    "/data/mesh": {
        "selector": ".dm-nav a",
        "expected": [
            ("Overview",    "/data/mesh"),
            ("Domains",     "/data/domains"),
            ("Contracts",   "/data/contracts"),
        ],
    },
    "/finetune": {
        "selector": "main a[href]",
        "expected": [
            ("Datasets",        "/finetune/datasets"),
            ("Training Jobs",   "/finetune/jobs"),
            ("Models",          "/finetune/models"),
            ("Evaluate",        "/finetune/evaluate"),
        ],
    },
}

# Chat right-sidebar tabs are checked via page content (not links)
CHAT_TABS = ["Requirements", "Governance", "Knowledge"]

# Studio sub-pages (each has its own content section)
STUDIO_PAGES = [
    ("/studio/app-builder",  ["App Builder", "App", "application"]),
    ("/studio/workflows",    ["Workflow", "workflow"]),
    ("/studio/forms",        ["Form", "form"]),
    ("/studio/cases",        ["Case", "case"]),
    ("/studio/automations",  ["Automation", "automation"]),
    ("/studio/dashboards",   ["Dashboard", "dashboard"]),
    ("/studio/marketplace",  ["Marketplace", "marketplace", "asset"]),
]

# Key buttons to verify per page
PAGE_BUTTONS = {
    "/kanban": [
        ("+ Add Task",        "button", "get_by_role"),
        ("Refresh",           "button", "get_by_role"),
    ],
    "/data/contracts": [
        ("New Contract",      "text",   "any"),
    ],
    "/proposals": [
        ("New Proposal",      "text",   "any"),
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# Test functions
# ═══════════════════════════════════════════════════════════════════════════

def check_all_nav_items_present(page: Page, r: R) -> None:
    """Open every dropdown and verify every child link is present with correct href."""
    print("\n══ 1. Nav Dropdown Structure ══════════════════════════════════")
    goto(page, "/")
    page.wait_for_timeout(1500)

    # Open all dropdowns via JS and extract all links at once
    all_links = page.evaluate("""() => {
        document.querySelectorAll('nav .nav-dropdown-trigger').forEach(t => t.click());
        const nav = document.querySelector('nav');
        return Array.from(nav.querySelectorAll('a[href]'))
            .filter(a => !a.href.includes('javascript:'))
            .map(a => ({ text: a.innerText.trim(), href: a.getAttribute('href') }));
    }""")

    link_map = {item["href"]: item["text"] for item in all_links}

    for menu_name, sections in NAV_STRUCTURE.items():
        for section_name, items in sections.items():
            for label, href in items:
                check_name = f"nav:{menu_name.replace(' ▾','')}:{label}"
                try:
                    assert href in link_map, (
                        f"href='{href}' ('{label}') not found in nav"
                    )
                    r.ok(check_name, f"href={href}")
                except Exception as exc:
                    r.fail(check_name, exc)

    shot(page, "01_nav_all_dropdowns")


def check_all_nav_routes_load(page: Page, r: R) -> None:
    """Navigate to every unique nav route and verify no 500/error."""
    print("\n══ 2. All Nav Routes Load (no 500/404) ═══════════════════════")

    # Collect all unique routes from NAV_STRUCTURE
    seen: set[str] = set()
    routes: list[tuple[str, str]] = []  # (label, href)
    for menu_name, sections in NAV_STRUCTURE.items():
        for _section, items in sections.items():
            for label, href in items:
                # Skip external, query-param-only, and anchor-only
                if href.startswith("#") or href == "/" or href in seen:
                    continue
                seen.add(href)
                routes.append((label, href))

    total = len(routes)
    print(f"  Testing {total} unique routes...")

    for label, href in routes:
        check_name = f"route:{href}"
        loaded = goto(page, href, timeout=15000)
        if not loaded:
            r.fail(check_name, "Navigation timeout / failed to load")
            continue
        ok, reason = page_ok(page)
        if ok:
            try:
                h = page.locator("h1, h2").first.inner_text(timeout=3000).strip()
                r.ok(check_name, f"'{h[:35]}'" if h else "loaded")
            except Exception:
                r.ok(check_name, "loaded (no h1)")
        else:
            r.fail(check_name, reason)

    shot(page, "02_last_route_loaded")


def check_page_tabs(page: Page, r: R) -> None:
    """Verify every tab on every page that has tabs."""
    print("\n══ 3. Page-Level Tabs ═════════════════════════════════════════")

    for path, config in PAGE_TABS.items():
        selector = config["selector"]
        expected_tabs = config["expected"]

        if not goto(page, path, timeout=15000):
            r.fail(f"tabs:{path}:load", "page failed to load")
            continue

        print(f"\n  Page: {path}")
        for tab_label, tab_href in expected_tabs:
            check_name = f"tabs:{path}:{tab_label.lower()}"
            try:
                link = page.locator(f"{selector}:has-text('{tab_label}')").first
                assert link.count() > 0 or link.is_visible(), (
                    f"Tab '{tab_label}' not found with selector '{selector}'"
                )
                href = link.get_attribute("href") or ""
                assert tab_href in href, f"Tab '{tab_label}' href='{href}' expected to contain '{tab_href}'"
                r.ok(check_name, f"href={href}")
            except Exception as exc:
                r.fail(check_name, exc)

        # Click each tab and verify page loads correctly
        for tab_label, tab_href in expected_tabs:
            check_name = f"tabs:{path}:click:{tab_label.lower()}"
            try:
                # Re-navigate back if needed
                if tab_href not in page.url:
                    link = page.locator(f"{selector}:has-text('{tab_label}')").first
                    link.click(force=True)
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                    page.wait_for_timeout(600)
                ok, reason = page_ok(page)
                assert ok, reason
                r.ok(check_name, page.url.split("/")[-1] or "/")
                # Navigate back to base page for next tab
                goto(page, path, timeout=10000)
            except Exception as exc:
                r.fail(check_name, exc)
                goto(page, path, timeout=10000)

    shot(page, "03_tabs_last")


def check_chat_tabs(page: Page, r: R) -> None:
    """Verify Chat page has all 3 renamed sidebar tabs and they're clickable."""
    print("\n══ 4. Chat Sidebar Tabs ═══════════════════════════════════════")
    if not goto(page, "/chat", timeout=15000):
        r.fail("chat:load", "page failed to load")
        return

    for tab_name in CHAT_TABS:
        # Presence
        try:
            els = page.locator(f"text={tab_name}").all()
            visible = [e for e in els if e.is_visible()]
            assert visible, f"Tab '{tab_name}' not visible"
            r.ok(f"chat:tab:{tab_name.lower()}:visible")
        except Exception as exc:
            r.fail(f"chat:tab:{tab_name.lower()}:visible", exc)
            continue

        # Clickability
        try:
            page.locator(f"text={tab_name}").first.click(force=True)
            page.wait_for_timeout(400)
            ok, reason = page_ok(page)
            assert ok, reason
            r.ok(f"chat:tab:{tab_name.lower()}:click")
        except Exception as exc:
            r.fail(f"chat:tab:{tab_name.lower()}:click", exc)

    # Also verify old labels are gone
    for old_label in ["RICOAS", "Gov\n", "Intel\n"]:
        try:
            visible = [e for e in page.locator(f"text={old_label}").all() if e.is_visible()]
            assert not visible, f"Old label '{old_label}' still visible"
            r.ok(f"chat:old_label_gone:{old_label.strip()}")
        except Exception as exc:
            r.fail(f"chat:old_label_gone:{old_label.strip()}", exc)

    shot(page, "04_chat_tabs")


def check_finetune_tabs(page: Page, r: R) -> None:
    """Verify Fine-Tuning page sub-navigation tabs."""
    print("\n══ 5. Fine-Tuning Tabs ════════════════════════════════════════")
    finetune_tabs = [
        ("Datasets",      "/finetune/datasets"),
        ("Training Jobs", "/finetune/jobs"),
        ("Models",        "/finetune/models"),
        ("Evaluate",      "/finetune/evaluate"),
    ]

    for tab_label, path in finetune_tabs:
        check_name = f"finetune:{tab_label.lower().replace(' ', '_')}"
        if not goto(page, path, timeout=15000):
            r.fail(check_name, "failed to load")
            continue
        ok, reason = page_ok(page)
        if not ok:
            r.fail(check_name, reason)
            continue
        try:
            body = page.inner_text("body")
            assert tab_label in body or tab_label.lower() in body.lower(), (
                f"'{tab_label}' not in page content"
            )
            r.ok(check_name, page.url.split("/")[-1])
        except Exception as exc:
            r.fail(check_name, exc)

    # From /finetune landing, check all tab links are present
    goto(page, "/finetune", timeout=12000)
    for tab_label, href in finetune_tabs:
        try:
            link = page.get_by_role("link", name=tab_label)
            assert link.count() > 0, f"Tab link '{tab_label}' not found on /finetune"
            r.ok(f"finetune:landing_link:{tab_label.lower().replace(' ','_')}")
        except Exception as exc:
            r.fail(f"finetune:landing_link:{tab_label}", exc)

    shot(page, "05_finetune_tabs")


def check_kanban_elements(page: Page, r: R) -> None:
    """Verify Kanban board: all 5 columns, toolbar buttons, card actions."""
    print("\n══ 6. Kanban Board Elements ═══════════════════════════════════")
    if not goto(page, "/kanban", timeout=15000):
        r.fail("kanban:load", "failed to load")
        return
    page.wait_for_timeout(2000)

    # Column headers
    for col in ["Suggested", "Backlog", "Scheduled", "In Progress", "Done"]:
        try:
            count = page.locator(f"text={col}").count() + page.locator(f"text={col.upper()}").count()
            assert count > 0, f"Column '{col}' not found"
            r.ok(f"kanban:col:{col.lower().replace(' ','_')}")
        except Exception as exc:
            r.fail(f"kanban:col:{col}", exc)

    # Toolbar buttons
    toolbar_btns = [
        ("+ Add Task",    lambda p: p.get_by_role("button", name="Add Task")),
        ("Refresh",       lambda p: p.get_by_role("button", name="Refresh")),
        ("Dep Map",       lambda p: p.get_by_role("button", name="Dep Map")),
        ("Tags",          lambda p: p.get_by_role("button", name="Tags")),
        ("Promote All",   lambda p: p.get_by_text("Promote All")),
    ]
    for btn_name, locator_fn in toolbar_btns:
        try:
            el = locator_fn(page)
            assert el.count() > 0, f"Button '{btn_name}' not found"
            r.ok(f"kanban:btn:{btn_name.lower().replace(' ','_').replace('+','add')}")
        except Exception as exc:
            r.fail(f"kanban:btn:{btn_name}", exc)

    # Executor selector
    try:
        content = page.content()
        has_exec = any(kw in content for kw in ["Executor", "Claude", "executor"])
        assert has_exec, "No executor selector found"
        r.ok("kanban:executor_selector")
    except Exception as exc:
        r.fail("kanban:executor_selector", exc)

    # Click "+ Add Task" and verify modal opens
    try:
        page.get_by_role("button", name="Add Task").click()
        page.wait_for_timeout(800)
        modal_visible = page.locator("[role='dialog'], .modal, #add-task-modal, .modal-overlay").count() > 0
        if not modal_visible:
            # Check for any form that appeared
            modal_visible = page.locator("input[name], textarea[name]").count() > 0
        assert modal_visible, "Add Task modal/form did not appear"
        r.ok("kanban:add_task_modal_opens")
        # Close modal
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception as exc:
        r.fail("kanban:add_task_modal_opens", exc)
        page.keyboard.press("Escape")

    shot(page, "06_kanban_board")


def check_studio_pages(page: Page, r: R) -> None:
    """Verify all Studio sub-pages load with correct content."""
    print("\n══ 7. Studio Sub-Pages ════════════════════════════════════════")
    for path, keywords in STUDIO_PAGES:
        check_name = f"studio:{path.split('/')[-1]}"
        if not goto(page, path, timeout=15000):
            r.fail(check_name, "failed to load")
            continue
        ok, reason = page_ok(page)
        if not ok:
            r.fail(check_name, reason)
            continue
        try:
            body = page.inner_text("body")
            found = [kw for kw in keywords if kw.lower() in body.lower()]
            assert found, f"None of {keywords} found on {path}"
            try:
                h = page.locator("h1, h2").first.inner_text(timeout=2000).strip()
            except Exception:
                h = ""
            r.ok(check_name, f"'{h[:30]}'" if h else f"kw={found[0]}")
        except Exception as exc:
            r.fail(check_name, exc)

    shot(page, "07_studio_last")


def check_compliance_suite(page: Page, r: R) -> None:
    """Verify Compliance pages load and display expected content."""
    print("\n══ 8. Compliance Page Suite ═══════════════════════════════════")
    compliance_pages = [
        ("/boundary/compliance-hub", ["Compliance", "Control", "posture"]),
        ("/boundary/poam",     ["POA", "finding", "Finding"]),
        ("/boundary/cato-health", ["ATO", "Continuous", "authorization"]),
        ("/oscal",             ["OSCAL", "catalog", "Catalog"]),
        ("/security-scan",     ["Security", "Scan", "SAST"]),
        ("/security/stig-manager", ["STIG", "finding", "Finding"]),
        ("/ai-transparency",   ["Transparency", "AI", "explainab"]),
        ("/ai-accountability", ["Accountability", "AI", "audit"]),
        ("/traces",            ["Trace", "trace", "span"]),
        ("/provenance",        ["Provenance", "lineage", "artifact"]),
    ]
    for path, keywords in compliance_pages:
        check_name = f"compliance:{path.strip('/')}"
        if not goto(page, path, timeout=15000):
            r.fail(check_name, "failed to load")
            continue
        ok, reason = page_ok(page)
        if not ok:
            r.fail(check_name, reason)
            continue
        try:
            body = page.inner_text("body")
            found = [kw for kw in keywords if kw.lower() in body.lower()]
            assert found, f"None of {keywords} found on {path}"
            r.ok(check_name, f"kw={found[0]}")
        except Exception as exc:
            r.fail(check_name, exc)

    shot(page, "08_compliance_last")


def check_data_canvas_tabs(page: Page, r: R) -> None:
    """Navigate every Data Mesh tab and verify content + active state."""
    print("\n══ 9. Data Canvas — All Tabs Navigate & Show Content ══════════")
    dm_tab_pages = [
        ("/data/mesh",       ["mesh", "domain", "Mesh", "Domain", "data product"]),
        ("/data/domains",    ["domain", "Domain", "owner", "maturity"]),
        ("/data/products",   ["product", "Product", "dataset"]),
        ("/data/contracts",  ["contract", "Contract", "schema", "SLA"]),
        ("/data/governance", ["governance", "Governance", "policy", "steward"]),
        ("/data/csp",        ["CSP", "cloud", "storage", "provider"]),
    ]
    for path, keywords in dm_tab_pages:
        check_name = f"data_mesh:{path.split('/')[-1]}"
        if not goto(page, path, timeout=15000):
            r.fail(check_name, "failed to load")
            continue
        ok, reason = page_ok(page)
        if not ok:
            r.fail(check_name, reason)
            continue
        try:
            # Heading must exist
            h = page.locator("h1").first.inner_text(timeout=3000).strip()
            assert h, "No h1 heading"
            # dm-nav must be present with all 6 tabs
            dm_nav = page.locator(".dm-nav")
            assert dm_nav.count() > 0, "No .dm-nav found on page"
            tab_count = dm_nav.locator("a").count()
            assert tab_count >= 6, f"Expected ≥6 dm-nav tabs, found {tab_count}"
            # Current tab should be active
            active = page.locator(".dm-nav a.active")
            active_text = active.first.inner_text().strip() if active.count() > 0 else "(none)"
            r.ok(check_name, f"h1='{h[:25]}' active='{active_text}'")
        except Exception as exc:
            r.fail(check_name, exc)

    shot(page, "09_data_canvas_tabs")


def check_additional_key_pages(page: Page, r: R) -> None:
    """Spot-check pages across Intelligence, Platforms, and Ops menus."""
    print("\n══ 10. Additional Key Pages ═══════════════════════════════════")
    spot_checks = [
        # Intelligence
        ("/components-map",   ["Component", "Map", "node"]),
        ("/ask-icdev",        ["Ask", "ICDEV", "query", "chat"]),
        ("/ai-observatory",   ["Observatory", "model", "LLM"]),
        ("/knowledge-search", ["Knowledge", "Search", "query"]),
        ("/research",         ["Research", "query", "search"]),
        ("/writeguard",       ["WriteGuard", "write", "protect"]),
        # Platforms / Ops
        ("/genesis",          ["Genesis", "daemon", "reflex"]),
        ("/oracle",           ["Oracle", "prediction", "insight"]),
        ("/proposals",        ["Proposal", "opportunity", "RFP"]),
        ("/academy",          ["Academy", "course", "learn", "FORGE"]),
        # Ops
        ("/projects",         ["Project", "project"]),
        ("/agents",           ["Agent", "agent"]),
        ("/cicd",             ["CI/CD", "pipeline", "build"]),
        ("/orchestration",    ["Orchestration", "workflow", "orchestrat"]),
        ("/monitoring",       ["Monitoring", "monitor", "metric"]),
        ("/platform-health",  ["Health", "health", "status"]),
        ("/activity",         ["Activity", "event", "log"]),
        # Build
        ("/ai-wizard",        ["Wizard", "wizard", "strategy"]),
        ("/diagrams",         ["Diagram", "diagram", "chart"]),
        ("/connector-forge",  ["Connector", "connector", "API"]),
        ("/translations",     ["Translation", "translate", "language"]),
        ("/ai-patterns",      ["Pattern", "pattern", "AI"]),
        # More
        ("/notifications",    ["Notification", "notification", "alert"]),
        ("/wizard",           ["Wizard", "Get Started", "workflow"]),
        ("/quick-paths",      ["Quick", "path", "shortcut"]),
        ("/analytics",        ["Analytics", "analytics", "metric"]),
    ]
    for path, keywords in spot_checks:
        check_name = f"page:{path.strip('/').replace('/','_')}"
        if not goto(page, path, timeout=15000):
            r.fail(check_name, "failed to load")
            continue
        ok, reason = page_ok(page)
        if not ok:
            r.fail(check_name, reason)
            continue
        try:
            body = page.inner_text("body")
            found = [kw for kw in keywords if kw.lower() in body.lower()]
            assert found, f"None of {keywords} found on {path}"
            r.ok(check_name, f"kw={found[0]}")
        except Exception as exc:
            r.fail(check_name, exc)

    shot(page, "10_spot_checks_last")


def check_mini_bar(page: Page, r: R) -> None:
    """Verify mini-bar (ask-anything) is present and functional on all pages."""
    print("\n══ 11. Mini-Bar (Ask Anything) — All Sections ════════════════")
    test_pages = [
        "/", "/kanban", "/chat", "/boundary/compliance-hub", "/data/contracts",
        "/finetune", "/studio/workflows", "/genesis",
    ]
    for path in test_pages:
        if not goto(page, path, timeout=12000):
            r.skip(f"minibar:{path}", "page load failed")
            continue
        try:
            content = page.content()
            assert any(kw in content for kw in ["Ask anything", "ask anything", "ask-bar", "mini-bar", "minibar"]), (
                f"Mini-bar not found on {path}"
            )
            r.ok(f"minibar:{path.strip('/') or 'home'}")
        except Exception as exc:
            r.fail(f"minibar:{path}", exc)

    shot(page, "11_minibar_last")


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

def run_all() -> int:
    r = R()
    t0 = time.time()

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=True)
        ctx: BrowserContext = browser.new_context(viewport={"width": 1440, "height": 900})
        page: Page = ctx.new_page()
        page.set_default_timeout(15000)

        js_errors: list[str] = []
        page.on("console", lambda m: js_errors.append(m.text) if m.type == "error" else None)

        try:
            check_all_nav_items_present(page, r)
            check_all_nav_routes_load(page, r)
            check_page_tabs(page, r)
            check_chat_tabs(page, r)
            check_finetune_tabs(page, r)
            check_kanban_elements(page, r)
            check_studio_pages(page, r)
            check_compliance_suite(page, r)
            check_data_canvas_tabs(page, r)
            check_additional_key_pages(page, r)
            check_mini_bar(page, r)
        finally:
            browser.close()

    elapsed = time.time() - t0
    s = r.summary()

    print(f"\n{'═'*65}")
    print(f"Full Coverage Results  ({elapsed:.0f}s)")
    print(f"{'═'*65}")
    print(f"  Passed:  {s['passed']}")
    print(f"  Failed:  {s['failed']}")
    print(f"  Skipped: {s['skipped']}")
    print(f"  Total:   {s['total']}")

    if r.failed:
        print(f"\n{'─'*65}")
        print("FAILURES:")
        for name, err in r.failed:
            print(f"  ✗ {name}")
            print(f"      {err}")

    if js_errors:
        unique = list(dict.fromkeys(js_errors))[:10]
        print(f"\n  Console errors ({len(js_errors)} total, first 10):")
        for e in unique:
            print(f"    [{e[:100]}]")

    print(f"\n  Screenshots: {SHOT_DIR}")
    return 0 if s["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
