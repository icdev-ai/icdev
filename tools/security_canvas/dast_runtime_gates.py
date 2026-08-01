# CUI // SP-CTI
"""DAST & Runtime Testing Gates — ZIG Application Pillar, Activity p2-21.

Implements Dynamic Application Security Testing gates and runtime protection
checks that block promotion of applications failing security baselines. DAST
findings (OWASP Top 10 categories) are recorded per application and gate the
deploy pipeline; runtime gates enforce WAF/RASP posture at release.

**Evidence-gated (fail-closed).** This module does not run a scanner itself; it
records findings supplied by one and scores the gate. A check with no supplied
observation is ``unknown`` — never ``pass``. Consequences:

  * ``zig_dast_scans`` only ever holds *observed* results. Unevaluated checks are
    not written, so the table cannot be mistaken for evidence of a scan.
  * A gate evaluation with any unevaluated check returns ``gate_status="unknown"``
    and can never return ``"pass"``. Scores are ``None`` when nothing was observed.
  * ``deploy_dast_gates`` marks ZIG activity p2-21 ``complete`` only when every
    application has full-coverage evidence; otherwise ``in_progress``.

Rationale: a gate that reports ``pass`` when no scan ran is worse than no gate,
because it manufactures compliance evidence for work that never happened — and
``continuous_authorization._resolve_dast_signal`` consumes these rows to compute
the cATO ongoing-authorization posture.

NIST 800-53: SA-11, SI-10, SI-7, CA-8
ZIG Activity: zig-act-p2-21 (Implement DAST and runtime testing gates)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from tools.security_canvas.db.init_db import get_connection

# ---------------------------------------------------------------------------
# DAST check catalog — OWASP Top 10 (2021) aligned
# ---------------------------------------------------------------------------

DAST_CHECKS = {
    "A01-broken-access":   {"name": "Broken access control probe", "severity": "CAT-I"},
    "A02-crypto-failure":  {"name": "Cryptographic failure / weak TLS", "severity": "CAT-I"},
    "A03-injection":       {"name": "Injection (SQLi/XSS/cmd) probe", "severity": "CAT-I"},
    "A04-insecure-design": {"name": "Insecure design pattern", "severity": "CAT-II"},
    "A05-misconfig":       {"name": "Security misconfiguration", "severity": "CAT-II"},
    "A06-vuln-components":  {"name": "Vulnerable/outdated components", "severity": "CAT-II"},
    "A07-auth-failure":    {"name": "Identification & authentication failure", "severity": "CAT-I"},
    "A08-integrity-failure":{"name": "Software & data integrity failure", "severity": "CAT-II"},
    "A09-logging-failure": {"name": "Security logging & monitoring failure", "severity": "CAT-III"},
    "A10-ssrf":            {"name": "Server-side request forgery", "severity": "CAT-II"},
}

# Runtime protection posture checks
RUNTIME_CHECKS = {
    "waf-enabled":      {"name": "Web application firewall active", "severity": "CAT-I"},
    "rasp-enabled":     {"name": "Runtime application self-protection active", "severity": "CAT-II"},
    "rate-limiting":    {"name": "Rate limiting / DoS protection", "severity": "CAT-II"},
    "tls-enforced":     {"name": "TLS 1.2+ enforced, HSTS set", "severity": "CAT-I"},
    "secrets-runtime":  {"name": "No secrets in runtime env/logs", "severity": "CAT-I"},
}

SEVERITY_WEIGHTS = {"CAT-I": 1.0, "CAT-II": 0.6, "CAT-III": 0.3}
# A gate blocks promotion if any CAT-I check fails or the weighted pass-rate < this
GATE_PASS_THRESHOLD = 0.85

# Per-check observation states
CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_UNKNOWN = "unknown"

# Scan coverage states
SCAN_NOT_RUN = "not_run"    # zero checks observed
SCAN_PARTIAL = "partial"    # some checks observed
SCAN_COMPLETE = "complete"  # every check in the catalog observed

# Gate decisions
GATE_PASS = "pass"
GATE_BLOCKED = "blocked"    # real failure(s) observed
GATE_UNKNOWN = "unknown"    # insufficient evidence to decide — never promotes


def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_dast_scans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            application  TEXT NOT NULL,
            scan_type    TEXT NOT NULL,
            check_id     TEXT NOT NULL,
            check_name   TEXT NOT NULL,
            passed       INTEGER NOT NULL DEFAULT 0,
            severity     TEXT,
            detail       TEXT,
            scanned_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_dast_gate_results (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            application  TEXT NOT NULL,
            gate_status  TEXT NOT NULL,
            dast_score   REAL,
            runtime_score REAL,
            cat1_failures INTEGER DEFAULT 0,
            blockers_json TEXT,
            evaluated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _observed(catalog: dict, supplied: dict | None) -> dict[str, bool]:
    """Keep only observations for known check ids, coerced to bool."""
    return {cid: bool(val) for cid, val in (supplied or {}).items() if cid in catalog}


def _score(catalog: dict, observed: dict[str, bool]) -> float | None:
    """Weighted pass-rate over OBSERVED checks only. None when nothing observed.

    Scoring over observed weight (not catalog weight) keeps a partial scan's score
    meaningful. Coverage is reported separately, and unevaluated checks block the
    gate on their own, so a high score from thin coverage cannot promote.
    """
    total_w = sum(SEVERITY_WEIGHTS[catalog[c]["severity"]] for c in observed)
    if not total_w:
        return None
    passed_w = sum(SEVERITY_WEIGHTS[catalog[c]["severity"]] for c, ok in observed.items() if ok)
    return round(passed_w / total_w, 4)


def _coverage_status(catalog: dict, observed: dict[str, bool]) -> str:
    if not observed:
        return SCAN_NOT_RUN
    return SCAN_COMPLETE if len(observed) == len(catalog) else SCAN_PARTIAL


def _cat1_failures(catalog: dict, observed: dict[str, bool]) -> int:
    return sum(1 for cid, ok in observed.items()
               if not ok and catalog[cid]["severity"] == "CAT-I")


def _record(conn, application: str, scan_type: str, catalog: dict,
            observed: dict[str, bool], detail: dict, now: str) -> None:
    """Persist one row per OBSERVED check. Unevaluated checks are never written."""
    payload = json.dumps(detail, sort_keys=True)
    for check_id, passed in observed.items():
        conn.execute(
            "INSERT INTO zig_dast_scans "
            "(application, scan_type, check_id, check_name, passed, severity, detail, scanned_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (application, scan_type, check_id, catalog[check_id]["name"],
             int(passed), catalog[check_id]["severity"], payload, now),
        )


def _statuses(catalog: dict, observed: dict[str, bool]) -> dict[str, str]:
    """Per-check status map covering the whole catalog, including unknowns."""
    return {
        cid: (CHECK_PASS if observed[cid] else CHECK_FAIL) if cid in observed else CHECK_UNKNOWN
        for cid in catalog
    }


def run_dast_scan(application: str, target_url: str = "",
                  findings: dict | None = None) -> dict[str, Any]:
    """Record a DAST scan's findings against an application (OWASP Top 10 probes).

    Args:
        application: application identifier.
        target_url: URL the external scanner exercised. Recorded as scan evidence;
            an empty value is reported back as ``target_url_missing`` because a
            finding set with no target cannot be reproduced.
        findings: ``{check_id: passed(bool)}`` from a live scanner. Only supplied
            checks are evaluated — **omitted checks are ``unknown``, not passing.**

    Returns:
        ``{application, scan_status, target_url, target_url_missing, dast_score,
        cat1_failures, evaluated, total_checks, unknown_checks, results}``.
        ``dast_score`` is None when nothing was observed; ``results`` maps every
        catalog check to pass/fail/unknown.
    """
    observed = _observed(DAST_CHECKS, findings)
    scan_status = _coverage_status(DAST_CHECKS, observed)
    detail = {
        "target_url": target_url or None,
        "scan_status": scan_status,
        "evidence": "external-scanner-findings" if observed else "none",
    }
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        _ensure_tables(conn)
        _record(conn, application, "dast", DAST_CHECKS, observed, detail, now)
        conn.commit()
    finally:
        conn.close()

    return {
        "application": application,
        "scan_status": scan_status,
        "target_url": target_url or None,
        "target_url_missing": bool(observed) and not target_url,
        "dast_score": _score(DAST_CHECKS, observed),
        "cat1_failures": _cat1_failures(DAST_CHECKS, observed),
        "evaluated": len(observed),
        "total_checks": len(DAST_CHECKS),
        "unknown_checks": [cid for cid in DAST_CHECKS if cid not in observed],
        "results": _statuses(DAST_CHECKS, observed),
    }


def run_runtime_check(application: str, posture: dict | None = None) -> dict[str, Any]:
    """Record runtime protection posture (WAF/RASP/TLS/rate-limit/secrets).

    Same evidence rule as :func:`run_dast_scan` — a posture key that is not
    supplied is ``unknown``, never ``pass``.
    """
    observed = _observed(RUNTIME_CHECKS, posture)
    scan_status = _coverage_status(RUNTIME_CHECKS, observed)
    detail = {
        "scan_status": scan_status,
        "evidence": "runtime-posture-probe" if observed else "none",
    }
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        _ensure_tables(conn)
        _record(conn, application, "runtime", RUNTIME_CHECKS, observed, detail, now)
        conn.commit()
    finally:
        conn.close()

    return {
        "application": application,
        "scan_status": scan_status,
        "runtime_score": _score(RUNTIME_CHECKS, observed),
        "cat1_failures": _cat1_failures(RUNTIME_CHECKS, observed),
        "evaluated": len(observed),
        "total_checks": len(RUNTIME_CHECKS),
        "unknown_checks": [cid for cid in RUNTIME_CHECKS if cid not in observed],
        "results": _statuses(RUNTIME_CHECKS, observed),
    }


def evaluate_gate(application: str, dast_findings: dict | None = None,
                  runtime_posture: dict | None = None,
                  target_url: str = "") -> dict[str, Any]:
    """Record DAST + runtime findings and return the promotion gate decision.

    Decision order (fail-closed):
      1. ``blocked`` — any CAT-I failure observed, or a computable combined score
         below :data:`GATE_PASS_THRESHOLD`.
      2. ``unknown`` — any check unevaluated. Never promotes; there is no evidence
         to promote on.
      3. ``pass`` — full coverage, no CAT-I failure, combined score at/above
         threshold.
    """
    dast = run_dast_scan(application, target_url=target_url, findings=dast_findings)
    runtime = run_runtime_check(application, posture=runtime_posture)

    cat1_failures = dast["cat1_failures"] + runtime["cat1_failures"]
    if dast["dast_score"] is None or runtime["runtime_score"] is None:
        combined = None
    else:
        combined = round(0.6 * dast["dast_score"] + 0.4 * runtime["runtime_score"], 4)

    blockers: list[str] = []
    if cat1_failures > 0:
        blockers.append(f"{cat1_failures} CAT-I failure(s)")
    if combined is not None and combined < GATE_PASS_THRESHOLD:
        blockers.append(f"combined score {combined} < {GATE_PASS_THRESHOLD}")

    evidence_gaps: list[str] = []
    if dast["scan_status"] == SCAN_NOT_RUN:
        evidence_gaps.append("no DAST scan evidence")
    elif dast["unknown_checks"]:
        evidence_gaps.append(f"{len(dast['unknown_checks'])} DAST check(s) unevaluated")
    if runtime["scan_status"] == SCAN_NOT_RUN:
        evidence_gaps.append("no runtime posture evidence")
    elif runtime["unknown_checks"]:
        evidence_gaps.append(f"{len(runtime['unknown_checks'])} runtime check(s) unevaluated")
    if dast["target_url_missing"]:
        evidence_gaps.append("DAST findings supplied without a target_url")

    if blockers:
        gate_status = GATE_BLOCKED
    elif evidence_gaps:
        gate_status = GATE_UNKNOWN
    else:
        gate_status = GATE_PASS

    # Evidence gaps are stored alongside blockers so the persisted row explains itself.
    recorded = blockers + evidence_gaps
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute(
            "INSERT INTO zig_dast_gate_results "
            "(application, gate_status, dast_score, runtime_score, cat1_failures, blockers_json, evaluated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (application, gate_status, dast["dast_score"], runtime["runtime_score"],
             cat1_failures, json.dumps(recorded), now),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "application": application,
        "gate_status": gate_status,
        "combined_score": combined,
        "dast_score": dast["dast_score"],
        "runtime_score": runtime["runtime_score"],
        "cat1_failures": cat1_failures,
        "dast_scan_status": dast["scan_status"],
        "runtime_scan_status": runtime["scan_status"],
        "blockers": blockers,
        "evidence_gaps": evidence_gaps,
    }


def deploy_dast_gates(applications: list[str] | None = None,
                      findings_by_app: dict[str, dict] | None = None) -> dict[str, Any]:
    """Evaluate DAST gates across applications and report ZIG activity honestly.

    Args:
        applications: application ids to evaluate.
        findings_by_app: optional ``{app: {"dast": {...}, "runtime": {...},
            "target_url": str}}`` scanner output. Apps with no entry are evaluated
            with no evidence and therefore land on ``gate_status="unknown"``.

    Activity p2-21 is marked ``complete`` only when every application produced a
    real decision (``pass``/``blocked``). With any ``unknown`` the activity is
    ``in_progress`` and the evidence note says so — the gate framework is deployed
    but no scanner is wired to it.
    """
    apps = applications or ["icdev-dashboard", "icdev-api", "security-canvas"]
    supplied = findings_by_app or {}

    results = []
    for app in apps:
        app_findings = supplied.get(app, {})
        results.append(evaluate_gate(
            app,
            dast_findings=app_findings.get("dast"),
            runtime_posture=app_findings.get("runtime"),
            target_url=app_findings.get("target_url", ""),
        ))

    passing = sum(1 for r in results if r["gate_status"] == GATE_PASS)
    blocked = sum(1 for r in results if r["gate_status"] == GATE_BLOCKED)
    unknown = sum(1 for r in results if r["gate_status"] == GATE_UNKNOWN)

    from tools.security_canvas.zig_activity_tracker import set_activity_status

    framework = (
        f"DAST + runtime testing gate framework deployed. {len(DAST_CHECKS)} OWASP "
        f"Top-10 DAST probes + {len(RUNTIME_CHECKS)} runtime posture checks "
        f"(WAF/RASP/TLS/rate-limit/secrets) defined. Gate blocks on any CAT-I "
        f"failure or combined score <{GATE_PASS_THRESHOLD}, and returns 'unknown' "
        f"(never 'pass') when a check has no observation. "
        f"Module: dast_runtime_gates.py"
    )
    if unknown:
        activity_status = "in_progress"
        evidence = (
            f"{framework} NOT YET OPERATIONAL: {unknown}/{len(apps)} application(s) "
            f"have no scan evidence — no external DAST scanner is wired to this gate. "
            f"{passing} passing, {blocked} blocked."
        )
    else:
        activity_status = "complete"
        evidence = (
            f"{framework} {passing}/{len(apps)} applications passing, {blocked} blocked, "
            f"all with full-coverage scan evidence."
        )
    set_activity_status("zig-act-p2-21", activity_status, evidence, "dast_runtime_gates")

    return {
        "applications": len(apps),
        "passing": passing,
        "blocked": blocked,
        "unknown": unknown,
        "activity_status": activity_status,
        "results": results,
    }


def get_gate_summary() -> dict[str, Any]:
    """DAST gate result summary keyed by gate status (pass/blocked/unknown)."""
    conn = get_connection()
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT gate_status, COUNT(*) as cnt FROM zig_dast_gate_results GROUP BY gate_status"
        ).fetchall()
        return {r["gate_status"]: r["cnt"] for r in rows}
    finally:
        conn.close()
