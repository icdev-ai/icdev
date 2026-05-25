#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.security.column_security."""

from tools.security.column_security import (
    _apply_mask,
    mask_columns,
    apply_column_policy,
    get_column_policies_for_role,
    grant_column_select,
    revoke_column_select,
)


class TestApplyMask:
    def test_null(self):
        assert _apply_mask("secret", "null") is None

    def test_redact(self):
        assert _apply_mask("secret", "redact") == "[REDACTED]"

    def test_hash(self):
        h = _apply_mask("secret", "hash")
        assert isinstance(h, str)
        assert len(h) == 16

    def test_hash_none(self):
        assert _apply_mask(None, "hash") is None

    def test_truncate(self):
        assert _apply_mask("long text", "truncate") == "..."

    def test_truncate_none(self):
        assert _apply_mask(None, "truncate") is None

    def test_unknown_strategy(self):
        assert _apply_mask("x", "unknown") == "x"


class TestMaskColumns:
    def test_single_mask(self):
        row = {"email": "a@b.com", "secret": "s1"}
        masked = mask_columns(row, {"secret": "null"})
        assert masked["secret"] is None
        assert masked["email"] == "a@b.com"

    def test_multiple_strategies(self):
        row = {"email": "a@b.com", "secret": "s1", "note": "hi"}
        masked = mask_columns(row, {"secret": "redact", "note": "truncate"})
        assert masked["secret"] == "[REDACTED]"
        assert masked["note"] == "..."
        assert masked["email"] == "a@b.com"

    def test_does_not_mutate_original(self):
        row = {"secret": "s1"}
        mask_columns(row, {"secret": "null"})
        assert row["secret"] == "s1"


class TestApplyColumnPolicy:
    def test_no_match_returns_copy(self):
        row = {"id": 1}
        result = apply_column_policy("nonexistent", "viewer", row)
        assert result == row
        assert result is not row


class TestGetColumnPoliciesForRole:
    def test_no_match_returns_empty(self):
        assert get_column_policies_for_role("unknown", "viewer") == {}


class TestGrantColumnSelect:
    def test_ddl(self):
        ddl = grant_column_select("users", ["email", "name"], "viewer")
        assert ddl == "GRANT SELECT (email, name) ON users TO viewer;"


class TestRevokeColumnSelect:
    def test_ddl(self):
        ddl = revoke_column_select("users", ["email", "name"], "viewer")
        assert ddl == "REVOKE SELECT (email, name) ON users FROM viewer;"
