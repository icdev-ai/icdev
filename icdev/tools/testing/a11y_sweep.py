# CUI // SP-CTI
"""Section 508 / WCAG 2.1 accessibility sweep for the ICDEV™ dashboard.

Drives a headless browser (Selenium — the pure-Python driver already used by
the repo's ``tests/e2e/*`` suite; no npm/Node build pipeline required), injects
the vendored ``axe-core`` engine into each of the top ~15 dashboard pages, runs
the automated accessibility rules, and reports violations grouped by impact
(critical / serious / moderate / minor).

Injection contract
-------------------
The vendored ``tools/testing/vendor/axe/axe.min.js`` (Deque axe-core, MPL-2.0,
license header preserved verbatim) is read from disk and evaluated in the page
via ``driver.execute_script`` — the Selenium equivalent of Playwright's
``page.add_script_tag(path=...)`` / evaluating the script content. axe then runs
entirely client-side; nothing is fetched from the network at scan time, so the
sweep is air-gap safe.

Baseline / gating model (warn-only start)
-----------------------------------------
* Existing violations are grandfathered via a committed baseline
  (``tests/e2e/a11y_baseline.json``), keyed by ``page`` + axe rule ``id``.
* A violation is *NEW* when its (page, rule-id) pair is absent from the baseline.
* Default posture is **warn-only**: the sweep reports everything and never fails.
* Only when ``ICDEV_A11Y_ENFORCE=1`` (or ``--enforce``) is set does the sweep
  return a non-zero ``new_critical`` gate — and even then it fails **only** on
  NEW *critical* violations, never on grandfathered ones.

This tool is meaningful only against a **live** dashboard. When no server is
reachable (e.g. the CI ``Test`` job, which does not boot a dashboard) the
pytest wrapper ``tests/e2e/test_a11y_section508.py`` self-skips, so this module
never contributes to the required Lint / Test / Security / Helm checks.

CLI
---
    # Sweep a live dashboard and print a JSON report
    python tools/testing/a11y_sweep.py --json

    # Regenerate the committed baseline from the current live state
    python tools/testing/a11y_sweep.py --update-baseline

    # Enforce: exit 1 if any NEW critical violation is found
    python tools/testing/a11y_sweep.py --enforce
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
AXE_JS_PATH: Path = PROJECT_ROOT / "tools" / "testing" / "vendor" / "axe" / "axe.min.js"
BASELINE_PATH: Path = PROJECT_ROOT / "tests" / "e2e" / "a11y_baseline.json"
SCREENSHOT_DIR: Path = PROJECT_ROOT / "playwright" / "screenshots"

DEFAULT_BASE_URL: str = os.environ.get("ICDEV_A11Y_BASE_URL", "http://127.0.0.1:5050")

IMPACT_ORDER: Tuple[str, ...] = ("critical", "serious", "moderate", "minor")

# Top ~15 dashboard pages that render server-side templates. Deliberately a
# stable, representative cross-section (home, board, chat, canvases, DIC) rather
# than the full route table — keeps the sweep fast and the baseline reviewable.
DEFAULT_PAGES: Tuple[str, ...] = (
    "/",                                   # Home / task board
    "/kanban",                             # Governed delivery pipeline
    "/chat",                               # Multi-pane chat
    "/projects",                           # Projects index
    "/agents",                             # Agent roster
    "/compliance",                         # Compliance dashboard
    "/knowledge-search",                   # RAG knowledge search
    "/components-map",                     # Self-awareness map
    "/security/",                          # Security canvas
    "/data/",                              # Data canvas
    "/observability/",                     # Observability canvas
    "/devops/",                            # DevOps / pipeline canvas
    "/cortex/",                            # ICDEV Cortex canvas
    "/document-intelligence/techwriter",   # DIC Tech Writer workspace
    "/bi_dashboard",                       # BI Studio canvas
)

# Axe run options: WCAG 2.0/2.1 A & AA + Section 508 tag set.
AXE_RUN_OPTIONS: Dict[str, Any] = {
    "runOnly": {
        "type": "tag",
        "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "section508"],
    },
}


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


def is_server_reachable(base_url: str = DEFAULT_BASE_URL, timeout: float = 2.0) -> bool:
    """True when a TCP connection to the dashboard host:port succeeds."""
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------


def load_baseline(path: Path = BASELINE_PATH) -> Dict[str, List[str]]:
    """Load the grandfather baseline: {page: [rule_id, ...]}.

    Missing/corrupt baseline degrades to empty (every violation counts as NEW),
    never raises — a broken baseline must not crash the sweep.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pages = data.get("pages", data) if isinstance(data, dict) else {}
    out: Dict[str, List[str]] = {}
    if isinstance(pages, dict):
        for page, rules in pages.items():
            if isinstance(rules, list):
                out[str(page)] = [str(r) for r in rules]
    return out


def build_baseline_payload(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Shape a sweep result set into the committed baseline JSON structure."""
    pages: Dict[str, List[str]] = {}
    for res in results:
        rule_ids = sorted({v["id"] for v in res.get("violations", [])})
        pages[res["page"]] = rule_ids
    return {
        "_comment": (
            "Section 508 / WCAG a11y baseline — grandfathered existing "
            "violations keyed by page -> [axe rule id]. Regenerate against a "
            "live dashboard with: python tools/testing/a11y_sweep.py "
            "--update-baseline"
        ),
        "axe_version": "4.10.2",
        "pages": pages,
    }


# ---------------------------------------------------------------------------
# Selenium driver
# ---------------------------------------------------------------------------


def _make_driver():  # pragma: no cover - requires a real browser
    """Create a headless Chrome driver. Raises on any missing dependency."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver


def _axe_source() -> str:
    """Read the vendored axe-core source (license header included)."""
    return AXE_JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Core sweep
# ---------------------------------------------------------------------------


def _summarize_impacts(violations: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {k: 0 for k in IMPACT_ORDER}
    for v in violations:
        impact = (v.get("impact") or "minor").lower()
        counts[impact] = counts.get(impact, 0) + 1
    return counts


def scan_page(driver, base_url: str, path: str, axe_src: str,
              screenshot: bool = True) -> Dict[str, Any]:
    """Load one page, inject axe, run it, and return a normalized result."""
    url = base_url.rstrip("/") + path
    result: Dict[str, Any] = {"page": path, "url": url, "violations": [], "error": None}
    try:
        driver.get(url)
        # Inject + run axe. execute_async_script lets axe's Promise resolve.
        driver.execute_script(axe_src)
        raw = driver.execute_async_script(
            "var opts = arguments[0];"
            "var done = arguments[arguments.length - 1];"
            "axe.run(document, opts).then(function(r){"
            "  done(r.violations);"
            "}).catch(function(e){ done([{axeError: String(e)}]); });",
            AXE_RUN_OPTIONS,
        )
        violations: List[Dict[str, Any]] = []
        for v in raw or []:
            if "axeError" in v:
                result["error"] = v["axeError"]
                continue
            violations.append({
                "id": v.get("id"),
                "impact": (v.get("impact") or "minor").lower(),
                "help": v.get("help"),
                "helpUrl": v.get("helpUrl"),
                "nodes": len(v.get("nodes", [])),
            })
        result["violations"] = violations
        result["impacts"] = _summarize_impacts(violations)
        if screenshot:
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            slug = path.strip("/").replace("/", "-") or "home"
            shot = SCREENSHOT_DIR / f"a11y-{slug}.png"
            try:
                driver.save_screenshot(str(shot))
                result["screenshot"] = str(shot.relative_to(PROJECT_ROOT))
            except Exception:  # pragma: no cover - screenshot best-effort
                pass
    except Exception as exc:  # pragma: no cover - network/browser errors
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def diff_against_baseline(results: List[Dict[str, Any]],
                          baseline: Dict[str, List[str]]) -> Dict[str, Any]:
    """Compute NEW violations (page,rule) not present in the baseline."""
    new_by_impact = {k: 0 for k in IMPACT_ORDER}
    new_critical: List[Dict[str, str]] = []
    new_all: List[Dict[str, str]] = []
    for res in results:
        grandfathered = set(baseline.get(res["page"], []))
        for v in res.get("violations", []):
            if v["id"] in grandfathered:
                continue
            impact = v["impact"]
            new_by_impact[impact] = new_by_impact.get(impact, 0) + 1
            entry = {"page": res["page"], "id": v["id"], "impact": impact,
                     "help": v.get("help"), "helpUrl": v.get("helpUrl"),
                     "nodes": v.get("nodes")}
            new_all.append(entry)
            if impact == "critical":
                new_critical.append(entry)
    return {
        "new_by_impact": new_by_impact,
        "new_critical": new_critical,
        "new_all": new_all,
    }


def run_sweep(base_url: str = DEFAULT_BASE_URL,
              pages: Optional[Tuple[str, ...]] = None,
              screenshot: bool = True) -> Dict[str, Any]:
    """Full sweep: drive the browser across ``pages`` and diff vs baseline.

    Returns a report dict. Raises RuntimeError only for setup failures the
    caller (pytest wrapper) is expected to translate into a skip.
    """
    pages = pages or DEFAULT_PAGES
    if not AXE_JS_PATH.exists():
        raise RuntimeError(f"vendored axe-core missing at {AXE_JS_PATH}")
    axe_src = _axe_source()
    driver = _make_driver()
    results: List[Dict[str, Any]] = []
    try:
        for path in pages:
            results.append(scan_page(driver, base_url, path, axe_src, screenshot))
    finally:
        try:
            driver.quit()
        except Exception:  # pragma: no cover
            pass

    baseline = load_baseline()
    totals = {k: 0 for k in IMPACT_ORDER}
    for res in results:
        for k, n in (res.get("impacts") or {}).items():
            totals[k] = totals.get(k, 0) + n
    diff = diff_against_baseline(results, baseline)
    return {
        "base_url": base_url,
        "pages_scanned": len(results),
        "axe_version": "4.10.2",
        "totals_by_impact": totals,
        "results": results,
        **diff,
    }


def format_remediation_list(report: Dict[str, Any], limit: int = 40) -> str:
    """Render NEW violations as a Markdown remediation checklist (PR-body use)."""
    new_all = sorted(
        report.get("new_all", []),
        key=lambda e: (IMPACT_ORDER.index(e["impact"]) if e["impact"] in IMPACT_ORDER else 99,
                       e["page"], e["id"]),
    )
    if not new_all:
        return "_No new accessibility violations vs baseline._"
    lines = ["| Impact | Page | Rule | Nodes | Guidance |",
             "|--------|------|------|-------|----------|"]
    for e in new_all[:limit]:
        help_txt = (e.get("help") or "").replace("|", "\\|")
        lines.append(
            f"| {e['impact']} | `{e['page']}` | [{e['id']}]({e.get('helpUrl','')}) "
            f"| {e.get('nodes','?')} | {help_txt} |"
        )
    if len(new_all) > limit:
        lines.append(f"| … | | | | _+{len(new_all) - limit} more_ |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Section 508 a11y sweep (axe-core).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Regenerate tests/e2e/a11y_baseline.json from live state")
    parser.add_argument("--enforce", action="store_true",
                        help="Exit 1 on NEW critical violations")
    args = parser.parse_args(argv)

    enforce = args.enforce or os.environ.get("ICDEV_A11Y_ENFORCE") == "1"

    if not is_server_reachable(args.base_url):
        msg = {"status": "skipped", "reason": f"no dashboard at {args.base_url}"}
        print(json.dumps(msg) if args.json else f"SKIP: {msg['reason']}")
        return 0

    try:
        report = run_sweep(args.base_url, screenshot=not args.no_screenshot)
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)})
              if args.json else f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        payload = build_baseline_payload(report["results"])
        BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline written: {BASELINE_PATH} "
              f"({sum(len(v) for v in payload['pages'].values())} grandfathered rules)")
        return 0

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"a11y sweep: {report['pages_scanned']} pages @ {report['base_url']}")
        print(f"  totals by impact : {report['totals_by_impact']}")
        print(f"  NEW by impact    : {report['new_by_impact']}")
        print("\n" + format_remediation_list(report))

    new_crit = len(report.get("new_critical", []))
    if enforce and new_crit:
        print(f"\nFAIL(enforce): {new_crit} NEW critical violation(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
# CUI // SP-CTI
