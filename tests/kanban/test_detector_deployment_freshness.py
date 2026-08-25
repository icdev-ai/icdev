# CUI // SP-CTI
"""A blocked deployment reaches the restore tier, and a card only for what the
tier could not clear (autonomy-dep-04).

autonomy-dep-03 measures whether this deployment can still update itself.
NOTHING consumed it: the freeze it reports was cleared by hand on 2026-08-21
and was back within a day, 1,340 refusals deep, with PR #1903's merged fix
absent from the live dashboard while every signal stayed green. "A report
nobody reads" is the defect dep-03 itself named.

This registers `deployment_freshness` as a fifth detector on the act-02 path
— but unlike the other four it CONSUMES the restore tier before it files:
on `blocked` it asks `restore_acts.restore_auto_managed_file` (prove ->
audit -> apply -> confirm), RE-MEASURES, and seeds a card only for a freeze
still standing, carrying the act's own refusal so the human knows why a
machine did not clear it.

THE THREE THINGS PINNED:
  1. A freeze the act clears is CLEAN — measured again, not inferred from the
     act's confirm — and no card is filed for it.
  2. A freeze the act refuses (a human edit, a foreign file) is ONE finding
     whose fingerprint is the guard's reason plus the files it names, with the
     refusal in its evidence and the manual repair in its advice.
  3. UNMEASURABLE clears nothing, and a dry run ACTS on nothing: `consume`
     hands every detector `dry_run`, and this one passes it to the act.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import detector_findings as df  # noqa: E402

REL = "args/projects.yaml"


def _report(state="blocked", conflicts=(REL,), behind=4,
            reason="local changes would be lost", root="C:/deploy", ref="origin/main"):
    return {"state": state, "behind_by": behind, "reason": reason,
            "conflicts": list(conflicts), "root": root, "ref": ref}


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


class _Freshness:
    """Successive freshness verdicts; the last one repeats."""

    def __init__(self, *reports):
        self.reports = list(reports)
        self.calls = 0

    def __call__(self, **_kw):
        self.calls += 1
        idx = min(self.calls - 1, len(self.reports) - 1)
        return self.reports[idx]


def _applied(act, target, **_kw):
    return {"act": act, "target": target, "outcome": df.__dict__.get("APPLIED", "applied"),
            "proven": True, "reason": "regenerable", "audit_id": 7, "confirmed": True}


def _refused(act, target, **_kw):
    return {"act": act, "target": target, "outcome": "refused", "proven": False,
            "reason": "project 'alpha' field 'name' differs from HEAD — a human edit, "
                      "which this act never reverts"}


def _run(monkeypatch, freshness, perform=_refused, cfg=None):
    import tools.awareness.restore_acts as ra
    import tools.genesis.deployment_freshness as dfm

    calls = []

    def _perform(act, target, **kw):
        calls.append((act, target, kw))
        return perform(act, target, **kw)

    monkeypatch.setattr(dfm, "freshness", freshness)
    monkeypatch.setattr(ra, "perform", _perform)
    monkeypatch.setattr(df, "end_read_txn", lambda _c: None)
    return df.run_deployment_freshness(_Conn(), cfg or {}), calls


# --------------------------------------------------------------------------- #
# 1. A freeze the act clears is clean — re-measured
# --------------------------------------------------------------------------- #
def test_a_cleared_freeze_is_clean_and_files_nothing(monkeypatch):
    fresh = _Freshness(_report(), _report(state="current", behind=0, conflicts=(),
                                          reason="already current"))
    result, calls = _run(monkeypatch, fresh, perform=_applied)
    assert result["state"] == df.RUN_CLEAN
    assert not result["findings"]
    assert fresh.calls == 2, "the clearance is MEASURED again, not inferred"
    assert result["summary"]["restore"]["outcome"] == "applied"
    assert result["summary"]["after"]["state"] == "current"
    assert calls == [("restore_auto_managed_file", REL,
                      {"root": Path("C:/deploy"), "dry_run": False})]


def test_an_act_that_applied_but_left_it_blocked_is_still_a_finding(monkeypatch):
    """`applied` is the act's word; the detector believes the guard."""
    fresh = _Freshness(_report(), _report(behind=4))
    result, _calls = _run(monkeypatch, fresh, perform=_applied)
    assert result["state"] == df.RUN_FINDINGS
    assert len(result["findings"]) == 1


# --------------------------------------------------------------------------- #
# 2. A freeze the act refuses is ONE finding carrying the refusal
# --------------------------------------------------------------------------- #
def test_a_refused_freeze_is_one_finding_with_the_refusal_in_evidence(monkeypatch):
    result, calls = _run(monkeypatch, _Freshness(_report()))
    assert result["state"] == df.RUN_FINDINGS
    assert len(calls) == 1
    (f,) = result["findings"]
    assert f["detector"] == df.DETECTOR_DEPLOYMENT_FRESHNESS
    assert f["priority"] == "high" and f["task_type"] == "fix"
    assert "frozen 4 commit(s) behind origin/main" in f["title"]
    assert f["evidence"]["restore"]["outcome"] == "refused"
    assert "human edit" in f["evidence"]["restore"]["reason"]
    assert f["evidence"]["conflicts"] == [REL]
    assert "deployment_freshness.py --root C:/deploy --json" in f["derivation"]
    assert "refused" in f["advice"] and "commit it" in f["advice"]
    assert "never widen the guard" in f["advice"]
    assert result["summary"]["restore"]["outcome"] == "refused"


def test_the_same_freeze_is_the_same_finding_and_a_different_file_set_is_not():
    a = df.freshness_findings(_report())[0]
    b = df.freshness_findings(_report(behind=9))[0]          # persisted another cycle
    c = df.freshness_findings(_report(conflicts=(REL, "tools/x.py")))[0]
    assert a["finding_id"] == b["finding_id"]
    assert a["finding_id"] != c["finding_id"]
    assert df.freshness_findings(_report(state="updatable")) == []


def test_a_foreign_file_is_not_attempted_and_the_card_says_so(monkeypatch):
    result, calls = _run(monkeypatch, _Freshness(_report(conflicts=("tools/x.py", REL))))
    assert [c[1] for c in calls] == [REL], "only the enumerated file is attempted"
    assert result["state"] == df.RUN_FINDINGS
    acts = result["findings"][0]["evidence"]["restore"]
    assert acts["outcome"] == "refused"
    summary_acts = result["summary"]["restore"]
    assert summary_acts["outcome"] == "refused"


def test_restore_off_files_the_card_without_asking(monkeypatch):
    result, calls = _run(monkeypatch, _Freshness(_report()), cfg={"restore": False})
    assert calls == []
    assert result["state"] == df.RUN_FINDINGS
    assert result["summary"]["restore"]["outcome"] == "disabled"
    assert result["findings"][0]["evidence"].get("restore") is None


# --------------------------------------------------------------------------- #
# 3. Unmeasurable clears nothing; a dry run acts on nothing
# --------------------------------------------------------------------------- #
def test_current_and_updatable_are_clean_without_asking_the_tier(monkeypatch):
    for state in ("current", "updatable"):
        result, calls = _run(monkeypatch, _Freshness(_report(state=state, conflicts=(),
                                                             reason="would pull")))
        assert result["state"] == df.RUN_CLEAN and calls == []
        assert result["summary"]["state"] == state


def test_unmeasurable_is_not_clean(monkeypatch):
    result, calls = _run(monkeypatch, _Freshness(
        _report(state="unmeasurable", behind=None, conflicts=(), reason="no remote")))
    assert result["state"] == df.RUN_UNMEASURABLE
    assert not result["findings"] and calls == []
    assert "no remote" in result["reason"]


def test_a_dry_run_reaches_the_act_as_a_dry_run(monkeypatch):
    _result, calls = _run(monkeypatch, _Freshness(_report()), cfg={"dry_run": True})
    assert calls[0][2]["dry_run"] is True


def test_consume_hands_every_detector_the_dry_run_flag(monkeypatch):
    seen = []

    def probe(_conn, cfg):
        seen.append(dict(cfg))
        return df._result(df.RUN_CLEAN)

    monkeypatch.setattr(df, "tables_present", lambda _c: True)
    monkeypatch.setattr(df, "end_read_txn", lambda _c: None)
    df.consume({"detectors": {"probe": {"ref": "x"}}}, conn=_Conn(), seed=False,
               runners={"probe": probe})
    df.consume({"detectors": {"probe": {"ref": "x"}}}, conn=_Conn(), seed=True,
               runners={"probe": probe})
    assert seen[0] == {"ref": "x", "dry_run": True}
    assert seen[1] == {"ref": "x", "dry_run": False}


# --------------------------------------------------------------------------- #
# 4. It is actually registered — the whole point of the card
# --------------------------------------------------------------------------- #
def test_the_detector_is_in_the_registry_and_the_dispatch_table():
    assert df.DETECTOR_DEPLOYMENT_FRESHNESS in df.DETECTORS
    assert df.DEFAULT_RUNNERS[df.DETECTOR_DEPLOYMENT_FRESHNESS] is df.run_deployment_freshness
    assert list(df.DEFAULT_RUNNERS)[-1] == df.DETECTOR_DEPLOYMENT_FRESHNESS, \
        "it leaves the database for git and runs last, after the cheap ones committed"


def test_it_has_a_blurb_like_every_other_detector():
    assert df.DETECTOR_DEPLOYMENT_FRESHNESS in df.DETECTOR_BLURB
    assert "restore" in df.DETECTOR_BLURB[df.DETECTOR_DEPLOYMENT_FRESHNESS]


def test_the_reflex_config_declares_it_with_restore_on():
    import yaml

    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    detectors = cfg["reflexes"]["detector_findings_reflex"]["detectors"]
    assert detectors[df.DETECTOR_DEPLOYMENT_FRESHNESS]["ref"] == "origin/main"
    assert detectors[df.DETECTOR_DEPLOYMENT_FRESHNESS]["restore"] is True
