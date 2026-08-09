#!/usr/bin/env python3
# CUI // SP-CTI
"""ATO Boundary Tier Tagging for security scan findings.

Tags every finding produced by ``tools/security/vuln_scanner.py`` (SAST,
dependency audit, secret detection, container/Dockerfile analysis) with a
4-tier ATO boundary impact, and optionally persists ORANGE/RED findings to
``boundary_impact_assessments``.

Tier rules (see goals/security_scan.md, Step 5):

    GREEN   0-25    LOW findings -- existing controls sufficient
    YELLOW  26-50   MEDIUM findings -- SSP addendum + ISSO notification
    ORANGE  51-75   HIGH SAST/container/dependency, Dockerfile root/SSH
    RED     76-100  any secret, CRITICAL findings, HIGH crypto/injection SAST,
                    Dockerfile secrets-in-ENV -- ATO-invalidating, FULL STOP

Usage:
  python tools/security/boundary_tagger.py --report .tmp/security-reports/scan.json \
      --project-id proj-1 --system-id sys-1 --create-assessments --json
  python tools/security/boundary_tagger.py --report scan.json --gate --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

TIERS = ("GREEN", "YELLOW", "ORANGE", "RED")

#: Representative risk score for each tier, inside the band documented above.
TIER_SCORES = {"GREEN": 15.0, "YELLOW": 40.0, "ORANGE": 65.0, "RED": 90.0}

#: Days of ATO delay each tier implies.
TIER_DELAY_DAYS = {"GREEN": 0, "YELLOW": 14, "ORANGE": 60, "RED": 180}

#: Tiers that require an ISSO/AO action and therefore an assessment record.
ACTIONABLE_TIERS = ("ORANGE", "RED")

#: Bandit test IDs that escalate a HIGH SAST finding to RED -- crypto failures,
#: code injection, and hardcoded credentials all invalidate an ATO boundary.
RED_BANDIT_TESTS = frozenset(
    [
        "B105", "B106", "B107",           # hardcoded passwords
        "B201",                            # flask debug=True
        "B321", "B323",                    # ftplib, unverified SSL context
        "B501", "B502", "B503", "B504", "B506",   # requests/ssl/yaml failures
        "B601", "B602", "B603", "B604", "B605", "B606", "B607",  # injection
    ]
)

#: Dockerfile check IDs (tools/security/container_scanner.py DOCKERFILE_CHECKS)
#: that carry a boundary consequence beyond their raw severity.
DOCKERFILE_RED = frozenset(["DS007"])           # secrets in ENV
DOCKERFILE_ORANGE = frozenset(["DS001", "DS006"])  # runs as root, SSH exposed

_SEVERITY_ALIASES = {
    "MODERATE": "MEDIUM",
    "INFO": "LOW",
    "INFORMATIONAL": "LOW",
    "UNKNOWN": "LOW",
    "NONE": "LOW",
}

#: Maps a finding to one of the impact_category values the
#: boundary_impact_assessments CHECK constraint allows.
_CATEGORY_KEYWORDS = (
    ("encryption", ("crypto", "ssl", "tls", "cipher", "hash", "md5", "certificate")),
    ("authentication", ("password", "credential", "secret", "token", "api key", "apikey")),
    ("authorization", ("root", "sudo", "privilege", "permission", "capabilit")),
    ("network", ("port", "expose", "ssh", "ftp", "bind", "listen")),
    ("logging", ("log", "audit")),
    ("data_flow", ("injection", "subprocess", "shell", "eval", "exec", "deserial")),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_id() -> str:
    return f"bia-scan-{uuid.uuid4().hex[:12]}"


def _normalize_severity(value: Optional[str]) -> str:
    sev = (value or "LOW").strip().upper()
    return _SEVERITY_ALIASES.get(sev, sev)


def collect_findings(aggregated: Dict) -> List[Dict]:
    """Flatten a vuln_scanner aggregate into a list of normalized findings.

    Handles both scan shapes the scanner produces: a single result dict
    (``scans.sast``, ``scans.secrets``) and a dict-of-results keyed by language
    or artifact (``scans.dependency``, ``scans.container``).
    """
    findings: List[Dict] = []
    scans = aggregated.get("scans") or {}

    def _emit(source: str, raw: Dict) -> None:
        findings.append(
            {
                "source": source,
                "type": raw.get("test_id")
                or raw.get("check_id")
                or raw.get("vulnerability_id")
                or "",
                "severity": _normalize_severity(raw.get("severity")),
                "message": raw.get("issue_text")
                or raw.get("title")
                or raw.get("name")
                or raw.get("description")
                or "",
                "file": raw.get("file", ""),
                "line": raw.get("line", 0),
                "package": raw.get("package", ""),
                "_raw": raw,
            }
        )

    for category in ("sast", "dependency", "secrets", "container"):
        block = scans.get(category)
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("findings"), list):
            # Flat result dict (sast, secrets).
            for raw in block["findings"]:
                if isinstance(raw, dict):
                    _emit(category, raw)
            continue
        # Nested: {lang_or_artifact: result_dict}
        for key, result in block.items():
            if not isinstance(result, dict):
                continue
            for raw in result.get("findings") or []:
                if isinstance(raw, dict):
                    _emit(f"{category}/{key}", raw)

    # Secrets are always CRITICAL regardless of what the detector reported.
    for finding in findings:
        if finding["source"].startswith("secrets"):
            finding["severity"] = "CRITICAL"
            if not finding["type"]:
                finding["type"] = str(finding["_raw"].get("type") or "SECRET")

    return findings


def classify_finding(finding: Dict) -> Dict:
    """Return ``{tier, risk_score, ato_delay_days, reason}`` for one finding."""
    source = (finding.get("source") or "").lower()
    ftype = (finding.get("type") or "").strip().upper()
    severity = _normalize_severity(finding.get("severity"))

    if source.startswith("secrets"):
        tier, reason = "RED", "Secret material committed to the repository"
    elif ftype in DOCKERFILE_RED:
        tier, reason = "RED", f"Dockerfile check {ftype}: secrets in ENV directive"
    elif severity == "CRITICAL":
        tier, reason = "RED", f"CRITICAL {source} finding"
    elif severity == "HIGH" and source.startswith("sast") and ftype in RED_BANDIT_TESTS:
        tier, reason = "RED", f"HIGH SAST crypto/injection finding ({ftype})"
    elif ftype in DOCKERFILE_ORANGE:
        tier, reason = "ORANGE", f"Dockerfile check {ftype}: root user or SSH exposure"
    elif severity == "HIGH":
        tier, reason = "ORANGE", f"HIGH {source} finding"
    elif severity == "MEDIUM":
        tier, reason = "YELLOW", f"MEDIUM {source} finding"
    else:
        tier, reason = "GREEN", f"{severity} {source} finding"

    return {
        "tier": tier,
        "risk_score": TIER_SCORES[tier],
        "ato_delay_days": TIER_DELAY_DAYS[tier],
        "reason": reason,
    }


def _detect_category(finding: Dict) -> str:
    haystack = " ".join(
        str(finding.get(k, "")) for k in ("source", "type", "message", "file", "package")
    ).lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(word in haystack for word in keywords):
            return category
    return "architecture"


def _highest_tier(tier_counts: Dict[str, int]) -> str:
    for tier in ("RED", "ORANGE", "YELLOW"):
        if tier_counts.get(tier):
            return tier
    return "GREEN"


def _persist_assessments(
    findings: List[Dict],
    project_id: str,
    system_id: str,
    db_path: Optional[str] = None,
) -> List[Dict]:
    """Insert one boundary_impact_assessments row per ORANGE/RED finding.

    ``project_id`` and ``system_id`` are both NOT NULL with foreign keys, so
    the caller must supply them; :func:`process_scan_result` skips persistence
    entirely when either is missing rather than writing a broken row.
    """
    created: List[Dict] = []
    conn = get_connection(db_path=db_path) if db_path else get_connection()
    try:
        cursor = conn.cursor()
        for finding in findings:
            boundary = finding.get("boundary_impact") or {}
            if boundary.get("tier") not in ACTIONABLE_TIERS:
                continue
            record_id = _generate_id()
            location = finding.get("file") or finding.get("package") or "n/a"
            description = (
                f"[{finding.get('source')}] {finding.get('type') or 'finding'} "
                f"at {location}: {finding.get('message') or boundary.get('reason')}"
            )
            try:
                cursor.execute(
                    """INSERT INTO boundary_impact_assessments
                       (id, project_id, system_id, impact_tier, impact_category,
                        impact_description, affected_components, remediation_required,
                        risk_score, assessed_by, assessed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        record_id,
                        project_id,
                        system_id,
                        boundary["tier"],
                        _detect_category(finding),
                        description[:2000],
                        json.dumps([location]),
                        json.dumps(
                            [
                                boundary.get("reason", ""),
                                f"ATO action within {boundary.get('ato_delay_days')} days",
                            ]
                        ),
                        boundary.get("risk_score", 0.0),
                        "security/boundary_tagger",
                        _now(),
                    ),
                )
            except Exception as exc:  # pragma: no cover - depends on live schema
                logger.warning("boundary assessment insert failed for %s: %s", location, exc)
                continue
            created.append(
                {
                    "id": record_id,
                    "impact_tier": boundary["tier"],
                    "source": finding.get("source"),
                    "location": location,
                }
            )
        conn.commit()
    finally:
        conn.close()
    return created


def process_scan_result(
    aggregated: Dict,
    project_id: Optional[str] = None,
    system_id: Optional[str] = None,
    create_assessments: bool = False,
    db_path: Optional[str] = None,
) -> Dict:
    """Tag every finding in ``aggregated`` with its ATO boundary tier.

    Mutates ``aggregated`` in place: each collected finding gains a
    ``boundary_impact`` block, and ``aggregated["boundary_impact_summary"]`` is
    set to the returned summary.

    Persisting assessments requires BOTH ``project_id`` and ``system_id`` --
    ``boundary_impact_assessments`` declares them NOT NULL with foreign keys to
    ``projects`` and ``ato_system_registry``. When either is absent the tagging
    still happens and ``assessments_skipped_reason`` explains the omission.

    This function does not write the audit trail; ``vuln_scanner`` logs the
    ``boundary_assessed`` / ``boundary_impact_red`` events for the scans it
    runs, and the CLI below logs them for standalone runs.

    Returns:
        The boundary impact summary dict.
    """
    findings = collect_findings(aggregated)
    tier_counts = {tier: 0 for tier in TIERS}

    for finding in findings:
        boundary = classify_finding(finding)
        finding["boundary_impact"] = boundary
        # Tag the original scanner finding too, so a caller reading
        # aggregated["scans"] sees the tier without re-collecting.
        raw = finding.pop("_raw", None)
        if isinstance(raw, dict):
            raw["boundary_impact"] = boundary
        tier_counts[boundary["tier"]] += 1

    highest = _highest_tier(tier_counts)
    summary: Dict = {
        "tier_counts": tier_counts,
        "highest_tier": highest,
        "total_findings": len(findings),
        "requires_ato_action": highest in ACTIONABLE_TIERS,
        "ato_delay_days": TIER_DELAY_DAYS[highest],
        "full_stop": highest == "RED",
        "assessments": [],
        "tagged_findings": findings,
        "tagged_at": _now(),
    }

    actionable = [f for f in findings if f["boundary_impact"]["tier"] in ACTIONABLE_TIERS]
    if create_assessments and actionable:
        if project_id and system_id:
            try:
                summary["assessments"] = _persist_assessments(
                    actionable, project_id, system_id, db_path=db_path
                )
            except Exception as exc:
                logger.warning("boundary assessment persistence failed: %s", exc)
                summary["assessments_skipped_reason"] = f"persistence failed: {exc}"
        else:
            missing = [
                name
                for name, val in (("project_id", project_id), ("system_id", system_id))
                if not val
            ]
            summary["assessments_skipped_reason"] = (
                f"{' and '.join(missing)} required -- "
                "boundary_impact_assessments declares both NOT NULL"
            )
            logger.info(
                "Skipping %d boundary assessment(s): %s",
                len(actionable),
                summary["assessments_skipped_reason"],
            )

    aggregated["boundary_impact_summary"] = summary
    return summary


def evaluate_gate(summary: Dict) -> Dict:
    """Block on any RED-tier finding; warn on ORANGE."""
    highest = summary.get("highest_tier", "GREEN")
    counts = summary.get("tier_counts", {})
    if highest == "RED":
        return {
            "gate": "boundary_impact",
            "passed": False,
            "blocking": True,
            "highest_tier": highest,
            "reason": (
                f"{counts.get('RED', 0)} RED-tier finding(s) are ATO-invalidating. "
                "FULL STOP -- notify the AO within 24 hours and generate alternative COAs."
            ),
        }
    if highest == "ORANGE":
        return {
            "gate": "boundary_impact",
            "passed": True,
            "blocking": False,
            "highest_tier": highest,
            "reason": (
                f"{counts.get('ORANGE', 0)} ORANGE-tier finding(s) require an SSP "
                "revision and ISSO review (60-day delay)."
            ),
        }
    return {
        "gate": "boundary_impact",
        "passed": True,
        "blocking": False,
        "highest_tier": highest,
        "reason": "No ORANGE or RED boundary impact detected.",
    }


def _log_audit(project_id: Optional[str], summary: Dict) -> None:
    """Append the boundary event to the audit trail (standalone CLI path)."""
    if not summary.get("requires_ato_action"):
        return
    highest = summary.get("highest_tier", "ORANGE")
    event_type = "boundary_impact_red" if highest == "RED" else "boundary_assessed"
    try:
        conn = get_connection()
        try:
            conn.cursor().execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, classification)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    project_id,
                    event_type,
                    "security/boundary_tagger",
                    f"Security scan produced {highest}-tier boundary impact",
                    json.dumps(
                        {
                            "tier": highest,
                            "tier_counts": summary.get("tier_counts", {}),
                            "assessments_created": len(summary.get("assessments", [])),
                        }
                    ),
                    "CUI",
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("boundary audit logging failed: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tag security scan findings with ATO boundary tier impact"
    )
    parser.add_argument("--report", required=True, help="Path to a vuln_scanner JSON report")
    parser.add_argument("--project-id", help="Project ID for assessment records and audit trail")
    parser.add_argument("--system-id", help="ATO system ID (ato_system_registry.id)")
    parser.add_argument(
        "--create-assessments",
        action="store_true",
        help="Persist ORANGE/RED findings to boundary_impact_assessments",
    )
    parser.add_argument("--gate", action="store_true", help="Evaluate the boundary impact gate")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"Error: report not found: {report_path}", file=sys.stderr)
        return 1
    try:
        aggregated = json.loads(report_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"Error: could not read report {report_path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(aggregated, dict):
        print(f"Error: report {report_path} is not a scan result object", file=sys.stderr)
        return 1

    summary = process_scan_result(
        aggregated,
        project_id=args.project_id or aggregated.get("project_id"),
        system_id=args.system_id,
        create_assessments=args.create_assessments,
    )
    _log_audit(args.project_id or aggregated.get("project_id"), summary)

    gate = evaluate_gate(summary) if args.gate else None
    payload = {"boundary_impact_summary": summary}
    if gate:
        payload["gate"] = gate

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        counts = summary["tier_counts"]
        print(f"Boundary impact: {summary['highest_tier']}")
        print(
            f"  RED:{counts['RED']} ORANGE:{counts['ORANGE']} "
            f"YELLOW:{counts['YELLOW']} GREEN:{counts['GREEN']}"
        )
        print(f"  Findings tagged: {summary['total_findings']}")
        print(f"  Assessments created: {len(summary['assessments'])}")
        if summary.get("assessments_skipped_reason"):
            print(f"  Assessments skipped: {summary['assessments_skipped_reason']}")
        if gate:
            print(f"  Gate: {'PASS' if gate['passed'] else 'BLOCK'} -- {gate['reason']}")

    if gate and gate["blocking"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
