# CUI // SP-CTI
"""Tests for the Contract Clause Risk Engine (crx-gov-02).

Deterministic-first: given synthetic solicitation text (no real customer data),
assert the expected indicator hits, fired risk rules, and aggregate score. The
LLM narrative path is intentionally NOT exercised here — it is optional and must
degrade gracefully — so these tests are fully offline/air-gap safe.
"""
import importlib

import pytest

cre = importlib.import_module("tools.govcon.clause_risk_engine")


# --- Synthetic fixtures (public-knowledge phrasing only) -------------------

FFP_UNBOUNDED = (
    "This is a Firm-Fixed-Price (FFP) contract under FAR 52.212-4. "
    "The contractor shall perform all necessary services and other duties as "
    "assigned to accomplish the mission, without limitation."
)

WORST_CASE = (
    "The award is a firm-fixed-price contract. Liquidated damages of $5,000 per "
    "calendar day shall be assessed for each day of delay. The scope is open-ended "
    "and the contractor shall provide any and all support as required."
)

UNLIMITED_LIABILITY = (
    "The contractor accepts unlimited liability for any loss. Liability shall not "
    "be limited under this agreement."
)

CLEAN = (
    "This commercial-item order under FAR Part 12 has a fixed deliverable list "
    "with a defined statement of work and a liability cap equal to the contract value."
)


def test_rulebook_loads_and_compiles():
    book = cre.load_rulebook()
    assert book["indicators"], "expected indicators to load"
    assert book["rules"], "expected risk rules to load"
    # every rule cites a clause source (TRUST grounding)
    for r in book["rules"]:
        assert r["clause_source"], f"rule {r['id']} missing clause_source"
    for i in book["indicators"].values():
        assert i["clause_source"], f"indicator {i['id']} missing clause_source"


def test_indicator_detection_ffp_and_scope():
    hits = {h.indicator_id for h in cre.detect_indicators(FFP_UNBOUNDED)}
    assert "ind-ffp" in hits
    assert "ind-unbounded-scope" in hits


def test_ffp_unbounded_scope_rule_fires():
    findings = cre.evaluate_rules(FFP_UNBOUNDED)
    ids = {f.rule_id for f in findings}
    assert "rule-ffp-unbounded-scope" in ids
    fired = next(f for f in findings if f.rule_id == "rule-ffp-unbounded-scope")
    assert fired.severity == "high"
    assert fired.mitigation  # mitigation suggestion present
    assert fired.clause_source  # FAR/DFARS citation present
    assert fired.citation == "[source: rule:rule-ffp-unbounded-scope]"


def test_worst_case_is_critical():
    report = cre.assess(WORST_CASE, opportunity_id="opp-synth-1")
    assert report.risk_level == "critical"
    assert report.critical_findings >= 1
    ids = {f["rule_id"] for f in report.findings}
    assert "rule-ld-ffp-unbounded" in ids  # the compound critical rule


def test_unlimited_liability_is_critical():
    report = cre.assess(UNLIMITED_LIABILITY)
    ids = {f["rule_id"] for f in report.findings}
    assert "rule-unlimited-liability" in ids
    assert report.risk_level == "critical"


def test_clean_text_scores_none_or_low():
    report = cre.assess(CLEAN)
    # No toxic combinations -> no fired findings.
    assert report.findings == []
    assert report.risk_level == "none"
    assert report.risk_score == 0


def test_findings_sorted_by_severity():
    report = cre.assess(WORST_CASE)
    order = cre._SEVERITY_ORDER
    sevs = [order[f["severity"]] for f in report.findings]
    assert sevs == sorted(sevs, reverse=True)


def test_report_serializes_and_has_hash():
    report = cre.assess(FFP_UNBOUNDED, opportunity_id="opp-x")
    d = report.to_dict()
    assert d["opportunity_id"] == "opp-x"
    assert len(d["input_hash"]) == 64  # sha256 hex
    assert d["llm_narrative"] is None  # no LLM used


def test_markdown_export_contains_citations():
    report = cre.assess(WORST_CASE)
    md = cre.export_markdown(report)
    assert "CUI // SP-CTI" in md
    assert "[source: rule:" in md
    assert "Mitigation" in md


def test_assess_no_llm_leaves_narrative_none():
    # use_llm=False must never populate a narrative regardless of environment
    report = cre.assess(WORST_CASE, use_llm=False)
    assert report.llm_narrative is None


def test_persist_roundtrip(tmp_path, monkeypatch):
    """persist() self-creates its table and writes a row (offline sqlite)."""
    import sqlite3

    db = tmp_path / "crk.db"
    monkeypatch.setattr(cre, "_DB_PATH", db)

    class _Wrap:
        """Minimal shim so %s placeholders work against sqlite3 in isolation."""

        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, params=()):
            return self._c.execute(sql.replace("%s", "?"), params)

        def commit(self):
            return self._c.commit()

    raw = sqlite3.connect(str(db))
    wrapped = _Wrap(raw)
    monkeypatch.setattr(cre, "get_connection", lambda db_path=None: wrapped)

    report = cre.assess(WORST_CASE, opportunity_id="opp-persist")
    row_id = cre.persist(report)
    assert row_id.startswith("crk-")

    cur = raw.execute(
        "SELECT opportunity_id, risk_level, tenant_id, classification "
        "FROM govcon_clause_risk_assessments WHERE id = ?",
        (row_id,),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "opp-persist"
    assert row[1] == "critical"
    assert row[2] == "default"
    assert row[3] == "CUI"
    raw.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
