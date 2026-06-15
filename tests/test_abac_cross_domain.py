#!/usr/bin/env python3
# CUI // SP-CTI
"""RED-phase TDD tests for ABAC cross-domain data pull with mandatory classification markings.

Story: Given a cleared analyst at a partner agency, when they request a cross-domain data pull,
the MCIP system must enforce ABAC with mandatory classification markings on all returned data objects.

Acceptance criteria:
  - The system enforces ABAC per the accreditation boundary.
  - All returned data objects carry mandatory classification markings.
  - Unauthorized cross-domain pulls are denied and logged.

NIST 800-53 controls: AC-3, AC-4, AC-4(4), AU-2, SC-7(5).
"""

import pytest
from tools.security.abac_engine import (
    CrossDomainPullResult,
    CrossDomainABACEnforcer,
    enforce_cross_domain_pull,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cleared_analyst():
    """A cleared analyst at a partner agency with IL4 clearance and cross-domain pull entitlement."""
    return {
        "user_id": "analyst-001",
        "role": "cleared_analyst",
        "agency": "partner_agency",
        "clearance_level": 3,  # SECRET (classification_manager scale: PUBLIC=0,CUI=1,ECI=2,SECRET=3)
        "classification": "SECRET",
        "compartments": ["COI_INTEL"],
        "entitlements": ["cross_domain_pull"],
        "impact_level": "IL4",
    }


@pytest.fixture
def uncleared_user():
    """A user without cross-domain pull entitlement."""
    return {
        "user_id": "user-002",
        "role": "viewer",
        "agency": "internal",
        "clearance_level": 1,  # CUI only
        "classification": "CUI",
        "compartments": [],
        "entitlements": [],
        "impact_level": "IL2",
    }


@pytest.fixture
def cross_domain_resource():
    """A cross-domain data resource at IL4."""
    return {
        "type": "cross_domain_data",
        "source_il": "IL4",
        "target_il": "IL2",
        "classification": "SECRET",
        "data_owner": "MCIP",
        "compartments": ["COI_INTEL"],
    }


@pytest.fixture
def sample_data_objects():
    """Raw data objects returned from a cross-domain source (no markings yet)."""
    return [
        {"id": "rec-001", "content": "Operational briefing A", "sensitivity": "high"},
        {"id": "rec-002", "content": "Mission summary B", "sensitivity": "medium"},
        {"id": "rec-003", "content": "Logistics report C", "sensitivity": "low"},
    ]


# ---------------------------------------------------------------------------
# CrossDomainPullResult tests
# ---------------------------------------------------------------------------

class TestCrossDomainPullResult:
    def test_denied_result_has_no_data(self):
        """A denied cross-domain pull must not expose any data objects."""
        result = CrossDomainPullResult(
            permitted=False,
            policy_name="deny_cross_domain",
            reason="Insufficient clearance",
            data_objects=[],
        )
        assert not result.permitted
        assert result.data_objects == []

    def test_permitted_result_carries_data(self):
        """A permitted cross-domain pull result carries data objects."""
        result = CrossDomainPullResult(
            permitted=True,
            policy_name="allow_cross_domain",
            reason="ABAC permit",
            data_objects=[{"id": "rec-001", "classification_marking": "SECRET"}],
        )
        assert result.permitted
        assert len(result.data_objects) == 1

    def test_to_dict_includes_permitted_and_data(self):
        """to_dict() must include permitted, policy_name, reason, and data_objects."""
        result = CrossDomainPullResult(
            permitted=True,
            policy_name="p1",
            reason="ok",
            data_objects=[{"id": "x", "classification_marking": "CUI"}],
        )
        d = result.to_dict()
        assert "permitted" in d
        assert "data_objects" in d
        assert "policy_name" in d
        assert "reason" in d

    def test_denied_result_to_dict_has_empty_data(self):
        """A denied result's to_dict() must have an empty data_objects list."""
        result = CrossDomainPullResult(
            permitted=False,
            policy_name="deny",
            reason="denied",
            data_objects=[],
        )
        d = result.to_dict()
        assert d["data_objects"] == []


# ---------------------------------------------------------------------------
# Classification marking enforcement tests
# ---------------------------------------------------------------------------

class TestClassificationMarkings:
    def test_all_objects_receive_classification_marking(self, sample_data_objects):
        """Every returned data object must have a 'classification_marking' field."""
        enforcer = CrossDomainABACEnforcer()
        marked = enforcer.apply_classification_markings(
            data_objects=sample_data_objects,
            source_classification="SECRET",
        )
        for obj in marked:
            assert "classification_marking" in obj, (
                f"Object {obj.get('id')} missing classification_marking"
            )

    def test_classification_marking_value_matches_source(self, sample_data_objects):
        """The classification_marking value must match the source classification."""
        enforcer = CrossDomainABACEnforcer()
        marked = enforcer.apply_classification_markings(
            data_objects=sample_data_objects,
            source_classification="SECRET",
        )
        for obj in marked:
            assert obj["classification_marking"] == "SECRET"

    def test_cui_classification_marking_applied(self, sample_data_objects):
        """CUI classification markings must be applied when source is CUI."""
        enforcer = CrossDomainABACEnforcer()
        marked = enforcer.apply_classification_markings(
            data_objects=sample_data_objects,
            source_classification="CUI",
        )
        for obj in marked:
            assert obj["classification_marking"] == "CUI"

    def test_original_data_fields_preserved(self, sample_data_objects):
        """Applying markings must not remove or alter any original data fields."""
        enforcer = CrossDomainABACEnforcer()
        marked = enforcer.apply_classification_markings(
            data_objects=sample_data_objects,
            source_classification="CUI",
        )
        for orig, result in zip(sample_data_objects, marked):
            for key, value in orig.items():
                assert result[key] == value, f"Field '{key}' altered on object {orig.get('id')}"

    def test_empty_objects_list_returns_empty(self):
        """An empty data objects list must return an empty marked list."""
        enforcer = CrossDomainABACEnforcer()
        marked = enforcer.apply_classification_markings([], "SECRET")
        assert marked == []

    def test_cui_sp_cti_subcategory_marking(self, sample_data_objects):
        """CUI // SP-CTI subcategory must be accepted as a valid classification marking."""
        enforcer = CrossDomainABACEnforcer()
        marked = enforcer.apply_classification_markings(
            data_objects=sample_data_objects,
            source_classification="CUI // SP-CTI",
        )
        for obj in marked:
            assert obj["classification_marking"] == "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# ABAC enforcement for cross-domain pull tests
# ---------------------------------------------------------------------------

class TestCrossDomainABACEnforcer:
    def test_cleared_analyst_permitted_for_cross_domain_pull(
        self, cleared_analyst, cross_domain_resource, sample_data_objects
    ):
        """A cleared analyst with cross_domain_pull entitlement must be permitted."""
        enforcer = CrossDomainABACEnforcer()
        result = enforcer.enforce(
            subject=cleared_analyst,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
            action="cross_domain_pull",
        )
        assert result.permitted, f"Expected PERMIT but got DENY: {result.reason}"

    def test_uncleared_user_denied_cross_domain_pull(
        self, uncleared_user, cross_domain_resource, sample_data_objects
    ):
        """A user without cross_domain_pull entitlement must be denied."""
        enforcer = CrossDomainABACEnforcer()
        result = enforcer.enforce(
            subject=uncleared_user,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
            action="cross_domain_pull",
        )
        assert not result.permitted, "Expected DENY for uncleared user"

    def test_denied_pull_returns_no_data_objects(
        self, uncleared_user, cross_domain_resource, sample_data_objects
    ):
        """A denied cross-domain pull must not leak any data objects."""
        enforcer = CrossDomainABACEnforcer()
        result = enforcer.enforce(
            subject=uncleared_user,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
            action="cross_domain_pull",
        )
        assert result.data_objects == [], "Denied pull must not expose data objects"

    def test_permitted_pull_returns_marked_data_objects(
        self, cleared_analyst, cross_domain_resource, sample_data_objects
    ):
        """A permitted cross-domain pull must return data objects with classification markings."""
        enforcer = CrossDomainABACEnforcer()
        result = enforcer.enforce(
            subject=cleared_analyst,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
            action="cross_domain_pull",
        )
        assert result.permitted
        assert len(result.data_objects) == len(sample_data_objects)
        for obj in result.data_objects:
            assert "classification_marking" in obj, (
                f"Object {obj.get('id')} missing mandatory classification_marking"
            )

    def test_insufficient_clearance_denied_even_with_entitlement(
        self, uncleared_user, cross_domain_resource, sample_data_objects
    ):
        """A user with entitlement but insufficient clearance must still be denied."""
        user = dict(uncleared_user)
        user["entitlements"] = ["cross_domain_pull"]  # grant entitlement but clearance is CUI=1, resource needs SECRET=3
        enforcer = CrossDomainABACEnforcer()
        result = enforcer.enforce(
            subject=user,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
            action="cross_domain_pull",
        )
        assert not result.permitted, "CUI-cleared user must not access SECRET cross-domain data"

    def test_missing_compartment_denied(self, cleared_analyst, sample_data_objects):
        """A user missing required compartments must be denied even with correct clearance."""
        resource = {
            "type": "cross_domain_data",
            "source_il": "IL4",
            "target_il": "IL2",
            "classification": "SECRET",
            "data_owner": "MCIP",
            "compartments": ["COI_INTEL", "COI_SIGINT"],  # user only has COI_INTEL
        }
        user = dict(cleared_analyst)
        user["compartments"] = ["COI_INTEL"]  # missing COI_SIGINT

        enforcer = CrossDomainABACEnforcer()
        result = enforcer.enforce(
            subject=user,
            resource=resource,
            data_objects=sample_data_objects,
            action="cross_domain_pull",
        )
        assert not result.permitted, "User missing required compartment must be denied"

    def test_classification_marking_sourced_from_resource(
        self, cleared_analyst, sample_data_objects
    ):
        """Classification markings applied to data objects must match the resource classification."""
        resource = {
            "type": "cross_domain_data",
            "source_il": "IL4",
            "target_il": "IL2",
            "classification": "SECRET",
            "data_owner": "MCIP",
            "compartments": ["COI_INTEL"],
        }
        enforcer = CrossDomainABACEnforcer()
        result = enforcer.enforce(
            subject=cleared_analyst,
            resource=resource,
            data_objects=sample_data_objects,
            action="cross_domain_pull",
        )
        assert result.permitted
        for obj in result.data_objects:
            assert obj["classification_marking"] == "SECRET"


# ---------------------------------------------------------------------------
# Public API convenience function
# ---------------------------------------------------------------------------

class TestPublicAPICrossDomain:
    def test_enforce_cross_domain_pull_permitted(self, cleared_analyst, cross_domain_resource, sample_data_objects):
        """Public API enforce_cross_domain_pull returns a CrossDomainPullResult."""
        result = enforce_cross_domain_pull(
            subject=cleared_analyst,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
        )
        assert isinstance(result, CrossDomainPullResult)
        assert result.permitted

    def test_enforce_cross_domain_pull_denied(self, uncleared_user, cross_domain_resource, sample_data_objects):
        """Public API enforce_cross_domain_pull denies unauthorized users."""
        result = enforce_cross_domain_pull(
            subject=uncleared_user,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
        )
        assert isinstance(result, CrossDomainPullResult)
        assert not result.permitted

    def test_enforce_cross_domain_pull_no_data_leak_on_deny(
        self, uncleared_user, cross_domain_resource, sample_data_objects
    ):
        """Public API denied result must not include any data objects."""
        result = enforce_cross_domain_pull(
            subject=uncleared_user,
            resource=cross_domain_resource,
            data_objects=sample_data_objects,
        )
        assert result.data_objects == []
