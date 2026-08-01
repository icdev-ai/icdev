#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the shared ACE role parse cache.

RoleLoader.__init__ calls _load(), which parsed every yaml in args/ace/roles/
(88 files as of writing). The TTL cache lives on the instance, so the seven
ACE call sites that construct RoleLoader() inline re-parsed the whole
directory every time — a single /coworker request did 174 yaml parses.

These tests pin parse COUNT rather than wall-clock so they stay meaningful on
a slow CI box, and they assert that RoleLoader's per-instance semantics (the
seams the rest of the ACE suite monkeypatches) are unchanged.
"""
from __future__ import annotations

import importlib

import pytest
import yaml

rl = importlib.import_module("icdev.tools.ace.role_loader")


@pytest.fixture(autouse=True)
def _clean_cache():
    rl.reset_role_parse_cache()
    yield
    rl.reset_role_parse_cache()


@pytest.fixture
def count_parses(monkeypatch):
    calls = {"n": 0}
    real = yaml.safe_load

    def counting(stream):
        calls["n"] += 1
        return real(stream)

    monkeypatch.setattr(yaml, "safe_load", counting)
    return calls


def test_repeated_construction_parses_once(count_parses):
    """The regression: N loaders must not mean N directory parses."""
    rl.RoleLoader()
    first = count_parses["n"]
    assert first > 0, "expected the first construction to actually parse roles"

    for _ in range(10):
        rl.RoleLoader()

    assert count_parses["n"] == first


def test_each_instance_still_independent():
    """Per-instance semantics must be unchanged — the ACE suite patches these."""
    a = rl.RoleLoader()
    b = rl.RoleLoader()
    assert a is not b
    assert a._cache is not b._cache
    assert {r.role_id for r in a.list_roles()} == {r.role_id for r in b.list_roles()}


def test_mutating_one_cache_does_not_affect_another():
    a = rl.RoleLoader()
    b = rl.RoleLoader()
    a._cache.pop(next(iter(a._cache)))
    assert len(b.list_roles()) > len(a.list_roles())


def test_cache_invalidates_when_a_role_file_changes(tmp_path, count_parses):
    """Editing a role yaml must take effect without a restart."""
    import os

    d = tmp_path / "roles"
    d.mkdir()
    (d / "r1.yaml").write_text(
        yaml.safe_dump(
            {
                "role_id": "r1",
                "steps": ["a"],
                "trust_tier": "low",
                "tool_permissions": [],
            }
        ),
        encoding="utf-8",
    )

    assert rl.RoleLoader(roles_dir=d).get_role("r1").steps
    after_first = count_parses["n"]

    rl.RoleLoader(roles_dir=d)
    assert count_parses["n"] == after_first, "unchanged dir should not re-parse"

    (d / "r2.yaml").write_text(
        yaml.safe_dump(
            {
                "role_id": "r2",
                "steps": ["b"],
                "trust_tier": "low",
                "tool_permissions": [],
            }
        ),
        encoding="utf-8",
    )
    st = (d / "r2.yaml").stat()
    os.utime(d / "r2.yaml", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    ids = {r.role_id for r in rl.RoleLoader(roles_dir=d).list_roles()}
    assert ids == {"r1", "r2"}
    assert count_parses["n"] > after_first


def test_separate_dirs_do_not_share_cache(tmp_path):
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    for d, rid in ((d1, "only_a"), (d2, "only_b")):
        d.mkdir()
        (d / "r.yaml").write_text(
            yaml.safe_dump(
                {
                    "role_id": rid,
                    "steps": ["s"],
                    "trust_tier": "low",
                    "tool_permissions": [],
                }
            ),
            encoding="utf-8",
        )

    assert {r.role_id for r in rl.RoleLoader(roles_dir=d1).list_roles()} == {"only_a"}
    assert {r.role_id for r in rl.RoleLoader(roles_dir=d2).list_roles()} == {"only_b"}


def test_real_roles_still_resolve():
    """Behaviour parity against the real roles directory."""
    loader = rl.RoleLoader()
    ids = {r.role_id for r in loader.list_roles()}
    assert "ai_developer" in ids
    assert loader.get_role("ai_developer").role_id == "ai_developer"
    with pytest.raises(rl.RoleNotFoundError):
        loader.get_role("definitely_not_a_role")
