# CUI // SP-CTI
"""A pending migration becomes a card that says what to do (autonomy-dep-02).

autonomy-dep-01 measures whether this deployment is running the schema that
MERGED. Nothing consumed it — which is the defect autonomy-act-02 exists to
close, so this registers it as a fourth detector on that same path rather than
building a second one.

WHY IT MATTERS HERE. `code_staleness` reported every live process as `no
recorded code version` because autonomy-id-01's migration had never been
applied. The code was on main, its tests were green, and the capability produced
nothing. A detector that finds that and files nothing is no better.

THE THREE THINGS PINNED:
  1. ONE finding, not one per migration. Three pending migrations are one
     condition with three names; three cards would queue the same repair thrice.
  2. The FINGERPRINT is the set of pending versions, so the same pending set is
     the SAME finding (seen_count rises, no second card) and a different set is
     a new one.
  3. UNMEASURABLE clears nothing — a run that could not look must never read as
     "the migrations arrived".
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import detector_findings as df  # noqa: E402


def _report(state="pending", pending=(), ref="origin/main", root="/deploy"):
    return {
        "state": state, "ref": ref, "root": root,
        "on_branch_count": 76, "applied_count": 74,
        "pending_count": len(pending),
        "pending": [{"version": v, "name": f"{v}_thing"} for v in pending],
        "applied_not_on_branch": ["999"],
    }


# --------------------------------------------------------------------------- #
# 1. One finding, not one per migration
# --------------------------------------------------------------------------- #
def test_three_pending_migrations_are_one_finding():
    """A deployment is either running the merged schema or it is not. Three
    cards would put the same repair in the queue three times."""
    findings = df.migration_drift_findings(
        _report(pending=["20260821024132", "20260821045946", "20260821050135"]))
    assert len(findings) == 1


def test_the_card_names_every_pending_migration():
    """A finding without its specifics cannot be acted on. The count is in the
    title; the NAMES have to be in the advice."""
    findings = df.migration_drift_findings(
        _report(pending=["20260821024132", "20260821045946"]))
    advice = findings[0]["advice"]
    assert "20260821024132_thing" in advice and "20260821045946_thing" in advice
    assert "3 migration" not in findings[0]["title"]
    assert "2 merged migration(s)" in findings[0]["title"]


def test_nothing_pending_is_no_finding():
    assert df.migration_drift_findings(_report(pending=[])) == []


def test_the_advice_warns_that_migrate_reads_the_filesystem():
    """The trap that cost an hour: `migrate.py --status` said "Pending: 0" on
    the frozen deployment because its checkout did not CONTAIN the migration
    directories, while the drift check read the branch and saw two."""
    findings = df.migration_drift_findings(_report(pending=["20260821024132"]))
    advice = findings[0]["advice"]
    assert "FILESYSTEM" in advice
    assert "deployment_freshness" in advice


def test_applying_is_not_automated():
    """Writing schema to a live database is a deployment act. autonomy-act-03's
    restore tier is deliberately closed, and this is not in it."""
    findings = df.migration_drift_findings(_report(pending=["1"]))
    assert "not automated" in findings[0]["advice"]


# --------------------------------------------------------------------------- #
# 2. The fingerprint decides what is the SAME finding
# --------------------------------------------------------------------------- #
def test_the_same_pending_set_is_the_same_finding():
    """Six-hourly runs while a migration stays pending must bump seen_count,
    never file a second card."""
    a = df.migration_drift_findings(_report(pending=["a", "b"]))[0]
    b = df.migration_drift_findings(_report(pending=["b", "a"]))[0]   # order differs
    assert a["finding_id"] == b["finding_id"], (
        "the fingerprint depends on ordering — every run would file a new card"
    )


def test_a_different_pending_set_is_a_different_finding():
    a = df.migration_drift_findings(_report(pending=["a"]))[0]
    b = df.migration_drift_findings(_report(pending=["a", "c"]))[0]
    assert a["finding_id"] != b["finding_id"]


def test_the_evidence_carries_the_report_but_not_the_noise():
    """`applied_not_on_branch` is context the detector reports and is NOT a
    finding — carrying it into the card's evidence would invite somebody to
    'fix' migrations that came from an unmerged branch."""
    f = df.migration_drift_findings(_report(pending=["a"]))[0]
    assert f["evidence"]["pending_count"] == 1
    assert "applied_not_on_branch" not in f["evidence"]


# --------------------------------------------------------------------------- #
# 3. The runner, and what clears a finding
# --------------------------------------------------------------------------- #
class _Conn:
    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _run(monkeypatch, report):
    import tools.db.migration_drift as md

    monkeypatch.setattr(md, "drift", lambda **_k: report)
    monkeypatch.setattr(df, "end_read_txn", lambda _c: None)
    return df.run_migration_drift(_Conn(), {})


def test_pending_produces_findings(monkeypatch):
    result = _run(monkeypatch, _report(pending=["20260821024132"]))
    assert result["state"] == df.RUN_FINDINGS
    assert len(result["findings"]) == 1


def test_current_is_clean(monkeypatch):
    result = _run(monkeypatch, _report(state="current", pending=[]))
    assert result["state"] == df.RUN_CLEAN
    assert not result["findings"]


def test_unmeasurable_is_not_clean(monkeypatch):
    """A fresh database, an unreadable branch, an unreachable schema_migrations.
    Reporting CLEAN would clear a live finding on the strength of a run that
    could not look."""
    result = _run(monkeypatch, {"state": "unmeasurable", "ref": "origin/main",
                                "reason": "no migration history", "pending": None})
    assert result["state"] == df.RUN_UNMEASURABLE
    assert not result["findings"]
    assert "no migration history" in result["reason"]


def test_the_summary_omits_the_pending_list_but_keeps_the_counts(monkeypatch):
    """The list is already on the finding; duplicating it into every run row
    would grow detector_runs without adding anything."""
    result = _run(monkeypatch, _report(pending=["a", "b"]))
    assert "pending" not in result["summary"]
    assert result["summary"]["pending_count"] == 2


# --------------------------------------------------------------------------- #
# 4. It is actually registered — the whole point of the card
# --------------------------------------------------------------------------- #
def test_the_detector_is_in_the_registry_and_the_dispatch_table():
    """A detector that exists and is not dispatched is the defect act-02 was
    built to close, reintroduced one card later."""
    assert df.DETECTOR_MIGRATION_DRIFT in df.DETECTORS
    assert df.DETECTOR_MIGRATION_DRIFT in df.DEFAULT_RUNNERS
    assert df.DEFAULT_RUNNERS[df.DETECTOR_MIGRATION_DRIFT] is df.run_migration_drift


def test_it_has_a_blurb_like_every_other_detector():
    """The blurb is what the card says the detector measures. A missing one
    renders an unexplained finding."""
    assert df.DETECTOR_MIGRATION_DRIFT in df.DETECTOR_BLURB
    assert len(df.DETECTOR_BLURB[df.DETECTOR_MIGRATION_DRIFT]) > 60


def test_the_reflex_config_declares_it():
    """Registered in code and absent from args/genesis_config.yaml would run it
    with an empty config — silently, and only until someone iterated the
    configured detectors instead."""
    import yaml

    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(
        encoding="utf-8"))
    detectors = cfg["reflexes"]["detector_findings_reflex"]["detectors"]
    assert df.DETECTOR_MIGRATION_DRIFT in detectors
    assert detectors[df.DETECTOR_MIGRATION_DRIFT]["ref"] == "origin/main"
