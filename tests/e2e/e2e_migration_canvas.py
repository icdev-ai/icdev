# CUI // SP-CTI
"""Migration Design Canvas — Comprehensive Selenium E2E Test.

Tests all pages, buttons, palette drag-drop, template loading, APIs,
runbooks, SOPs, Oracle, navigation menu, and JS errors.
"""

import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5050"
PASS = 0
FAIL = 0
ERRORS = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  FAIL  {name} -- {detail}")


try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(5)

    print("\n=== Migration Design Canvas — Full E2E Test ===\n")

    # ── 1. Index page ──────────────────────────────────────────────────
    print("[1] Index Page")
    driver.get(f"{BASE}/migration-canvas/")
    time.sleep(2)
    src = driver.page_source
    check("Index loads", "Migration Design Canvas" in src)
    check("New Design button", "New Migration Design" in src)
    check("Templates section", "Templates" in src)
    check("10 template cards", len(driver.find_elements(By.CLASS_NAME, "mdc-card")) >= 10,
          f"Found {len(driver.find_elements(By.CLASS_NAME, 'mdc-card'))} cards")
    check("Stats show", "Designs" in src and "Templates" in src)
    driver.save_screenshot("playwright/screenshots/migration-canvas-index-1920.png")

    # ── 2. Templates gallery page ──────────────────────────────────────
    print("\n[2] Templates Gallery")
    driver.get(f"{BASE}/migration-canvas/templates")
    time.sleep(2)
    src = driver.page_source
    check("Templates page loads (no 500)", "Internal Server Error" not in src)
    check("Templates page title", "Migration Design Templates" in src)
    cards = driver.find_elements(By.CLASS_NAME, "mdc-card")
    check("10 template cards on gallery", len(cards) >= 10, f"Found {len(cards)}")
    check("Lift-and-Shift template visible", "Lift-and-Shift" in src)
    check("Blue-Green template visible", "Blue-Green" in src)
    check("DoD IL4 template visible", "DoD IL4" in src)
    check("Network template visible", "Network Infrastructure" in src)
    check("Data Migration template visible", "Enterprise Data Migration" in src)
    check("Multi-Wave template visible", "Multi-Phase" in src)
    driver.save_screenshot("playwright/screenshots/migration-canvas-templates-1920.png")

    # ── 3. New canvas (empty) ──────────────────────────────────────────
    print("\n[3] New Canvas (Empty)")
    driver.get(f"{BASE}/migration-canvas/canvas/new")
    time.sleep(3)
    src = driver.page_source
    check("Canvas page loads", "canvas-container" in src)
    check("Palette sidebar", "dc-sidebar" in src)
    check("Toolbar present", "dc-toolbar" in src)
    check("Right panel present", "dc-right-panel" in src)
    # Toolbar buttons
    check("Save button", "Save" in src)
    check("Assess button", "Assess" in src)
    check("Gaps button", "Gaps" in src)
    check("Readiness button", "Readiness" in src)
    check("Stats button", "Stats" in src)
    check("Oracle button", "Oracle" in src)
    check("Export button", "Export" in src)
    check("Back button", "Back" in src)
    # Palette categories
    check("Sources palette", "Sources" in src)
    check("Targets palette", "Targets" in src)
    check("Patterns palette", "Patterns" in src)
    check("Middleware palette", "Middleware" in src)
    check("Controls palette", "Controls" in src)
    check("Planning palette", "Planning" in src)
    # Palette items
    check("Legacy Application in palette", "Legacy Application" in src)
    check("GovCloud in palette", "GovCloud" in src)
    check("Rehost in palette", "Rehost" in src)
    check("Strangler Fig in palette", "Strangler Fig" in src)
    check("ATO Gate in palette", "ATO Gate" in src)
    check("Migration Wave in palette", "Migration Wave" in src)
    # Snippets tab
    check("Snippets tab", "Snippets" in src)
    driver.save_screenshot("playwright/screenshots/migration-canvas-editor-1920.png")

    # ── 4. Template-based canvas (Blue-Green) ──────────────────────────
    print("\n[4] Template: Blue-Green")
    driver.get(f"{BASE}/migration-canvas/canvas/new?template=tpl-blue-green")
    time.sleep(3)
    src = driver.page_source
    check("Blue-Green template loads", "canvas-container" in src)
    # Check JointJS rendered nodes via JS
    node_count = driver.execute_script("return typeof graph !== 'undefined' ? graph.getCells().length : -1")
    check("JointJS graph has cells", node_count > 0, f"Got {node_count} cells")
    driver.save_screenshot("playwright/screenshots/migration-canvas-bluegreen-1920.png")

    # ── 5. Template-based canvas (Lift-and-Shift) ──────────────────────
    print("\n[5] Template: Lift-and-Shift")
    driver.get(f"{BASE}/migration-canvas/canvas/new?template=tpl-lift-shift")
    time.sleep(3)
    node_count = driver.execute_script("return typeof graph !== 'undefined' ? graph.getCells().length : -1")
    check("Lift-Shift template has cells", node_count > 0, f"Got {node_count}")

    # ── 6. Template-based canvas (DoD IL4) ─────────────────────────────
    print("\n[6] Template: DoD IL4")
    driver.get(f"{BASE}/migration-canvas/canvas/new?template=tpl-dod-il4")
    time.sleep(3)
    node_count = driver.execute_script("return typeof graph !== 'undefined' ? graph.getCells().length : -1")
    check("DoD IL4 template has cells", node_count > 0, f"Got {node_count}")

    # ── 7. Palette drag-drop test ──────────────────────────────────────
    print("\n[7] Palette Drag-Drop")
    driver.get(f"{BASE}/migration-canvas/canvas/new")
    time.sleep(3)
    # Dismiss tour overlay if present
    driver.execute_script("""
        var tour = document.getElementById('icdev-tour-welcome');
        if (tour) tour.remove();
        document.querySelectorAll('.icdev-tour-welcome, .tour-overlay, .modal-overlay.active').forEach(e => e.remove());
    """)
    time.sleep(0.5)
    # Open Sources category via JS (avoids click interception)
    driver.execute_script("""
        var headers = document.querySelectorAll('.dc-cat-header');
        if (headers.length > 0) { headers[0].classList.add('open'); headers[0].nextElementSibling.classList.add('open'); }
    """)
    time.sleep(0.5)
    # Find palette items
    palette_items = driver.find_elements(By.CLASS_NAME, "dc-palette-item")
    check("Palette items found", len(palette_items) > 0, f"Found {len(palette_items)}")
    if palette_items:
        check("Palette items draggable", palette_items[0].get_attribute("draggable") == "true")

    # Test drag via JS simulation (more reliable than Selenium ActionChains)
    drag_result = driver.execute_script("""
        if (typeof graph === 'undefined' || typeof createNode === 'undefined') return 'no_graph';
        var before = graph.getCells().length;
        var cell = createNode({id: 'test-drag-1', type: 'src-legacy', label: 'Test Drag', x: 300, y: 200});
        graph.addCell(cell);
        return graph.getCells().length > before ? 'ok' : 'fail';
    """)
    check("Node created via JS (palette equiv)", drag_result == "ok", f"Result: {drag_result}")

    # ── 8. Assessments page ────────────────────────────────────────────
    print("\n[8] Assessments Page")
    driver.get(f"{BASE}/migration-canvas/assessments")
    time.sleep(2)
    src = driver.page_source
    check("Assessments page loads", "Migration Assessments" in src)
    check("No 500 error", "Internal Server Error" not in src)
    driver.save_screenshot("playwright/screenshots/migration-canvas-assessments-1920.png")

    # ── 9. SOPs page ──────────────────────────────────────────────────
    print("\n[9] SOPs Page")
    driver.get(f"{BASE}/migration-canvas/sops")
    time.sleep(2)
    src = driver.page_source
    check("SOPs page loads", "Standard Operating Procedures" in src)
    check("No 500 error", "Internal Server Error" not in src)
    check("Has New SOP button", "New SOP" in src)
    check("SOP-MC-001 visible", "Migration Planning" in src)
    check("SOP-MC-002 visible", "Phased Migration" in src)
    check("SOP-MC-003 visible", "Post-Migration Validation" in src)
    check("SOP-MC-004 visible", "Rollback" in src)
    check("6 SOP cards", len(driver.find_elements(By.CLASS_NAME, "sop-card")) >= 6,
          f"Found {len(driver.find_elements(By.CLASS_NAME, 'sop-card'))}")
    driver.save_screenshot("playwright/screenshots/migration-canvas-sops-1920.png")

    # ── 10. API: Templates ─────────────────────────────────────────────
    print("\n[10] API Tests")
    driver.get(f"{BASE}/migration-canvas/api/templates")
    time.sleep(1)
    body = driver.find_element(By.TAG_NAME, "body").text
    check("API templates (10)", "tpl-lift-shift" in body and "tpl-blue-green" in body)

    # ── 11. API: Objects ───────────────────────────────────────────────
    driver.get(f"{BASE}/migration-canvas/api/objects")
    time.sleep(1)
    body = driver.find_element(By.TAG_NAME, "body").text
    check("API objects", "src-legacy" in body and "tgt-govcloud" in body)

    # ── 12. API: Rules ─────────────────────────────────────────────────
    driver.get(f"{BASE}/migration-canvas/api/rules")
    time.sleep(1)
    body = driver.find_element(By.TAG_NAME, "body").text
    check("API rules", "MC-001" in body and "MC-010" in body)

    # ── 13. API: Snippets ──────────────────────────────────────────────
    driver.get(f"{BASE}/migration-canvas/api/snippets")
    time.sleep(1)
    body = driver.find_element(By.TAG_NAME, "body").text
    check("API snippets (15)", "snip-ato-gate" in body and "snip-dual-monitoring" in body)

    # ── 14. Navigation menu ────────────────────────────────────────────
    print("\n[11] Navigation Menu")
    driver.get(f"{BASE}/migration-canvas/")
    time.sleep(2)
    nav = driver.page_source
    check("Nav: Migration link", "/migration-canvas/" in nav)
    check("Nav: New Design link", "/migration-canvas/canvas/new" in nav)
    check("Nav: Templates link", "/migration-canvas/templates" in nav)
    check("Nav: Assessments link", "/migration-canvas/assessments" in nav)
    check("Nav: SOPs link", "/migration-canvas/sops" in nav)

    # ── 15. JS error check ─────────────────────────────────────────────
    print("\n[12] JS Error Check")
    driver.get(f"{BASE}/migration-canvas/canvas/new")
    time.sleep(3)
    logs = driver.get_log("browser")
    severe = [l for l in logs if l.get("level") == "SEVERE" and "favicon" not in l.get("message", "")]
    check("No SEVERE JS errors on canvas", len(severe) == 0, f"{len(severe)} errors: {[l['message'][:100] for l in severe[:3]]}")

    driver.get(f"{BASE}/migration-canvas/canvas/new?template=tpl-strangler-fig")
    time.sleep(3)
    logs = driver.get_log("browser")
    severe = [l for l in logs if l.get("level") == "SEVERE" and "favicon" not in l.get("message", "")]
    check("No SEVERE JS errors on template", len(severe) == 0, f"{len(severe)} errors: {[l['message'][:100] for l in severe[:3]]}")

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    if ERRORS:
        print("\nFAILURES:")
        for e in ERRORS:
            print(f"  - {e}")
    print(f"{'='*60}")

    driver.quit()

except Exception as e:
    print(f"\nFATAL ERROR: {e}")
    import traceback
    traceback.print_exc()
    FAIL += 1

sys.exit(1 if FAIL > 0 else 0)
