#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.security.security_context."""

import pytest
from tools.security.security_context import (
    SecurityContext,
    get_security_context,
    set_security_context,
    clear_security_context,
    from_request,
    _get_clearance_order,
)


class TestSecurityContext:
    def test_defaults(self):
        ctx = SecurityContext()
        assert ctx.user_id == ""
        assert ctx.role == ""
        assert ctx.clearance_level == 0
        assert ctx.compartments == frozenset()
        assert ctx.impact_level == "IL4"
        assert ctx.tenant_id is None
        assert ctx.classification == "CUI"
        assert ctx.mtls_cn is None
        assert ctx.auth_method == ""

    def test_to_dict(self):
        ctx = SecurityContext(
            user_id="u1",
            role="admin",
            clearance_level=2,
            compartments=frozenset({"COI_FINANCE"}),
            tenant_id="t1",
        )
        d = ctx.to_dict()
        assert d["user_id"] == "u1"
        assert d["role"] == "admin"
        assert d["clearance_level"] == 2
        assert d["compartments"] == ["COI_FINANCE"]
        assert d["tenant_id"] == "t1"

    def test_has_compartment(self):
        ctx = SecurityContext(compartments=frozenset({"COI_FINANCE", "LAC_DC_EAST"}))
        assert ctx.has_compartment("COI_FINANCE")
        assert not ctx.has_compartment("COI_ENGINEERING")

    def test_has_any_compartment(self):
        ctx = SecurityContext(compartments=frozenset({"COI_FINANCE"}))
        assert ctx.has_any_compartment({"COI_FINANCE", "COI_ENGINEERING"})
        assert not ctx.has_any_compartment({"COI_ENGINEERING"})

    def test_has_all_compartments(self):
        ctx = SecurityContext(compartments=frozenset({"A", "B"}))
        assert ctx.has_all_compartments({"A"})
        assert ctx.has_all_compartments({"A", "B"})
        assert not ctx.has_all_compartments({"A", "B", "C"})

    def test_frozen(self):
        ctx = SecurityContext()
        with pytest.raises(AttributeError):
            ctx.user_id = "x"


class TestClearanceOrder:
    def test_known_levels(self):
        assert _get_clearance_order("PUBLIC") == 0
        assert _get_clearance_order("CUI") == 1
        assert _get_clearance_order("ECI") == 2      # G-10: IL5 sensitive unclassified
        assert _get_clearance_order("SECRET") == 3
        assert _get_clearance_order("TOP SECRET") == 4
        assert _get_clearance_order("TOP SECRET//SCI") == 5

    def test_case_insensitive(self):
        assert _get_clearance_order("cui") == 1
        assert _get_clearance_order("Secret") == 3

    def test_unknown_defaults_cui(self):
        assert _get_clearance_order("foobar") == 1


class TestThreadLocal:
    def test_set_get_clear(self):
        clear_security_context()
        assert get_security_context() is None
        ctx = SecurityContext(user_id="u1")
        set_security_context(ctx)
        assert get_security_context() == ctx
        clear_security_context()
        assert get_security_context() is None


class TestFromRequest:
    def test_from_headers(self):
        class FakeRequest:
            headers = {
                "X-User-ID": "u1",
                "X-User-Role": "admin",
                "X-Tenant-ID": "t1",
                "X-Classification": "SECRET",
                "X-Compartments": "COI_FINANCE, LAC_DC_EAST",
            }

        ctx = from_request(FakeRequest())
        assert ctx.user_id == "u1"
        assert ctx.role == "admin"
        assert ctx.tenant_id == "t1"
        assert ctx.clearance_level == 3   # SECRET → order 3 after ECI inserted at 2
        assert ctx.compartments == frozenset({"COI_FINANCE", "LAC_DC_EAST"})

    def test_defaults_when_no_headers(self):
        class FakeRequest:
            headers = {}

        ctx = from_request(FakeRequest())
        assert ctx.user_id == ""
        assert ctx.clearance_level == 1  # CUI default
