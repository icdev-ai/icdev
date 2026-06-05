#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.govcon.far_dfars_verifier.

Verifies acceptance criteria:
  1. Detects applicable FAR parts and DFARS clauses from solicitation text.
  2. Identifies required procurement documentation per detected clause.
  3. Computes pass / warn / fail gate (critical -> fail, high -> warn).
  4. Persists verification results to pg_far_dfars_verification table.
  5. CLI supports --gate, --save, --export, --list-clauses, --json, --format md.
"""
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Force SQLite (per feedback: do not use PG in pytest from a worktree)
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
os.environ.setdefault("ICDEV_DB_PATH", str(_REPO_ROOT / "data" / "icdev.db"))


# ── Module import ─────────────────────────────────────────────────────


def test_module_imports():
    from tools.govcon import far_dfars_verifier  # noqa: F401
    assert hasattr(far_dfars_verifier, "verify_initiative")
    assert hasattr(far_dfars_verifier, "detect_clauses")
    assert hasattr(far_dfars_verifier, "save_verification")


# ── Clause catalog completeness ──────────────────────────────────────


def test_clause_catalog_has_expected_clauses():
    from tools.govcon.far_dfars_verifier import _CLAUSE_CATALOG
    # Required key clauses per ICDEV™ FedRAMP/CMMC baseline
    required_keys = [
        "FAR-19.7",            # Subcontracting plan
        "FAR-22.10",           # SCA
        "FAR-25.103",          # Buy American
        "FAR-27.4",            # Data rights
        "FAR-30.2",            # CAS
        "FAR-37.1",            # PWS
        "FAR-42.15",           # CPARs
        "FAR-52.204-21",       # Basic safeguarding
        "FAR-52.222-50",       # Anti-trafficking
        "FAR-52.222-54",       # E-Verify
        "DFARS-252.204-7012",  # CDI / NIST 800-171
        "DFARS-252.204-7018",  # SPRS
        "DFARS-252.204-7021",  # CMMC
        "DFARS-252.225-7001",  # BAA DoD
        "DFARS-252.225-7012",  # Berry Amendment
        "DFARS-252.225-7047",  # Export control
        "DFARS-252.227-7013",  # Tech data rights
        "DFARS-252.239-7010",  # Cloud computing
        "DFARS-252.246-7007",  # Counterfeit parts
    ]
    for k in required_keys:
        assert k in _CLAUSE_CATALOG, f"Missing key clause: {k}"
        spec = _CLAUSE_CATALOG[k]
        assert spec["family"], f"{k} missing family"
        assert spec["title"], f"{k} missing title"
        assert spec["severity"] in ("low", "medium", "high", "critical"), \
            f"{k} invalid severity {spec['severity']}"
        assert isinstance(spec["required_docs"], list) and spec["required_docs"], \
            f"{k} missing required_docs"


# ── Explicit clause detection ────────────────────────────────────────


def test_explicit_clause_citation_detected():
    from tools.govcon.far_dfars_verifier import detect_clauses
    text = (
        "This contract incorporates DFARS 252.204-7012 and DFARS 252.225-7012. "
        "The contractor shall comply with FAR 52.222-54 (E-Verify) and FAR 19.7."
    )
    clauses = detect_clauses(text)
    ids = {c.clause_id for c in clauses}
    assert "DFARS-252.204-7012" in ids
    assert "DFARS-252.225-7012" in ids
    assert "FAR-52.222-54" in ids
    assert "FAR-19.7" in ids
    for cid in ("DFARS-252.204-7012", "DFARS-252.225-7012", "FAR-52.222-54", "FAR-19.7"):
        c = next(c for c in clauses if c.clause_id == cid)
        assert c.source == "explicit_citation"


# ── Trigger / keyword detection ─────────────────────────────────────


def test_keyword_trigger_detects_buy_american_and_cyber():
    from tools.govcon.far_dfars_verifier import detect_clauses
    text = (
        "All supplies shall comply with Buy American Act restrictions. "
        "The contractor must report cyber incidents per Covered Defense Information "
        "and NIST 800-171 requirements within 72 hours."
    )
    clauses = detect_clauses(text)
    ids = {c.clause_id for c in clauses}
    assert "FAR-25.103" in ids           # Buy American triggered
    assert "DFARS-252.204-7012" in ids   # CDI / 72-hour cyber triggered


def test_keyword_trigger_detects_cmmc_and_itar():
    from tools.govcon.far_dfars_verifier import detect_clauses
    text = (
        "The contractor shall maintain CMMC Level 2 certification with a C3PAO. "
        "All ITAR-controlled items require DDTC registration and a Technology Control Plan."
    )
    clauses = detect_clauses(text)
    ids = {c.clause_id for c in clauses}
    assert "DFARS-252.204-7021" in ids   # CMMC
    assert "DFARS-252.225-7047" in ids   # Export-controlled / ITAR


def test_part_reference_detection():
    from tools.govcon.far_dfars_verifier import _detect_top_level_parts
    text = "This is a FAR Part 12 commercial item acquisition under DFARS Part 225."
    parts = _detect_top_level_parts(text)
    assert "far_part_12" in parts
    assert "dfars_part_225" in parts


# ── Required documentation output ───────────────────────────────────


def test_required_documentation_listed_per_clause():
    from tools.govcon.far_dfars_verifier import verify_initiative
    r = verify_initiative(
        opportunity_id="opp-test-1",
        solicitation_text=(
            "This contract incorporates DFARS 252.204-7012 and DFARS 252.239-7010. "
            "CMMC Level 2 required."
        ),
    )
    # Critical clauses
    assert "DFARS-252.204-7012" in {c.clause_id for c in r.detected_clauses}
    assert "DFARS-252.239-7010" in {c.clause_id for c in r.detected_clauses}
    # Required docs pulled from catalog
    assert any("System Security Plan" in d for d in r.required_documentation)
    assert any("Plan of Action" in d for d in r.required_documentation)
    assert any("Cyber Incident Reporting" in d for d in r.required_documentation)
    assert any("FedRAMP" in d for d in r.required_documentation)


# ── Gate logic ───────────────────────────────────────────────────────


def test_gate_critical_clauses_fail():
    from tools.govcon.far_dfars_verifier import verify_initiative
    r = verify_initiative(
        opportunity_id="opp-crit",
        solicitation_text=(
            "DFARS 252.204-7012 NIST 800-171 CDI safeguarding applies. "
            "DFARS 252.239-7010 cloud computing requirements apply."
        ),
    )
    assert r.status == "fail"
    assert r.critical_clauses >= 2


def test_gate_high_severity_warns():
    from tools.govcon.far_dfars_verifier import verify_initiative
    r = verify_initiative(
        opportunity_id="opp-high",
        solicitation_text=(
            "FAR 19.7 subcontracting plan required. "
            "FAR 25.103 Buy American compliance required. "
            "FAR 22.10 service contract act wage determination applies."
        ),
    )
    assert r.status == "warn"
    assert r.critical_clauses == 0
    assert r.high_severity_clauses >= 3


def test_gate_clean_text_passes():
    from tools.govcon.far_dfars_verifier import verify_initiative
    r = verify_initiative(opportunity_id="opp-clean", solicitation_text="No clauses here.")
    assert r.status == "pass"
    assert r.total_clauses_detected == 0


# ── Documentation gap detection ──────────────────────────────────────


def test_documentation_gap_when_required_doc_missing():
    from tools.govcon.far_dfars_verifier import verify_initiative
    r = verify_initiative(
        opportunity_id="opp-gap",
        solicitation_text="DFARS 252.204-7012 applies.",
        provided_docs=["some unrelated doc", "cost proposal"],
    )
    # Gap should be the SSP/POAM/Cyber Incident Plan — none of these appear in provided_docs
    assert any("System Security Plan" in g for g in r.documentation_gaps)
    assert any("Plan of Action" in g for g in r.documentation_gaps)


def test_no_gap_when_required_doc_provided():
    from tools.govcon.far_dfars_verifier import verify_initiative
    r = verify_initiative(
        opportunity_id="opp-no-gap",
        solicitation_text="DFARS 252.204-7012 applies.",
        provided_docs=[
            "System Security Plan (SSP) per NIST 800-171 r2",
            "Plan of Action & Milestones (POAM) attached",
            "Cyber Incident Reporting Plan — 72-hour",
            "NDA / CUI markings per 32 CFR Part 2002 attached",
        ],
    )
    assert r.documentation_gaps == []


# ── Persistence ──────────────────────────────────────────────────────


def test_save_and_load_latest(tmp_path):
    from tools.govcon import far_dfars_verifier
    from tools.govcon.far_dfars_verifier import (
        verify_initiative,
        save_verification,
        load_latest_verification,
    )
    # Use temp DB
    db_path = tmp_path / "test_far_dfars.db"
    # Monkeypatch DB_PATH
    monkey = pytest.MonkeyPatch()
    monkey.setattr(far_dfars_verifier, "DB_PATH", db_path)
    r = verify_initiative(
        opportunity_id="opp-save",
        solicitation_text="DFARS 252.204-7012 applies.",
    )
    vid = save_verification(r)
    assert vid.startswith("fdv-")
    rec = load_latest_verification("opp-save")
    assert rec is not None
    assert rec["opportunity_id"] == "opp-save"
    assert rec["status"] == "fail"
    # cleanup
    monkey.undo()


# ── CLI smoke ────────────────────────────────────────────────────────


def test_cli_list_clauses_json(capsys):
    from tools.govcon.far_dfars_verifier import main
    rc = main(["--list-clauses", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert payload["clause_count"] > 50
    assert "DFARS-252.204-7012" in payload["clauses"]


def test_cli_list_clauses_filter_by_family(capsys):
    from tools.govcon.far_dfars_verifier import main
    rc = main(["--list-clauses", "--family", "dfars_part_204", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    # All returned clauses must be in dfars_part_204
    for cid, spec in payload["clauses"].items():
        assert spec["family"] == "dfars_part_204"


def test_cli_verify_and_gate(capsys):
    from tools.govcon.far_dfars_verifier import main
    rc = main([
        "--opportunity-id", "opp-cli-1",
        "--solicitation-text", "DFARS 252.204-7012 NIST 800-171 applies. FAR 19.7 subcontracting plan.",
        "--gate",
        "--json",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    # Critical clauses -> fail -> exit 1
    assert rc == 1
    assert payload["gate"] == "fail"
    assert payload["report"]["total_clauses_detected"] >= 2


def test_cli_clean_text_passes(capsys):
    from tools.govcon.far_dfars_verifier import main
    rc = main([
        "--opportunity-id", "opp-cli-clean",
        "--solicitation-text", "Hello world without any clauses.",
        "--gate",
        "--json",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["gate"] == "pass"
