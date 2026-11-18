# CUI // SP-CTI
"""Scorecards with delivery evidence, and a signed conformance bundle per repo (icx-coe-03).

What the card promises, each clause a test here:

  * per repo, every ADOPTED standard that applies is scored: a scorecard rule by machine (with the live
    exemptions applied; an EXPIRED exemption fails it), a manual evidence step as needs_evidence, never passed;
    a standard not adopted is listed, not scored; a check that measured nothing is unverified;
  * delivery evidence (DORA four keys plus AI-assisted rework) computed deterministically from declared sources
    (git, CI snapshot, board export), each metric with its window, sample size and source digests; no data is
    UNVERIFIED, never zero;
  * one icos-conformance/v1 bundle per repo, validated against the draft schema, its digest recomputable,
    deterministic given its inputs; ICDEV[IT], ICDEV[OS] and ICDEV[FT] declared;
  * publishing is a person's declared step, never the preparer's, bound to the digest; the ledger is append-only
    and hash-chained;
  * the page and routes, the twin (threads that resolve, replay) and the MCP read and draft.

People here are labelled test persons (synthetic); every git history, CI run and board card is synthetic. All on
the conftest SQLite platform DB.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
PREPARER = {"id": "u-coe03-preparer", "role": "reviewer"}
PUBLISHER = {"id": "u-coe03-publisher", "role": "reviewer"}
DEVELOPER = {"id": "u-coe03-dev", "role": "developer"}
CARD = "component-readiness"
AS_OF = "2026-11-17"
TODAY = datetime.now(timezone.utc).date()

_CARD_YAML = """
key: component-readiness
name: Component readiness (coe03, synthetic)
collection: test.coe03
adapter_module: tools.iqe
ladder:
  levels:
  - {name: Bronze, rank: 1}
rules:
- identifier: completeness-gate
  level: Bronze
  weight: 10
  expression: foreach c in test.coe03 where c.complete == true select c.key
- identifier: e2e-spec
  weight: 10
  expression: foreach c in test.coe03 where c.has_e2e == true select c.key
- identifier: probes-healthy
  weight: 5
  filter: foreach c in test.coe03 where c.probed == true select c.key
  expression: foreach c in test.coe03 where c.probed == true select c.key
"""


@pytest.fixture()
def coe_db(wf_db):
    """The platform DB (conftest SQLite) with the CoE ledgers, the exemption log and the wf_ tables."""
    return wf_db


@pytest.fixture()
def seeded(coe_db):
    from tools.idp import standards

    standards.seed()
    return coe_db


@pytest.fixture()
def synthetic_card(monkeypatch):
    """component-readiness, replaced by three synthetic services: every one complete, bravo without an e2e spec."""
    from tools.idp import scorecard
    from tools.iqe.executor import register_collection

    rows = [{"key": "alpha", "complete": True, "has_e2e": True, "probed": False},
            {"key": "bravo", "complete": True, "has_e2e": False, "probed": False},
            {"key": "charlie", "complete": True, "has_e2e": True, "probed": False}]
    register_collection("test.coe03", lambda conn=None: [dict(r) for r in rows])
    card = scorecard.parse_scorecard(yaml.safe_load(textwrap.dedent(_CARD_YAML)), source_path="<test>")
    real = scorecard.load_scorecard
    monkeypatch.setattr(scorecard, "load_scorecard",
                        lambda key, directory=None: card if key == CARD else real(key, directory))
    return card


def _c():
    from tools.idp import conformance

    return conformance


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _std(stds, sid):
    return next(s for s in stds if s["id"] == sid)


# ── a planted delivery history (synthetic) ───────────────────────────────────


def _m(sha, merged, commits, *, ai=False, card=None):
    return {"sha": sha, "merged_at": merged, "ai": ai, "card": card,
            "commits": [{"sha": f"{sha}c{i}", "authored_at": a, "ai": ai} for i, a in enumerate(commits)]}


def planted_git():
    return {"schema": "icdev-it/git-history/v1", "repo": "it", "ref": "origin/main", "head": "f" * 40,
            "since": "2026-10-19T00:00:00Z", "until": "2026-11-18T00:00:00Z",
            "merges": [
                _m("m0" * 20, "2026-10-01T00:00:00Z", ["2026-09-30T00:00:00Z"], ai=True, card="abc-def-00"),
                _m("m1" * 20, "2026-10-20T10:00:00Z", ["2026-10-20T08:00:00Z"], ai=True, card="abc-def-01"),
                _m("m2" * 20, "2026-10-25T12:00:00Z", ["2026-10-24T12:00:00Z"], card="abc-def-02"),
                _m("m3" * 20, "2026-10-26T12:00:00Z", ["2026-10-26T11:00:00Z"], ai=True, card="abc-def-01"),
                _m("m4" * 20, "2026-11-01T00:00:00Z", ["2026-10-31T00:00:00Z"], ai=True, card="abc-def-04"),
                _m("m5" * 20, "2026-11-03T00:00:00Z", ["2026-11-02T18:00:00Z"], ai=True, card="abc-def-05"),
                _m("m7" * 20, "2026-11-04T00:00:00Z", []),
                _m("m6" * 20, "2026-11-15T00:00:00Z", ["2026-11-14T12:00:00Z"], ai=True, card="abc-def-06"),
            ],
            "reverts": [{"sha": "r1" * 20, "at": "2026-11-02T00:00:00Z", "reverts": ("m4" * 20 + "c0")[:12]}]}


def planted_ci():
    return {"schema": "icdev-it/ci-runs/v1", "source": "github-actions", "repo": "it", "fetched_at": AS_OF,
            "runs": [{"sha": "m1" * 20, "conclusion": "success", "finished_at": "2026-10-20T11:00:00Z"},
                     {"sha": "m2" * 20, "conclusion": "success", "finished_at": "2026-10-25T12:30:00Z"},
                     {"sha": "m2" * 20, "conclusion": "failure", "finished_at": "2026-10-25T13:00:00Z"},
                     {"sha": "m3" * 20, "conclusion": "success", "finished_at": "2026-10-26T13:00:00Z"},
                     {"sha": "m4" * 20, "conclusion": "success", "finished_at": "2026-11-01T01:00:00Z"},
                     {"sha": "m5" * 20, "conclusion": "success", "finished_at": "2026-11-03T01:00:00Z"},
                     {"sha": "m6" * 20, "conclusion": "cancelled", "finished_at": "2026-11-15T01:00:00Z"}]}


def planted_board():
    return {"schema": "icdev-it/board-export/v1", "exported_at": AS_OF,
            "tasks": [{"id": "abc-def-05", "status": "done", "completed_at": "2026-11-03T00:00:00Z"},
                      {"id": "abc-def-07", "status": "done", "depends_on_task_id": "abc-def-05",
                       "completed_at": "2026-11-05T00:00:00Z"}]}


def _defs():
    from tools.idp import dora

    return dora.definitions(_c().load_config()["delivery"])


# ── scorecards against ADOPTED standards ─────────────────────────────────────


def test_an_adopted_scorecard_standard_is_scored_by_machine(seeded, synthetic_card):
    stds, cards = _c().standard_results("it", AS_OF)
    page = _std(stds, "ENG-PAGE-COMPLETENESS")
    assert page["status"] == "pass" and page["applies"] and page["in_force"]
    assert "3 of 3 services pass completeness-gate by machine" in page["why"]
    e2e = _std(stds, "ENG-E2E-SPEC")
    assert e2e["status"] == "fail" and "1 of 3 services fail e2e-spec" in e2e["why"]
    res = next(c for c in cards if c["standard_id"] == "ENG-E2E-SPEC")
    assert res["failing"] == ["bravo"] and res["services"] == {"applicable": 3, "passing": 2, "failing": 1,
                                                             "exempt": 0, "expired": 0}
    ev = res["evidence"]
    assert ev["collection"] == "test.coe03" and "has_e2e" in ev["expression"] and ev["evaluated_on"] == AS_OF


def test_a_manual_evidence_standard_needs_evidence_and_never_passes(seeded, synthetic_card):
    stds, cards = _c().standard_results("it", AS_OF)
    manual = [s for s in stds if s["check"]["kind"] == "manual_evidence" and s["in_force"] and s["applies"]]
    assert {s["id"] for s in manual} == {"ENG-SEC-GATES", "ENG-RED-FIRST", "ENG-COHERENCE", "ENG-FIVE-RULES",
                                         "ENG-GUARD-HOOKS"}
    assert all(s["status"] == "needs_evidence" and s["why"].startswith("a person reads the evidence") for s in manual)
    assert not any(c["standard_id"] in {s["id"] for s in manual} for c in cards), "a manual step is never scored"


def test_an_expired_exemption_fails_the_standard(seeded, synthetic_card):
    """PLANTED. bravo's e2e-spec waived by a person: the standard passes with it exempt. The same exemption, granted
    40 days ago for 30 (an injected clock), has expired: the standard FAILS and names it."""
    from tools.idp import exemptions as e

    def grant(expires, clock=None):
        kw = {"clock": clock} if clock else {}
        row = e.request_exemption(CARD, "e2e-spec", "bravo", reason="Headless (synthetic).",
                                  requested_by=PREPARER["id"], expires=expires, **kw)
        cur = e._latest(e._open_connection(), row["exemption_key"])
        e.decide_exemption(row["exemption_key"], decision="grant", reason="ok (synthetic)",
                           read_digest=e.exemption_digest(cur), actor=PUBLISHER, **kw)
        return row["exemption_key"]

    then = datetime.now(timezone.utc) - timedelta(days=40)
    key = grant((then.date() + timedelta(days=30)).isoformat(), clock=lambda: then)
    today = TODAY.isoformat()
    before = (then.date() + timedelta(days=5)).isoformat()
    stds, cards = _c().standard_results("it", before)
    assert _std(stds, "ENG-E2E-SPEC")["status"] == "pass", "while the exemption is live, bravo is exempt"
    assert next(c for c in cards if c["standard_id"] == "ENG-E2E-SPEC")["exempt"][0]["component"] == "bravo"
    stds, cards = _c().standard_results("it", today)
    std = _std(stds, "ENG-E2E-SPEC")
    assert std["status"] == "fail" and std["why"].startswith("an exemption expired: ") and key in std["why"]
    res = next(c for c in cards if c["standard_id"] == "ENG-E2E-SPEC")
    assert res["services"]["expired"] == 1 and res["expired"][0]["exemption_key"] == key
    assert res["evidence"]["exemption_gate"] == "fail"
    rows = _c().exemption_rows("it", today, {CARD}, ["ENG-E2E-SPEC"])
    assert [(r["exemption_key"], r["state"]) for r in rows] == [(key, "expired")]


def test_a_standard_not_adopted_is_listed_not_scored(seeded, synthetic_card):
    stds, _ = _c().standard_results("os", AS_OF)
    for sid in ("ICOS-INTERFACES", "ICOS-CONTROLS"):
        s = _std(stds, sid)
        assert s["applies"] and not s["in_force"] and s["status"] == "not_scored" and "listed, not scored" in s["why"]
    assert _std(stds, "ENG-E2E-SPEC")["status"] == "not_applicable", "IT's standards are not declared for ICDEV[OS]"
    ft, cards = _c().standard_results("ft", AS_OF)
    assert {s["status"] for s in ft if s["in_force"]} == {"not_applicable"} and cards == []


def test_unverified_when_nothing_is_measured(seeded, synthetic_card, monkeypatch):
    from tools.idp import scorecard

    _, cards = _c().standard_results("it", AS_OF)
    report = {"results": [{"entity": "alpha", "rules": [{"identifier": "e2e-spec", "status": "not_applicable"}]}],
              "rules": [], "exemptions": {}}
    assert _c()._rule_result("ENG-E2E-SPEC", {"scorecard": CARD, "rule": "e2e-spec"}, report)["status"] == "unverified"

    def broken(key, directory=None):
        raise scorecard.ScorecardError("unreadable (synthetic)")

    monkeypatch.setattr(scorecard, "load_scorecard", broken)
    stds, cards = _c().standard_results("it", AS_OF)
    s = _std(stds, "ENG-PAGE-COMPLETENESS")
    assert s["status"] == "unverified" and "could not be graded" in s["why"], "a scorecard that will not grade is never a pass"


# ── delivery evidence ────────────────────────────────────────────────────────


def test_dora_definitions_on_a_planted_history():
    from tools.idp import dora

    res = dora.compute(git=planted_git(), ci=planted_ci(), board=planted_board(), as_of=AS_OF, defs=_defs())
    m = res["metrics"]
    assert res["window"] == {"start": "2026-10-19T00:00:00Z", "end": "2026-11-18T00:00:00Z", "days": 30,
                             "as_of": AS_OF}
    # seven merges in the window (m0 is before it): 7 / 30 days * 7
    assert m["deployment_frequency"]["value"] == 1.63 and m["deployment_frequency"]["sample_size"] == 7
    # first authored commit -> merge; m7 brought no commit: [2, 24, 1, 24, 6, 12] hours
    lt = m["lead_time"]
    assert lt["value"] == 9.0 and lt["sample_size"] == 6 and lt["detail"]["p90_hours"] == 24.0
    assert lt["detail"]["excluded_no_commits"] == 1
    # m1..m5 have an outcome (m6 cancelled, m7 no run); m2's latest run failed, m4 was reverted the next day
    cfr = m["change_failure_rate"]
    assert cfr["value"] == 0.4 and cfr["sample_size"] == 5
    assert cfr["detail"] == {"failed": 2, "observed": 5, "unobserved": 2, "by_revert": 1}
    # m2 -> m3 (24 h); m4 -> its revert (24 h), sooner than the next good deployment m5 (48 h)
    assert m["time_to_restore"]["value"] == 24.0 and m["time_to_restore"]["sample_size"] == 2
    # AI-assisted in the window: m1, m3, m4, m5, m6; m6 is too recent. m1 followed up by m3 (same card), m4
    # reverted, m5 followed up on the board; m3 was not reworked.
    rw = m["ai_rework_rate"]
    assert rw["value"] == 0.75 and rw["sample_size"] == 4
    assert {k: rw["detail"][k] for k in ("revert", "follow_up", "board", "too_recent", "ai_assisted")} == {
        "revert": 1, "follow_up": 1, "board": 1, "too_recent": 1, "ai_assisted": 5}
    for k, v in m.items():
        assert v["status"] == "measured" and v["window_days"] == 30 and v["sources"], k
    assert set(m["change_failure_rate"]["sources"]) == {res["sources"][0]["digest"], res["sources"][1]["digest"]}
    assert res["definitions_digest"] == dora.digest(_defs())


def test_a_metric_with_no_data_is_unverified_never_zero():
    from tools.idp import dora

    defs = _defs()
    none = dora.compute(git=None, ci=None, board=None, as_of=AS_OF, defs=defs, missing={"git": "not readable here"})
    assert all(v["status"] == "unverified" and v["value"] is None and "not readable here" in v["why"]
               for v in none["metrics"].values())
    no_ci = dora.compute(git=planted_git(), ci=None, board=None, as_of=AS_OF, defs=defs)["metrics"]
    assert no_ci["deployment_frequency"]["status"] == "measured"
    for k in ("change_failure_rate", "time_to_restore"):
        assert no_ci[k]["status"] == "unverified" and no_ci[k]["value"] is None and "no CI run records" in no_ci[k]["why"]
    quiet = dora.compute(git=planted_git(), ci=planted_ci(), board=None, as_of="2026-09-01", defs=defs)["metrics"]
    assert all(v["status"] == "unverified" and v["value"] is None and v["sample_size"] == 0 for v in quiet.values()), \
        "a window with no deployment measures nothing: not zero"
    good = planted_ci()
    good["runs"] = [r for r in good["runs"] if r["conclusion"] == "success"]
    git = planted_git()
    git["reverts"] = []
    clean = dora.compute(git=git, ci=good, board=None, as_of=AS_OF, defs=defs)["metrics"]
    assert clean["change_failure_rate"]["value"] == 0.0, "no failure among observed outcomes is a measured zero"
    assert clean["time_to_restore"]["status"] == "unverified" and "nothing to restore" in clean["time_to_restore"]["why"]


def test_compute_is_deterministic_and_digests_its_sources():
    from tools.idp import dora

    a = dora.compute(git=planted_git(), ci=planted_ci(), board=planted_board(), as_of=AS_OF, defs=_defs())
    b = dora.compute(git=planted_git(), ci=planted_ci(), board=planted_board(), as_of=AS_OF, defs=_defs())
    assert dora.canonical(a) == dora.canonical(b)
    assert [s["digest"] for s in a["sources"]] == [dora.digest(planted_git()), dora.digest(planted_ci()),
                                                   dora.digest(planted_board())]
    changed = planted_ci()
    changed["runs"][0]["conclusion"] = "failure"
    c = dora.compute(git=planted_git(), ci=changed, board=planted_board(), as_of=AS_OF, defs=_defs())
    assert c["sources"][1]["digest"] != a["sources"][1]["digest"] and c["metrics"] != a["metrics"]


def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def test_read_git_from_a_real_checkout(tmp_path):
    """A real (throwaway) repo: a co-authored branch merged with --no-ff, a person's branch, and a revert."""
    import os

    from tools.idp import dora

    repo = tmp_path / "r"
    repo.mkdir()
    base = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

    def at(ts):
        return {**base, "GIT_AUTHOR_DATE": ts, "GIT_COMMITTER_DATE": ts}

    _git(repo, "init", "-q", "-b", "main", env=base)
    (repo / "f").write_text("0")
    _git(repo, "add", "f", env=base)
    _git(repo, "commit", "-qm", "root", env=at("2026-11-01T00:00:00Z"))
    _git(repo, "checkout", "-qb", "feat", env=base)
    (repo / "f").write_text("1")
    _git(repo, "commit", "-qam", "xyz-abc-01: a change\n\nCo-Authored-By: An Agent <noreply@agent.invalid>",
         env=at("2026-11-02T00:00:00Z"))
    _git(repo, "checkout", "-q", "main", env=base)
    _git(repo, "merge", "-q", "--no-ff", "feat", "-m", "Merge branch 'feat'", env=at("2026-11-02T06:00:00Z"))
    _git(repo, "checkout", "-qb", "human", env=base)
    (repo / "g").write_text("1")
    _git(repo, "add", "g", env=base)
    _git(repo, "commit", "-qm", "a person's change\n\nCo-Authored-By: A Person <person@example.invalid>",
         env=at("2026-11-03T00:00:00Z"))
    _git(repo, "checkout", "-q", "main", env=base)
    _git(repo, "merge", "-q", "--no-ff", "human", "-m", "Merge branch 'human'", env=at("2026-11-03T02:00:00Z"))
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "main^2"], capture_output=True, text=True,
                         check=True).stdout.strip()
    _git(repo, "revert", "--no-edit", sha, env=at("2026-11-04T00:00:00Z"))
    win = dora.window("2026-11-10", 30)
    snap = dora.read_git(repo, "main", since=win["start"], until=win["end"], defs=_defs(), repo="t")
    dora.check_git(snap)
    assert [m["merged_at"] for m in snap["merges"]] == ["2026-11-02T06:00:00Z", "2026-11-03T02:00:00Z"]
    feat, human = snap["merges"]
    assert feat["ai"] is True and feat["card"] == "xyz-abc-01" and len(feat["commits"]) == 1
    assert human["ai"] is False, "a person's address is never a machine co-author"
    assert snap["reverts"][0]["reverts"] == sha and snap["reverts"][0]["at"] == "2026-11-04T00:00:00Z"
    assert "a change" not in json.dumps(snap), "no commit text is kept"
    m = dora.compute(git=snap, ci=None, board=None, as_of="2026-11-10", defs=_defs())["metrics"]
    assert m["lead_time"]["value"] == 4.0 and m["deployment_frequency"]["sample_size"] == 2
    with pytest.raises(dora.SourceError):
        dora.read_git(tmp_path / "nowhere", "main", since=win["start"], until=win["end"], defs=_defs())


def test_ci_and_board_snapshots_normalise_and_refuse_garbage():
    from tools.idp import dora

    gh = [{"headSha": "a" * 40, "conclusion": "success", "updatedAt": "2026-11-01T00:00:00Z", "headBranch": "main"},
          {"headSha": "b" * 40, "conclusion": "failure", "updatedAt": "2026-11-01T00:00:00Z", "headBranch": "feat-x"}]
    ci = dora.normalise_ci(gh, repo="it", fetched_at=AS_OF)
    assert ci["schema"] == dora.CI_SCHEMA and [r["sha"] for r in ci["runs"]] == ["a" * 40], "only main's runs"
    assert dora.normalise_ci(ci) == ci
    mixed = gh + [{"headSha": "a" * 40, "conclusion": "success", "updatedAt": "2026-11-02T00:00:00Z",
                   "headBranch": "main", "workflowName": "Docs"},
                  {"headSha": "a" * 40, "conclusion": "failure", "updatedAt": "2026-11-01T00:30:00Z",
                   "headBranch": "main", "workflowName": "ICDEV CI"}]
    only = dora.normalise_ci(mixed, workflow="ICDEV CI")
    assert [r["conclusion"] for r in only["runs"]] == ["success", "failure"] and only["workflow"] == "ICDEV CI", \
        "another workflow's success never masks the main CI run"
    board = dora.normalise_board(planted_board())
    assert [t["id"] for t in board["tasks"]] == ["abc-def-05", "abc-def-07"]
    for bad in ({"schema": "nope"}, "text", [{"conclusion": "success"}]):
        with pytest.raises(dora.SourceError):
            dora.normalise_ci(bad)
    with pytest.raises(dora.SourceError):
        dora.normalise_board({"tasks": []})
    with pytest.raises(dora.SourceError):
        dora.check_git({"schema": "icdev-it/git-history/v1", "merges": [{"sha": "x"}]})


# ── the bundle ───────────────────────────────────────────────────────────────


@pytest.fixture()
def sources_dir(tmp_path, monkeypatch):
    """Snapshots for IT (git, CI, board) and ICDEV[OS] (git only); none for ICDEV[FT]. No live git."""
    d = tmp_path / "sources"
    d.mkdir()
    (d / "it.git.json").write_text(json.dumps(planted_git()))
    (d / "it.ci.json").write_text(json.dumps(planted_ci()))
    (d / "it.board.json").write_text(json.dumps(planted_board()))
    os_git = {**planted_git(), "repo": "os", "ref": "gitlab/main",
              "files": {"args/interfaces.yaml": "1" * 64, "args/controls.yaml": None}}
    (d / "os.git.json").write_text(json.dumps(os_git))
    monkeypatch.setenv("ICDEV_COE_SOURCES_DIR", str(d))
    monkeypatch.setenv("ICDEV_COE_LIVE_GIT", "0")
    return d


def test_the_bundle_validates_against_the_draft_schema_and_its_digest(seeded, synthetic_card, sources_dir):
    import jsonschema

    c = _c()
    sch = c.schema()
    # osx-coe-01: ICDEV[OS] adopted the draft; this is its published copy, vendored verbatim and pinned
    assert sch["description"].startswith("Owned by ICDEV[OS] (osx-coe-01)") and "DRAFT" not in sch["description"]
    assert sch["x-icos-contract"] == c.SCHEMA_CONTRACT == "1.193.0"
    assert sch["$id"] == "urn:icdev-os:contract:conformance:1" and sch["x-license"] == "Apache-2.0"
    jsonschema.Draft202012Validator.check_schema(sch)
    bundles = {r: c.build(r, as_of=AS_OF, at="2026-11-17T12:00:00Z") for r in ("it", "os", "ft")}
    for r, b in bundles.items():
        assert c.validate(b) == [], r
        assert b["schema"] == "icos-conformance/v1" and b["publication"] is None
        assert b["content_digest"] == c.content_digest(b)
        assert b["producer"]["tool"] == "icdev-it/conformance_export" and b["produced_at"] == "2026-11-17T12:00:00Z"
    it = bundles["it"]
    assert it["summary"]["standards"]["pass"] == 1 and it["summary"]["standards"]["needs_evidence"] == 5
    assert it["summary"]["metrics_measured"] == 5 and it["repo"]["read"] == {"git": "snapshot", "ci": "snapshot",
                                                                            "board": "snapshot"}
    os_ = bundles["os"]
    assert os_["summary"]["metrics_unverified"] == ["change_failure_rate", "time_to_restore"]
    pins = {p["path"]: p for p in os_["pinned_files"]}
    assert pins["args/interfaces.yaml"]["drifted"] is True and pins["args/interfaces.yaml"]["pins"][0][
        "standard_id"] == "ICOS-INTERFACES"
    assert pins["args/controls.yaml"]["sha256"] is None and "not read" in pins["args/controls.yaml"]["why"]
    ft = bundles["ft"]
    assert all(v["status"] == "unverified" for v in ft["delivery"]["metrics"].values())
    assert "live git reads are off" in ft["delivery"]["metrics"]["lead_time"]["why"]
    # tamper: the digest no longer matches; a bad status is refused by the schema
    bad = json.loads(json.dumps(it))
    bad["summary"]["standards"]["pass"] = 9
    assert c.validate(bad) == ["content_digest is not the digest of the bundle's content"]
    bad["standards"][0]["status"] = "compliant"
    assert any("compliant" in e for e in c.validate(bad))
    bad = json.loads(json.dumps(it))
    bad["delivery"]["metrics"]["lead_time"].update(status="unverified")
    bad["content_digest"] = c.content_digest(bad)
    assert c.validate(bad), "an unverified metric with a value is refused"


def test_the_schema_is_pinned(seeded, monkeypatch):
    c = _c()
    monkeypatch.setattr(c, "SCHEMA_SHA256", "0" * 64)
    with pytest.raises(c.ConformanceRefused) as exc:
        c.schema()
    assert exc.value.code == "schema"


def test_the_export_is_deterministic_given_its_inputs(seeded, synthetic_card, sources_dir, tmp_path, capsys):
    from tools.idp import conformance_export as x

    outs = []
    for n in (1, 2):
        assert x.main(["--repo", "it", "--as-of", AS_OF, "--at", "2026-11-17T12:00:00Z",
                       "--out", str(tmp_path / f"o{n}")]) == 0
        outs.append((tmp_path / f"o{n}" / "conformance-it.json").read_bytes())
    assert outs[0] == outs[1], "same inputs, same bytes"
    rep = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rep["status"] == "ok" and rep["published"] is False and rep["metrics"]["lead_time"]["n"] == 6
    later = x.export("it", as_of=AS_OF, at="2026-11-18T09:00:00Z")
    assert later["content_digest"] == json.loads(outs[0])["content_digest"], "produced_at is outside the digest"
    ci = planted_ci()
    ci["runs"] = ci["runs"][:3]
    (tmp_path / "ci.json").write_text(json.dumps(ci))
    other = x.export("it", as_of=AS_OF, ci=tmp_path / "ci.json")
    assert other["content_digest"] != later["content_digest"], "a source passed in is a different input"
    assert x.main(["--repo", "nowhere"]) == 1


def test_the_repos_and_definitions_are_declared():
    c = _c()
    cfg = c.load_config()
    assert set(cfg["repos"]) == {"it", "os", "ft"}
    assert cfg["repos"]["ft"]["standards"] == [] and cfg["repos"]["os"]["standards"] == ["ICOS-*"]
    assert cfg["repos"]["os"]["git"]["ref"] == "gitlab/main" and "args/controls.yaml" in cfg["repos"]["os"]["pinned_files"]
    d = cfg["delivery"]
    assert d["rework_days"] == 7 and d["window_days"] == 30 and d["ai_coauthor_address"] == "^noreply@"
    assert c.applies("ENG-RED-FIRST", ["ENG-*"]) and not c.applies("ICOS-CONTROLS", ["ENG-*"])


# ── publishing is a person's step ────────────────────────────────────────────


def test_publishing_is_a_persons_step_never_the_preparers(seeded, synthetic_card, sources_dir, tmp_path):
    from tools.idp import conformance_export as x

    c = _c()
    with pytest.raises(c.ConformanceRefused) as exc:
        c.publish("it", decision="publish", reason="r", read_digest="x", actor=PUBLISHER)
    assert exc.value.code == "nothing_prepared"
    prep = c.prepare("it", PREPARER, as_of=AS_OF)
    assert prep["prepared_by"] == PREPARER["id"] and prep["actor_kind"] == "person"
    d = prep["content_digest"]
    for actor, kw, code in ((PREPARER, {}, "separation"), (PUBLISHER, {"read_digest": "0" * 64}, "digest"),
                            (PUBLISHER, {"reason": ""}, "reason"), (DEVELOPER, {}, "role"),
                            ({"id": "agent:x", "role": "service"}, {}, "not_a_person"), (None, {}, "no_person")):
        with pytest.raises(c.ConformanceRefused) as exc:
            c.publish("it", decision="publish", **{"reason": "ok (synthetic)", "read_digest": d, **kw}, actor=actor)
        assert exc.value.code == code, (actor, kw)
    assert c.published_bundle("it") is None, "nothing published yet"
    out = c.publish("it", decision="publish", reason="Read it (synthetic).", read_digest=d, actor=PUBLISHER)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"] and out["prepared_by"] == PREPARER["id"]
    b = out["bundle"]
    pub = b["publication"]
    assert pub["signed_by"] == PUBLISHER["id"] and pub["content_digest"] == d and pub["prepared_by"] == PREPARER["id"]
    assert pub["template"] == "decl-wft-coe-conformance-publish" and pub["step_id"] == out["step"]["step_id"]
    assert c.validate(b) == [] and b["content_digest"] == d
    with pytest.raises(c.ConformanceRefused) as exc:
        c.publish("it", decision="publish", reason="again", read_digest=d, actor=PUBLISHER)
    assert exc.value.code == "already_decided"
    # the export carries the publication only for the same content
    assert x.export("it", as_of=AS_OF)["publication"]["step_id"] == pub["step_id"]
    assert x.export("it", as_of="2026-11-16")["publication"] is None, "other content is a draft"
    assert x.main(["--repo", "it", "--published", "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "conformance-it.json").read_text())["publication"]["signed_by"] == PUBLISHER["id"]
    # a refusal: the bundle never leaves
    p2 = c.prepare("os", PUBLISHER, as_of=AS_OF)
    r = c.publish("os", decision="refuse", reason="Not yet (synthetic).", read_digest=p2["content_digest"],
                  actor=PREPARER)
    assert r["event"] == "refused" and "bundle" not in r and c.published_bundle("os") is None
    steps = _rows(seeded, "SELECT template_id, actor, subject FROM wf_decision_steps WHERE subject LIKE 'conformance:%' "
                          "ORDER BY created_at")
    assert steps == [("decl-wft-coe-conformance-publish", PUBLISHER["id"], "conformance:it"),
                     ("decl-wft-coe-conformance-publish", PREPARER["id"], "conformance:os")]


def test_the_ledger_is_append_only_and_hash_chained(seeded, synthetic_card, sources_dir):
    c = _c()
    p = c.prepare("it", PREPARER, as_of=AS_OF)
    c.publish("it", decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    rows = c.read_events(repo="it")
    ch = c.chain(rows)
    assert ch["_problems"] == [] and [r["event"] for r in ch["it"]] == ["prepared", "published"]
    assert ch["it"][0]["prev_hash"] == c.GENESIS and ch["it"][1]["prev_hash"] == ch["it"][0]["row_hash"]
    for sql in ("UPDATE coe_conformance_events SET actor = 'x'", "DELETE FROM coe_conformance_events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn = sqlite3.connect(str(seeded))
            try:
                conn.execute(sql)
            finally:
                conn.close()
    forged = [dict(r) for r in rows]
    forged[0]["bundle_json"] = forged[0]["bundle_json"].replace('"pass"', '"fail"', 1)
    forged[0]["row_hash"] = c._row_hash(forged[0])
    probs = c.chain(forged)["_problems"]
    assert any(x["problem"] == "the bundle does not match its digest" for x in probs)
    assert any(x["problem"] == "not on the repo's hash chain" for x in probs), "the next row no longer links"
    agent = c.prepare("it", {"id": "agent:coe03-drafter", "role": "service"}, as_of=AS_OF)
    assert agent["actor_kind"] == "agent"
    with pytest.raises(c.ConformanceRefused) as exc:
        c.prepare("it", None, as_of=AS_OF)
    assert exc.value.code == "no_actor"


# ── the console, the routes ──────────────────────────────────────────────────


def _page_app(user):
    from tools.dashboard.app import app as dash
    from tools.idp.blueprint import bp

    app = Flask(__name__, template_folder=dash.template_folder, static_folder=dash.static_folder)
    app.config.update(dash.config)
    app.jinja_env.filters.update(dash.jinja_env.filters)
    app.jinja_env.globals.update(dash.jinja_env.globals)
    app.template_context_processors[None].extend(dash.template_context_processors.get(None, []))
    app.secret_key = dash.secret_key or "test-secret"
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user)
        g.security_context = {"tenant_id": "default", "user_id": user["id"], "classification": "CUI"}

    app.register_blueprint(bp, url_prefix="/idp")
    return app.test_client()


def test_the_routes_and_the_page(seeded, synthetic_card, sources_dir):
    prep, pub = _page_app(PREPARER), _page_app(PUBLISHER)
    html = prep.get("/idp/conformance").get_data(as_text=True)
    for want in ("Standards scorecards and conformance bundles", "needs evidence", "expired exemption",
                 "decl-wft-coe-conformance-publish", "idp.idp_api_conformance_repo", "idp.idp_api_conformance_prepare",
                 "idp.idp_api_conformance_publish", "idp.idp_api_conformance_bundle", "idp.idp_api_standards_twin",
                 'data-repo="it"', 'data-repo="os"', 'data-repo="ft"'):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/idp/conformance.html").read_text(encoding="utf-8"), \
        "routes are named by endpoint id"
    lst = prep.get("/idp/api/conformance").get_json()
    assert [r["key"] for r in lst["repos"]] == ["it", "os", "ft"] and lst["schema"] == "icos-conformance/v1"
    v = prep.get(f"/idp/api/conformance/it?as_of={AS_OF}").get_json()
    assert v["bundle"]["as_of"] == AS_OF and v["ledger"]["next"] == ["prepare"]
    assert prep.get("/idp/api/conformance/zz").status_code == 404
    bad = prep.get("/idp/api/conformance/it?as_of=yesterday")
    assert bad.status_code == 400 and bad.get_json()["code"] == "as_of", "a date that is not one is refused"
    assert prep.get("/idp/api/conformance/it/bundle").get_json()["code"] == "unpublished"
    r = prep.post("/idp/api/conformance/it/prepare", json={"as_of": AS_OF})
    assert r.status_code == 201, r.get_data(as_text=True)
    d = r.get_json()["content_digest"]
    led = pub.get(f"/idp/api/conformance/it?as_of={AS_OF}").get_json()["ledger"]
    assert led["next"] == ["publish"] and PREPARER["id"] in led["builders"]
    body = {"decision": "publish", "reason": "Read it (synthetic).", "digest": d}
    refused = prep.post("/idp/api/conformance/it/publish", json=body)
    assert refused.status_code == 403 and refused.get_json()["code"] == "separation"
    out = pub.post("/idp/api/conformance/it/publish", json=body).get_json()
    assert out["event"] == "published" and out["bundle"]["publication"]["signed_by"] == PUBLISHER["id"]
    got = pub.get("/idp/api/conformance/it/bundle").get_json()
    assert got["content_digest"] == d and got["publication"]["content_digest"] == d
    # linked from the standards register, the exemptions page and the portal, and in the nav
    for page in ("standards.html", "exemptions.html", "adrs.html", "page.html"):
        text = (ROOT / "tools/dashboard/templates/idp" / page).read_text(encoding="utf-8")
        assert "url_for('idp.idp_conformance_page')" in text or 'href="/idp/conformance"' in text, page
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    idp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "idp")
    assert "/idp/conformance" in [lk["href"] for lk in idp["nav"]["links"]]


# ── the twin ─────────────────────────────────────────────────────────────────


def test_the_twin_observes_bundles_with_threads(seeded, synthetic_card, sources_dir):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    c = _c()
    adapter = governance.adapter_for("idp")
    assert adapter.observes == ("coe.standards", "coe.exemptions", "coe.adrs", "coe.conformance", "coe.golden_paths",
                                "finops.focus")
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["coe.conformance.ledger"]["status"] == "verified"
    for r in ("it", "os", "ft"):
        assert chk[f"coe.conformance.{r}"]["status"] == "unverified", "an unreported repo is never a pass"
    p = c.prepare("os", PREPARER, as_of=AS_OF)
    c.publish("os", decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    p = c.prepare("it", PREPARER, as_of=AS_OF)
    c.publish("it", decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    env = governance.observe(adapter)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["coe.conformance.it"]["status"] == "verified" and chk["coe.conformance.ft"]["status"] == "unverified"
    found = [v for v in env["violations"] if v["rule_id"].startswith("coe.conformance")]
    rules = {(v["rule_id"], v["title"].split(":")[0]) for v in found}
    assert ("coe.conformance.standard_failing", "it") in rules, "ENG-E2E-SPEC fails in the published bundle"
    assert ("coe.conformance.metric_unverified", "os") in rules, "ICDEV[OS] has no CI snapshot"
    f = next(v for v in found if v["rule_id"] == "coe.conformance.standard_failing")
    t = governance.resolve_thread(f["thread_id"])
    assert t["resolved"] and t["source"] == "coe_conformance_events" and t["record"]["event"] == "published"
    assert t["record"]["actor"] == PUBLISHER["id"]
    assert env["extra"]["summary"]["conformance_published"] == 2
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep.get("reason")


def test_the_twin_says_unverified_when_the_ledger_cannot_be_read(monkeypatch):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    c = _c()

    def boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(c, "read_events", boom)
    env = governance.observe(governance.adapter_for("idp"), record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["coe.conformance.ledger"]["status"] == "unverified" and env["verdict"] != "pass"


# ── AI-native: read, and draft only ──────────────────────────────────────────


def test_the_mcp_tools_read_and_only_prepare(seeded, synthetic_card, sources_dir):
    from tools.conformance.feature_audit import approval_policy
    from tools.mcp.gap_handlers import handle_coe_conformance, handle_coe_conformance_prepare
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert {"coe_conformance", "coe_conformance_prepare"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["coe_conformance"] is True and tool_authorization("coe_conformance")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["coe_conformance_prepare"] is False
    policy = approval_policy(ROOT, "args/agent_approval_policy.yaml")
    assert policy["tiers"].get("coe_conformance_prepare", policy["default"]) in policy["halts"]
    assert [r["key"] for r in handle_coe_conformance({})["repos"]["repos"]] == ["it", "os", "ft"]
    one = handle_coe_conformance({"repo": "it", "as_of": AS_OF})["repo"]
    assert one["bundle"]["repo"]["key"] == "it"
    assert handle_coe_conformance({"repo": "it", "published": True})["code"] == "unpublished"
    out = handle_coe_conformance_prepare({"agent_id": "coe03-drafter", "repo": "it", "as_of": AS_OF})
    assert out["draft"]["prepared_by"] == "agent:coe03-drafter" and out["draft"]["actor_kind"] == "agent"
    assert "publishes it" in out["next"]
    assert handle_coe_conformance_prepare({"repo": "zz"})["code"] == "unknown_repo"
    assert handle_coe_conformance({"repo": "it", "published": True})["code"] == "unpublished", "a draft never leaves"
