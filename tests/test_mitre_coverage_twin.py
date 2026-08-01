# CUI // SP-CTI
"""Math-contract coverage for tools/observability_canvas/mitre_coverage_twin.py (obx-test-02).

Pins the *derivation math* of the coverage-gap engine against the unified
mitre_catalog — NOT the persistence wiring (obx-cov-01 will later change how
results are stored). Expected values are recomputed independently from
build_coverage_catalog() so the assertions track the catalog, not a snapshot.

Covers:
  * score_technique_coverage — covered / partial / gap state machine.
  * compute_gap_score — counts partition the catalog, weighted gap_score,
    coverage_pct, by_tactic tallies, cmp-baseline manual override.
  * generate_gap_report — remediation only for gap/partial, critical-first
    ordering, quick_wins ranking, total_remediation_items.

Where the module writes odc_gap_scores / odc_technique_coverage, a translating
StorageConnection on a temp SQLite DB is wired in so persistence succeeds (and
is asserted) without touching the shared canvas DB.

NIST 800-53: SI-4, CA-7, RA-5
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.observability_canvas import mitre_coverage_twin as mct  # noqa: E402
from tools.observability_canvas.mitre_catalog import build_coverage_catalog  # noqa: E402

_CATALOG = build_coverage_catalog()


@pytest.fixture
def persist_db(tmp_path, monkeypatch):
    """Temp canvas DB so _persist_gap_score writes succeed (and are observable)."""
    init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")
    from tools.db.storage import StorageConnection

    db_path = tmp_path / "twin.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(init_db_mod.SCHEMA)
    raw.commit()
    raw.close()

    def _conn():
        r = sqlite3.connect(str(db_path))
        r.row_factory = sqlite3.Row
        return StorageConnection(r, "sqlite")

    monkeypatch.setattr(init_db_mod, "get_connection", _conn, raising=True)
    return _conn


def _graph(source_types):
    return {"nodes": [{"id": f"n{i}", "type": t} for i, t in enumerate(source_types)], "edges": []}


def _expected_state(tdef, present):
    required = set(tdef["signal_sources"])
    have = required & set(present)
    minreq = tdef.get("min_sources", 1)
    if len(have) >= minreq and len(have) >= len(required):
        return "covered"
    if len(have) >= minreq or len(have) > 0:
        return "partial"
    return "gap"


# ── score_technique_coverage state machine ───────────────────────────────────

def test_score_covered_when_all_sources_present():
    tid = "T1078"  # signal_sources: src-iam, src-os-log ; min 1
    tdef = _CATALOG[tid]
    state, present, missing = mct.score_technique_coverage(
        tid, tdef, set(tdef["signal_sources"])
    )
    assert state == "covered"
    assert present == sorted(tdef["signal_sources"])
    assert missing == []


def test_score_partial_when_some_sources_present():
    tid = "T1078"
    tdef = _CATALOG[tid]
    one = tdef["signal_sources"][0]
    state, present, missing = mct.score_technique_coverage(tid, tdef, {one})
    assert state == "partial"
    assert present == [one]
    assert missing == sorted(set(tdef["signal_sources"]) - {one})


def test_score_gap_when_no_sources_present():
    tid = "T1078"
    tdef = _CATALOG[tid]
    state, present, missing = mct.score_technique_coverage(tid, tdef, {"src-unrelated"})
    assert state == "gap"
    assert present == []
    assert missing == sorted(tdef["signal_sources"])


# ── compute_gap_score math contracts ─────────────────────────────────────────

def test_counts_partition_the_catalog(persist_db):
    present = ["src-os-log", "src-iam"]
    result = mct.compute_gap_score("d-math", _graph(present))

    total = len(_CATALOG)
    assert result["total_techniques"] == total
    assert (
        result["covered_count"] + result["partial_count"] + result["gap_count"] == total
    )

    # Recompute expected counts independently from the catalog.
    exp = {"covered": 0, "partial": 0, "gap": 0}
    for tdef in _CATALOG.values():
        exp[_expected_state(tdef, present)] += 1
    assert result["covered_count"] == exp["covered"]
    assert result["partial_count"] == exp["partial"]
    assert result["gap_count"] == exp["gap"]


def test_gap_score_and_coverage_pct_formula(persist_db):
    present = ["src-os-log"]
    result = mct.compute_gap_score("d-formula", _graph(present))
    total = result["total_techniques"]

    expected_gap_score = round(
        (result["gap_count"] * 1.0 + result["partial_count"] * 0.5) / total, 3
    )
    assert result["gap_score"] == expected_gap_score
    assert 0.0 <= result["gap_score"] <= 1.0

    expected_pct = round(100 * result["covered_count"] / total, 1)
    assert result["coverage_pct"] == expected_pct


def test_by_tactic_tally_matches_per_technique(persist_db):
    present = ["src-endpoint", "src-network-log"]
    result = mct.compute_gap_score("d-tactic", _graph(present))

    rebuilt = {}
    for cov in result["coverage_by_technique"].values():
        bucket = rebuilt.setdefault(cov["tactic"], {"covered": 0, "partial": 0, "gap": 0})
        bucket[cov["coverage_state"]] += 1
    assert result["by_tactic"] == rebuilt

    # gap list equals the techniques scored 'gap'.
    gaps = sorted(
        tid for tid, cov in result["coverage_by_technique"].items()
        if cov["coverage_state"] == "gap"
    )
    assert result["technique_gaps"] == gaps


def test_cmp_baseline_manual_override_forces_covered(persist_db):
    # No source nodes => every technique is a gap, UNLESS overridden by baseline.
    override_tid = "T1055"
    graph = {
        "nodes": [
            {
                "id": "b", "type": "cmp-baseline",
                "config_json": {"techniques": [{"id": override_tid, "covered": True}]},
            }
        ],
        "edges": [],
    }
    result = mct.compute_gap_score("d-override", graph)
    assert result["coverage_by_technique"][override_tid]["coverage_state"] == "covered"
    assert result["coverage_by_technique"][override_tid]["gap_score"] == 0.0
    assert override_tid not in result["technique_gaps"]


def test_compute_gap_score_persists_rows(persist_db):
    mct.compute_gap_score("d-persist", _graph(["src-os-log", "src-iam"]))
    conn = persist_db()
    gap_rows = conn.execute(
        "SELECT COUNT(*) FROM odc_gap_scores WHERE design_id=%s", ("d-persist",)
    ).fetchone()[0]
    tech_rows = conn.execute(
        "SELECT COUNT(*) FROM odc_technique_coverage WHERE design_id=%s", ("d-persist",)
    ).fetchone()[0]
    conn.close()
    assert gap_rows == 1
    assert tech_rows == len(_CATALOG)


# ── generate_gap_report ──────────────────────────────────────────────────────

def test_gap_report_remediation_and_quick_wins(persist_db):
    present = ["src-os-log"]
    report = mct.generate_gap_report("d-report", _graph(present))

    # Remediation items only for gap/partial techniques.
    remediated_ids = {r["technique_id"] for r in report["remediation_steps"]}
    non_covered = {
        tid for tid, cov in report["coverage_by_technique"].items()
        if cov["coverage_state"] in ("gap", "partial")
    }
    assert remediated_ids == non_covered
    assert report["total_remediation_items"] == len(report["remediation_steps"])

    # Critical (gap) items sort before recommended (partial) items.
    priorities = [r["priority"] for r in report["remediation_steps"]]
    assert priorities == sorted(priorities, key=lambda p: 0 if p == "critical" else 1)

    # quick_wins ranked by descending techniques_unlocked.
    unlocked = [q["techniques_unlocked"] for q in report["quick_wins"]]
    assert unlocked == sorted(unlocked, reverse=True)


def test_gap_report_fully_covered_has_no_remediation(persist_db):
    # Union of every catalog signal source => nothing left uncovered.
    all_sources = sorted({s for t in _CATALOG.values() for s in t["signal_sources"]})
    report = mct.generate_gap_report("d-full", _graph(all_sources))
    assert report["gap_count"] == 0
    assert report["remediation_steps"] == []
    assert report["quick_wins"] == []
    assert report["coverage_pct"] == 100.0
