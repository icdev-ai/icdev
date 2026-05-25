#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.security.classification_enforcer."""

from tools.security.classification_enforcer import (
    can_read,
    can_write,
    can_access_compartment,
    check_mac,
    _get_clearance_order,
    _get_required_clearance,
    _is_readonly_for_user,
)
from tools.security.security_context import SecurityContext


class TestCanRead:
    def test_public_from_cui(self):
        ctx = SecurityContext(clearance_level=1)
        assert can_read("PUBLIC", ctx)

    def test_cui_from_cui(self):
        ctx = SecurityContext(clearance_level=1)
        assert can_read("CUI", ctx)

    def test_secret_from_cui_denied(self):
        ctx = SecurityContext(clearance_level=1)
        assert not can_read("SECRET", ctx)

    def test_secret_from_secret(self):
        ctx = SecurityContext(clearance_level=2)
        assert can_read("SECRET", ctx)

    def test_top_secret_from_secret_denied(self):
        ctx = SecurityContext(clearance_level=2)
        assert not can_read("TOP SECRET", ctx)

    def test_none_ctx_allowed(self):
        assert can_read("SECRET", None)


class TestCanWrite:
    def test_same_level(self):
        ctx = SecurityContext(clearance_level=1)
        assert can_write("CUI", ctx)

    def test_write_down_denied(self):
        ctx = SecurityContext(clearance_level=2)
        assert not can_write("CUI", ctx)

    def test_write_up_denied(self):
        ctx = SecurityContext(clearance_level=1)
        assert not can_write("SECRET", ctx)

    def test_none_ctx_allowed(self):
        assert can_write("SECRET", None)


class TestCanAccessCompartment:
    def test_empty_resource_allowed(self):
        ctx = SecurityContext(compartments=frozenset({"COI_FINANCE"}))
        assert can_access_compartment(set(), ctx)

    def test_subset_allowed(self):
        ctx = SecurityContext(compartments=frozenset({"COI_FINANCE", "LAC_DC_EAST"}))
        assert can_access_compartment({"COI_FINANCE"}, ctx)

    def test_non_subset_denied(self):
        ctx = SecurityContext(compartments=frozenset({"COI_FINANCE"}))
        assert not can_access_compartment({"COI_ENGINEERING"}, ctx)

    def test_none_ctx_allowed(self):
        assert can_access_compartment({"COI_FINANCE"}, None)


class TestCheckMac:
    def test_read_pass(self):
        ctx = SecurityContext(clearance_level=2)
        assert check_mac("SECRET", None, ctx, "read")

    def test_read_fail(self):
        ctx = SecurityContext(clearance_level=1)
        assert not check_mac("SECRET", None, ctx, "read")

    def test_write_pass(self):
        ctx = SecurityContext(clearance_level=2)
        assert check_mac("SECRET", None, ctx, "write")

    def test_write_fail(self):
        ctx = SecurityContext(clearance_level=1)
        assert not check_mac("SECRET", None, ctx, "write")

    def test_compartment_fail(self):
        ctx = SecurityContext(clearance_level=2, compartments=frozenset({"A"}))
        assert not check_mac("SECRET", {"B"}, ctx, "read")


class TestHelpers:
    def test_get_clearance_order(self):
        assert _get_clearance_order("CUI") == 1

    def test_get_required_clearance(self):
        assert _get_required_clearance("SECRET") == "SECRET"

    def test_is_readonly_for_user(self):
        assert _is_readonly_for_user("SECRET", "CUI")
        assert not _is_readonly_for_user("CUI", "SECRET")
