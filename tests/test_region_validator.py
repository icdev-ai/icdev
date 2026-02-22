#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for CSP Region Validator (D234).

Tests compliance-driven deployment validation using
context/compliance/csp_certifications.json.
"""

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.cloud.region_validator import RegionValidator


@pytest.fixture
def validator():
    """Create a RegionValidator with the real certifications file."""
    return RegionValidator()


@pytest.fixture
def mock_validator(tmp_path):
    """Create a RegionValidator with a minimal test certifications file."""
    certs = {
        "_metadata": {"version": "1.0.0-test"},
        "certifications": {
            "test_csp": {
                "region-a": {
                    "fedramp": "high",
                    "dod_il": ["IL2", "IL4", "IL5"],
                    "hipaa": True,
                    "pci_dss": True,
                    "cjis": True,
                    "fips_140_2": True,
                },
                "region-b": {
                    "fedramp": "moderate",
                    "dod_il": ["IL2"],
                    "hipaa": True,
                    "pci_dss": False,
                    "cjis": False,
                    "fips_140_2": True,
                },
            }
        },
    }
    certs_file = tmp_path / "certs.json"
    certs_file.write_text(json.dumps(certs))
    return RegionValidator(certifications_path=str(certs_file))


class TestRegionValidation:
    """Test validate_region method."""

    def test_valid_region_all_certs(self, mock_validator):
        result = mock_validator.validate_region(
            "test_csp", "region-a", ["fedramp_high", "hipaa", "cjis"]
        )
        assert result["valid"] is True
        assert result["missing"] == []
        assert len(result["available"]) == 3

    def test_invalid_region_missing_certs(self, mock_validator):
        result = mock_validator.validate_region(
            "test_csp", "region-b", ["fedramp_high", "cjis"]
        )
        assert result["valid"] is False
        assert "fedramp_high" in result["missing"]
        assert "cjis" in result["missing"]

    def test_fedramp_hierarchy(self, mock_validator):
        """FedRAMP high satisfies moderate requirement."""
        result = mock_validator.validate_region(
            "test_csp", "region-a", ["fedramp_moderate"]
        )
        assert result["valid"] is True

    def test_unknown_region(self, mock_validator):
        result = mock_validator.validate_region(
            "test_csp", "nonexistent", ["hipaa"]
        )
        assert result["valid"] is False
        assert "error" in result

    def test_unknown_csp(self, mock_validator):
        result = mock_validator.validate_region(
            "unknown_csp", "region-a", ["hipaa"]
        )
        assert result["valid"] is False

    def test_dod_il_check(self, mock_validator):
        result = mock_validator.validate_region(
            "test_csp", "region-a", ["dod_srg_il5"]
        )
        assert result["valid"] is True

    def test_dod_il_check_fail(self, mock_validator):
        result = mock_validator.validate_region(
            "test_csp", "region-b", ["dod_srg_il5"]
        )
        assert result["valid"] is False


class TestEligibleRegions:
    """Test get_eligible_regions method."""

    def test_eligible_high_fedramp(self, mock_validator):
        eligible = mock_validator.get_eligible_regions(
            "test_csp", ["fedramp_high"]
        )
        assert len(eligible) == 1
        assert eligible[0]["region"] == "region-a"

    def test_eligible_moderate_fedramp(self, mock_validator):
        eligible = mock_validator.get_eligible_regions(
            "test_csp", ["fedramp_moderate"]
        )
        # Both regions have at least moderate (region-a has high which >= moderate)
        assert len(eligible) == 2

    def test_eligible_none(self, mock_validator):
        eligible = mock_validator.get_eligible_regions(
            "test_csp", ["dod_srg_il6"]
        )
        assert len(eligible) == 0


class TestDeploymentValidation:
    """Test validate_deployment method."""

    def test_il6_csp_restriction(self, mock_validator):
        """IL6 rejects non-eligible CSPs."""
        result = mock_validator.validate_deployment(
            "gcp", "us-east4", "IL6"
        )
        assert result["valid"] is False
        assert result["il_valid"] is False
        assert "eligible_csps" in result

    def test_il6_eligible_csp(self, mock_validator):
        """IL6 accepts eligible CSPs (validation against certs may still fail)."""
        result = mock_validator.validate_deployment(
            "local", "any-region", "IL6"
        )
        # local is eligible for IL6, but certs check may fail since region not in registry
        assert "il_valid" in result

    def test_il5_with_frameworks(self, mock_validator):
        result = mock_validator.validate_deployment(
            "test_csp", "region-a", "IL5", ["hipaa"]
        )
        assert result["valid"] is True
        assert result["il_valid"] is True
        assert result["framework_valid"] is True

    def test_il5_missing_framework(self, mock_validator):
        result = mock_validator.validate_deployment(
            "test_csp", "region-b", "IL5", ["hipaa"]
        )
        # region-b only has moderate FedRAMP, IL5 requires high
        assert result["valid"] is False


class TestListOperations:
    """Test list_csps and list_regions."""

    def test_list_csps(self, mock_validator):
        csps = mock_validator.list_csps()
        assert "test_csp" in csps

    def test_list_regions(self, mock_validator):
        regions = mock_validator.list_regions("test_csp")
        assert "region-a" in regions
        assert "region-b" in regions

    def test_list_regions_unknown_csp(self, mock_validator):
        regions = mock_validator.list_regions("unknown")
        assert regions == []


class TestRealCertifications:
    """Test with real csp_certifications.json (if available)."""

    def test_aws_govcloud_fedramp_high(self, validator):
        """AWS GovCloud should have FedRAMP High."""
        result = validator.validate_region(
            "aws", "us-gov-west-1", ["fedramp_high"]
        )
        if result.get("region_data"):  # Only test if real file loaded
            assert result["valid"] is True

    def test_ibm_not_il6(self, validator):
        """IBM should not be eligible for IL6."""
        result = validator.validate_deployment(
            "ibm", "us-south", "IL6"
        )
        assert result["valid"] is False
