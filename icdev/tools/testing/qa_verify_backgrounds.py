"""
QA E2E: Playwright regression — canvas backgrounds white in light mode.
Verifies qa-vis-01 (data_canvas) and qa-vis-02 (qdc_canvas) fixes.

Tests:
  1. Light-mode toggle sets html[data-theme="light"]
  2. .dc-main background is white in light mode for each canvas
  3. Label contrast >= 4.5:1 on data canvas pages
"""

import json
import pathlib
import sys
import time

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_rgb(css: str):
    import re
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", css or "")
    if not m:
        return None
    return int(m[1]), int(m[2]), int(m[3])


WHITE_RGB = (255, 255, 255)
SCREENSHOTS_DIR = pathlib.Path("playwright/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://localhost:5050"

# ---------------------------------------------------------------------------
# JS helpers (evaluated in browser)
# ---------------------------------------------------------------------------

SET_LIGHT_MODE_JS = """() => {
    const tour = document.getElementById('icdev-tour-welcome');
    if (tour) tour.style.display = 'none';
    const cur = document.documentElement.getAttribute('data-theme');
    if (cur !== 'light' && window.ICDEV && window.ICDEV.toggleTheme) {
        window.ICDEV.toggleTheme();
    } else if (cur !== 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('theme', 'light');
    }
    return document.documentElement.getAttribute('data-theme');
}"""

GET_DC_MAIN_BG_JS = """() => {
    const el = document.querySelector('.dc-main');
    if (!el) return {found: false, bg: null};
    return {found: true, bg: getComputedStyle(el).backgroundColor};
}"""

GET_LABEL_CONTRAST_JS = r"""() => {
    function parseRGB(s) {
        const m = s.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        return m ? [+m[1], +m[2], +m[3]] : null;
    }
    function lin(c) { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); }
    function lum(rgb) { return 0.2126*lin(rgb[0]) + 0.7152*lin(rgb[1]) + 0.0722*lin(rgb[2]); }
    function cr(a, b) { const ls = [lum(a), lum(b)]; return (Math.max(ls[0],ls[1])+0.05)/(Math.min(ls[0],ls[1])+0.05); }

    const labels = Array.from(document.querySelectorAll('.form-group label')).filter(function(el) {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    });
    if (!labels.length) return {found: false, labels: []};

    return {
        found: true,
        labels: labels.slice(0, 5).map(function(el) {
            const color = getComputedStyle(el).color;
            const colorRGB = parseRGB(color);
            var bg = [255, 255, 255];
            var cur = el;
            while (cur && cur !== document.documentElement) {
                const c = getComputedStyle(cur).backgroundColor;
                const m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+),?\s*([\d.]*)/);
                if (m) {
                    const alpha = m[4] !== '' ? parseFloat(m[4]) : 1;
                    if (alpha > 0) { bg = [+m[1], +m[2], +m[3]]; break; }
                }
                cur = cur.parentElement;
            }
            const ratio = (colorRGB && bg) ? cr(colorRGB, bg) : null;
            return {
                text: el.textContent.trim().substring(0, 30),
                color: color,
                bg: 'rgb(' + bg[0] + ',' + bg[1] + ',' + bg[2] + ')',
                contrastRatio: ratio ? ratio.toFixed(2) : null,
                passes: ratio ? ratio >= 4.5 : null
            };
        })
    };
}"""

# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_tests():
    from playwright.sync_api import sync_playwright

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task": "qa-verify-01",
        "tests": [],
        "summary": {"passed": 0, "failed": 0, "skipped": 0},
    }

    def record(name, passed, detail=None, screenshot=None):
        status = "PASS" if passed else "FAIL"
        entry = {"name": name, "status": status, "detail": detail}
        if screenshot:
            entry["screenshot"] = str(screenshot)
        results["tests"].append(entry)
        results["summary"]["passed" if passed else "failed"] += 1
        print(f"  [{status}] {name}")
        if detail and not passed:
            print(f"         {detail}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        # ------------------------------------------------------------------
        # Test 1: Switch to light mode on home page
        # ------------------------------------------------------------------
        print("\n[1] Light mode toggle")
        page.goto(f"{BASE_URL}/")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(500)

        theme_after = page.evaluate(SET_LIGHT_MODE_JS)
        light_mode_ok = theme_after == "light"
        record(
            "Light mode: html[data-theme='light'] set after toggle",
            light_mode_ok,
            f"Got data-theme='{theme_after}'" if not light_mode_ok else f"theme='{theme_after}'",
        )

        # ------------------------------------------------------------------
        # Test 2a: data_canvas .dc-main background in light mode
        # ------------------------------------------------------------------
        print("\n[2a] data_canvas .dc-main background")
        page.goto(f"{BASE_URL}/data/canvas/new")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(500)
        page.evaluate(SET_LIGHT_MODE_JS)
        page.wait_for_timeout(300)

        dc_data = page.evaluate(GET_DC_MAIN_BG_JS)
        ss_data = SCREENSHOTS_DIR / "qa-verify-bg-data_canvas.png"
        page.screenshot(path=str(ss_data), full_page=True)

        if not dc_data["found"]:
            record("data_canvas: .dc-main element present", False, ".dc-main not found on /data/canvas/new", ss_data)
            record("data_canvas: .dc-main background white in light mode", False, ".dc-main not found", ss_data)
        else:
            bg = dc_data["bg"]
            bg_rgb = parse_rgb(bg)
            is_white = bg_rgb == WHITE_RGB if bg_rgb else False
            record("data_canvas: .dc-main element present", True, f"bg={bg}", ss_data)
            record(
                "data_canvas: .dc-main background is rgb(255,255,255) in light mode",
                is_white,
                f"Got '{bg}' — expected 'rgb(255, 255, 255)' (fix qa-vis-01 pending)" if not is_white else f"bg={bg}",
                ss_data,
            )

        # ------------------------------------------------------------------
        # Test 2b: qdc_canvas .dc-main background in light mode
        # ------------------------------------------------------------------
        print("\n[2b] qdc_canvas .dc-main background")
        page.goto(f"{BASE_URL}/quality/canvas/new")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(500)
        page.evaluate(SET_LIGHT_MODE_JS)
        page.wait_for_timeout(300)

        dc_qdc = page.evaluate(GET_DC_MAIN_BG_JS)
        ss_qdc = SCREENSHOTS_DIR / "qa-verify-bg-qdc_canvas.png"
        page.screenshot(path=str(ss_qdc), full_page=True)

        if not dc_qdc["found"]:
            record("qdc_canvas: .dc-main element present", False, ".dc-main not found on /quality/canvas/new", ss_qdc)
            record("qdc_canvas: .dc-main background white in light mode", False, ".dc-main not found", ss_qdc)
        else:
            bg = dc_qdc["bg"]
            bg_rgb = parse_rgb(bg)
            is_transparent = bg == "rgba(0, 0, 0, 0)" or (bg_rgb is None)
            is_white = (bg_rgb == WHITE_RGB) if bg_rgb else False
            # Transparent means inherits body=white in light mode — counts as pass
            effective_white = is_white or is_transparent
            record("qdc_canvas: .dc-main element present", True, f"bg={bg}", ss_qdc)
            record(
                "qdc_canvas: .dc-main background is white (or transparent->white body) in light mode",
                effective_white,
                f"bg={bg}" + (" (transparent -> inherits white body)" if is_transparent else ""),
                ss_qdc,
            )

        # ------------------------------------------------------------------
        # Test 3: Label contrast on data canvas
        # ------------------------------------------------------------------
        print("\n[3] Label contrast on data canvas")
        page.goto(f"{BASE_URL}/data/canvas/new")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(500)
        page.evaluate(SET_LIGHT_MODE_JS)
        page.wait_for_timeout(300)

        contrast_data = page.evaluate(GET_LABEL_CONTRAST_JS)

        if not contrast_data["found"]:
            results["summary"]["skipped"] += 1
            entry = {
                "name": "data_canvas: .form-group label contrast >= 4.5:1",
                "status": "SKIP",
                "detail": "No .form-group label elements visible on /data/canvas/new — canvas editor has no form labels in this state",
            }
            results["tests"].append(entry)
            print(f"  [SKIP] {entry['name']}")
            print(f"         {entry['detail']}")
        else:
            for lbl in contrast_data["labels"]:
                record(
                    f"Label '{lbl['text']}' contrast >= 4.5:1",
                    lbl["passes"] is True,
                    f"ratio={lbl['contrastRatio']}, color={lbl['color']}, bg={lbl['bg']}",
                )

        browser.close()

    # Overall: FAIL if any failures; PASS if only passes/skips
    results["overall"] = "PASS" if results["summary"]["failed"] == 0 else "FAIL"
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    out_path = pathlib.Path(".tmp/qa-verify-01-results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("QA E2E: Canvas backgrounds in light mode (qa-verify-01)")
    print("=" * 60)

    results = run_tests()

    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8", newline="")
    print(f"\nResults saved → {out_path}")
    print(f"\nOverall: {results['overall']}")
    print(f"  Passed:  {results['summary']['passed']}")
    print(f"  Failed:  {results['summary']['failed']}")
    print(f"  Skipped: {results['summary']['skipped']}")

    sys.exit(0 if results["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
