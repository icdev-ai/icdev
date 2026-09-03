# CUI // SP-CTI
"""Device Compliance Scanner — ZIG Device Pillar, Activity p1-09.

Automated compliance scanning of managed endpoints against CIS Controls
and STIG baselines. Integrates with the device trust adapter (CrowdStrike
Falcon stub) and persists per-device compliance scores to the security
canvas DB.

THREE VERDICTS, NEVER TWO (rmf-zt-01)
-------------------------------------
Every check used to be ``passed = bool(ctx.get(check_id, True))``. A check
nobody probed reported a PASS — so a device the scanner knew nothing about
scored 100% compliant, and the ZIG device-pillar maturity number was computed
over it. Measured on the SQLite canvas corpus 2026-09-02: **108 of 108 recorded
checks passed, all six devices scored 1.0, and not one caller of
``scan_device`` anywhere in the tree supplied a single probe.** Re-derive with
``python -m tools.security_canvas.zt_verdict_survey``.

A check now returns ``pass`` | ``fail`` | ``unknown``:

  pass / fail   a probe answered, or the device-trust adapter returned a
                MEASURED posture for a derived check.
  unknown       nothing measured it. Excluded from BOTH the numerator and the
                denominator of every score, and reported separately under
                ``unknown_checks`` — never merged into ``gaps``, because a gap
                is a known deficiency with a remediation and an unknown is an
                unmeasured control with an instrumentation task. They are
                different findings and they have different fixes.

Consequently ``compliance_score`` is ``None`` — never 0.0 and never 1.0 — when
nothing was measured, and ``overall_pass`` is ``None`` rather than True. A
scanner that cannot tell "measured clean" from "never looked" is the defect,
not the reporting of it.

``health_score`` follows the same rule. It used to fall back to the constant
``0.75`` whenever the adapter returned no number, which is why all six
registry rows read 0.75 — a constant wearing the name of a measurement. It is
``None`` when the posture was not evaluated, and ``health_basis`` says which.

FAIL-CLOSED IS A MEASURED REFUSAL, NOT AN UNKNOWN. When the posture is
explicitly ``unknown`` (the CrowdStrike stub) and the zero-trust stub gate is
NOT enabled, the device is denied: score 0.0, ``overall_pass`` False, with
``score_basis`` naming the refusal so the zero is never read as a measurement
of the checks themselves.

NIST 800-53: CM-6, CM-7, SI-2, SI-4
ZIG Activity: zig-act-p1-09 (Deploy automated device compliance scanning)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.security_canvas.db.init_db import get_connection
from tools.security.device_trust import verify_device_posture, DeviceTrustResult
from tools.security.stub_gate import record_stub_decision, stub_allowed
from tools.assets.identity import zig_device_id

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

#: The only three values a check may carry. A boolean cannot express the third,
#: which is the whole point of this module's rmf-zt-01 change.
VERDICTS = (PASS, FAIL, UNKNOWN)

#: Posture statuses that represent a REAL evaluation by the device-trust
#: adapter. Anything else ("unknown" from the stub, "" when device trust is not
#: required) means nothing was measured, so the checks derived from it are
#: ``unknown`` rather than inheriting a fabricated default.
MEASURED_POSTURE = ("healthy", "unhealthy")

# ---------------------------------------------------------------------------
# Compliance baseline definitions
# ---------------------------------------------------------------------------

CIS_CONTROL_CHECKS = {
    "cc-01-inventory":      "All devices appear in authoritative inventory",
    "cc-02-software-inv":   "Software inventory current within 24 h",
    "cc-03-data-protect":   "Data-at-rest encryption enabled",
    "cc-04-vuln-mgmt":      "Open CVEs ≤ CVSS 7.0 are remediated within 30 days",
    "cc-05-account-mgmt":   "No local admin accounts outside approved list",
    "cc-06-access-control": "MFA enforced for all interactive logons",
    "cc-07-continuous-mon": "EDR sensor reporting within last 60 minutes",
    "cc-08-incident-resp":  "Device enrolled in incident-response playbook",
    "cc-09-network-seg":    "Device connected only via approved network segment",
    "cc-10-data-recovery":  "Backup agent installed and reporting",
}

STIG_CHECKS = {
    "stig-os-patch":   "OS patch level within 30-day SLA (CAT-II)",
    "stig-firewall":   "Host-based firewall enabled and configured (CAT-I)",
    "stig-antivirus":  "Antivirus/EDR active with current signatures (CAT-I)",
    "stig-screen-lock":"Screen lock enforced ≤ 15 min (CAT-II)",
    "stig-usb-control":"USB mass-storage blocked or audited (CAT-II)",
    "stig-bitlocker":  "Full-disk encryption active (CAT-I)",
    "stig-tpm":        "TPM present, enabled, and measured (CAT-II)",
    "stig-logging":    "Audit logging enabled and forwarded to SIEM (CAT-I)",
}

#: Checks whose verdict comes from the device-trust adapter rather than from a
#: caller-supplied probe. Declared here so ``zt_verdict_survey`` reads the same
#: list the scanner applies instead of keeping a second copy that can drift.
DERIVED_CHECKS = frozenset({"cc-07-continuous-mon", "stig-antivirus"})

SEVERITY_WEIGHTS = {"CAT-I": 1.0, "CAT-II": 0.6, "CAT-III": 0.3}
_HEALTH_THRESHOLD = 0.70  # min score to consider device compliant

#: The dev fallback that used to stand in for an unmeasured health score. Kept
#: as a named constant ONLY so the tests that pin its removal can name it.
_LEGACY_DEV_HEALTH_SCORE = 0.75


def _severity_of(desc: str) -> str:
    return desc.split("(")[-1].rstrip(")") if "(" in desc else "CAT-II"


def _column_names(conn, table: str) -> set[str]:
    """The columns the LIVE table has, whatever the backend.

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a canvas
    database created before ``verdict`` existed keeps the old shape while the
    DDL here moves on — and the INSERT then raises on every scan. Feature-detect
    rather than assume (CLAUDE.md: "Every column in an INSERT must exist in the
    LIVE schema, not just in the source DDL").
    """
    backend = getattr(conn, "_backend", "sqlite")
    try:
        if backend == "postgresql":
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,),
            ).fetchall()
            return {dict(r)["column_name"] for r in rows}
        rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()  # nosec B608 - fixed literal
        return {r[1] for r in rows}
    except Exception:  # noqa: BLE001 - an unreadable schema is handled by the caller
        return set()


def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_device_registry (
            device_id        TEXT PRIMARY KEY,
            hostname         TEXT,
            os_platform      TEXT,
            mdm_enrolled     INTEGER DEFAULT 0,
            edr_installed    INTEGER DEFAULT 0,
            nac_authorized   INTEGER DEFAULT 0,
            last_seen_at     TEXT,
            health_score     REAL DEFAULT 0.0,
            compliance_score REAL DEFAULT 0.0,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zig_device_compliance_scans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id    TEXT NOT NULL,
            scan_type    TEXT NOT NULL,
            check_id     TEXT NOT NULL,
            check_name   TEXT NOT NULL,
            passed       INTEGER NOT NULL DEFAULT 0,
            verdict      TEXT NOT NULL DEFAULT 'unknown',
            severity     TEXT,
            detail       TEXT,
            scanned_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # rmf-zt-01: an existing canvas DB predates `verdict`. Add it in place —
    # the alternative is an INSERT that raises on every scan of every device
    # that has ever been scanned on this deployment.
    if "verdict" not in _column_names(conn, "zig_device_compliance_scans"):
        # PostgreSQL gets IF NOT EXISTS: a failed DDL there aborts the whole
        # transaction, so losing a race with a concurrent scanner would take
        # the entire scan down with it. SQLite has no such clause and the
        # feature detection above is the guard.
        if getattr(conn, "_backend", "sqlite") == "postgresql":
            conn.execute(
                "ALTER TABLE zig_device_compliance_scans "
                "ADD COLUMN IF NOT EXISTS verdict TEXT NOT NULL DEFAULT 'unknown'"
            )
        else:
            try:
                conn.execute(
                    "ALTER TABLE zig_device_compliance_scans "
                    "ADD COLUMN verdict TEXT NOT NULL DEFAULT 'unknown'"
                )
            except Exception:  # noqa: BLE001 - a concurrent scanner won the race
                pass
    conn.commit()


def _device_fingerprint(hostname: str) -> str:
    # rmf-ident-01: ONE definition of the ZIG fingerprint rule. This name is
    # kept because callers and tests import it; the rule itself lives in
    # tools/assets/identity.py, which is what asset_identity.zig_device_id
    # resolves onto. A second copy here could drift from the key it claims.
    return zig_device_id(hostname)


def _probe_verdict(ctx: dict, check_id: str) -> str:
    """``pass`` | ``fail`` | ``unknown`` from caller-supplied probe data.

    An ABSENT key is ``unknown`` — that is the whole fix. An explicit ``None``
    is also ``unknown``: a probe that ran and could not determine the answer is
    exactly as unmeasured as one that never ran, and forcing it to a boolean
    would put the fail-open default straight back.
    """
    if check_id not in ctx:
        return UNKNOWN
    value = ctx[check_id]
    if value is None:
        return UNKNOWN
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in VERDICTS:
            return lowered
    return PASS if bool(value) else FAIL


def _score(verdicts: dict[str, str], weights: dict[str, float] | None = None):
    """``(score, measured, unknown)`` over MEASURED verdicts only.

    ``score`` is ``None`` — never 0.0 and never 1.0 — when the denominator is
    empty. ``if measured else 100.0`` here would be exactly the defect
    ``args/perfect_score_gate.yaml`` was ratcheted to zero to stop.
    """
    total = 0.0
    passed = 0.0
    measured = 0
    unknown = 0
    for check_id, verdict in verdicts.items():
        if verdict == UNKNOWN:
            unknown += 1
            continue
        weight = (weights or {}).get(check_id, 1.0)
        total += weight
        measured += 1
        if verdict == PASS:
            passed += weight
    score = round(passed / total, 4) if total else None
    return score, measured, unknown


def scan_device(hostname: str, os_platform: str = "linux",
                context: dict | None = None) -> dict[str, Any]:
    """Run CIS + STIG compliance scan against a single managed device.

    Uses the device trust adapter for real-time posture, then evaluates each
    check against the ``context`` dict of probe results. A check with no probe
    behind it is ``unknown`` and is excluded from every score.

    Returns:
        {device_id, hostname, health_score, health_basis, compliance_score,
         score_basis, cis_results, stig_results, overall_pass, gaps,
         unknown_checks, coverage, stub, scanned_at}
    """
    ctx = context or {}
    device_id = _device_fingerprint(hostname)
    now = datetime.now(timezone.utc).isoformat()

    trust: DeviceTrustResult = verify_device_posture(device_id)
    posture_status = getattr(trust, "status", "") or "not_evaluated"
    posture_measured = posture_status in MEASURED_POSTURE
    # "unknown" is the CrowdStrike stub specifically: the adapter TRIED and only
    # has a simulated answer. "not_evaluated" is device trust switched off. The
    # stub gate speaks to the first; both leave the derived checks unmeasured.
    posture_unknown = posture_status == "unknown"
    stub_ok = stub_allowed()

    # rmf-zt-01: ICDEV_ZT_ALLOW_STUB is a security posture decision, so every
    # scan that consults it leaves a record — the permit leg AND the refusal
    # leg. A surface that records only its positive outcome can answer "was
    # this permitted?" but never "was this evaluated?".
    stub_audit: dict[str, Any] | None = None
    if posture_unknown:
        stub_audit = record_stub_decision(
            component="device_compliance_scanner",
            subject=device_id,
            honored=stub_ok,
            detail={
                "hostname": hostname,
                "os_platform": os_platform,
                "posture_status": posture_status,
                "posture_reason": getattr(trust, "reason", ""),
            },
        )

    if posture_measured:
        health_score: float | None = float(trust.health_score)
        health_basis = "measured"
    elif posture_unknown and not stub_ok:
        # A deny is a verdict, not a measurement — say which.
        health_score = 0.0
        health_basis = "fail_closed_unknown_posture"
    else:
        # NOT 0.75. Nothing measured this device's health; a number here is
        # invented, and it is the number the six live registry rows carried.
        health_score = None
        health_basis = (
            "unmeasured_stub_honored" if posture_unknown else "unmeasured_posture_not_evaluated"
        )

    conn = get_connection()
    try:
        _ensure_tables(conn)
        has_verdict_column = "verdict" in _column_names(conn, "zig_device_compliance_scans")

        cis_results: dict[str, str] = {}
        stig_results: dict[str, str] = {}
        gaps: list[str] = []
        unknown_checks: list[str] = []

        def _record(scan_type: str, check_id: str, desc: str, verdict: str, severity: str):
            if verdict == FAIL:
                gaps.append(f"{scan_type.upper()} {check_id}: {desc}")
            elif verdict == UNKNOWN:
                unknown_checks.append(f"{scan_type.upper()} {check_id}: {desc}")
            columns = "device_id, scan_type, check_id, check_name, passed, severity, scanned_at"
            values: tuple = (
                device_id, scan_type, check_id, desc,
                int(verdict == PASS), severity, now,
            )
            if has_verdict_column:
                # `passed` is LEGACY and lossy — it cannot spell `unknown`, so it
                # records 0 there, which is fail-CLOSED rather than fail-open.
                # `verdict` is the authoritative column; read that one.
                columns = (
                    "device_id, scan_type, check_id, check_name, passed, verdict, "
                    "severity, scanned_at"
                )
                values = (
                    device_id, scan_type, check_id, desc,
                    int(verdict == PASS), verdict, severity, now,
                )
            placeholders = ",".join(["%s"] * len(values))
            conn.execute(
                f"INSERT INTO zig_device_compliance_scans ({columns}) "  # nosec B608 - fixed column literals
                f"VALUES ({placeholders})",
                values,
            )

        # --- CIS Controls evaluation ---
        for check_id, desc in CIS_CONTROL_CHECKS.items():
            if check_id == "cc-07-continuous-mon":
                # Derived from the adapter. Unmeasured posture => unknown; the
                # old code read a fabricated last_seen of 0 as "reporting now".
                verdict = (
                    (PASS if trust.last_seen_seconds_ago < 3600 else FAIL)
                    if posture_measured else UNKNOWN
                )
            else:
                verdict = _probe_verdict(ctx, check_id)
            cis_results[check_id] = verdict
            _record("cis", check_id, desc, verdict, "CIS")

        # --- STIG checks evaluation ---
        for check_id, desc in STIG_CHECKS.items():
            severity = _severity_of(desc)
            if check_id == "stig-antivirus":
                verdict = (PASS if trust.trusted else FAIL) if posture_measured else UNKNOWN
            else:
                verdict = _probe_verdict(ctx, check_id)
            stig_results[check_id] = verdict
            _record("stig", check_id, desc, verdict, severity)

        cis_score, cis_measured, cis_unknown = _score(cis_results)
        stig_weights = {
            check_id: SEVERITY_WEIGHTS.get(_severity_of(desc), 0.3)
            for check_id, desc in STIG_CHECKS.items()
        }
        stig_score, stig_measured, stig_unknown = _score(stig_results, stig_weights)

        if cis_score is not None and stig_score is not None:
            compliance_score: float | None = round(0.5 * cis_score + 0.5 * stig_score, 4)
            score_basis = "blended"
        elif cis_score is not None:
            compliance_score, score_basis = cis_score, "cis_only"
        elif stig_score is not None:
            compliance_score, score_basis = stig_score, "stig_only"
        else:
            compliance_score, score_basis = None, "unmeasured"

        # Fail closed on unknown device posture (CrowdStrike stub unavailable):
        # a device we could not verify must not be reported as compliant. This
        # 0.0 is a POLICY VERDICT, and `score_basis` says so — it is not a
        # measurement of the checks, which were unknown either way.
        if posture_unknown and not stub_ok:
            compliance_score = 0.0
            score_basis = "fail_closed_unknown_posture"
            gaps.append(
                "Device posture UNKNOWN (CrowdStrike stub unavailable) — fail closed; "
                "set ICDEV_ZT_ALLOW_STUB to permit in dev"
            )

        total_checks = len(cis_results) + len(stig_results)
        measured_checks = cis_measured + stig_measured
        overall_pass = (
            None if compliance_score is None else compliance_score >= _HEALTH_THRESHOLD
        )

        conn.execute(
            """INSERT INTO zig_device_registry
               (device_id, hostname, os_platform, health_score, compliance_score, last_seen_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(device_id) DO UPDATE SET
               health_score=excluded.health_score,
               compliance_score=excluded.compliance_score,
               last_seen_at=excluded.last_seen_at,
               updated_at=excluded.updated_at""",
            (device_id, hostname, os_platform, health_score, compliance_score, now, now),
        )
        conn.commit()

        return {
            "device_id": device_id,
            "hostname": hostname,
            "health_score": health_score,
            "health_basis": health_basis,
            "posture_status": posture_status,
            "compliance_score": compliance_score,
            "score_basis": score_basis,
            "cis_results": cis_results,
            "stig_results": stig_results,
            "overall_pass": overall_pass,
            "gaps": gaps,
            "unknown_checks": unknown_checks,
            "coverage": {
                "total_checks": total_checks,
                "measured_checks": measured_checks,
                "unknown_checks": cis_unknown + stig_unknown,
                # None, never 100.0, over an empty catalogue.
                "measured_pct": (
                    round(100.0 * measured_checks / total_checks, 1)
                    if total_checks else None
                ),
            },
            "stub": {
                "allowed": stub_ok,
                "consulted": posture_unknown,
                "audit": stub_audit,
            },
            "scanned_at": now,
        }
    finally:
        conn.close()


def run_fleet_scan(hostnames: list[str], context_by_host: dict | None = None) -> dict[str, Any]:
    """Scan a fleet of managed devices and record ZIG activity completion.

    ``context_by_host`` maps a hostname to that device's probe results. Absent,
    every check on every device is ``unknown`` — which is now what the report
    says, instead of a 100% fleet compliance score over nothing.
    """
    probes = context_by_host or {}
    results = [scan_device(h, context=probes.get(h)) for h in hostnames]

    # THREE counts, never two. An unmeasured device is not a failing one, and
    # folding it into `failing` would make an uninstrumented fleet read as a
    # broken fleet — a different problem with a different fix.
    passing = sum(1 for r in results if r["overall_pass"] is True)
    failing = sum(1 for r in results if r["overall_pass"] is False)
    unmeasured = sum(1 for r in results if r["overall_pass"] is None)

    measured_scores = [
        r["compliance_score"] for r in results if r["compliance_score"] is not None
    ]
    fleet_score = (
        round(sum(measured_scores) / len(measured_scores), 4) if measured_scores else None
    )

    # Mark ZIG activity complete — scanning infrastructure is deployed. The
    # evidence STATES its own coverage: an activity completed on a sweep that
    # measured nothing is the fail-open claim one layer up from the checks.
    from tools.security_canvas.zig_activity_tracker import set_activity_status
    if fleet_score is None:
        status = "in_progress"
        evidence = (
            f"Device compliance scanner deployed; fleet of {len(results)} device(s) "
            f"scanned and NOTHING WAS MEASURED — every CIS/STIG check returned "
            f"unknown (no probe data, device posture "
            f"{results[0]['posture_status'] if results else 'unmeasured'}). "
            f"Not complete: an uninstrumented sweep is not evidence of compliance. "
            f"Scanner: device_compliance_scanner.py"
        )
    else:
        status = "complete"
        evidence = (
            f"Device compliance scanner deployed. Fleet: {len(results)} devices scanned, "
            f"{passing} passing, {failing} failing, {unmeasured} unmeasured "
            f"({fleet_score * 100:.1f}% mean compliance score over the measured ones). "
            f"CIS Controls + STIG baselines evaluated. Scanner: device_compliance_scanner.py"
        )
    set_activity_status("zig-act-p1-09", status, evidence, "device_compliance_scanner")

    return {
        "fleet_size": len(results),
        "passing": passing,
        "failing": failing,
        "unmeasured": unmeasured,
        "fleet_compliance_score": fleet_score,
        "activity_status": status,
        "devices": results,
    }


def get_compliance_summary() -> dict[str, Any]:
    """Return fleet-wide compliance summary from stored scan results.

    A device whose ``compliance_score`` is NULL was never measured. It is
    excluded from BOTH sides of ``compliance_rate`` and counted under
    ``unmeasured`` — leaving it in the denominator would report an
    uninstrumented fleet as a non-compliant one.
    """
    conn = get_connection()
    try:
        _ensure_tables(conn)
        total = conn.execute("SELECT COUNT(*) FROM zig_device_registry").fetchone()[0]
        measured = conn.execute(
            "SELECT COUNT(*) FROM zig_device_registry WHERE compliance_score IS NOT NULL"
        ).fetchone()[0]
        compliant = conn.execute(
            "SELECT COUNT(*) FROM zig_device_registry "
            "WHERE compliance_score IS NOT NULL AND compliance_score >= %s",
            (_HEALTH_THRESHOLD,),
        ).fetchone()[0]
        avg_score = conn.execute(
            "SELECT AVG(compliance_score) FROM zig_device_registry "
            "WHERE compliance_score IS NOT NULL"
        ).fetchone()[0]
        return {
            "total_devices": total,
            "measured_devices": measured,
            "unmeasured_devices": total - measured,
            "compliant": compliant,
            "non_compliant": measured - compliant,
            # None, never 0.0 and never 1.0, over an empty denominator.
            "compliance_rate": round(compliant / measured, 4) if measured else None,
            "avg_compliance_score": round(avg_score, 4) if avg_score is not None else None,
        }
    finally:
        conn.close()
