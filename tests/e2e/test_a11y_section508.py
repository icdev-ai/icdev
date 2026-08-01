# CUI // SP-CTI
"""E2E: Section 508 / WCAG accessibility sweep of the top dashboard pages.

Injects the vendored ``axe-core`` engine into ~15 pages via a headless browser
and reports violations grouped by impact. Warm-only by default; fails (only when
``ICDEV_A11Y_ENFORCE=1``) on NEW *critical* violations vs the committed baseline
``tests/e2e/a11y_baseline.json``.

Self-skips — so it NEVER breaks the required Lint / Test / Security / Helm
checks — when any of the following is true:
  * ``selenium`` (or a Chrome/driver) is unavailable, OR
  * no live dashboard is reachable at ``ICDEV_A11Y_BASE_URL`` (default
    ``http://127.0.0.1:5050``). The CI ``Test`` job does not boot a dashboard,
    so this test collects and skips cleanly there.

Run against a live instance:
    python tools/dashboard/app.py            # in another shell
    pytest tests/e2e/test_a11y_section508.py -v -s

Regenerate the baseline (grandfather current violations):
    python tools/testing/a11y_sweep.py --update-baseline
"""
from __future__ import annotations

import importlib

import pytest

a11y = importlib.import_module("tools.testing.a11y_sweep")


def _selenium_available() -> bool:
    try:
        import selenium  # noqa: F401
    except Exception:
        return False
    return True


@pytest.fixture(scope="module")
def sweep_report():
    """Run the live sweep once, or skip when prerequisites are absent."""
    if not _selenium_available():
        pytest.skip("selenium not installed — a11y sweep requires a browser driver")
    if not a11y.AXE_JS_PATH.exists():
        pytest.skip(f"vendored axe-core missing at {a11y.AXE_JS_PATH}")
    if not a11y.is_server_reachable():
        pytest.skip(
            f"no live dashboard at {a11y.DEFAULT_BASE_URL} — "
            "a11y sweep is meaningful only against a running instance"
        )
    try:
        return a11y.run_sweep(screenshot=True)
    except Exception as exc:  # browser/driver failure → skip, never fail CI
        pytest.skip(f"a11y sweep could not run: {type(exc).__name__}: {exc}")


# ── Offline-safe unit-ish checks (these DO run in CI; no server needed) ──────


def test_vendored_axe_present_and_licensed():
    """The vendored axe-core exists, is the real minified lib, license intact."""
    assert a11y.AXE_JS_PATH.exists(), "axe.min.js not vendored"
    head = a11y.AXE_JS_PATH.read_text(encoding="utf-8")[:800]
    assert "axe v4" in head, "not the expected axe-core version banner"
    assert "Mozilla Public" in head, "MPL license header missing from axe.min.js"
    assert a11y.AXE_JS_PATH.stat().st_size > 200_000, "axe.min.js looks like a stub"


def test_baseline_loads_and_is_wellformed():
    """The committed baseline parses into {page: [rule_id...]}."""
    baseline = a11y.load_baseline()
    assert isinstance(baseline, dict)
    for page, rules in baseline.items():
        assert isinstance(page, str)
        assert isinstance(rules, list)


def test_page_set_is_reasonable():
    """We sweep a stable ~15-page cross-section including home, chat, DIC."""
    pages = a11y.DEFAULT_PAGES
    assert 10 <= len(pages) <= 20
    assert "/" in pages
    assert "/chat" in pages
    assert any("document-intelligence" in p for p in pages)


# ── Live sweep assertions (skip without a running dashboard) ─────────────────


@pytest.mark.selenium
@pytest.mark.timeout(600)
def test_live_sweep_covers_all_pages(sweep_report):
    assert sweep_report["pages_scanned"] == len(a11y.DEFAULT_PAGES)


@pytest.mark.selenium
@pytest.mark.timeout(600)
def test_no_new_critical_violations(sweep_report):
    """Warn-only by default; enforce NEW-critical gate only under the env flag."""
    import os

    new_crit = sweep_report.get("new_critical", [])
    remediation = a11y.format_remediation_list(sweep_report)
    if os.environ.get("ICDEV_A11Y_ENFORCE") == "1":
        assert not new_crit, (
            f"{len(new_crit)} NEW critical a11y violation(s) vs baseline:\n{remediation}"
        )
    else:
        # Warn-only: surface the report, never fail.
        if new_crit:
            print(f"\n[warn-only] {len(new_crit)} NEW critical violation(s):\n{remediation}")
# CUI // SP-CTI
