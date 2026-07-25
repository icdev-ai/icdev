# CUI // SP-CTI
"""lpx-keys-01 — virtual-key issuance tests.

Proves the security contract:
  * an issued key is returned exactly once and only its SHA-256 hash is stored;
  * the master/admin key is never read into any output;
  * list/show/lookup never expose the key or its hash;
  * budgets/rate ceilings are recorded at issuance;
  * LiteLLM sync is best-effort and OFF by default (litellm_synced=False).

Uses a temp SQLite DB via ICDEV_DB_PATH + the shared conftest schema and the
storage translate layer (%s params) — no raw sqlite3 in the query path.
"""

from __future__ import annotations

import hashlib
import importlib
import sqlite3

import pytest


@pytest.fixture
def keys_mod(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    # Proxy OFF by default; master key present to prove it never leaks.
    monkeypatch.delenv("ICDEV_LLM_PROXY_ENABLED", raising=False)
    monkeypatch.setenv("ICDEV_LLM_PROXY_MASTER_KEY", "sk-master-SECRET-should-never-appear")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    mod = importlib.import_module("tools.llm.proxy_keys")
    mod._migrated = False
    mod.ensure_schema()
    return mod


def _raw_rows(mod):
    from tools.db.storage import get_connection

    c = get_connection()
    rows = c.execute("SELECT key_id, key_hash, key_prefix FROM llm_proxy_keys").fetchall()
    return [dict(r) for r in rows]


def test_issue_returns_key_once_and_stores_only_hash(keys_mod):
    result = keys_mod.issue_key(alias="team-blue", scope_type="team", scope_ref="7", session_id="42")
    vk = result["virtual_key"]
    assert vk.startswith("sk-icdev-")
    assert result["status"] == "active"

    rows = _raw_rows(keys_mod)
    assert len(rows) == 1
    stored = rows[0]
    # Plaintext key is NOT stored; only its sha256 hash is.
    assert stored["key_hash"] == hashlib.sha256(vk.encode()).hexdigest()
    assert vk not in stored["key_hash"]
    assert stored["key_prefix"] == vk[:16]


def test_master_key_never_in_output(keys_mod):
    result = keys_mod.issue_key(alias="x")
    blob = repr(result)
    assert "SECRET" not in blob
    assert "sk-master" not in blob


def test_lookup_by_key_matches_hash(keys_mod):
    result = keys_mod.issue_key(scope_type="user", scope_ref="alice")
    vk = result["virtual_key"]
    found = keys_mod.lookup_by_key(vk)
    assert found is not None
    assert found["key_id"] == result["key_id"]
    assert "key_hash" not in found  # never exposed
    assert "virtual_key" not in found
    # A wrong key resolves to nothing.
    assert keys_mod.lookup_by_key("sk-icdev-not-a-real-key") is None


def test_list_and_show_never_expose_secrets(keys_mod):
    r1 = keys_mod.issue_key(scope_type="team", scope_ref="1", session_id="9")
    keys_mod.issue_key(scope_type="team", scope_ref="2", session_id="9")

    listed = keys_mod.list_keys(session_id="9")
    assert len(listed) == 2
    for row in listed:
        assert "key_hash" not in row
        assert "virtual_key" not in row

    shown = keys_mod.show_key(r1["key_id"])
    assert shown["key_id"] == r1["key_id"]
    assert "key_hash" not in shown

    assert keys_mod.show_key("does-not-exist") is None


def test_budget_and_rate_recorded(keys_mod):
    result = keys_mod.issue_key(
        scope_type="team",
        scope_ref="3",
        max_budget_usd=10.0,
        budget_window="exercise",
        rpm_limit=60,
        tpm_limit=100000,
    )
    shown = keys_mod.show_key(result["key_id"])
    assert shown["max_budget_usd"] == 10.0
    assert shown["budget_window"] == "exercise"
    assert shown["rpm_limit"] == 60
    assert shown["tpm_limit"] == 100000


def test_litellm_sync_off_by_default(keys_mod):
    result = keys_mod.issue_key(scope_type="tenant", scope_ref="default")
    assert result["litellm_synced"] is False
    shown = keys_mod.show_key(result["key_id"])
    assert shown["litellm_synced"] is False


def test_invalid_scope_and_window_rejected(keys_mod):
    with pytest.raises(ValueError):
        keys_mod.issue_key(scope_type="student")
    with pytest.raises(ValueError):
        keys_mod.issue_key(budget_window="fortnight")


def test_list_filter_by_scope(keys_mod):
    keys_mod.issue_key(scope_type="team", scope_ref="10")
    keys_mod.issue_key(scope_type="guild", scope_ref="20")
    teams = keys_mod.list_keys(scope_type="team")
    assert len(teams) == 1
    assert teams[0]["scope_type"] == "team"
