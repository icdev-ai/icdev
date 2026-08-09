#!/usr/bin/env python3
"""pytest-timeout must be declared, installed, and set to a survivable value.

pyproject.toml has carried a `timeout` ini option and the suite has carried 21
@pytest.mark.timeout markers for a long time, but pytest-timeout was never a
declared dependency. Every CI job installs `-r requirements.txt`, so the plugin
was absent there and all of it was inert: pytest logged "PytestConfigWarning:
Unknown config option: timeout", each marker warned as unknown, and a hung test
ran until the job did — the `test` job sets no timeout-minutes, so that is
GitHub's 6h default. It only ever bound on developer machines that happened to
have the plugin from something else.

The floor below exists because the first instinct on turning the plugin on is to
keep the old `timeout = 30`, and 30 does not survive contact: the slowest test in
the merge-gate selection measures 31s on a warm workstation and a hosted runner
is slower. A default that fails healthy tests gets the plugin removed rather than
the tests fixed.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQS = (REPO / "requirements.txt").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

#: Below this, the ini default starts failing tests that are merely slow.
#: Anchored on the 31s measured for the slowest merge-gate test, with runner
#: headroom. Raising a specific test's budget is a marker, not a change here.
_MIN_SURVIVABLE_TIMEOUT_S = 120


def _active_lines(prefix: str) -> list[str]:
    return [
        ln for ln in REQS.splitlines()
        if ln.strip().lower().startswith(prefix) and not ln.strip().startswith("#")
    ]


def test_pytest_timeout_is_declared_in_requirements():
    assert _active_lines("pytest-timeout"), (
        "pytest-timeout must be in requirements.txt — CI installs only from it, "
        "and without the plugin the `timeout` ini option and every "
        "@pytest.mark.timeout marker are silently ignored"
    )


def test_pytest_timeout_is_declared_in_the_testing_extra():
    extra = PYPROJECT["project"]["optional-dependencies"]["testing"]
    assert any(dep.lower().startswith("pytest-timeout") for dep in extra), (
        "the `testing` extra must declare pytest-timeout alongside requirements.txt, "
        "or `pip install icdev[testing]` gets a suite whose budgets do not bind"
    )


def test_the_plugin_is_actually_live_in_this_session(pytestconfig):
    """On-disk declaration is not the claim — the claim is that it is loaded.

    This is what fails if someone drops the dependency: the two tests above read
    files and would still pass against a stale install.
    """
    assert pytestconfig.pluginmanager.hasplugin("timeout"), (
        "pytest-timeout is declared but not loaded — reinstall test dependencies"
    )


def test_the_default_timeout_is_survivable():
    configured = int(PYPROJECT["tool"]["pytest"]["ini_options"]["timeout"])
    assert configured >= _MIN_SURVIVABLE_TIMEOUT_S, (
        f"timeout = {configured} is a performance budget, not a hang killer. The "
        "slowest merge-gate test measures ~31s on a warm workstation and CI "
        "runners are slower, so a low default fails healthy tests. Put tight "
        "budgets on individual tests with @pytest.mark.timeout instead."
    )
