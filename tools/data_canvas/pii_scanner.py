#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Data Design Canvas — PII Scanner.

Scans data design column profiles for potential PII exposure risks.
Works on data_profiler output — does NOT scan actual data rows.

Detection strategies:
  1. Name heuristics — column name suggests PII (email, ssn, phone, dob, etc.)
  2. Pattern sampling — if top_values contain email/phone/SSN-like patterns
  3. High cardinality + no classification marking — possible free-text PII column

Severity:
  high   — column name is a known PII identifier AND classification < CUI
  medium — column name suggests PII or pattern match in top_values
  low    — high cardinality free-text column with no explicit marking

Output: {findings, overall_risk, columns_scanned, pii_candidates, scanned_at}

Usage:
  python -m tools.data_canvas.pii_scanner --profile-json out.json --output-json
  python -m tools.data_canvas.pii_scanner --db data/my.db --classification CUI --output-json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

# ── Detection patterns ────────────────────────────────────────────────────────

_PII_HIGH = {
    "ssn", "social_security", "social_security_number",
    "sin", "tax_id", "taxpayer_id", "nin", "nino",
    "passport", "passport_number", "drivers_license", "license_number",
    "date_of_birth", "dob", "birth_date", "birthdate",
    "biometric", "fingerprint", "retina", "iris",
}

_PII_MEDIUM = {
    "email", "email_address", "e_mail",
    "phone", "phone_number", "mobile", "cell", "fax", "telephone",
    "address", "street_address", "mailing_address",
    "zip", "zipcode", "postal_code", "postcode",
    "first_name", "last_name", "full_name", "name",
    "username", "user_name", "login", "userid", "user_id",
    "ip_address", "ip_addr", "ipv4", "ipv6",
    "credit_card", "card_number", "cvv", "pan",
    "bank_account", "account_number", "routing_number", "iban",
    "gender", "race", "ethnicity", "religion",
    "salary", "income", "wage",
    "location", "gps", "latitude", "longitude",
    "device_id", "mac_address", "imei", "cookie",
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(\+?1?\s?)?(\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}")
_SSN_RE = re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")
_CC_RE = re.compile(r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b")

_RISK_ORDER = {"high": 3, "medium": 2, "low": 1, "none": 0}

_CLASSIFICATION_LEVELS_HIGH_RISK = {"public", "unclassified"}

# Technical/metadata column-name prefixes that, when combined with a PII-looking
# suffix (e.g. "name", "id"), do NOT indicate personal data.
# Examples: column_name, table_name, schema_id, rule_type are metadata.
_TECHNICAL_NON_PII_PREFIXES = {
    "column", "table", "schema", "database", "db", "field", "attribute",
    "property", "type", "class", "object", "method", "function",
    "module", "variable", "constant", "enum", "param", "parameter",
    "config", "setting", "option", "path", "file", "dir", "directory",
    "folder", "label", "tag", "category", "status", "state", "code",
    "value", "item", "entity", "resource", "index", "sort",
    "group", "parent", "child", "source", "target", "rule",
    "check", "constraint", "format", "pattern", "template", "query",
    "filter", "action", "event", "audit", "metric", "key", "hash",
}


def _col_name_severity(col_lower: str) -> tuple[str | None, str | None]:
    """Return (severity, matched_pii_term) or (None, None)."""
    col_parts = set(re.split(r"[_\-. ]", col_lower))
    # Compound technical names like column_name / table_name are metadata, not PII
    if len(col_parts) > 1 and (col_parts & _TECHNICAL_NON_PII_PREFIXES):
        return None, None
    for part in col_parts:
        if part in _PII_HIGH or col_lower in _PII_HIGH:
            return "high", part
    for part in col_parts:
        if part in _PII_MEDIUM or col_lower in _PII_MEDIUM:
            return "medium", part if part in _PII_MEDIUM else col_lower
    return None, None


def _pattern_match(top_values: list[str]) -> list[str]:
    matched = []
    for val in top_values:
        val_str = str(val)
        if _EMAIL_RE.search(val_str):
            matched.append("email")
        if _PHONE_RE.search(val_str):
            matched.append("phone")
        if _SSN_RE.search(val_str):
            matched.append("ssn")
        if _CC_RE.search(val_str):
            matched.append("credit_card")
    return list(set(matched))


def check_column(
    col_name: str,
    stats: dict[str, Any],
    table_classification: str = "CUI",
) -> dict[str, Any] | None:
    """Return a PII finding dict or None if no concern detected."""
    col_lower = col_name.lower().replace("-", "_")
    name_sev, matched_term = _col_name_severity(col_lower)
    top_values = stats.get("top_values") or []
    distinct_count = stats.get("distinct_count") or 0
    row_count = stats.get("row_count") or 0

    # Pattern scan on sampled top values
    pattern_hits = _pattern_match([str(v) for v in top_values if v is not None])

    # Severity escalation if classification is low
    cls_lower = table_classification.lower()
    low_classification = any(c in cls_lower for c in _CLASSIFICATION_LEVELS_HIGH_RISK)

    if name_sev == "high" and low_classification:
        severity = "high"
        reason = f"Column name '{col_name}' is a known PII identifier and classification is '{table_classification}'."
    elif name_sev == "high":
        severity = "medium"
        reason = f"Column name '{col_name}' is a known PII identifier."
    elif name_sev == "medium" or pattern_hits:
        severity = "medium"
        parts = []
        if name_sev == "medium":
            parts.append(f"column name '{col_name}' suggests PII")
        if pattern_hits:
            parts.append(f"sample values match {', '.join(pattern_hits)} patterns")
        reason = "Detected: " + "; ".join(parts) + "."
    elif distinct_count > 1000 and row_count > 0 and (distinct_count / row_count) > 0.8:
        # High-cardinality free-text — might be free-form PII
        severity = "low"
        reason = (
            f"Column '{col_name}' has high cardinality ({distinct_count} distinct values, "
            f"{distinct_count/row_count:.0%} unique) and no explicit PII classification."
        )
    else:
        return None

    # Derive a human-readable PII type label for UI display
    if matched_term:
        pii_type = matched_term.upper()
    elif pattern_hits:
        pii_type = pattern_hits[0].upper()
    else:
        pii_type = "PII"

    return {
        "column": col_name,
        "severity": severity,
        "pii_type": pii_type,
        "finding_type": "pii_risk",
        "description": reason,
        "pattern_matches": pattern_hits,
    }


def scan_profile(
    profile: dict[str, Any],
    classification: str = "CUI",
) -> dict[str, Any]:
    """Scan a data_profiler result for PII risks.

    Args:
        profile: Output of data_profiler.profile_database() or profile_table().
        classification: Default classification if not embedded in profile.

    Returns:
        {findings, overall_risk, columns_scanned, pii_candidates, scanned_at}
    """
    findings: list[dict] = []
    columns_scanned = 0

    # profile may be a full db profile or a single table profile
    tables = profile.get("tables", None)
    if tables is None:
        # Single table profile
        tables = [profile]

    for tbl in tables:
        tbl_cls = tbl.get("classification", classification)
        row_count = tbl.get("row_count", 0)
        for col in tbl.get("columns", []):
            columns_scanned += 1
            col_name = col.get("name") or col.get("column_name", "?")
            stats = dict(col)
            stats.setdefault("row_count", row_count)
            result = check_column(col_name, stats, tbl_cls)
            if result:
                result["table"] = tbl.get("name", "?")
                findings.append(result)

    risk_levels = [_RISK_ORDER.get(f["severity"], 0) for f in findings]
    max_risk = max(risk_levels, default=0)
    overall_risk = {3: "high", 2: "medium", 1: "low", 0: "none"}[max_risk]

    return {
        "findings": findings,
        "overall_risk": overall_risk,
        "columns_scanned": columns_scanned,
        "pii_candidates": len(findings),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def scan_result(
    col_names: list[str],
    rows: list[list[str]],
    classification: str = "CUI // SP-CTI",
) -> dict[str, Any]:
    """Scan query result rows for PII exposure risk.

    Samples up to 20 rows for pattern detection. Advisory only — never blocks.

    Returns:
        {warnings: [...], has_warnings: bool}
        Each warning: {column, severity, description, pattern_matches}
    """
    warnings: list[dict] = []
    sample_rows = rows[:20]

    for idx, col_name in enumerate(col_names):
        top_values = [row[idx] for row in sample_rows if idx < len(row)]
        stats = {
            "top_values": top_values,
            "distinct_count": len(set(top_values)),
            "row_count": len(rows),
        }
        finding = check_column(col_name, stats, classification)
        if finding:
            warnings.append(finding)

    return {"warnings": warnings, "has_warnings": bool(warnings)}


def save_pii_scan(result: dict, design_id: str | None = None) -> str:
    """Persist PII scan result to dd_pii_scans."""
    import uuid
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(result)

    # dd_pii_scans is a canvas table (no classification/tenant_id columns), so use
    # get_canvas_connection() — get_connection() would attach the global RLS
    # predicate and raise UndefinedColumn on every query.
    try:
        from tools.db.storage import get_canvas_connection
        conn = get_canvas_connection()
    except Exception as exc:
        # storage unavailable — scan result not persisted
        print(f"[pii_scanner] save_pii_scan: storage unavailable: {exc}", file=sys.stderr)
        return run_id

    try:
        conn.execute(
            "INSERT INTO dd_pii_scans (scan_id, design_id, overall_risk, findings_json, scanned_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, design_id, result.get("overall_risk", "none"), payload, now),
        )
        conn.commit()
    except Exception as exc:
        print(f"[pii_scanner] save_pii_scan: write failed: {exc}", file=sys.stderr)
    finally:
        conn.close()

    return run_id


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="DDC PII Scanner — detect PII exposure in column profiles")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--profile-json", metavar="FILE", help="data_profiler JSON output file")
    src.add_argument("--db", metavar="SQLITE_PATH", help="SQLite DB to profile and scan")
    ap.add_argument("--classification", default="CUI", help="Classification label (default: CUI)")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    if args.profile_json:
        try:
            with open(args.profile_json, encoding="utf-8") as fh:
                profile = json.load(fh)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        from tools.data_canvas.data_profiler import profile_database
        conn_params = {"db_type": "sqlite", "path": args.db}
        profile = profile_database(conn_params, classification=args.classification)

    result = scan_profile(profile, classification=args.classification)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[pii_scanner] {result['columns_scanned']} columns scanned — "
              f"{result['pii_candidates']} PII candidates — risk={result['overall_risk']}")
        for f in result["findings"]:
            tbl = f.get("table", "?")
            print(f"  [{f['severity'].upper():6s}] {tbl}.{f['column']}: {f['description']}")


if __name__ == "__main__":
    main()
