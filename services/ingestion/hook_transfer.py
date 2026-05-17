# CUI // SP-CTI
"""Cross-agency transfer logging hook for the event ingestion service.

Intercepts all cross-agency data transfer requests, validates them against
configured rules, and writes an append-only audit record before the request
is allowed to proceed.

NIST 800-53: AU-2 (Audit Events), AU-9 (Protection of Audit Information).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from icdev.tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config" / "rules.yaml"


class TransferHookError(Exception):
    """Raised when the transfer hook encounters an unrecoverable error."""


class TransferValidationError(Exception):
    """Raised when a transfer request fails rule validation."""


def _load_rules() -> dict[str, Any]:
    """Load validation rules from the adjacent YAML config file."""
    if not _CONFIG_PATH.exists():
        log.warning("rules.yaml not found at %s — using defaults", _CONFIG_PATH)
        return {
            "validation": {"required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"], "allowed_agency_pairs": None},
            "classifications": {"default": "CUI", "supported": ["CUI", "SECRET", "TOP_SECRET"]},
        }
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _validate_request(request: dict[str, Any], rules: dict[str, Any]) -> None:
    """Validate a transfer request against configured rules.

    Raises TransferValidationError on the first violation.
    """
    required = rules.get("validation", {}).get("required_fields", [])
    missing = [f for f in required if f not in request or request[f] is None or request[f] == ""]
    if missing:
        raise TransferValidationError(f"Missing required fields: {missing}")

    classification = request.get("data_classification", rules.get("classifications", {}).get("default", "CUI"))
    supported = rules.get("classifications", {}).get("supported", ["CUI"])
    if classification not in supported:
        raise TransferValidationError(f"Unsupported classification: {classification}")

    allowed_pairs = rules.get("validation", {}).get("allowed_agency_pairs")
    if allowed_pairs is not None:
        pair = [request["source_agency"], request["target_agency"]]
        if pair not in allowed_pairs:
            raise TransferValidationError(
                f"Agency pair not allowed: {request['source_agency']} -> {request['target_agency']}"
            )


def intercept_transfer_request(request: dict[str, Any]) -> dict[str, Any]:
    """Intercept a cross-agency transfer request, validate it, log it, and return a verdict.

    Args:
        request: Transfer request dict. Must contain at least ``transfer_id``,
            ``source_agency``, ``target_agency``, ``actor``, and ``data_type``.

    Returns:
        Verdict dict with keys:
        - ``allowed`` (bool): whether the request may proceed.
        - ``event_id`` (str | None): audit event UUID when logged, else ``""``.
        - ``transfer_id`` (str): echoed from the request.
        - ``reason`` (str | None): human-readable explanation when rejected.
    """
    logger = CrossAgencyTransferLogger()
    rules = _load_rules()
    transfer_id = request.get("transfer_id", "")

    try:
        _validate_request(request, rules)
    except TransferValidationError as exc:
        reason = str(exc)
        log.warning("[transfer-hook] rejecting %s: %s", transfer_id, reason)
        event_id = logger.log_failed(
            transfer_id=transfer_id,
            source_agency=request.get("source_agency", ""),
            target_agency=request.get("target_agency", ""),
            actor=request.get("actor", "system"),
            rejection_reason=reason,
            error_code="VALIDATION_ERROR",
            data_classification=request.get("data_classification", rules.get("classifications", {}).get("default", "CUI")),
        )
        return {
            "allowed": False,
            "event_id": event_id,
            "transfer_id": transfer_id,
            "reason": reason,
        }

    classification = request.get("data_classification", rules.get("classifications", {}).get("default", "CUI"))

    # LOG BEFORE PROCEEDING — AU-2 compliance requires the audit record to exist
    # before the transfer operation continues.
    event_id = logger.log_initiated(
        transfer_id=transfer_id,
        source_agency=request["source_agency"],
        target_agency=request["target_agency"],
        data_type=request.get("data_type", ""),
        actor=request["actor"],
        data_classification=classification,
        project_id=request.get("project_id"),
        details=request.get("details"),
    )

    log.info(
        "[transfer-hook] allowed %s (%s -> %s) event_id=%s",
        transfer_id,
        request["source_agency"],
        request["target_agency"],
        event_id,
    )

    return {
        "allowed": True,
        "event_id": event_id,
        "transfer_id": transfer_id,
        "reason": None,
    }


def run_transfer(
    request: dict[str, Any],
    transfer_fn: Any | None = None,
) -> dict[str, Any]:
    """Run the full cross-agency transfer pipeline.

    Steps:
        1. Validate and log ``initiated`` via ``intercept_transfer_request``.
        2. Execute the actual transfer (or a no-op simulation).
        3. Log ``completed`` **before** returning the result.
        4. If the transfer raises, log ``failed`` and surface the error.

    Args:
        request: Transfer request dict (same shape as
            ``intercept_transfer_request``).
        transfer_fn: Optional callable that performs the real transfer.
            It receives the *request* dict and should return a dict with
            keys such as ``bytes_transferred``, ``checksum``, and
            ``duration_ms``.  If omitted, a no-op simulation is used.

    Returns:
        Result dict with keys:
        - ``success`` (bool)
        - ``transfer_id`` (str)
        - ``event_id`` (str | None): audit event UUID for the *completion*
          or *failure* event.
        - ``initiated_event_id`` (str | None): audit event UUID for the
          *initiated* event.
        - ``error`` (str | None): human-readable error when transfer fails.
    """
    # 1. Validate + log initiated
    verdict = intercept_transfer_request(request)
    transfer_id = verdict["transfer_id"]
    initiated_event_id = verdict["event_id"]

    if not verdict["allowed"]:
        return {
            "success": False,
            "transfer_id": transfer_id,
            "event_id": initiated_event_id,
            "initiated_event_id": initiated_event_id,
            "error": verdict["reason"],
        }

    classification = request.get(
        "data_classification",
        _load_rules().get("classifications", {}).get("default", "CUI"),
    )
    actor = request.get("actor", "system")
    source_agency = request["source_agency"]
    target_agency = request["target_agency"]

    # 2. Execute transfer
    try:
        if transfer_fn is not None:
            result = transfer_fn(request)
        else:
            # No-op simulation — still yields minimal metrics
            result = {}
    except Exception as exc:
        log.exception("[transfer-hook] transfer execution failed for %s", transfer_id)
        event_id = fail_transfer(
            transfer_id=transfer_id,
            source_agency=source_agency,
            target_agency=target_agency,
            actor=actor,
            rejection_reason=str(exc),
            error_code="TRANSFER_EXEC_ERROR",
            data_classification=classification,
        )
        return {
            "success": False,
            "transfer_id": transfer_id,
            "event_id": event_id,
            "initiated_event_id": initiated_event_id,
            "error": str(exc),
        }

    # 3. Log completed *before* returning — AU-2 compliance
    event_id = complete_transfer(
        transfer_id=transfer_id,
        source_agency=source_agency,
        target_agency=target_agency,
        actor=actor,
        bytes_transferred=result.get("bytes_transferred"),
        checksum=result.get("checksum"),
        duration_ms=result.get("duration_ms"),
        data_classification=classification,
    )

    log.info("[transfer-hook] completed %s event_id=%s", transfer_id, event_id)

    return {
        "success": True,
        "transfer_id": transfer_id,
        "event_id": event_id,
        "initiated_event_id": initiated_event_id,
        "error": None,
    }

def complete_transfer(
    transfer_id: str,
    source_agency: str,
    target_agency: str,
    actor: str,
    *,
    bytes_transferred: int | None = None,
    checksum: str | None = None,
    duration_ms: int | None = None,
    data_classification: str = "CUI",
) -> str:
    """Log the successful completion of a cross-agency transfer.

    Returns:
        Audit event UUID (or empty string on graceful failure).
    """
    logger = CrossAgencyTransferLogger()
    return logger.log_completed(
        transfer_id=transfer_id,
        source_agency=source_agency,
        target_agency=target_agency,
        actor=actor,
        bytes_transferred=bytes_transferred,
        checksum=checksum,
        duration_ms=duration_ms,
        data_classification=data_classification,
    )


def fail_transfer(
    transfer_id: str,
    source_agency: str,
    target_agency: str,
    actor: str,
    *,
    rejection_reason: str | None = None,
    error_code: str | None = None,
    data_classification: str = "CUI",
) -> str:
    """Log the failure of a cross-agency transfer.

    Returns:
        Audit event UUID (or empty string on graceful failure).
    """
    logger = CrossAgencyTransferLogger()
    return logger.log_failed(
        transfer_id=transfer_id,
        source_agency=source_agency,
        target_agency=target_agency,
        actor=actor,
        rejection_reason=rejection_reason,
        error_code=error_code,
        data_classification=data_classification,
    )
