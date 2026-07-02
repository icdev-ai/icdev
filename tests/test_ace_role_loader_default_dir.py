# CUI // SP-CTI
"""Regression test: tools.ace.role_loader's default _ROLES_DIR must resolve to the
real args/ace/roles directory (not one level too high), else RoleLoader() loads
zero roles by default — a pre-existing off-by-one bug, fixed alongside the
BI Dashboard bi_analyst role addition.
"""
from __future__ import annotations


def test_default_roles_dir_resolves_and_loads_roles():
    from tools.ace.role_loader import RoleLoader, _ROLES_DIR

    assert _ROLES_DIR.exists(), f"_ROLES_DIR does not exist: {_ROLES_DIR}"
    loader = RoleLoader()
    assert len(loader._cache) > 50, "default RoleLoader() should load the full role set"
    assert "bi_analyst" in loader._cache
    assert "data_analyst" in loader._cache
