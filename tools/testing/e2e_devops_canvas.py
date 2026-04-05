#!/usr/bin/env python3
# CUI // SP-CTI — DevOps Pipeline Design Canvas Full Lifecycle E2E Test
"""Comprehensive Selenium E2E test for the DevOps Pipeline Design Canvas.

Tests all features: nav, index, canvas, templates, snippets, auto-connect,
air-gap filtering, NDC bridge, boundaries, legend, analysis, deploy, export,
tooltips, undo/redo, delete.

Usage:
    python tools/testing/e2e_devops_canvas.py [--port 5050]
"""

import os
import sys
import time

from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 5050
BASE = f"http://localhost:{PORT}"

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1920,1080")
opts.add_argument("--no-sandbox")
driver = webdriver.Chrome(options=opts)
os.makedirs("playwright/screenshots", exist_ok=True)

PASS = 0
FAIL = 0
results = []


def check(name, condition, detail=""):
    global PASS, FAIL
    status = "PASS" if condition else "FAIL"
    if condition:
        PASS += 1
    else:
        FAIL += 1
    results.append((status, name, detail))
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail else ""))


def js(script):
    return driver.execute_script(script)


def screenshot(name):
    driver.save_screenshot(f"playwright/screenshots/e2e-{name}.png")


try:
    print("=" * 70)
    print("ICDEV DevOps Pipeline Design Canvas -- Full Lifecycle E2E Test")
    print("=" * 70)

    # ── Phase 1: Dashboard Integration ────────────────────────────────
    print("\n--- Phase 1: Dashboard Integration ---")
    driver.get(f"{BASE}/")
    time.sleep(2)
    check("Nav has DevOps link", "/devops/" in driver.page_source)
    screenshot("01-nav")

    driver.get(f"{BASE}/devops/api/health")
    time.sleep(1)
    check("Health endpoint OK", "pipeline_canvas" in driver.find_element(By.TAG_NAME, "body").text)

    # ── Phase 2: Index Page ───────────────────────────────────────────
    print("\n--- Phase 2: Index Page ---")
    driver.get(f"{BASE}/devops/")
    time.sleep(2)
    body = driver.find_element(By.TAG_NAME, "body").text
    check("Index page loads", "Pipeline Design Canvas" in body)
    cards = driver.find_elements(By.CSS_SELECTOR, ".pdc-card")
    check("Has template/snippet cards", len(cards) >= 23, f"{len(cards)} cards")
    check("Clear All button exists", len(driver.find_elements(By.CSS_SELECTOR, ".pdc-clear-btn")) > 0)
    screenshot("02-index")

    # ── Phase 3: Canvas Init ──────────────────────────────────────────
    print("\n--- Phase 3: Canvas Initialization ---")
    driver.get(f"{BASE}/devops/canvas/new")
    time.sleep(3)

    logs = driver.get_log("browser")
    severe = [entry for entry in logs if entry["level"] == "SEVERE" and "favicon" not in entry.get("message", "")]
    check("Canvas: 0 JS errors on load", len(severe) == 0, f"{len(severe)} errors")
    check("JointJS SVG initialized", js("return document.querySelectorAll('#pc-canvas-container svg').length") >= 1)

    palette_count = js("return document.querySelectorAll('.palette-item').length")
    check("Palette: 221 items", palette_count == 221, f"{palette_count} items")
    check(
        "Palette collapsed by default",
        js(
            "var v=0;document.querySelectorAll('.palette-items').forEach(function(e){if(e.style.display!=='none')v++});return v"
        )
        == 0,
    )

    btns = [b.text.strip() for b in driver.find_elements(By.CSS_SELECTOR, ".tb-btn") if b.text.strip()]
    for label in [
        "Dashboard",
        "Deploy",
        "CFN",
        "Bicep",
        "Legend",
        "Boundaries",
        "Scorecard",
        "OWASP",
        "SLSA",
        "Compliance",
        "Cost",
    ]:
        check(f"Toolbar has '{label}'", any(label in b for b in btns))
    screenshot("03-canvas-empty")

    # ── Phase 4: Snippet as New Pipeline ──────────────────────────────
    print("\n--- Phase 4: Snippet as New Pipeline ---")
    driver.get(f"{BASE}/devops/")
    time.sleep(2)
    js('loadSnippetAsNew("snip-dod-software-factory")')
    time.sleep(3)
    check("Snippet creates new pipeline", "/devops/canvas/" in driver.current_url)
    n = js("return graph.getElements().length")
    check("Nodes loaded from snippet", n >= 5, f"{n} nodes")
    screenshot("04-snippet-as-new")

    # ── Phase 5: Template Loading ─────────────────────────────────────
    print("\n--- Phase 5: Template Loading ---")
    driver.get(f"{BASE}/devops/canvas/new")
    time.sleep(3)
    js("openTemplatesModal()")
    time.sleep(1)
    tpl_cards = driver.find_elements(By.CSS_SELECTOR, ".tpl-card")
    check("Templates modal: 17 templates", len(tpl_cards) == 17, f"{len(tpl_cards)}")
    tpl_cards[0].click()
    time.sleep(3)
    check("Template loaded into canvas", js("return graph.getElements().length") >= 3)
    screenshot("05-template")

    # ── Phase 6: Snippet Deconfliction + Auto-Connect ─────────────────
    print("\n--- Phase 6: Deconfliction + Auto-Connect ---")
    driver.get(f"{BASE}/devops/canvas/new")
    time.sleep(3)
    js('loadSnippet("snip-cncf-supply-chain")')
    time.sleep(2)
    n1 = js("return graph.getElements().length")
    l1 = js("return graph.getLinks().length")

    js('loadSnippet("snip-aws-codepipeline")')
    time.sleep(2)
    n2 = js("return graph.getElements().length")
    l2 = js("return graph.getLinks().length")
    check("ID deconfliction works", n2 == n1 + 7, f"{n1}+7={n2}")

    sugs = js("return (window._pendingSuggestions || []).length")
    check("Auto-connect suggestions generated", sugs > 0, f"{sugs} suggestions")

    header = js('var h=document.querySelector(".pc-config-header span"); return h ? h.textContent : ""')
    check("Integration Guide opens", header == "Integration Guide")

    js("applyAllSuggestions()")
    time.sleep(1)
    l3 = js("return graph.getLinks().length")
    check("Connect All applies links", l3 > l2, f"+{l3 - l2} links")
    screenshot("06-autoconnect")

    # ── Phase 7: Air-Gap Filtering ────────────────────────────────────
    print("\n--- Phase 7: Air-Gap Classification Filtering ---")
    driver.get(f"{BASE}/devops/canvas/new")
    time.sleep(3)
    js('loadSnippet("snip-aws-codepipeline")')
    time.sleep(2)
    js('loadSnippet("snip-airgap-il5")')
    time.sleep(2)

    ag_body = js('var b=document.querySelector(".pc-config-body"); return b ? b.textContent : ""')
    check("Air-gap warning displayed", "Air-Gapped" in ag_body)
    check("SECRET badge shown", "SECRET" in ag_body)
    ag_sugs = js("return (window._pendingSuggestions || []).length")
    check("Cloud services blocked (0 suggestions)", ag_sugs == 0, f"{ag_sugs}")
    screenshot("07-airgap")

    # ── Phase 8: NDC-PDC Bridge ───────────────────────────────────────
    print("\n--- Phase 8: NDC-PDC Infrastructure Bridge ---")
    driver.get(f"{BASE}/devops/canvas/new")
    time.sleep(3)
    js('createNode("ndc-topology", 100, 200, "NDC Topology")')
    time.sleep(1)
    js("var els=graph.getElements(); if(els.length) selectCell(els[0])")
    time.sleep(2)

    ndc_header = js('var h=document.querySelector(".pc-config-header span"); return h ? h.textContent : ""')
    check("NDC Bridge panel opens", ndc_header == "NDC Bridge")
    check("Topology picker loaded", js('return document.getElementById("ndc-topology-list") !== null'))

    js('createNode("hybrid-directconnect", 300, 200, "Direct Connect")')
    js('createNode("aws-eks", 500, 200, "EKS")')
    js('createNode("onprem-datacenter", 100, 350, "On-Prem DC")')
    time.sleep(1)
    js('loadSnippet("snip-aws-codepipeline")')
    time.sleep(2)
    bridge_sugs = js("return (window._pendingSuggestions || []).length")
    check("Hybrid auto-connect suggestions", bridge_sugs > 0, f"{bridge_sugs}")
    screenshot("08-ndc-bridge")

    # ── Phase 9: Boundaries + Legend ──────────────────────────────────
    print("\n--- Phase 9: Boundaries + Legend ---")
    js("autoGroupByStage()")
    time.sleep(1)
    bounds = js('return graph.getElements().filter(function(e){return e.get("isBoundary")}).length')
    check("Auto boundaries created", bounds >= 2, f"{bounds} groups")
    screenshot("09-boundaries")

    js("toggleLegend()")
    time.sleep(0.5)
    check("Legend visible", js('var l=document.getElementById("pc-legend"); return l && l.style.display !== "none"'))
    check(
        "Legend has Infrastructure entry",
        js('var l=document.getElementById("pc-legend"); return l && l.textContent.includes("Infrastructure")'),
    )
    screenshot("10-legend")

    # ── Phase 10: Save + All Analysis Types ───────────────────────────
    print("\n--- Phase 10: Save + Analysis ---")
    js("savePipeline()")
    time.sleep(3)
    pid = js("return pipelineId")
    check("Pipeline saved", pid and pid != "new", f"id={pid}")

    if pid and pid != "new":
        for analysis, title in [
            ("runScorecard()", "Pipeline Scorecard"),
            ('runAnalysis("security_coverage")', "OWASP Top 10 Coverage"),
            ('runAnalysis("slsa")', "SLSA Level Assessment"),
            ('runAnalysis("compliance")', "Compliance Audit"),
            ('runAnalysis("cost")', "Cost Estimate"),
        ]:
            js(analysis)
            time.sleep(2)
            h = js('var h=document.querySelector(".pc-config-header span"); return h ? h.textContent : ""')
            blen = js('var b=document.querySelector(".pc-config-body"); return b ? b.textContent.length : 0')
            check(f"{title} renders", h == title and blen > 50, f'header="{h}", {blen} chars')
        screenshot("11-analysis")

    # ── Phase 11: Deploy IaC Bundle ───────────────────────────────────
    print("\n--- Phase 11: Deploy IaC Bundle ---")
    if pid and pid != "new":
        js("deployBundle()")
        time.sleep(4)
        try:
            driver.switch_to.alert.accept()
        except Exception:
            pass
        deploy_body = js('var b=document.querySelector(".pc-config-body"); return b ? b.textContent : ""')
        check("Deploy shows files", "Generated Files" in deploy_body)
        check("Deploy has download", "Download" in deploy_body)
        screenshot("12-deploy")

    # ── Phase 12: Export Formats ──────────────────────────────────────
    print("\n--- Phase 12: Export Formats ---")
    if pid and pid != "new":
        for fmt in [
            "gitlab_ci",
            "github_actions",
            "jenkinsfile",
            "tekton",
            "azure_pipelines",
            "drawio",
            "svg",
            "cloudformation",
            "bicep",
        ]:
            js(
                f'window._exp_{fmt}=null; fetch("/devops/api/export/{pid}",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{format:"{fmt}"}})}}).then(r=>r.json()).then(d=>{{window._exp_{fmt}=d}})'
            )
        time.sleep(3)
        for fmt in [
            "gitlab_ci",
            "github_actions",
            "jenkinsfile",
            "tekton",
            "azure_pipelines",
            "drawio",
            "svg",
            "cloudformation",
            "bicep",
        ]:
            exp = js(f"return window._exp_{fmt} || null")
            ok = exp and exp.get("content") and len(exp.get("content", "")) > 20
            check(f"Export: {fmt}", ok, f"{len(exp.get('content', '')) if exp else 0} bytes")

    # ── Phase 13: Tooltips ────────────────────────────────────────────
    print("\n--- Phase 13: Tooltips ---")
    ti_count = js('return typeof TOOL_INFO !== "undefined" ? Object.keys(TOOL_INFO).length : 0')
    check("TOOL_INFO loaded", ti_count >= 220, f"{ti_count} entries")
    sample = js('return TOOL_INFO["registry-harbor"] || null')
    check("Tooltip has description", sample and len(sample.get("desc", "")) > 10)
    check("Tooltip has category", sample and bool(sample.get("category")))
    check("Tooltip has deploy method", sample and bool(sample.get("deploy")))

    # ── Phase 14: Undo/Redo ───────────────────────────────────────────
    print("\n--- Phase 14: Undo/Redo ---")
    # Start fresh canvas and clear undo stack
    driver.get(f"{BASE}/devops/canvas/new")
    time.sleep(3)
    js("undoStack = []; redoStack = [];")  # Reset stacks
    js('pushUndo(); createNode("scan-trivy", 100, 100, "A")')
    time.sleep(0.5)
    n_after_add = js("return graph.getElements().length")
    check("Node added", n_after_add == 1)
    js("undoAction()")
    time.sleep(0.5)
    n_after_undo = js("return graph.getElements().length")
    check("Undo works", n_after_undo == 0, f"{n_after_add} -> {n_after_undo}")
    js("redoAction()")
    time.sleep(0.5)
    check("Redo works", js("return graph.getElements().length") == 1)

    # ── Phase 15: Delete Pipeline ─────────────────────────────────────
    print("\n--- Phase 15: Delete Pipeline ---")
    if pid and pid != "new":
        js(f'fetch("/devops/api/pipelines/{pid}",{{method:"DELETE"}})')
        time.sleep(1)
        js(
            f'window._delCheck=null; fetch("/devops/api/pipelines/{pid}").then(r=>r.json()).then(d=>{{window._delCheck=d}})'
        )
        time.sleep(1)
        del_r = js("return window._delCheck || {}")
        check("Pipeline deleted (404)", del_r.get("error") == "Not found")

    # ── Phase 16: Final Error Check ───────────────────────────────────
    print("\n--- Phase 16: Final JS Error Check ---")
    all_logs = driver.get_log("browser")
    # Exclude: favicon 404, and deleted-pipeline autosave 404 (expected after Phase 15)
    all_severe = [
        entry
        for entry in all_logs
        if entry["level"] == "SEVERE"
        and "favicon" not in entry.get("message", "")
        and "Failed to load resource" not in entry.get("message", "")
    ]
    check("Total JS errors = 0", len(all_severe) == 0, f"{len(all_severe)} errors")
    for entry in all_severe[:5]:
        print(f"    ERROR: {entry['message'][:120]}")
    screenshot("17-final")

except Exception as e:
    print(f"\nFATAL ERROR: {e}")
    import traceback

    traceback.print_exc()
    driver.save_screenshot("playwright/screenshots/e2e-fatal-error.png")
    FAIL += 1
finally:
    driver.quit()

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"RESULTS: {PASS} PASSED, {FAIL} FAILED, {PASS + FAIL} TOTAL")
print("=" * 70)
if FAIL > 0:
    print("\nFAILED TESTS:")
    for status, name, detail in results:
        if status == "FAIL":
            print(f"  [FAIL] {name} -- {detail}")
    sys.exit(1)
else:
    print("\nALL TESTS PASSED")
    sys.exit(0)
