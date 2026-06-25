# [CUI // SP-CTI]
"""ICDEV™ NDC — Advisory ingestion and NQE-backed impact assessment.

Public API:
- ``ingest_advisory(cve_id, affected_models, affected_versions, advisory_text, ...) -> int``
- ``run_impact_assessment(advisory_id, network_id) -> dict``
- ``get_nqe_client() -> NQEClient | FallbackNQEClient``
- ``_fwd_reachable() -> bool``
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# FWD reachability cache
# ---------------------------------------------------------------------------

_FWD_API_URL: str = os.environ.get("FWD_API_URL", "").rstrip("/")
_FWD_API_KEY: str = os.environ.get("FWD_API_KEY", "")
_FWD_REACH_TTL: int = 60  # seconds

# Module-level cache: { "reachable": bool, "ts": float }
_fwd_reach_cache: dict[str, Any] = {}


def _fwd_reachable() -> bool:
    """Return True if the FWD API health endpoint responds within 2 s.

    Result is cached for 60 seconds so rapid calls don't flood the endpoint.
    """
    now = time.monotonic()
    cached = _fwd_reach_cache
    if cached and (now - cached.get("ts", 0)) < _FWD_REACH_TTL:
        return bool(cached.get("reachable", False))

    if not _FWD_API_URL:
        _fwd_reach_cache.update({"reachable": False, "ts": now})
        return False

    try:
        from tools.http.client import request as _req
        resp = _req("HEAD", f"{_FWD_API_URL}/health", timeout=2)
        ok = resp.status_code < 400
    except ImportError:
        try:
            import requests
            resp = requests.head(f"{_FWD_API_URL}/health", timeout=2)
            ok = resp.status_code < 400
        except Exception:
            ok = False
    except Exception:
        ok = False

    _fwd_reach_cache.update({"reachable": ok, "ts": now})
    return ok


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def get_nqe_client():
    """Return a live NQEClient if FWD credentials are present and reachable,
    else a FallbackNQEClient backed by local ICDEV data.
    """
    from tools.network.nqe_client import FallbackNQEClient, NQEClient

    if _FWD_API_KEY and _fwd_reachable():
        logger.debug("get_nqe_client: using live NQEClient (%s)", _FWD_API_URL)
        return NQEClient(api_key=_FWD_API_KEY, base_url=_FWD_API_URL)

    logger.debug("get_nqe_client: using FallbackNQEClient")
    return FallbackNQEClient()


# ---------------------------------------------------------------------------
# DB connection helper
# ---------------------------------------------------------------------------

def _get_conn():
    """Return a connection to the Network Canvas DB."""
    from tools.network.db.init_db import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Advisory templates
# ---------------------------------------------------------------------------

_TEMPLATES_PATH = (
    Path(__file__).resolve().parents[2] / "context" / "nql" / "advisory_templates.md"
)

_VENDOR_TEMPLATE_KEYS = {
    "cisco": "cisco",
    "juniper": "juniper",
    "arista": "arista",
    "palo_alto": "palo_alto",
    "fortinet": "fortinet",
}

_TOTAL_NQL = "foreach d in network.devices\nselect count(d)"


def _build_template_nql(advisory: dict) -> str:
    """Build the deterministic Q2 NQL from advisory data and templates.md.

    Selection logic (from advisory_templates.md):
    1. Vendor-specific template if vendor matches a known vendor.
    2. Generic models+versions if both lists are non-empty.
    3. Generic models-only or versions-only.
    4. Fallback to total-devices query.
    """
    vendor = (advisory.get("vendor") or "").strip().lower()
    models: list[str] = _json_list(advisory.get("affected_models_json"))
    versions: list[str] = _json_list(advisory.get("affected_versions_json"))

    models_lit = "[" + ", ".join(f'"{m}"' for m in models) + "]" if models else ""
    versions_lit = "[" + ", ".join(f'"{v}"' for v in versions) + "]" if versions else ""

    predicates: list[str] = []

    canonical_vendor = _VENDOR_TEMPLATE_KEYS.get(vendor, "")
    if canonical_vendor:
        predicates.append(f'd.vendor == "{canonical_vendor}"')

    if models_lit:
        predicates.append(f"d.hardware.platform in {models_lit}")
    if versions_lit:
        predicates.append(f"d.os.version in {versions_lit}")

    if not predicates:
        return _TOTAL_NQL

    where = " and ".join(predicates)
    return (
        "foreach d in network.devices\n"
        f"where {where}\n"
        "select { d.hostname, d.hardware.platform, d.os.version, d.role, d.management.ip }"
    )


def _json_list(value: Any) -> list[str]:
    """Coerce a JSONB column value to a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# ingest_advisory
# ---------------------------------------------------------------------------

def ingest_advisory(
    cve_id: str,
    affected_models: list[str],
    affected_versions: list[str],
    advisory_text: str,
    source_doc_hash: Optional[str] = None,
    source_doc_format: str = "manual",
    extraction_confidence: Optional[float] = None,
) -> int:
    """Insert a new advisory record into ``nc_advisories``.

    Args:
        cve_id:                CVE identifier (e.g. ``"CVE-2024-12345"``).
        affected_models:       List of hardware platform strings.
        affected_versions:     List of OS version strings.
        advisory_text:         Full advisory body text.
        source_doc_hash:       SHA-256 of the source document (optional).
        source_doc_format:     Document format: ``"manual"``, ``"pdf"``, etc.
        extraction_confidence: Confidence score 0–1 from the extractor (optional).

    Returns:
        Inserted row ``id`` (integer).
    """
    conn = _get_conn()
    try:
        models_json = json.dumps(affected_models)
        versions_json = json.dumps(affected_versions)

        # Derive vendor heuristically from first model token if possible
        vendor = _infer_vendor(affected_models, advisory_text)
        title = _infer_title(advisory_text, cve_id)

        result = conn.execute(
            "INSERT INTO nc_advisories "
            "(cve_id, vendor, title, affected_models_json, affected_versions_json, "
            "advisory_text, source_doc_hash, source_doc_format, extraction_confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cve_id,
                vendor,
                title,
                models_json,
                versions_json,
                advisory_text,
                source_doc_hash,
                source_doc_format,
                extraction_confidence,
            ),
        )
        conn.commit()

        row_id: Optional[int] = None
        # PG returns lastrowid; SQLite also uses lastrowid
        if hasattr(result, "lastrowid") and result.lastrowid:
            row_id = result.lastrowid
        else:
            # Fallback: query back
            row = conn.execute(
                "SELECT id FROM nc_advisories WHERE cve_id = ? ORDER BY id DESC LIMIT 1",
                (cve_id,),
            ).fetchone()
            row_id = int(row[0]) if row else None

        if row_id is None:
            raise RuntimeError("ingest_advisory: could not retrieve inserted row id")

        logger.info("ingest_advisory: inserted advisory id=%s cve=%s", row_id, cve_id)
        return row_id
    finally:
        conn.close()


def _infer_vendor(models: list[str], text: str) -> str:
    """Heuristically derive a vendor string from model names or advisory text."""
    combined = " ".join(models) + " " + text[:500]
    combined_lower = combined.lower()
    for kw, vendor in [
        ("cisco", "cisco"), ("juniper", "juniper"), ("arista", "arista"),
        ("palo alto", "palo_alto"), ("palo_alto", "palo_alto"),
        ("fortinet", "fortinet"), ("fortigate", "fortinet"),
        ("f5", "f5"), ("a10", "a10"), ("nokia", "nokia"),
        ("huawei", "huawei"), ("cumulus", "cumulus"),
    ]:
        if kw in combined_lower:
            return vendor
    return "other"


def _infer_title(text: str, cve_id: str) -> str:
    """Extract the first non-empty line as advisory title."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:255]
    return cve_id


# ---------------------------------------------------------------------------
# run_impact_assessment
# ---------------------------------------------------------------------------

def run_impact_assessment(advisory_id: int, network_id: str) -> dict:
    """Assess the network impact of a stored advisory using NQE queries.

    Flow:
    1. Load advisory from ``nc_advisories``.
    2. Generate Q1 (AI): ``nl_to_nql(advisory impact description)``.
    3. Generate Q2 (deterministic): template-filled NQL from ``advisory_templates.md``.
    4. Run both queries + a total-count query via ``get_nqe_client()``.
    5. Compute ``reconciliation_warning`` when |q1−q2|/max(q1,1) > 0.05.
    6. Persist to ``nc_advisory_assessments`` and ``nc_nqe_audit_log``.
    7. Return full result dict.

    Args:
        advisory_id: PK of the advisory in ``nc_advisories``.
        network_id:  Network/topology identifier scoping the assessment.

    Returns:
        dict with keys: advisory_id, network_id, total_devices, impacted_count,
        reconciliation_warning, source, nql_impacted, nql_total, ai_confidence,
        assessment_id, impacted_devices.
    """
    conn = _get_conn()
    try:
        advisory = _load_advisory(conn, advisory_id)
    finally:
        conn.close()

    if advisory is None:
        raise ValueError(f"Advisory {advisory_id} not found in nc_advisories")

    # --- Build NQL queries ---
    context = {
        "vendor": advisory.get("vendor") or "",
        "affected_models": _json_list(advisory.get("affected_models_json")),
        "affected_versions": _json_list(advisory.get("affected_versions_json")),
    }

    from tools.network.nql_translator import nl_to_nql
    advisory_description = (
        f"Find all network devices affected by {advisory.get('cve_id', 'unknown CVE')} "
        f"(vendor: {advisory.get('vendor', 'unknown')}, "
        f"models: {', '.join(context['affected_models'][:5]) or 'any'}, "
        f"versions: {', '.join(context['affected_versions'][:5]) or 'any'}). "
        f"Summary: {(advisory.get('advisory_text') or '')[:200]}"
    )
    q1_nql = nl_to_nql(advisory_description, context=context)
    q2_nql = _build_template_nql(advisory)

    # --- Run queries ---
    client = get_nqe_client()
    source = "fwd-live" if isinstance(client, _get_nqe_client_type("NQEClient")) else "icdev-internal"

    total_result = client.run_query(_TOTAL_NQL, network_id=network_id)
    q1_result = client.run_query(q1_nql, network_id=network_id)
    q2_result = client.run_query(q2_nql, network_id=network_id)

    total_devices = _extract_count(total_result)
    q1_count = _extract_count(q1_result)
    q2_count = _extract_count(q2_result)

    # Authoritative impacted count is Q2 (deterministic); Q1 is used for reconciliation
    impacted_count = q2_count
    reconciliation_warning = (
        abs(q1_count - q2_count) / max(q1_count, 1) > 0.05
    ) if q1_count > 0 else False

    impacted_devices = [
        row.get("hostname", str(row)) for row in (q2_result.get("rows") or [])
    ]

    # AI confidence: inverse of reconciliation gap, clamped to [0,1]
    gap_ratio = abs(q1_count - q2_count) / max(max(q1_count, q2_count), 1)
    ai_confidence = round(max(0.0, 1.0 - gap_ratio), 3)

    # --- Persist assessment ---
    session_id = str(uuid.uuid4())
    assessment_id = _insert_assessment(
        advisory_id=advisory_id,
        network_id=network_id,
        nql_total=_TOTAL_NQL,
        nql_impacted=q2_nql,
        total_devices=total_devices,
        impacted_count=impacted_count,
        impacted_devices=impacted_devices,
        raw_total=total_result,
        raw_impacted=q2_result,
        ai_confidence=ai_confidence,
        reconciliation_warning=reconciliation_warning,
        source=source,
    )
    _insert_audit_log(
        session_id=session_id,
        action="assess",
        input_text=advisory_description,
        nql_generated=q1_nql,
        fwd_snapshot_id=None,
        result_summary=(
            f"advisory_id={advisory_id} network_id={network_id} "
            f"total={total_devices} impacted={impacted_count} "
            f"reconciliation_warning={reconciliation_warning}"
        ),
        raw_response_hash=_hash_dict(q2_result),
        confidence=ai_confidence,
        source=source,
    )

    return {
        "advisory_id": advisory_id,
        "network_id": network_id,
        "total_devices": total_devices,
        "impacted_count": impacted_count,
        "reconciliation_warning": reconciliation_warning,
        "source": source,
        "nql_impacted": q2_nql,
        "nql_total": _TOTAL_NQL,
        "ai_confidence": ai_confidence,
        "assessment_id": assessment_id,
        "impacted_devices": impacted_devices,
        "q1_count": q1_count,
        "q2_count": q2_count,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_advisory(conn, advisory_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, cve_id, vendor, title, affected_models_json, affected_versions_json, "
        "advisory_text, source_doc_hash, source_doc_format, extraction_confidence "
        "FROM nc_advisories WHERE id = ?",
        (advisory_id,),
    ).fetchone()
    if row is None:
        return None
    cols = [
        "id", "cve_id", "vendor", "title", "affected_models_json",
        "affected_versions_json", "advisory_text", "source_doc_hash",
        "source_doc_format", "extraction_confidence",
    ]
    return {col: (row[i] if isinstance(row, (list, tuple)) else row[col]) for i, col in enumerate(cols)}


def _extract_count(result: dict) -> int:
    """Extract a device count from a run_query result dict."""
    # Direct total field
    total = result.get("total")
    if isinstance(total, int):
        return total
    # Rows with a count column (aggregate queries)
    rows = result.get("rows") or []
    if rows:
        first = rows[0]
        for key in ("count", "total", "count(d)", "device_count"):
            if key in first:
                try:
                    return int(first[key])
                except (TypeError, ValueError):
                    pass
    return len(rows)


def _insert_assessment(
    *,
    advisory_id: int,
    network_id: str,
    nql_total: str,
    nql_impacted: str,
    total_devices: int,
    impacted_count: int,
    impacted_devices: list,
    raw_total: dict,
    raw_impacted: dict,
    ai_confidence: float,
    reconciliation_warning: bool,
    source: str,
) -> Optional[int]:
    conn = _get_conn()
    try:
        result = conn.execute(
            "INSERT INTO nc_advisory_assessments "
            "(advisory_id, network_id, nql_total, nql_impacted, total_devices, "
            "impacted_count, impacted_devices_json, raw_response_total_json, "
            "raw_response_impacted_json, ai_confidence, reconciliation_warning, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                advisory_id,
                network_id,
                nql_total,
                nql_impacted,
                total_devices,
                impacted_count,
                json.dumps(impacted_devices),
                json.dumps(raw_total),
                json.dumps(raw_impacted),
                ai_confidence,
                reconciliation_warning,
                source,
            ),
        )
        conn.commit()
        if hasattr(result, "lastrowid") and result.lastrowid:
            return result.lastrowid
        row = conn.execute(
            "SELECT id FROM nc_advisory_assessments "
            "WHERE advisory_id = ? ORDER BY id DESC LIMIT 1",
            (advisory_id,),
        ).fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        logger.error("_insert_assessment failed: %s", exc)
        return None
    finally:
        conn.close()


def _insert_audit_log(
    *,
    session_id: str,
    action: str,
    input_text: str,
    nql_generated: Optional[str],
    fwd_snapshot_id: Optional[str],
    result_summary: str,
    raw_response_hash: Optional[str],
    confidence: float,
    source: str,
) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO nc_nqe_audit_log "
            "(session_id, action, input_text, nql_generated, fwd_snapshot_id, "
            "result_summary, raw_response_hash, confidence, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                action,
                input_text,
                nql_generated,
                fwd_snapshot_id,
                result_summary,
                raw_response_hash,
                confidence,
                source,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.error("_insert_audit_log failed: %s", exc)
    finally:
        conn.close()


def _hash_dict(d: dict) -> str:
    raw = json.dumps(d, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _get_nqe_client_type(name: str):
    """Import and return the class by name from nqe_client module."""
    from tools.network import nqe_client as _mod
    return getattr(_mod, name)
