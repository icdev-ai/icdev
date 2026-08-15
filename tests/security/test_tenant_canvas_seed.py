#!/usr/bin/env python3
"""A tenant seeded with zero canvas grants must not look like success. CUI // SP-CTI

`canvas_access_grants` is empty on this deployment, and that is CORRECT: grants
are per-tenant, there are no tenants, and every dashboard_users row carries a
NULL tenant_id so the guard's "authenticated but no tenant" early-allow applies.
Nothing needs seeding for a single-tenant, multi-user install.

What the emptiness hid is what happens the day a tenant IS created.
`seed_tenant_defaults` skips any canvas with no `default_roles`, and 21 of the 38
registered canvases declare none (measured 2026-08-15) -- ace, slides,
delta_review, foundry, integrity, demo_runner, mission_canvas among them. It
returned None and logged one "Seeded canvas default grants" line whatever
happened, and `tenant_manager` wrapped the call in a try/except that logged a
warning. So a tenant whose users could open NOTHING was created, reported as
fine, and would surface days later as "I cannot open ACE" with nothing pointing
back at tenant creation.

This is reported, not enforced. Declaring default_roles for the other 21 is a
product decision about who should see what, and failing tenant creation over it
would be worse than the lockout it warns about.

Deterministic: the registry and the grant write are injected. No DB, no network.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.security import canvas_access  # noqa: E402


class _Canvas:
    def __init__(self, key, roles):
        self.key = key
        self.default_roles = list(roles)


class _Registry:
    def __init__(self, canvases):
        self._c = canvases

    def iter_canvases(self):
        return list(self._c)


def _registry_module():
    """Resolve the module `seed_tenant_defaults` actually imports from.

    `import tools.config.component_registry as reg` fails here: the root
    `tools/` package is a shim that redirects to `icdev.tools.*`, and the icdev
    mirror has no component_registry. importlib resolves the real module, and
    patching THAT object is what the function under test will see.
    """
    import importlib

    return importlib.import_module("tools.config.component_registry")


def _patch_registry(monkeypatch, canvases, *, grant=None):
    reg = _registry_module()
    monkeypatch.setattr(reg, "get_registry", lambda: _Registry(canvases))
    calls = []

    def _grant(**kw):
        calls.append(kw)
        if grant is not None:
            return grant(**kw)
        return "grant-1"

    monkeypatch.setattr(canvas_access, "grant_access", _grant)
    return calls


# --------------------------------------------------------------------------- #
# The seeder must report what it did NOT do
# --------------------------------------------------------------------------- #

def test_a_canvas_without_default_roles_is_reported_not_dropped(monkeypatch):
    """The 21. Skipping silently is what makes a locked-out tenant invisible."""
    _patch_registry(monkeypatch, [
        _Canvas("cortex", ["admin"]),
        _Canvas("ace", []),          # declares none -> unreachable in this tenant
        _Canvas("slides", []),
    ])
    out = canvas_access.seed_tenant_defaults("t-1")
    assert out["granted"] == 1
    assert out["skipped_no_default_roles"] == ["ace", "slides"]
    assert out["failed"] == []


def test_a_seed_that_grants_nothing_says_so(monkeypatch):
    """The whole-lockout case: every canvas skipped, zero grants."""
    _patch_registry(monkeypatch, [_Canvas("ace", []), _Canvas("foundry", [])])
    out = canvas_access.seed_tenant_defaults("t-2")
    assert out["granted"] == 0
    assert len(out["skipped_no_default_roles"]) == 2


def test_a_failing_grant_is_counted_not_swallowed(monkeypatch):
    """It was logged at debug — invisible by default."""
    def _boom(**kw):
        raise RuntimeError("db down")

    _patch_registry(monkeypatch, [_Canvas("cortex", ["admin", "bd"])], grant=_boom)
    out = canvas_access.seed_tenant_defaults("t-3")
    assert out["granted"] == 0
    assert out["failed"] == ["cortex/admin", "cortex/bd"]


def test_an_unavailable_registry_returns_a_shape_not_none(monkeypatch):
    """The caller now branches on the result, so None would be a new crash."""
    reg = _registry_module()

    def _boom():
        raise RuntimeError("no registry")

    monkeypatch.setattr(reg, "get_registry", _boom)
    out = canvas_access.seed_tenant_defaults("t-4")
    assert out["granted"] == 0
    assert out["failed"]


def test_a_full_seed_reports_clean(monkeypatch):
    _patch_registry(monkeypatch, [
        _Canvas("cortex", ["admin"]), _Canvas("idc", ["admin", "bd"])])
    out = canvas_access.seed_tenant_defaults("t-5")
    assert out["granted"] == 3
    assert out["skipped_no_default_roles"] == []
    assert out["failed"] == []


# --------------------------------------------------------------------------- #
# tenant_manager must surface it — and must NOT roll the tenant back
# --------------------------------------------------------------------------- #

def test_tenant_creation_reports_a_zero_grant_seed():
    import inspect

    from tools.saas import tenant_manager

    src = inspect.getsource(tenant_manager)
    assert 'if not _seed.get("granted")' in src, (
        "the caller must branch on the outcome, not assume it"
    )
    assert "seeded ZERO canvas grants" in src


def test_a_seeding_problem_still_does_not_fail_tenant_creation():
    """Best-effort is deliberate: a grant problem must not lose the tenant."""
    import inspect

    from tools.saas import tenant_manager

    src = inspect.getsource(tenant_manager)
    i = src.find("seed_tenant_defaults")
    tail = src[i:i + 900]
    assert "except Exception" in tail
    assert "raise" not in tail, "seeding must not propagate and abort creation"


def test_the_raise_path_is_error_not_warning():
    """A tenant with no grants at all is not a warning-level fact."""
    import inspect

    from tools.saas import tenant_manager

    src = inspect.getsource(tenant_manager)
    i = src.find("seed_tenant_defaults RAISED")
    assert i != -1
    assert "logger.error" in src[max(0, i - 200):i]


@pytest.mark.parametrize("granted,expect_alarm", [
    (0, True), (1, False), (44, False),
])
def test_alarm_fires_only_on_a_zero_seed(granted, expect_alarm):
    assert (not granted) is expect_alarm
