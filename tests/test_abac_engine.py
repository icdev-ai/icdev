#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.security.abac_engine."""

import time
from tools.security.abac_engine import (
    Decision,
    PIP,
    PDP,
    evaluate,
    reload_policies,
)


class TestDecision:
    def test_permit(self):
        d = Decision(permit=True, policy_name="p1", reason="ok")
        assert d.permit
        assert d.to_dict()["permit"] is True

    def test_deny(self):
        d = Decision(permit=False)
        assert not d.permit


class TestPIP:
    def test_static_value(self):
        assert PIP.resolve(42, {}) == 42
        assert PIP.resolve("foo", {}) == "foo"

    def test_reference(self):
        ctx = {"subject": {"role": "admin"}, "resource": {"id": "r1"}}
        assert PIP.resolve("${subject.role}", ctx) == "admin"
        assert PIP.resolve("${resource.id}", ctx) == "r1"

    def test_missing_reference(self):
        assert PIP.resolve("${subject.missing}", {}) is None


class TestPDP:
    def test_permit_policy(self):
        pdp = PDP(
            policies=[
                {
                    "name": "allow_admin",
                    "subject": {"role": "admin"},
                    "resource": {"type": "project"},
                    "action": ["GET"],
                    "decision": "Permit",
                }
            ]
        )
        d = pdp.evaluate(
            subject={"role": "admin"},
            resource={"type": "project"},
            action="GET",
        )
        assert d.permit
        assert d.policy_name == "allow_admin"

    def test_deny_policy(self):
        pdp = PDP(
            policies=[
                {
                    "name": "deny_viewer_delete",
                    "subject": {"role": "viewer"},
                    "resource": {"type": "project"},
                    "action": ["DELETE"],
                    "decision": "Deny",
                }
            ]
        )
        d = pdp.evaluate(
            subject={"role": "viewer"},
            resource={"type": "project"},
            action="DELETE",
        )
        assert not d.permit

    def test_no_match_defaults_deny(self):
        pdp = PDP(policies=[])
        d = pdp.evaluate(subject={}, resource={}, action="GET")
        assert not d.permit
        assert d.policy_name == "default_deny"

    def test_action_wildcard(self):
        pdp = PDP(
            policies=[
                {
                    "name": "allow_all",
                    "subject": {"role": "superuser"},
                    "resource": {"type": "*"},
                    "action": ["*"],
                    "decision": "Permit",
                }
            ]
        )
        d = pdp.evaluate(
            subject={"role": "superuser"},
            resource={"type": "anything"},
            action="POST",
        )
        assert d.permit

    def test_operator_ge(self):
        pdp = PDP(
            policies=[
                {
                    "name": "min_clearance",
                    "subject": {"clearance_level": {"operator": ">=", "value": 2}},
                    "resource": {"type": "project"},
                    "action": ["GET"],
                    "decision": "Permit",
                }
            ]
        )
        assert pdp.evaluate(
            subject={"clearance_level": 2}, resource={"type": "project"}, action="GET"
        ).permit
        assert not pdp.evaluate(
            subject={"clearance_level": 1}, resource={"type": "project"}, action="GET"
        ).permit

    def test_operator_in(self):
        pdp = PDP(
            policies=[
                {
                    "name": "role_list",
                    "subject": {"role": {"operator": "in", "values": ["admin", "editor"]}},
                    "resource": {"type": "project"},
                    "action": ["GET"],
                    "decision": "Permit",
                }
            ]
        )
        assert pdp.evaluate(
            subject={"role": "editor"}, resource={"type": "project"}, action="GET"
        ).permit
        assert not pdp.evaluate(
            subject={"role": "viewer"}, resource={"type": "project"}, action="GET"
        ).permit

    def test_cache_hit(self):
        pdp = PDP(
            policies=[
                {
                    "name": "cached",
                    "subject": {"role": "admin"},
                    "resource": {"type": "project"},
                    "action": ["GET"],
                    "decision": "Permit",
                }
            ]
        )
        d1 = pdp.evaluate(
            subject={"role": "admin"}, resource={"type": "project"}, action="GET"
        )
        # Second call should hit cache
        d2 = pdp.evaluate(
            subject={"role": "admin"}, resource={"type": "project"}, action="GET"
        )
        assert d1.permit and d2.permit

    def test_cache_ttl_expires(self):
        pdp = PDP(
            policies=[
                {
                    "name": "cached",
                    "subject": {"role": "admin"},
                    "resource": {"type": "project"},
                    "action": ["GET"],
                    "decision": "Permit",
                }
            ]
        )
        pdp._cache_ttl = 0.01  # 10 ms for test
        d1 = pdp.evaluate(
            subject={"role": "admin"}, resource={"type": "project"}, action="GET"
        )
        time.sleep(0.02)
        d2 = pdp.evaluate(
            subject={"role": "admin"}, resource={"type": "project"}, action="GET"
        )
        assert d1.permit and d2.permit  # still permit after re-evaluation

    def test_clear_cache(self):
        pdp = PDP(policies=[])
        pdp.evaluate({}, {}, "GET")
        assert len(pdp._cache) == 1
        pdp.clear_cache()
        assert len(pdp._cache) == 0


class TestPublicAPI:
    def test_evaluate_import(self):
        reload_policies()
        d = evaluate(
            subject={"role": "tenant_admin"},
            resource={"type": "project"},
            action="GET",
        )
        assert isinstance(d, Decision)
