"""Unit tests for ACE RoleLoader — verifies role YAML files load without errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from icdev.tools.ace.role_loader import RoleLoader, RoleNotFoundError, RoleTemplate


ROLES_DIR = Path(__file__).parent.parent / "args" / "ace" / "roles"


@pytest.fixture(scope="module")
def loader() -> RoleLoader:
    return RoleLoader(roles_dir=ROLES_DIR, hot_reload=False)


def test_adversarial_verifier_loads(loader: RoleLoader) -> None:
    role = loader.get_role("adversarial_verifier")
    assert isinstance(role, RoleTemplate)
    assert role.role_id == "adversarial_verifier"


def test_adversarial_verifier_required_fields(loader: RoleLoader) -> None:
    role = loader.get_role("adversarial_verifier")
    assert role.trust_tier, "trust_tier must be non-empty"
    assert role.tool_permissions, "tool_permissions must be non-empty"
    assert role.steps, "steps must be non-empty"


def test_adversarial_verifier_step_names(loader: RoleLoader) -> None:
    role = loader.get_role("adversarial_verifier")
    step_names = [s.name for s in role.steps]
    assert "emit_verdict" in step_names


def test_unknown_role_raises(loader: RoleLoader) -> None:
    with pytest.raises(RoleNotFoundError):
        loader.get_role("__nonexistent_role__")


def test_all_roles_load_without_error(loader: RoleLoader) -> None:
    roles = loader.list_roles()
    assert len(roles) >= 1
    ids = {r.role_id for r in roles}
    assert "adversarial_verifier" in ids
