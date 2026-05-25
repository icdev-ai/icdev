#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.security.field_security."""

from tools.security.field_security import (
    _apply_mask,
    filter_response_fields,
    apply_field_policy,
    get_field_policies_for_role,
    _infer_schema,
)


class TestApplyMask:
    def test_null(self):
        assert _apply_mask("x", "null") is None

    def test_redact(self):
        assert _apply_mask("x", "redact") == "[REDACTED]"

    def test_hash(self):
        h = _apply_mask("x", "hash")
        assert isinstance(h, str)
        assert len(h) == 16


class TestFilterResponseFields:
    def test_flat_dict(self):
        data = {"email": "a@b.com", "ssn": "123-45-6789"}
        filtered = filter_response_fields(data, {"ssn": "redact"})
        assert filtered["ssn"] == "[REDACTED]"
        assert filtered["email"] == "a@b.com"

    def test_nested_dict(self):
        data = {"user": {"email": "a@b.com", "ssn": "123-45-6789"}}
        filtered = filter_response_fields(data, {"ssn": "redact"})
        assert filtered["user"]["ssn"] == "[REDACTED]"
        assert filtered["user"]["email"] == "a@b.com"

    def test_list_of_dicts(self):
        data = [{"email": "a@b.com", "ssn": "123-45-6789"}]
        filtered = filter_response_fields(data, {"ssn": "null"})
        assert filtered[0]["ssn"] is None
        assert filtered[0]["email"] == "a@b.com"

    def test_primitives_unchanged(self):
        assert filter_response_fields(42, {}) == 42
        assert filter_response_fields("hello", {}) == "hello"

    def test_no_matching_policy(self):
        data = {"id": 1}
        assert filter_response_fields(data, {"email": "redact"}) == {"id": 1}


class TestApplyFieldPolicy:
    def test_no_match_returns_data(self):
        data = {"id": 1}
        assert apply_field_policy("unknown", "viewer", data) == data


class TestGetFieldPoliciesForRole:
    def test_no_match_returns_empty(self):
        assert get_field_policies_for_role("unknown", "viewer") == {}


class TestInferSchema:
    def test_user_profile(self):
        assert _infer_schema("/api/users/123") == "user_profile"
        assert _infer_schema("/api/profile") == "user_profile"

    def test_tenant(self):
        assert _infer_schema("/api/tenants") == "tenant"

    def test_project(self):
        assert _infer_schema("/api/projects") == "project"

    def test_unknown(self):
        assert _infer_schema("/api/unknown") is None
