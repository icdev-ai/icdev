# [TEMPLATE: CUI // SP-CTI]
"""
Unit tests for ICDEV™ IC IE Multi-Agency Data Sharing via Data Fabric.
GREEN phase — implementation in place; all tests must pass.

NIST 800-53 Controls: AC-3, AC-4, AU-2, AU-3, AU-12, SC-28, SI-12
"""

import pytest

# Each test uses an in-memory SQLite DB (':memory:') to guarantee isolation
# between runs and avoid UNIQUE constraint collisions on persistent data files.
_MEM = {"backend": "sqlite", "db_path": ":memory:"}


class TestDataFabricService:
    """Tests for DataFabricService core operations."""

    def test_register_asset_returns_dict_with_id(self):
        """Test that registering an asset returns a dict containing a fabric-generated id."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        asset = fabric.register_asset(
            classification="SECRET",
            owner_agency="NSA",
            metadata={"description": "Unit test asset"},
        )

        assert isinstance(asset, dict), "register_asset must return a dict"
        assert "id" in asset, "Returned asset must have 'id' field"
        assert asset["id"].startswith("fabric-"), "Asset id must start with 'fabric-'"

    def test_register_asset_preserves_classification(self):
        """Test that the registered asset stores the correct classification label."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        asset = fabric.register_asset(
            classification="TS//SI",
            owner_agency="CIA",
            metadata={},
        )

        assert asset["classification"] == "TS//SI"

    def test_register_asset_with_explicit_id(self):
        """Test that supplying an explicit asset_id is honored."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        asset = fabric.register_asset(
            asset_id="asset-explicit-01",
            classification="UNCLASSIFIED",
            owner_agency="DHS",
            metadata={},
        )

        assert asset["id"] == "asset-explicit-01"

    def test_get_asset_retrieves_registered_asset(self):
        """Test that get_asset returns the asset stored via register_asset."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        registered = fabric.register_asset(
            classification="SECRET",
            owner_agency="FBI",
            metadata={"purpose": "counterterrorism"},
        )

        retrieved = fabric.get_asset(registered["id"])
        assert retrieved is not None
        assert retrieved["id"] == registered["id"]
        assert retrieved["owner_agency"] == "FBI"

    def test_get_asset_returns_none_for_unknown_id(self):
        """Test that get_asset returns None when the id does not exist."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        result = fabric.get_asset("nonexistent-id")
        assert result is None


class TestDataFabricSharing:
    """Tests for data sharing operations between agencies."""

    def test_share_asset_returns_active_status(self):
        """Test that sharing an asset with an authorized partner returns active status."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="share-test-01",
            classification="SECRET//REL TO USA, GBR",
            owner_agency="NSA",
            metadata={},
        )

        result = fabric.share_asset(
            asset_id="share-test-01",
            target_agency="GCHQ",
            justification="Five Eyes SIGINT sharing",
        )

        assert result["status"] == "active"
        assert result["asset_id"] == "share-test-01"
        assert result["target_agency"] == "GCHQ"

    def test_share_noforn_asset_with_foreign_partner_is_denied(self):
        """Test that NOFORN assets cannot be shared with foreign partners."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="noforn-asset-01",
            classification="TS//SI//NOFORN",
            owner_agency="NSA",
            metadata={},
        )

        result = fabric.share_asset(
            asset_id="noforn-asset-01",
            target_agency="FVEY_UK",
            justification="Attempted share with foreign partner",
        )

        assert result["status"] == "denied"
        assert "NOFORN" in result.get("denial_reason", "")

    def test_share_asset_creates_audit_entry(self):
        """Test that a successful share creates an audit trail entry."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="audit-test-asset-01",
            classification="SECRET",
            owner_agency="CIA",
            metadata={},
        )
        fabric.share_asset(
            asset_id="audit-test-asset-01",
            target_agency="DIA",
            justification="HUMINT correlation",
        )

        audit_entries = fabric.get_audit_entries(event_type="share_created")
        assert len(audit_entries) >= 1

    def test_denied_share_creates_audit_entry(self):
        """Test that a denied share attempt is logged in the audit trail."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="denied-audit-01",
            classification="TS//SI//NOFORN",
            owner_agency="NSA",
            metadata={},
        )
        fabric.share_asset(
            asset_id="denied-audit-01",
            target_agency="FVEY_AU",
            justification="Test denial audit",
        )

        audit_entries = fabric.get_audit_entries(event_type="share_denied")
        assert len(audit_entries) >= 1

    def test_get_asset_for_agency_returns_asset_when_shared(self):
        """Test that target agency can retrieve an asset after a successful share."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="access-test-01",
            classification="SECRET",
            owner_agency="NSA",
            metadata={},
        )
        fabric.share_asset(
            asset_id="access-test-01",
            target_agency="DIA",
            justification="Joint mission support",
        )

        asset = fabric.get_asset_for_agency(asset_id="access-test-01", agency="DIA")
        assert asset is not None
        assert asset["id"] == "access-test-01"

    def test_get_asset_for_agency_returns_none_for_unauthorized_agency(self):
        """Test that an agency not on the share list cannot retrieve the asset."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="access-test-02",
            classification="SECRET",
            owner_agency="NSA",
            metadata={},
        )

        asset = fabric.get_asset_for_agency(asset_id="access-test-02", agency="DHS")
        assert asset is None


class TestDataFabricQuery:
    """Tests for querying the data fabric catalog."""

    def test_query_shared_assets_returns_only_authorized_assets(self):
        """Test that query results are scoped to the requesting agency."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="query-test-01",
            classification="SECRET",
            owner_agency="CIA",
            metadata={},
        )
        fabric.register_asset(
            asset_id="query-test-02",
            classification="SECRET",
            owner_agency="NSA",
            metadata={},
        )
        # Share only asset-01 with DIA
        fabric.share_asset(
            asset_id="query-test-01",
            target_agency="DIA",
            justification="Sharing only 01 with DIA",
        )

        results = fabric.query_shared_assets(agency="DIA")
        result_ids = [a["id"] for a in results]

        assert "query-test-01" in result_ids
        assert "query-test-02" not in result_ids

    def test_query_result_assets_include_classification(self):
        """Test that all queried assets carry a classification label."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="query-cls-01",
            classification="TS//SI",
            owner_agency="NSA",
            metadata={},
        )
        fabric.share_asset(
            asset_id="query-cls-01",
            target_agency="CIA",
            justification="Classification label test",
        )

        results = fabric.query_shared_assets(agency="CIA")
        for asset in results:
            assert "classification" in asset
            assert asset["classification"]


class TestDataFabricRevocation:
    """Tests for share revocation operations."""

    def test_revoke_share_returns_revoked_status(self):
        """Test that revoking an active share returns revoked status."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="revoke-test-01",
            classification="SECRET",
            owner_agency="CIA",
            metadata={},
        )
        fabric.share_asset(
            asset_id="revoke-test-01",
            target_agency="FBI",
            justification="Initial share",
        )

        result = fabric.revoke_share(asset_id="revoke-test-01", target_agency="FBI")
        assert result["status"] == "revoked"

    def test_revoked_agency_cannot_access_asset(self):
        """Test that after revocation the target agency loses access."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="revoke-access-01",
            classification="SECRET",
            owner_agency="CIA",
            metadata={},
        )
        fabric.share_asset(
            asset_id="revoke-access-01",
            target_agency="FBI",
            justification="Then revoke",
        )
        fabric.revoke_share(asset_id="revoke-access-01", target_agency="FBI")

        asset = fabric.get_asset_for_agency(asset_id="revoke-access-01", agency="FBI")
        assert asset is None

    def test_revocation_creates_audit_entry(self):
        """Test that revoking a share creates a share_revoked audit entry."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="revoke-audit-01",
            classification="SECRET",
            owner_agency="NSA",
            metadata={},
        )
        fabric.share_asset(
            asset_id="revoke-audit-01",
            target_agency="DIA",
            justification="Setup share",
        )
        fabric.revoke_share(asset_id="revoke-audit-01", target_agency="DIA")

        audit_entries = fabric.get_audit_entries(event_type="share_revoked")
        assert len(audit_entries) >= 1

    def test_revoke_nonexistent_share_raises_value_error(self):
        """Test that attempting to revoke a share that doesn't exist raises ValueError."""
        from icdev.tools.ic_ie.data_fabric import DataFabricService

        fabric = DataFabricService(**_MEM)
        fabric.register_asset(
            asset_id="revoke-noexist-01",
            classification="UNCLASSIFIED",
            owner_agency="DHS",
            metadata={},
        )

        with pytest.raises(ValueError, match="No active share found"):
            fabric.revoke_share(asset_id="revoke-noexist-01", target_agency="CBP")
