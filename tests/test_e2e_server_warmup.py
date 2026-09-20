"""The E2E globalSetup warms the dashboard before the first test (qa-fail-21bfa13285d6c50f).

`activity_feed.spec.ts`, the first test of a sweep batch, died with
``page.goto: Timeout 30000ms exceeded navigating to /activity`` against a
just-spawned dashboard. It did not reproduce: the same canvas passed 4/4 alone
against the same server and the stall sampler did not overlap it. Measured on a
fresh server, a cold /activity answers in well under a second, so the fix is a
mitigation plus a measurement -- ``warmDashboard`` pays the first hit outside a
test's own 30s ``navigationTimeout`` and records how long each first hit took.

Structural, like ``test_e2e_baseurl_reachability.py``: the subject is TypeScript
and CI runs Node 20, which cannot execute it from pytest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

GLOBAL_SETUP = Path(__file__).resolve().parents[1] / "globalSetup.ts"


@pytest.fixture(scope="module")
def setup_source() -> str:
    return GLOBAL_SETUP.read_text(encoding="utf-8")


def _body(source: str, signature: str) -> str:
    start = source.index(signature)
    return source[start : start + 3000]


def test_the_warmup_is_exported(setup_source: str) -> None:
    assert re.search(r"export async function warmDashboard\(", setup_source)
    assert re.search(r"export function staticAssetPaths\(", setup_source)


def test_the_hook_runs_it_after_the_asserts(setup_source: str) -> None:
    hook = _body(setup_source, "export default async function globalSetup")
    reach = hook.index("assertBaseUrlReachable")
    isolated = hook.index("assertDatabaseIsolated")
    warm = hook.index("warmDashboard(")
    # An unreachable or wrongly-isolated server must be refused before it is
    # spent time on, and the warm-up must not run at all if either throws.
    assert reach < isolated < warm


def test_the_warmup_covers_the_page_that_failed(setup_source: str) -> None:
    assert re.search(r"WARMUP_PATHS\s*=\s*\[[^\]]*'/activity'", setup_source)


def test_the_warmup_never_throws(setup_source: str) -> None:
    once = _body(setup_source, "async function warmOnce")
    assert "catch (err)" in once
    warm = _body(setup_source, "export async function warmDashboard")
    assert "throw " not in warm


def test_a_slow_first_hit_is_named_not_swallowed(setup_source: str) -> None:
    assert "SLOW FIRST HIT" in setup_source
    assert re.search(r"WARMUP_SLOW_MS\s*=\s*\d+", setup_source)


def test_disabling_the_warmup_is_announced(setup_source: str) -> None:
    warm = _body(setup_source, "export async function warmDashboard")
    assert "ICDEV_E2E_WARMUP" in warm
    assert "DISABLED" in warm


def test_the_warmup_budget_exceeds_a_tests_navigation_timeout(setup_source: str) -> None:
    # The point is to absorb a first hit that would have blown a test's 30s.
    warm = _body(setup_source, "export async function warmDashboard")
    match = re.search(r"timeoutMs \?\? (\d+)", warm)
    assert match and int(match.group(1)) > 30000
