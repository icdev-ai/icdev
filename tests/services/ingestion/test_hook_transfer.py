# CUI // SP-CTI
"""Unit tests for services.ingestion.hook_transfer.

Acceptance criterion:
- Any simulated cross-agency transfer request is automatically logged with the
  correct metadata before proceeding.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ingestion.hook_transfer import (  # noqa: E402
    TransferValidationError,
    _validate_request,
    complete_transfer,
    fail_transfer,
    intercept_transfer_request,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def valid_request():
    """A fully valid cross-agency transfer request."""
    return {
        "transfer_id": "xfer-001",
        "source_agency": "DoD",
        "target_agency": "DHS",
        "actor": "alice",
        "data_type": "intelligence",
        "data_classification": "CUI",
        "project_id": "proj-42",
        "details": {"priority": "high"},
    }


# ---------------------------------------------------------------------------
# Validation helper tests
# ---------------------------------------------------------------------------


class TestValidateRequest:
    def test_passes_with_valid_request(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI", "SECRET"]},
        }
        _validate_request(valid_request, rules)  # should not raise

    def test_missing_required_field_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI"]},
        }
        del valid_request["actor"]
        with pytest.raises(TransferValidationError, match="Missing required fields"):
            _validate_request(valid_request, rules)

    def test_unsupported_classification_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI"]},
        }
        valid_request["data_classification"] = "SECRET"
        with pytest.raises(TransferValidationError, match="Unsupported classification"):
            _validate_request(valid_request, rules)

    def test_restricted_agency_pair_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": [["DoD", "CIA"]],
            },
            "classifications": {"default": "CUI", "supported": ["CUI", "SECRET", "TOP_SECRET"]},
        }
        with pytest.raises(TransferValidationError, match="Agency pair not allowed"):
            _validate_request(valid_request, rules)

    def test_empty_string_required_field_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI"]},
        }
        valid_request["actor"] = ""
        with pytest.raises(TransferValidationError, match="Missing required fields"):
            _validate_request(valid_request, rules)


# ---------------------------------------------------------------------------
# Hook interception tests — acceptance criterion
# ---------------------------------------------------------------------------


class TestInterceptTransferRequest:
    """Verify that every simulated transfer request is logged with correct
    metadata before the function returns the proceed verdict."""

    def test_valid_request_logged_before_proceeding(self, valid_request):
        """log_initiated is called with correct metadata; result contains the
        same event_id, proving logging happened before proceeding."""
        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-uuid-123"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is True
        assert result["event_id"] == "evt-uuid-123"
        assert result["transfer_id"] == "xfer-001"
        assert result["reason"] is None

        mock_instance.log_initiated.assert_called_once_with(
            transfer_id="xfer-001",
            source_agency="DoD",
            target_agency="DHS",
            data_type="intelligence",
            actor="alice",
            data_classification="CUI",
            project_id="proj-42",
            details={"priority": "high"},
        )
        mock_instance.log_failed.assert_not_called()

    def test_missing_field_rejected_and_logged_as_failed(self, valid_request):
        """A request missing a required field is rejected AND logged as failed
        with a VALIDATION_ERROR code."""
        del valid_request["actor"]

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_failed.return_value = "evt-uuid-456"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is False
        assert result["event_id"] == "evt-uuid-456"
        assert result["transfer_id"] == "xfer-001"
        assert "Missing required fields" in result["reason"]

        mock_instance.log_failed.assert_called_once()
        call_kwargs = mock_instance.log_failed.call_args.kwargs
        assert call_kwargs["transfer_id"] == "xfer-001"
        assert call_kwargs["source_agency"] == "DoD"
        assert call_kwargs["target_agency"] == "DHS"
        assert call_kwargs["actor"] == "system"  # fallback when actor missing
        assert call_kwargs["error_code"] == "VALIDATION_ERROR"
        assert "Missing required fields" in call_kwargs["rejection_reason"]
        mock_instance.log_initiated.assert_not_called()

    def test_unsupported_classification_rejected_and_logged(self, valid_request):
        """Unsupported classification triggers rejection and a failed audit log."""
        valid_request["data_classification"] = "INVALID"

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_failed.return_value = "evt-uuid-789"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is False
        assert "Unsupported classification" in result["reason"]
        mock_instance.log_failed.assert_called_once()
        mock_instance.log_initiated.assert_not_called()

    def test_default_classification_used_when_not_provided(self, valid_request):
        """If data_classification is omitted, the default from rules.yaml is used."""
        del valid_request["data_classification"]

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-uuid-abc"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is True
        call_kwargs = mock_instance.log_initiated.call_args.kwargs
        assert call_kwargs["data_classification"] == "CUI"

    def test_rejected_when_agency_pair_restricted(self, valid_request):
        """When rules.yaml restricts pairs, an invalid pair is rejected."""
        with patch("services.ingestion.hook_transfer._load_rules") as mock_load:
            mock_load.return_value = {
                "validation": {
                    "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                    "allowed_agency_pairs": [["DoD", "CIA"]],
                },
                "classifications": {"default": "CUI", "supported": ["CUI", "SECRET", "TOP_SECRET"]},
            }

            with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
                mock_instance = MockLogger.return_value
                mock_instance.log_failed.return_value = "evt-uuid-def"

                result = intercept_transfer_request(valid_request)

        assert result["allowed"] is False
        assert "Agency pair not allowed" in result["reason"]
        mock_instance.log_failed.assert_called_once()
        mock_instance.log_initiated.assert_not_called()

    def test_empty_details_is_ok(self, valid_request):
        """A request with no details field is still valid and logged."""
        del valid_request["details"]

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-uuid-ghi"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is True
        call_kwargs = mock_instance.log_initiated.call_args.kwargs
        assert call_kwargs.get("details") is None


# ---------------------------------------------------------------------------
# Completion / failure helpers
# ---------------------------------------------------------------------------


class TestCompleteTransfer:
    def test_logs_completed_event(self):
        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_completed.return_value = "evt-uuid-comp"

            eid = complete_transfer(
                transfer_id="xfer-002",
                source_agency="NSA",
                target_agency="CIA",
                actor="bob",
                bytes_transferred=1024,
                checksum="sha256:abc123",
                duration_ms=150,
                data_classification="SECRET",
            )

        assert eid == "evt-uuid-comp"
        mock_instance.log_completed.assert_called_once_with(
            transfer_id="xfer-002",
            source_agency="NSA",
            target_agency="CIA",
            actor="bob",
            bytes_transferred=1024,
            checksum="sha256:abc123",
            duration_ms=150,
            data_classification="SECRET",
        )


class TestFailTransfer:
    def test_logs_failed_event(self):
        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_failed.return_value = "evt-uuid-fail"

            eid = fail_transfer(
                transfer_id="xfer-003",
                source_agency="FBI",
                target_agency="DEA",
                actor="charlie",
                rejection_reason="auth failure",
                error_code="AUTH_401",
                data_classification="CUI",
            )

        assert eid == "evt-uuid-fail"
        mock_instance.log_failed.assert_called_once_with(
            transfer_id="xfer-003",
            source_agency="FBI",
            target_agency="DEA",
            actor="charlie",
            rejection_reason="auth failure",
            error_code="AUTH_401",
            data_classification="CUI",
        )
