# CUI // SP-CTI
"""A claim is learned from a VERIFIED INCIDENT, or it is not registered (autonomy-lrn-01).

Every defect this week was fixed, tested and documented — with a fixture-based
unit test pinning ONE function. When the same defect lives at a second site the
test still passes: hgx-park-01 made `workflow_runner._park_for_approval` atomic
and its structural tests read that function's source, while
`mcp_executor.open_approval_gate` kept the identical two-commit park for weeks
(rem-hyg-19). A claim over the DATA has no second site.

These tests pin the path from incident to claim: every registered claim cites
its incident; a citation is a fact only when the card is done AND on main;
unmeasurable is never verified; an incident cited twice is one incident; a
board with no fixes is unmeasurable, never "0 unguarded"; and the park claim's
two sides share no code and catch both halves of the half-commit.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import incident_claims as ic  # noqa: E402
from tools.awareness.claim_verifier import (  # noqa: E402
    AGREES,
    DISAGREES,
    UNMEASURABLE,
    Claim,
    Incident,
    main,
    verify,
)


def _claim(cid, incident=None, reported=1, derived=1, agree=None):
    kw = {"agree": agree} if agree else {}
    return Claim(claim_id=cid, description="a defect that actually happened " * 2,
                 reported=lambda: reported, derived=lambda: derived,
                 incident=incident, **kw)


def _fixed(*ids):
    return [{"id": i, "title": f"title {i}", "finished_at": "2026-08-20T00:00:00"} for i in ids]


class _Conn:
    """A connection whose execute() hands back result sets in call order."""

    def __init__(self, *result_sets, raise_on_execute=None):
        self._sets = list(result_sets)
        self._raise = raise_on_execute
        self.closed = False

    def execute(self, sql, params=()):
        if self._raise:
            raise self._raise
        self._rows = self._sets.pop(0) if self._sets else []
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


# --------------------------------------------------------------------------- #
# 1. The registry's discipline: every claim cites its incident
# --------------------------------------------------------------------------- #
def test_every_registered_claim_cites_a_well_formed_incident():
    """A claim citing nothing could be 'reports clean because it does nothing'."""
    from tools.awareness.claims import REGISTRY

    assert ic.claims_without_incident(REGISTRY) == []
    for c in REGISTRY:
        assert ic.incident_is_well_formed(c.incident), c.claim_id
        assert c.incident.fixed_by, f"{c.claim_id} does not say what the fix changed"


def test_the_park_claim_cites_both_sites():
    """One claim guards two incidents: the same defect, fixed weeks apart."""
    from tools.awareness.claims import REGISTRY

    park = next(c for c in REGISTRY if c.claim_id == "approval_park_is_whole")
    assert set(park.incident.task_ids) == {"hgx-park-01", "rem-hyg-19"}
    assert ic.cited_incidents(REGISTRY)["hgx-park-01"] == ["approval_park_is_whole"]


def test_a_citation_needs_card_shaped_ids_and_an_iso_date():
    assert ic.incident_is_well_formed(Incident(["rem-hyg-09"], "2026-08-20"))
    assert not ic.incident_is_well_formed(None)
    assert not ic.incident_is_well_formed(Incident([], "2026-08-20"))
    assert not ic.incident_is_well_formed(Incident(["fixed it"], "2026-08-20"))
    assert not ic.incident_is_well_formed(Incident(["rem-hyg-09"], "yesterday"))


# --------------------------------------------------------------------------- #
# 2. A citation is a VERIFIED FACT, or it is not verified
# --------------------------------------------------------------------------- #
def test_done_and_landed_is_verified():
    v = ic.verify_incident(Incident(["a-b-01"], "2026-08-20"),
                           board={"a-b-01": "done"}, landed={"a-b-01": "merge_ref"})
    assert v["verified"] is True


def test_a_fix_still_in_pr_opened_has_not_happened_yet():
    v = ic.verify_incident(Incident(["a-b-01"], "2026-08-20"),
                           board={"a-b-01": "pr_opened"}, landed={"a-b-01": "merge_ref"})
    assert v["verified"] is False
    assert "pr_opened" in v["reason"]


def test_done_on_the_board_but_absent_from_main_is_not_a_fact():
    """The 'board says done but it is not on main' bug, refused here too."""
    v = ic.verify_incident(Incident(["a-b-01"], "2026-08-20"),
                           board={"a-b-01": "done"}, landed={"a-b-01": None})
    assert v["verified"] is False
    assert "default branch" in v["reason"]


def test_every_cited_site_must_be_verified_not_just_one():
    v = ic.verify_incident(Incident(["a-b-01", "a-b-02"], "2026-08-20"),
                           board={"a-b-01": "done", "a-b-02": None},
                           landed={"a-b-01": "subject", "a-b-02": "subject"})
    assert v["verified"] is False
    assert "a-b-02=absent" in v["reason"]


def test_an_unreadable_source_is_unmeasurable_never_verified():
    inc = Incident(["a-b-01"], "2026-08-20")
    assert ic.verify_incident(inc, board=None, landed={"a-b-01": "subject"})["verified"] is None
    assert ic.verify_incident(inc, board={"a-b-01": "done"}, landed=None)["verified"] is None


def test_a_malformed_citation_is_refused_not_looked_up():
    v = ic.verify_incident(Incident(["not a card"], "2026-08-20"), board={}, landed={})
    assert v["verified"] is False and v["reason"] == "citation is malformed"


def test_git_that_cannot_answer_is_none_not_not_landed(monkeypatch):
    """landed_check is FAIL-OPEN (`checked: false`); that must surface as
    unmeasurable, never as 'the fix is not on main'."""
    import tools.kanban.landed_check as lc

    monkeypatch.setattr(lc, "check_landed_bulk", lambda ids, repo_root=None: {
        i: {"checked": False, "landed": False, "confidence": None} for i in ids})
    assert ic.landed_status(["a-b-01"]) is None


def test_an_unreadable_board_is_none_not_absent(monkeypatch):
    monkeypatch.setattr(ic, "_conn", lambda: _Conn(raise_on_execute=RuntimeError("down")))
    assert ic.board_status(["a-b-01"]) is None


# --------------------------------------------------------------------------- #
# 3. Coverage: which fixed incidents have a claim? Named, never counted.
# --------------------------------------------------------------------------- #
def test_an_empty_window_is_unmeasurable_with_none_counts():
    """A fresh database must not read as 'every incident is guarded'."""
    r = ic.coverage_report([_claim("c", Incident(["a-b-01"], "2026-08-20"))],
                           fixed=[], verify=False)
    assert r["state"] == "unmeasurable"
    assert r["guarded"] is None and r["unguarded"] is None and r["fixed"] is None


def test_unguarded_incidents_are_named():
    r = ic.coverage_report([_claim("c", Incident(["a-b-01"], "2026-08-20"))],
                           fixed=_fixed("a-b-01", "a-b-02", "a-b-03"), verify=False)
    assert r["state"] == "measured"
    assert (r["fixed"], r["guarded"], r["unguarded"]) == (3, 1, 2)
    assert [f["id"] for f in r["unguarded_ids"]] == ["a-b-02", "a-b-03"]
    assert r["guarded_ids"][0]["claims"] == ["c"]


def test_an_incident_cited_by_two_claims_is_one_incident():
    """Repetition is not corroboration — for provenance as much as evidence."""
    reg = [_claim("c1", Incident(["a-b-01"], "2026-08-20")),
           _claim("c2", Incident(["a-b-01"], "2026-08-20"))]
    r = ic.coverage_report(reg, fixed=_fixed("a-b-01"), verify=False)
    assert r["incidents_cited"] == 1
    assert r["guarded"] == 1
    assert r["guarded_ids"][0]["claims"] == ["c1", "c2"]


def test_a_claim_citing_two_sites_guards_two_incidents():
    reg = [_claim("park", Incident(["a-b-01", "a-b-02"], "2026-08-20"))]
    r = ic.coverage_report(reg, fixed=_fixed("a-b-01", "a-b-02"), verify=False)
    assert (r["guarded"], r["unguarded"]) == (2, 0)


def test_a_duplicate_board_row_is_one_incident(monkeypatch):
    rows = [{"id": "a-b-01", "title": "t", "finished_at": "x"}] * 3
    monkeypatch.setattr(ic, "_conn", lambda: _Conn(rows))
    assert [f["id"] for f in ic.fixed_incidents(7)] == ["a-b-01"]


def test_an_unreadable_board_is_an_error_not_a_clean_report(monkeypatch):
    monkeypatch.setattr(ic, "_conn", lambda: _Conn(raise_on_execute=RuntimeError("down")))
    r = ic.coverage_report([], verify=False)
    assert r["state"] == "error"
    assert r["unguarded"] is None


def test_a_claim_without_an_incident_is_named_in_the_report():
    r = ic.coverage_report([_claim("bare"), _claim("cited", Incident(["a-b-01"], "2026-08-20"))],
                           fixed=_fixed("a-b-01"), verify=False)
    assert r["claims_without_incident"] == ["bare"]


def test_an_unverified_citation_is_reported_not_hidden(monkeypatch):
    monkeypatch.setattr(ic, "board_status", lambda ids: {i: "pr_opened" for i in ids})
    monkeypatch.setattr(ic, "landed_status", lambda ids, repo_root=None: {i: "subject" for i in ids})
    r = ic.coverage_report([_claim("c", Incident(["a-b-01"], "2026-08-20"))],
                           fixed=_fixed("a-b-01"))
    assert r["unverified_incidents"][0]["claim_id"] == "c"
    assert r["unverified_incidents"][0]["verified"] is False
    text = ic.render(r)
    assert "!! c cites a-b-01" in text and "UNGUARDED 0" in text


# --------------------------------------------------------------------------- #
# 4. The learned claim catches BOTH halves of the half-commit, at any site
# --------------------------------------------------------------------------- #
def test_a_gate_under_an_unparked_run_is_the_first_half():
    from tools.awareness.claims import _park_is_whole

    assert _park_is_whole(["sr-1"], []) is False


def test_a_parked_run_without_a_gate_is_the_other_half():
    from tools.awareness.claims import _park_is_whole

    assert _park_is_whole([], ["run-without-gate:r-1"]) is False


def test_a_whole_park_agrees_regardless_of_order():
    from tools.awareness.claims import _park_is_whole

    assert _park_is_whole(["sr-2", "sr-1"], ["sr-1", "sr-2"]) is True


def test_the_derived_side_reads_the_run_table_and_names_gateless_runs(monkeypatch):
    import tools.awareness.claims as claims

    monkeypatch.setattr(claims, "_conn", lambda: _Conn(
        [{"g": "sr-1"}],            # gates under parked runs
        [{"g": "r-2"}],             # parked runs with no gate
    ))
    assert claims._derived_pending_gates() == ["run-without-gate:r-2", "sr-1"]


def test_the_two_park_sides_share_no_code():
    """If the derived side called the runner it would prove only that the
    runner is deterministic."""
    from tools.awareness.claims import _derived_pending_gates, _reported_pending_gates

    assert _reported_pending_gates.__code__ is not _derived_pending_gates.__code__
    assert "workflow_runner" not in inspect.getsource(_derived_pending_gates)
    assert "get_pending_approvals" not in inspect.getsource(_derived_pending_gates)


def test_a_quiet_board_is_unmeasurable_never_agrees():
    """Measured 2026-08-21: no gate has ever been parked on this board."""
    from tools.awareness.claims import _park_is_whole

    assert verify(_claim("park", reported=[], derived=[], agree=_park_is_whole)).verdict == UNMEASURABLE
    assert verify(_claim("park", reported=["sr-1"], derived=[], agree=_park_is_whole)).verdict == DISAGREES
    assert verify(_claim("park", reported=["sr-1"], derived=["sr-1"], agree=_park_is_whole)).verdict == AGREES


# --------------------------------------------------------------------------- #
# 5. The CLI surfaces it
# --------------------------------------------------------------------------- #
def test_the_list_flag_shows_what_each_claim_was_learned_from(capsys):
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "approval_park_is_whole" in out and "hgx-park-01,rem-hyg-19" in out
    assert "NO INCIDENT" not in out


def test_the_incidents_flag_reports_an_empty_board_as_unmeasurable(monkeypatch, capsys):
    monkeypatch.setattr(ic, "fixed_incidents", lambda window_days=7: [])
    monkeypatch.setattr(ic, "board_status", lambda ids: {i: "done" for i in ids})
    monkeypatch.setattr(ic, "landed_status", lambda ids, repo_root=None: {i: "subject" for i in ids})
    assert main(["--incidents"]) == 0
    out = capsys.readouterr().out
    assert "[unmeasurable]" in out
    assert "UNGUARDED" not in out, "an empty board must not print a count"
