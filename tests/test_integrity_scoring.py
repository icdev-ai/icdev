# CUI // SP-CTI
"""Tests for the SIPA scoring + verdict stage (sipa-score-01).

Covers the acceptance criteria for ``tools/integrity/scoring.py``:

  * ``assess_score(findings, capabilities)`` — the pure DB-free core: severity
    points, the capability contribution cap, the threshold→verdict mapping, and
    the two hard QUARANTINE overrides (critical ``known_bad_signature`` /
    undisclosed ``process_exec``).
  * ``score(assessment_id)`` end-to-end against in-memory SQLite:
      - **benign => ALLOW** (clean / disclosed signals), and
      - **planted backdoor => QUARANTINE** with a rationale citing the
        capabilities,
    plus the append-only ``integrity_verdicts`` row (``decided_by='engine'``)
    and the verdict/score stamped back onto the assessment.
"""
import json
import sqlite3

from tools.integrity import scoring
from tools.integrity import constants
from tools.integrity.db import init_db as init_db_mod


# --------------------------------------------------------------------------- #
# Pure-core helpers
# --------------------------------------------------------------------------- #
def _finding(finding_type, severity, scanner="capability", capability_type=None,
             file_path="mod.py", line=1):
    detail = {}
    if capability_type:
        detail["capability_type"] = capability_type
    return {
        "source_scanner": scanner,
        "finding_type": finding_type,
        "severity": severity,
        "file_path": file_path,
        "line": line,
        "detail": detail,
    }


def _cap(cap_type, count=1):
    return [
        {
            "file_path": "mod.py",
            "function_name": None,
            "capability_type": cap_type,
            "line_start": i + 1,
            "risk_weight": constants.RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0),
        }
        for i in range(count)
    ]


# --------------------------------------------------------------------------- #
# Pure core — assess_score
# --------------------------------------------------------------------------- #
def test_no_signals_is_allow_zero():
    result = scoring.assess_score([], [])
    assert result["risk_score"] == 0
    assert result["verdict"] == scoring.ALLOW


def test_severity_points_track_multiplier():
    # critical finding alone = FINDING_BASE_POINTS * 1.0 = 50.
    crit = scoring.assess_score([_finding("dangerous_api", "critical")], [])
    assert crit["rationale"]["components"]["findings_total"] == 50.0
    # low finding = 50 * 0.2 = 10.
    low = scoring.assess_score([_finding("dangerous_api", "low")], [])
    assert low["rationale"]["components"]["findings_total"] == 10.0


def test_threshold_mapping_allow_review_quarantine():
    # One high undisclosed network finding (35) + network cap (0.85*8=6.8) ~= 41.8 -> REVIEW.
    review = scoring.assess_score(
        [_finding("undisclosed_capability", "high", scanner="reconciliation",
                  capability_type="network_egress")],
        _cap("network_egress"),
    )
    assert review["verdict"] == scoring.REVIEW

    # Two criticals saturate findings well past quarantine_at (70) -> QUARANTINE.
    quar = scoring.assess_score(
        [_finding("dangerous_api", "critical"), _finding("vuln_dependency", "critical")],
        [],
    )
    assert quar["risk_score"] >= 70
    assert quar["verdict"] == scoring.QUARANTINE

    # A single low finding (10) stays under review_at (40) -> ALLOW.
    allow = scoring.assess_score([_finding("dangerous_api", "low")], [])
    assert allow["verdict"] == scoring.ALLOW


def test_capability_contribution_is_capped():
    # Many high-weight capabilities, but NO findings: capped, must not quarantine.
    caps = []
    for cap_type in constants.CAPABILITY_TYPES:
        caps.extend(_cap(cap_type))
    result = scoring.assess_score([], caps)
    comp = result["rationale"]["components"]
    assert comp["capability_total"] == scoring.CAPABILITY_CONTRIBUTION_CAP
    assert comp["capability_capped"] is True
    assert result["risk_score"] <= scoring.CAPABILITY_CONTRIBUTION_CAP
    # Disclosed-but-capable tooling must never quarantine on capabilities alone.
    assert result["verdict"] != scoring.QUARANTINE


def test_forced_quarantine_on_critical_known_bad_signature():
    # Score is tiny (one info finding) but a critical malware signature forces QUARANTINE.
    findings = [
        _finding("known_bad_signature", "critical", scanner="semgrep"),
    ]
    result = scoring.assess_score(findings, [])
    assert result["verdict"] == scoring.QUARANTINE
    assert result["rationale"]["forced_quarantine"] is True
    assert any("known_bad_signature" in r for r in result["rationale"]["forced_reasons"])


def test_forced_quarantine_on_undisclosed_process_exec():
    # An undisclosed process_exec is high (35), below quarantine_at, yet forced.
    findings = [
        _finding("undisclosed_capability", "high", scanner="reconciliation",
                 capability_type="process_exec"),
    ]
    result = scoring.assess_score(findings, _cap("process_exec"))
    assert result["verdict"] == scoring.QUARANTINE
    assert result["rationale"]["forced_quarantine"] is True
    assert any("process_exec" in r for r in result["rationale"]["forced_reasons"])
    # The score-based verdict (pre-override) was NOT quarantine — proving the override fired.
    assert result["rationale"]["score_verdict"] != scoring.QUARANTINE


def test_config_thresholds_are_respected():
    cfg = {"thresholds": {"review_at": 5, "quarantine_at": 8}}
    # One low finding (10 pts) clears the lowered quarantine_at=8.
    result = scoring.assess_score([_finding("dangerous_api", "low")], [], cfg=cfg)
    assert result["verdict"] == scoring.QUARANTINE


def test_inverted_thresholds_are_normalized():
    cfg = {"thresholds": {"review_at": 80, "quarantine_at": 30}}
    # Swapped -> review_at=30, quarantine_at=80. A 50-pt critical lands in REVIEW.
    result = scoring.assess_score([_finding("dangerous_api", "critical")], [], cfg=cfg)
    assert result["verdict"] == scoring.REVIEW


# --------------------------------------------------------------------------- #
# DB round-trip helpers
# --------------------------------------------------------------------------- #
def _conn_with_assessment():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db_mod.init_db(conn)
    conn.execute(
        "INSERT INTO integrity_assessments "
        "(id, source_type, source_ref, mode, status, tenant_id, classification) "
        "VALUES (1, 'local', '/tmp/x', 'provenance_blind', 'quarantine', 'default', 'CUI')"
    )
    conn.commit()
    return conn


def _insert_finding(conn, finding_type, severity, scanner, capability_type=None,
                    file_path="mod.py", line=1):
    detail = {"capability_type": capability_type} if capability_type else {}
    conn.execute(
        "INSERT INTO integrity_findings "
        "(assessment_id, source_scanner, finding_type, severity, file_path, line, "
        "detail, tenant_id, classification) VALUES (1, ?, ?, ?, ?, ?, ?, 'default', 'CUI')",
        (scanner, finding_type, severity, file_path, line, json.dumps(detail)),
    )
    conn.commit()


def _insert_capability(conn, cap_type, file_path="mod.py", line=1):
    conn.execute(
        "INSERT INTO integrity_capabilities "
        "(assessment_id, file_path, function_name, capability_type, evidence, "
        "line_start, line_end, risk_weight, tenant_id, classification) "
        "VALUES (1, ?, NULL, ?, ?, ?, ?, ?, 'default', 'CUI')",
        (file_path, cap_type, json.dumps({}), line, line,
         constants.RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0)),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# score() end-to-end — benign => ALLOW
# --------------------------------------------------------------------------- #
def test_score_benign_assessment_is_allow():
    conn = _conn_with_assessment()
    # A benign tool: one disclosed capability, no findings.
    _insert_capability(conn, "filesystem")

    result = scoring.score(1, conn=conn)
    assert result["verdict"] == scoring.ALLOW
    assert result["risk_score"] < 40

    # Verdict row persisted, decided_by='engine'.
    row = conn.execute(
        "SELECT verdict, risk_score, decided_by, rationale FROM integrity_verdicts "
        "WHERE assessment_id = 1"
    ).fetchone()
    assert row["verdict"] == scoring.ALLOW
    assert row["decided_by"] == "engine"
    assert json.loads(row["rationale"])["verdict"] == scoring.ALLOW

    # Assessment row stamped + transitioned to 'assessed'.
    arow = conn.execute(
        "SELECT verdict, risk_score, status FROM integrity_assessments WHERE id = 1"
    ).fetchone()
    assert arow["verdict"] == scoring.ALLOW
    assert arow["status"] == "assessed"
    conn.close()


# --------------------------------------------------------------------------- #
# score() end-to-end — planted backdoor => QUARANTINE citing the capabilities
# --------------------------------------------------------------------------- #
def test_score_planted_backdoor_is_quarantine_with_capability_rationale():
    conn = _conn_with_assessment()
    # The decode-then-exec + exfil shape: socket + base64-decode-then-exec.
    for cap_type in ("network_egress", "dynamic_code", "obfuscation"):
        _insert_capability(conn, cap_type)
    # Reconciliation + signature findings the upstream stages would have written.
    _insert_finding(conn, "undisclosed_capability", "high", "reconciliation",
                    capability_type="network_egress")
    _insert_finding(conn, "dangerous_api", "critical", "reconciliation")
    _insert_finding(conn, "known_bad_signature", "critical", "semgrep")

    result = scoring.score(1, conn=conn)
    assert result["verdict"] == scoring.QUARANTINE

    rationale = result["rationale"]
    # Rationale cites the capabilities that drove the verdict.
    contributor_caps = {
        c.get("capability_type")
        for c in rationale["top_contributors"]
        if c.get("capability_type")
    }
    assert {"network_egress", "dynamic_code", "obfuscation"} & contributor_caps
    assert rationale["counts"]["capabilities"] == 3

    # Append-only verdict row written by the engine.
    vrow = conn.execute(
        "SELECT verdict, decided_by FROM integrity_verdicts WHERE assessment_id = 1"
    ).fetchone()
    assert vrow["verdict"] == scoring.QUARANTINE
    assert vrow["decided_by"] == "engine"
    conn.close()


def test_score_is_idempotent_appends_each_verdict():
    # Append-only: scoring twice writes two verdict rows (no UPDATE/DELETE).
    conn = _conn_with_assessment()
    _insert_capability(conn, "filesystem")
    scoring.score(1, conn=conn)
    scoring.score(1, conn=conn)
    n = conn.execute(
        "SELECT COUNT(*) FROM integrity_verdicts WHERE assessment_id = 1"
    ).fetchone()[0]
    assert n == 2
    conn.close()
