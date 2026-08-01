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
    _extract_from_flask_g,
    classifications_dominated_by,
)


class TestClassificationsDominatedBy:
    """Bell-LaPadula read-down: a clearance dominates lower classifications,
    never higher ones. Guards the RLS predicate (classification IN <set>)."""

    def test_read_down_only(self):
        # Asserted as an invariant, not a literal set: the ladder gains labels
        # over time (UNCLASSIFIED was added at order 0 alongside PUBLIC), and a
        # hardcoded equality turns every such addition into a false failure.
        # What must hold is that the set is exactly the labels at or below CUI.
        s = classifications_dominated_by("CUI")
        assert {"PUBLIC", "UNCLASSIFIED", "CUI"} <= s
        assert all(_get_clearance_order(lbl) <= _get_clearance_order("CUI") for lbl in s)

    def test_secret_reads_down_not_up(self):
        s = classifications_dominated_by("SECRET")
        assert "CUI" in s and "SECRET" in s
        assert "TOP SECRET" not in s and "TOP SECRET//SCI" not in s

    def test_top_secret_sci_reads_all(self):
        assert {"PUBLIC", "CUI", "SECRET", "TOP SECRET", "TOP SECRET//SCI"} <= classifications_dominated_by(
            "TOP SECRET//SCI"
        )

    def test_no_read_up(self):
        # The crux of the RLS bug: a lower clearance must never see a higher row.
        assert "TOP SECRET" not in classifications_dominated_by("CUI")

    def test_empty_when_no_classification(self):
        assert classifications_dominated_by(None) == set()
        assert classifications_dominated_by("") == set()


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


class TestExtractFromFlaskG:
    """_extract_from_flask_g derives SecurityContext from g.current_user
    (dashboard_users row shape: role/clearance_level/compartments), the
    dict written by tools.dashboard.auth._auth_before_request (prop-sec-02).

    Regression coverage for two bugs found while wiring g.security_context
    into the real auth flow: (1) this function read user["classification"]
    instead of the actual dashboard_users column user["clearance_level"],
    so every authenticated user always got CUI-tier clearance regardless of
    their real clearance_level; (2) it did `set(user["compartments"])`
    directly on the raw JSON-encoded string dashboard_users.compartments
    stores (e.g. '["COI_FINANCE"]'), which iterates the string's
    characters instead of parsing it as JSON.
    """

    def _app(self):
        from flask import Flask
        return Flask(__name__)

    def test_reads_clearance_level_not_classification_key(self):
        app = self._app()
        with app.test_request_context():
            from flask import g
            g.current_user = {"id": "u1", "role": "admin", "clearance_level": "SECRET", "compartments": "[]"}
            ctx = _extract_from_flask_g()
            assert ctx.clearance_level == 3  # SECRET

    def test_ignores_stale_classification_key_when_clearance_level_present(self):
        """A user dict that (incorrectly) also carries a 'classification' key
        must not let that override the real clearance_level column."""
        app = self._app()
        with app.test_request_context():
            from flask import g
            g.current_user = {
                "id": "u1", "role": "admin",
                "clearance_level": "SECRET", "classification": "CUI",
                "compartments": "[]",
            }
            ctx = _extract_from_flask_g()
            assert ctx.clearance_level == 3  # SECRET, not the stale CUI classification key

    def test_parses_json_encoded_compartments_string(self):
        app = self._app()
        with app.test_request_context():
            from flask import g
            g.current_user = {
                "id": "u1", "role": "admin", "clearance_level": "SECRET",
                "compartments": '["COI_FINANCE", "LAC_DC_EAST"]',
            }
            ctx = _extract_from_flask_g()
            assert ctx.compartments == frozenset({"COI_FINANCE", "LAC_DC_EAST"})

    def test_empty_compartments_string_yields_empty_set(self):
        app = self._app()
        with app.test_request_context():
            from flask import g
            g.current_user = {"id": "u1", "role": "admin", "clearance_level": "CUI", "compartments": "[]"}
            ctx = _extract_from_flask_g()
            assert ctx.compartments == frozenset()

    def test_malformed_compartments_json_degrades_to_empty_set(self):
        app = self._app()
        with app.test_request_context():
            from flask import g
            g.current_user = {"id": "u1", "role": "admin", "clearance_level": "CUI", "compartments": "not json"}
            ctx = _extract_from_flask_g()
            assert ctx.compartments == frozenset()

    def test_already_list_compartments_still_works(self):
        """Backward-compat: some callers may already pass a real list
        (not the raw DB string) -- must not break on isinstance(str) check."""
        app = self._app()
        with app.test_request_context():
            from flask import g
            g.current_user = {"id": "u1", "role": "admin", "clearance_level": "CUI", "compartments": ["COI_FINANCE"]}
            ctx = _extract_from_flask_g()
            assert ctx.compartments == frozenset({"COI_FINANCE"})

    def test_no_current_user_returns_none(self):
        app = self._app()
        with app.test_request_context():
            ctx = _extract_from_flask_g()
            assert ctx is None or ctx.clearance_level == 1  # CUI default, no crash

    def test_attaches_to_flask_g(self):
        app = self._app()
        with app.test_request_context():
            from flask import g
            g.current_user = {"id": "u1", "role": "admin", "clearance_level": "SECRET", "compartments": "[]"}
            _extract_from_flask_g()
            assert isinstance(g.security_context, SecurityContext)
            assert g.security_context.clearance_level == 3
