# CUI // SP-CTI
"""Tests for Cortex service keys (ctx-expose-02).

Covers the security-critical bindings:
  * tenant_id ALWAYS comes from the key row — a request can never override it
  * classification is clamped to the key's ceiling (Bell-LaPadula)
  * strictness (air_gap / fail_closed) can only be raised, never lowered
  * trusted_content is force-cleared for network callers
  * hashes at rest, plaintext shown once, revocation
"""
from __future__ import annotations

import importlib

import pytest

from tools.db.storage import get_connection

service_keys = importlib.import_module("tools.cortex.service_keys")


@pytest.fixture
def keys_db(icdev_db, monkeypatch):
    """Point service_keys at the MINIMAL_ICDEV_SCHEMA tmp DB (shim-aware)."""
    monkeypatch.setattr(
        service_keys, "_get_db", lambda: get_connection(db_path=str(icdev_db))
    )
    return icdev_db


def _create(**kwargs):
    defaults = {"label": "compass", "tenant_id": "compass"}
    defaults.update(kwargs)
    return service_keys.create_key(defaults.pop("label"), defaults.pop("tenant_id"), **defaults)


# ---------------------------------------------------------------------------
# Key lifecycle
# ---------------------------------------------------------------------------
def test_create_and_verify_roundtrip(keys_db):
    created = _create()
    assert created["raw_key"].startswith(service_keys.API_KEY_PREFIX)

    record = service_keys.verify_key(created["raw_key"])
    assert record is not None
    assert record["tenant_id"] == "compass"
    assert record["label"] == "compass"
    assert record["scopes"] == list(service_keys.DEFAULT_SCOPES)


def test_raw_key_never_stored(keys_db):
    created = _create()
    conn = get_connection(db_path=str(keys_db))
    try:
        row = conn.execute(
            "SELECT key_hash, key_prefix FROM cortex_service_keys WHERE id = %s",
            (created["key_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["key_hash"] != created["raw_key"]
    assert created["raw_key"] not in (row["key_hash"] or "")
    # prefix identifies but cannot reconstruct
    assert len(row["key_prefix"]) == 8


def test_verify_rejects_unknown_and_wrong_prefix(keys_db):
    assert service_keys.verify_key("icdev_ctx_notarealkey") is None
    assert service_keys.verify_key("icdev_dash_wrongfamily") is None
    assert service_keys.verify_key("") is None


def test_revoked_key_rejected(keys_db):
    created = _create()
    assert service_keys.verify_key(created["raw_key"]) is not None
    service_keys.revoke_key(created["key_id"], revoked_by="test")
    assert service_keys.verify_key(created["raw_key"]) is None


def test_create_rejects_unknown_scope(keys_db):
    with pytest.raises(ValueError, match="unknown scopes"):
        _create(scopes=["cortex:search", "cortex:frobnicate"])


def test_agent_scope_not_in_vocabulary():
    # REST surface has no agent endpoint — team launches are MCP-only.
    assert "cortex:agent" not in service_keys.ALL_SCOPES
    assert all(s.startswith(("cortex:", "databridge:")) for s in service_keys.ALL_SCOPES)


def test_list_keys_redacts_hashes(keys_db):
    _create()
    listed = service_keys.list_keys()
    assert listed and "key_hash" not in listed[0]
    assert isinstance(listed[0]["scopes"], list)


# ---------------------------------------------------------------------------
# resolve_context — the security bindings
# ---------------------------------------------------------------------------
def test_tenant_never_from_request(keys_db):
    created = _create(tenant_id="compass")
    binding = service_keys.resolve_context(
        created["raw_key"], {"tenant_id": "tenant-evil", "user_id": "u1"}
    )
    assert binding is not None
    assert binding["ctx"].tenant_id == "compass"
    assert binding["ctx"].user_id == "u1"  # attribution passes through


def test_classification_clamped_to_ceiling(keys_db):
    created = _create(classification_ceiling="CUI")
    binding = service_keys.resolve_context(
        created["raw_key"], {"classification": "SECRET"}
    )
    assert binding["ctx"].classification == "CUI"

    # Unset request classification -> ceiling
    binding = service_keys.resolve_context(created["raw_key"], {})
    assert binding["ctx"].classification == "CUI"


def test_strictness_only_raised(keys_db):
    created = _create()
    # Caller may raise strictness…
    binding = service_keys.resolve_context(
        created["raw_key"], {"air_gap": True, "fail_closed": True}
    )
    assert binding["ctx"].air_gap is True
    assert binding["ctx"].fail_closed is True
    # …but never lower it: False defers to server policy (None), not False.
    binding = service_keys.resolve_context(
        created["raw_key"], {"air_gap": False, "fail_closed": False}
    )
    assert binding["ctx"].air_gap is False  # server default, not raised
    assert binding["ctx"].fail_closed is None  # platform policy, NOT hard False


def test_trusted_content_force_cleared(keys_db):
    created = _create()
    binding = service_keys.resolve_context(created["raw_key"], {"trusted_content": True})
    assert binding["ctx"].trusted_content is False


def test_resolve_context_invalid_key(keys_db):
    assert service_keys.resolve_context("icdev_ctx_bogus", {}) is None


# -- grant (prem-msr-07) -----------------------------------------------------

def test_grant_scopes_widens_an_existing_key(keys_db):
    """A key's scopes are frozen at creation, so a newly-added scope would 403
    forever on every key issued before it. Grant widens in place."""
    created = _create(scopes=["cortex:search"])

    result = service_keys.grant_scopes(created["key_id"], ["cortex:slides"])

    assert result["added"] == ["cortex:slides"]
    assert set(result["scopes"]) == {"cortex:search", "cortex:slides"}

    binding = service_keys.verify_key(created["raw_key"])
    assert "cortex:slides" in binding["scopes"]


def test_grant_is_additive_and_idempotent(keys_db):
    created = _create(scopes=["cortex:search"])
    service_keys.grant_scopes(created["key_id"], ["cortex:slides"])

    again = service_keys.grant_scopes(created["key_id"], ["cortex:slides"])

    assert again["added"] == []
    # The original scope is never dropped.
    assert set(again["scopes"]) == {"cortex:search", "cortex:slides"}


def test_grant_rejects_an_unknown_scope(keys_db):
    created = _create(scopes=["cortex:search"])
    with pytest.raises(ValueError, match="unknown scopes"):
        service_keys.grant_scopes(created["key_id"], ["cortex:root"])


def test_grant_on_a_revoked_key_fails(keys_db):
    created = _create(scopes=["cortex:search"])
    service_keys.revoke_key(created["key_id"])
    with pytest.raises(ValueError, match="no active service key"):
        service_keys.grant_scopes(created["key_id"], ["cortex:slides"])
