"""E2E base URL must be reachable, and the suite must say so ONCE (qa-fail-e2e-baseurl-01).

`.env` carried ``ICDEV_DASHBOARD_URL=http://host.docker.internal:5050`` — correct
for an agent inside a container, wrong for a test runner on the host.
``playwright.config.ts`` read that same variable as Playwright's ``baseURL``, so
every ``page.goto`` spent the full 30s ``navigationTimeout`` and died with
``net::ERR_CONNECTION_TIMED_OUT``. MEASURED on the reporting box, same three spec
files, nothing else changed::

    baseURL host.docker.internal:5050   43 of 45 FAILED
    baseURL localhost:5050              14 passed, 1 failed

Two separable defects, and this module guards both:

1. ONE VARIABLE ANSWERED TWO QUESTIONS. "How does a process reach the dashboard"
   and "what URL does the test runner navigate to" are different questions with
   different right answers inside a container. ``ICDEV_E2E_BASE_URL`` answers only
   the second and takes precedence.

2. NOTHING CHECKED. An unreachable base URL surfaced as N product-looking test
   failures rather than one infrastructure error. ``globalSetup.ts`` now probes it
   and throws.

The assertions here are structural because the subject is TypeScript and CI runs
Node 20 (no type stripping), so executing the module from pytest is not
available. They still discriminate: at the merge base neither the precedence
chain nor the guard exists.

Deliberately NOT asserted: that the probe classifies a live socket correctly.
That needs the TS module executed, and a test that quietly cannot run its subject
is the skip-as-coverage defect CLAUDE.md warns about — better absent than
pretended.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT_CONFIG = REPO_ROOT / "playwright.config.ts"
GLOBAL_SETUP = REPO_ROOT / "globalSetup.ts"

# Names that only resolve meaningfully from inside a container. `.env` is
# untracked so it cannot be asserted on; the SAMPLES are what a fresh checkout
# copies, and they are what must never carry one of these.
CONTAINER_GATEWAY_HOSTS = (
    "host.docker.internal",
    "gateway.docker.internal",
    "kubernetes.docker.internal",
    "host.containers.internal",
    "host.lima.internal",
)

ENV_SAMPLES = (".env.example", ".env.sample")


@pytest.fixture(scope="module")
def config_source() -> str:
    return PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def setup_source() -> str:
    return GLOBAL_SETUP.read_text(encoding="utf-8")


class TestBaseUrlPrecedence:
    """A dedicated variable answers the test runner's question."""

    def test_dedicated_e2e_base_url_is_honoured(self, config_source: str) -> None:
        assert "ICDEV_E2E_BASE_URL" in config_source, (
            "playwright.config.ts must honour ICDEV_E2E_BASE_URL so a deployment can "
            "keep a container-gateway ICDEV_DASHBOARD_URL for agents and still run "
            "the E2E suite."
        )

    def test_precedence_is_e2e_then_dashboard_then_derived(self, config_source: str) -> None:
        """ICDEV_E2E_BASE_URL wins; ICDEV_DASHBOARD_URL still works; localhost is the floor."""
        assignment = re.search(
            r"const DASHBOARD_URL\s*=\s*(.*?);", config_source, re.DOTALL
        )
        assert assignment, "playwright.config.ts no longer assigns DASHBOARD_URL"
        expr = assignment.group(1)

        e2e_at = expr.find("ICDEV_E2E_BASE_URL")
        dash_at = expr.find("ICDEV_DASHBOARD_URL")
        assert e2e_at != -1, "ICDEV_E2E_BASE_URL is not part of the baseURL expression"
        assert dash_at != -1, (
            "ICDEV_DASHBOARD_URL must remain honoured — pointing the suite at a remote "
            "dashboard that way is a legitimate use, and the reachability guard, not "
            "precedence, is what stops a wrong value costing 838 failures."
        )
        assert e2e_at < dash_at, (
            "ICDEV_E2E_BASE_URL must take precedence over ICDEV_DASHBOARD_URL"
        )
        assert "localhost:${PORT}" in expr, (
            "the derived http://localhost:$PORT fallback must survive — it is what an "
            "unset environment gets"
        )


class TestReachabilityGuardExists:
    """An unreachable base URL fails the run once, not once per spec."""

    def test_guard_is_exported(self, setup_source: str) -> None:
        assert "export async function assertBaseUrlReachable" in setup_source, (
            "globalSetup.ts must export assertBaseUrlReachable — without it an "
            "unreachable dashboard surfaces as N navigation timeouts that read as "
            "product defects."
        )

    def test_guard_runs_from_the_global_setup_hook(self, setup_source: str) -> None:
        """The hook, because `webServer` starts BEFORE globalSetup."""
        hook = setup_source.split("export default async function globalSetup", 1)
        assert len(hook) == 2, "globalSetup.ts has no default export"
        assert "assertBaseUrlReachable(" in hook[1], (
            "assertBaseUrlReachable must be invoked from the default globalSetup export"
        )

    def test_guard_is_not_invoked_at_config_load(self, config_source: str) -> None:
        """The false-positive trap.

        `logEnvironmentDiagnostics` is called at config load precisely because
        Playwright skips globalSetup for `--list`. The reachability probe must NOT
        follow it there: config load happens before the `webServer` plugin starts
        the dashboard, so probing then would fail every correct local run.
        """
        assert "assertBaseUrlReachable" not in config_source, (
            "assertBaseUrlReachable must not be called from playwright.config.ts — at "
            "config load the Playwright-managed dashboard has not started yet, so the "
            "probe would report a healthy run as unreachable."
        )

    def test_guard_can_throw(self, setup_source: str) -> None:
        """It must be able to fail the run — unlike the diagnostics beside it."""
        body = setup_source.split("export async function assertBaseUrlReachable", 1)[1]
        assert "throw new Error(" in body.split("\n}\n", 1)[0], (
            "assertBaseUrlReachable must throw on an unreachable base URL; logging and "
            "continuing is the defect it exists to fix."
        )

    def test_disabling_the_guard_is_announced(self, setup_source: str) -> None:
        """A guard that stands itself down quietly is the `|| true` defect again."""
        assert "ICDEV_E2E_REACHABILITY_CHECK" in setup_source
        body = setup_source.split("export async function assertBaseUrlReachable", 1)[1]
        disabled_branch = body.split("ICDEV_E2E_REACHABILITY_CHECK", 1)[1][:400]
        assert "console.log" in disabled_branch, (
            "the disabled path must print that the check is off"
        )

    def test_container_gateway_hosts_are_named(self, setup_source: str) -> None:
        """The one hostname that caused this must produce the diagnosis, not a shrug."""
        for host in CONTAINER_GATEWAY_HOSTS:
            assert host in setup_source, (
                f"{host} must be recognised as a container gateway so the error names "
                "the actual cause instead of reporting a generic timeout"
            )

    def test_failure_causes_are_not_merged(self, setup_source: str) -> None:
        """dns_failure / refused / unreachable go to three different fixes."""
        for verdict in ("dns_failure", "refused", "unreachable"):
            assert verdict in setup_source, (
                f"{verdict} must stay a distinct verdict — 'host.docker.internal' "
                "RESOLVES and times out, so reporting it as a DNS failure would send "
                "the reader to the wrong fix."
            )


class TestEnvSamplesAreReachable:
    """A fresh checkout must not copy the broken value in."""

    @pytest.mark.parametrize("sample", ENV_SAMPLES)
    def test_dashboard_url_is_not_a_container_gateway(self, sample: str) -> None:
        path = REPO_ROOT / sample
        assert path.exists(), f"{sample} is missing"
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("ICDEV_DASHBOARD_URL="):
                continue
            value = stripped.split("=", 1)[1]
            for host in CONTAINER_GATEWAY_HOSTS:
                assert host not in value, (
                    f"{sample} sets ICDEV_DASHBOARD_URL={value}. {host} is a "
                    "container-to-host gateway; on the host it does not accept "
                    "connections, and playwright.config.ts reads this as baseURL."
                )

    @pytest.mark.parametrize("sample", ENV_SAMPLES)
    def test_the_trap_is_documented_where_it_is_set(self, sample: str) -> None:
        text = (REPO_ROOT / sample).read_text(encoding="utf-8")
        assert "host.docker.internal" in text, (
            f"{sample} must WARN against the container-gateway value next to "
            "ICDEV_DASHBOARD_URL — the value is a reasonable-looking thing to set, "
            "which is why it was set."
        )
