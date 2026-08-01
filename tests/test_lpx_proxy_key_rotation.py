# CUI // SP-CTI
"""lpx-keys-03 — key rotation, revocation, expiry, and append-only audit tests."""

from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timezone

import pytest


@pytest.fixture
def keys(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.delenv("ICDEV_LLM_PROXY_ENABLED", raising=False)
    # Deterministic default TTL for expiry-default assertions.
    monkeypatch.setenv("ICDEV_LLM_PROXY_KEY_TTL_DAYS", "30")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    mod = importlib.import_module("tools.llm.proxy_keys")
    mod._migrated = False
    mod.ensure_schema()
    return mod


def test_issue_applies_default_expiry_and_audits(keys):
    k = keys.issue_key(scope_type="team", scope_ref="1", created_by="facilitator")
    assert k["expires_at"] is not None  # default TTL applied
    trail = keys.audit_trail(k["key_id"])
    assert len(trail) == 1
    assert trail[0]["action"] == "issued"
    assert trail[0]["actor"] == "facilitator"


def test_explicit_ttl_zero_disables_default(keys, monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_KEY_TTL_DAYS", "0")
    k = keys.issue_key(scope_type="user", scope_ref="alice")
    assert k["expires_at"] is None


def test_revoke_flips_status_and_audits(keys):
    k = keys.issue_key(scope_type="user", scope_ref="bob")
    kid = k["key_id"]
    out = keys.revoke_key(kid, actor="admin", reason="left cohort")
    assert out["status"] == "revoked"
    # lookup now reports revoked — enforcement paths block on this
    found = keys.lookup_by_key(k["virtual_key"])
    assert found["status"] == "revoked"
    actions = [a["action"] for a in keys.audit_trail(kid)]
    assert "revoked" in actions


def test_revoke_is_scoped_to_one_key(keys):
    a = keys.issue_key(scope_type="user", scope_ref="a")
    b = keys.issue_key(scope_type="user", scope_ref="b")
    keys.revoke_key(a["key_id"])
    assert keys.show_key(a["key_id"])["status"] == "revoked"
    assert keys.show_key(b["key_id"])["status"] == "active"  # untouched


def test_rotate_issues_successor_linked_to_predecessor(keys):
    old = keys.issue_key(scope_type="team", scope_ref="7", session_id="42",
                         max_budget_usd=10.0, budget_window="exercise", rpm_limit=60)
    new = keys.rotate_key(old["key_id"], actor="admin")
    # Old key is rotated (inactive), new key is active and carries the params.
    assert keys.show_key(old["key_id"])["status"] == "rotated"
    assert new["status"] == "active"
    assert new["rotated_from"] == old["key_id"]
    assert new["max_budget_usd"] == 10.0
    assert new["rpm_limit"] == 60
    assert new["virtual_key"] != old["virtual_key"]
    # Both keys audited.
    assert "rotated" in [a["action"] for a in keys.audit_trail(old["key_id"])]
    assert "rotated" in [a["action"] for a in keys.audit_trail(new["key_id"])]


def test_expire_sweep_flips_past_due_keys(keys):
    # Issue with an expiry already in the past.
    past = (datetime(2000, 1, 1, tzinfo=timezone.utc)).isoformat()
    k = keys.issue_key(scope_type="user", scope_ref="c", expires_at=past)
    assert keys.show_key(k["key_id"])["status"] == "active"
    res = keys.expire_keys()
    assert k["key_id"] in res["expired"]
    assert keys.show_key(k["key_id"])["status"] == "expired"
    assert "expired" in [a["action"] for a in keys.audit_trail(k["key_id"])]
    # Idempotent: second sweep expires nothing new.
    assert keys.expire_keys()["count"] == 0


def test_audit_is_append_only_in_practice(keys):
    """Every lifecycle event adds a row; none are removed."""
    k = keys.issue_key(scope_type="user", scope_ref="d")
    keys.revoke_key(k["key_id"])
    trail = keys.audit_trail(k["key_id"])
    # issued + revoked, newest first, monotonic append.
    assert [a["action"] for a in trail] == ["revoked", "issued"]


def test_revoke_unknown_key_raises(keys):
    with pytest.raises(ValueError):
        keys.revoke_key("nope")
