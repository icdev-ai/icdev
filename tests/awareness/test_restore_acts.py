# CUI // SP-CTI
"""The restore tier is a CLOSED set, and every act audits BEFORE it acts (autonomy-act-03).

Four invariants, each of which fails GREEN if broken:

  * the registry holds exactly three names and cannot grow at runtime — an
    open-ended "the agent decides what to fix" is an unaudited actuator with
    write access to its own guardrails;
  * cannot-tell REFUSES. A dead pid alone never reaps a lease (the holder pid is
    the dispatcher's, which exits while the worker runs on); an unreadable
    heartbeat, an unknown supervisor, an unreadable command line all refuse;
  * the intent row is written before the act, with the act's evidence, and an
    intent row that cannot be written means the act does not run;
  * no act edits a claim, a threshold or an assertion, and nothing here
    UPDATEs or DELETEs audit_trail.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import restore_acts as ra  # noqa: E402
from tools.awareness.claim_verifier import TIER  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeLeases:
    """A lease store with a settable liveness answer."""

    def __init__(self, meta=None, alive=None):
        self.meta = meta
        self.alive = alive
        self.release_calls = 0

    def holder(self, resource):
        return self.meta

    def holder_is_alive(self, resource):
        return self.alive

    def release_stale(self, resource):
        self.release_calls += 1
        self.meta = None
        return True

    def list_leases(self):
        return [self.meta] if self.meta else []


class Recorder:
    def __init__(self, fail=False):
        self.rows = []
        self.fail = fail

    def __call__(self, action, details):
        if self.fail:
            raise RuntimeError("CHECK constraint audit_trail_event_type_check failed")
        json.dumps(details)                    # the row must be serialisable
        self.rows.append((action, details))
        return len(self.rows)


_META = {"resource": "kanban:task:t-1", "pid": 4242, "holder_session": "s",
         "acquired_at": "2026-08-21T00:00:00+00:00"}


# --------------------------------------------------------------------------- #
# 1. The set is closed
# --------------------------------------------------------------------------- #
def test_exactly_four_acts_and_the_registry_is_frozen():
    # Three from autonomy-act-03; the fourth (autonomy-dep-04) was a deliberate
    # addition to a CLOSED set, pinned here so a fifth is a decision too.
    assert set(ra.ACTS) == {"reap_dead_lease", "prune_gone_census_entry",
                            "restart_stale_daemon", "restore_auto_managed_file"}
    with pytest.raises(TypeError):
        ra.ACTS["edit_threshold_so_it_agrees"] = None  # type: ignore[index]
    for act in ra.ACTS.values():
        assert act.reverse, f"{act.name} must state how it is undone"
        with pytest.raises(Exception):
            act.name = "other"                 # frozen dataclass


def test_an_unenumerated_act_is_refused_without_touching_anything():
    rec = Recorder()
    r = ra.perform("rewrite_claim", "x", audit=rec)
    assert r["outcome"] == ra.REFUSED
    assert rec.rows == []


def test_tier_text_names_the_registry_and_no_editing_tier_exists():
    assert "restore_acts.py::ACTS" in TIER["restore"]
    assert set(TIER) == {"report", "restore", "propose"}


def test_no_act_imports_the_claims_registry_or_touches_audit_rows():
    """Structural. The module that performs repairs must not be able to reach
    the claims it is meant to satisfy, and must never UPDATE/DELETE the audit
    table that makes it accountable."""
    src = (ROOT / "tools/awareness/restore_acts.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert "tools.awareness.claims" not in imported
    assert not any(name.startswith("tools.awareness.claims.") for name in imported)
    upper = src.upper()
    assert "UPDATE AUDIT_TRAIL" not in upper and "DELETE FROM AUDIT_TRAIL" not in upper
    assert ".yaml" not in src.lower(), "no act may write a threshold file"


# --------------------------------------------------------------------------- #
# 2. reap_dead_lease — two signals, and cannot-tell is alive
# --------------------------------------------------------------------------- #
def test_dead_pid_and_no_heartbeat_is_proven():
    p = ra.prove_dead_lease("t-1", leases=FakeLeases(_META, alive=False),
                            heartbeating=lambda tid: False)
    assert p.proven is True
    assert p.evidence["holder_alive"] is False
    assert p.evidence["task_heartbeating"] is False


def test_dead_pid_but_heartbeating_refuses():
    """rem-hyg-13 measured holder_is_alive() False four seconds after a
    heartbeat: the dispatcher's pid dies while the worker runs on."""
    p = ra.prove_dead_lease("t-1", leases=FakeLeases(_META, alive=False),
                            heartbeating=lambda tid: True)
    assert p.proven is False
    assert "heartbeating" in p.reason


def test_cannot_tell_liveness_refuses():
    p = ra.prove_dead_lease("t-1", leases=FakeLeases(_META, alive=None),
                            heartbeating=lambda tid: False)
    assert p.proven is None, "an unknown must never license a reap"


def test_unreadable_heartbeat_refuses():
    def boom(tid):
        raise RuntimeError("db down")
    p = ra.prove_dead_lease("t-1", leases=FakeLeases(_META, alive=False), heartbeating=boom)
    assert p.proven is None


def test_live_holder_and_free_lease_refuse():
    assert ra.prove_dead_lease("t-1", leases=FakeLeases(_META, alive=True),
                               heartbeating=lambda t: False).proven is False
    assert ra.prove_dead_lease("t-1", leases=FakeLeases(None, alive=False),
                               heartbeating=lambda t: False).proven is False


def test_only_the_kanban_task_namespace_is_in_scope():
    p = ra.prove_dead_lease("service:dashboard", leases=FakeLeases(_META, alive=False),
                            heartbeating=lambda t: False)
    assert p.proven is False


def test_reap_writes_the_intent_row_before_releasing_and_confirms():
    store = FakeLeases(dict(_META), alive=False)
    rec = Recorder()
    order = []
    original = store.release_stale

    def release(resource):
        order.append(("release", len(rec.rows)))
        return original(resource)
    store.release_stale = release

    r = ra.perform("reap_dead_lease", "t-1", audit=rec, leases=store,
                   heartbeating=lambda t: False)
    assert r["outcome"] == ra.APPLIED and r["confirmed"] is True
    assert order == [("release", 1)], "the intent row must exist BEFORE the release"
    actions = [a for a, _ in rec.rows]
    assert actions == ["restore.reap_dead_lease.intent", "restore.reap_dead_lease.applied"]
    intent = rec.rows[0][1]
    assert intent["evidence"]["holder_pid"] == 4242 and intent["reverse"]


def test_unwritable_intent_row_stops_the_act():
    store = FakeLeases(dict(_META), alive=False)
    r = ra.perform("reap_dead_lease", "t-1", audit=Recorder(fail=True), leases=store,
                   heartbeating=lambda t: False)
    assert r["outcome"] == ra.UNAUDITED_REFUSED
    assert store.release_calls == 0, "no row, no act"
    assert store.meta is not None


def test_dry_run_proves_and_neither_audits_nor_acts():
    store = FakeLeases(dict(_META), alive=False)
    rec = Recorder()
    r = ra.perform("reap_dead_lease", "t-1", dry_run=True, audit=rec, leases=store,
                   heartbeating=lambda t: False)
    assert r["outcome"] == ra.WOULD_APPLY
    assert rec.rows == [] and store.release_calls == 0


def test_refusal_writes_no_row():
    rec = Recorder()
    r = ra.perform("reap_dead_lease", "t-1", audit=rec, leases=FakeLeases(_META, alive=None),
                   heartbeating=lambda t: False)
    assert r["outcome"] == ra.REFUSED and rec.rows == []


# --------------------------------------------------------------------------- #
# 3. prune_gone_census_entry — the file must be GONE, and only one line moves
# --------------------------------------------------------------------------- #
def _repo(tmp_path: Path) -> Path:
    (tmp_path / "args").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "alive.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "args" / "undeclared_import_census.txt").write_text(
        "# header\n"
        "tools/alive.py::<module>::yaml\n"
        "tools/gone.py::<module>::yaml\n"
        "tools/gone.py::other::requests\n",
        encoding="utf-8")
    (tmp_path / "args" / "ci_test_backlog.txt").write_text(
        "tests/test_alive.py\ntests/test_gone.py\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_alive.py").write_text("", encoding="utf-8")
    return tmp_path


def test_gone_entries_are_found_only_where_the_file_is_missing(tmp_path):
    repo = _repo(tmp_path)
    gone = ra.gone_census_entries(repo)
    assert {(g["census"], g["entry"]) for g in gone} == {
        ("args/undeclared_import_census.txt", "tools/gone.py::<module>::yaml"),
        ("args/undeclared_import_census.txt", "tools/gone.py::other::requests"),
        ("args/ci_test_backlog.txt", "tests/test_gone.py"),
    }


def test_an_existing_file_is_never_pruned_here(tmp_path):
    """A FIXED site with a live file is the scanner's --prune to decide; this
    act proves only what Path.exists() can."""
    p = ra.prove_gone_entry("tools/alive.py::<module>::yaml", root=_repo(tmp_path))
    assert p.proven is False and "still exists" in p.reason


def test_prune_removes_exactly_one_line_and_confirms(tmp_path):
    repo = _repo(tmp_path)
    rec = Recorder()
    r = ra.perform("prune_gone_census_entry", "tools/gone.py::<module>::yaml",
                   audit=rec, root=repo)
    assert r["outcome"] == ra.APPLIED and r["applied"]["dropped"] == 1
    text = (repo / "args/undeclared_import_census.txt").read_text(encoding="utf-8")
    assert text == ("# header\n"
                    "tools/alive.py::<module>::yaml\n"
                    "tools/gone.py::other::requests\n"), "the other gone line stays — one act, one line"
    assert [a for a, _ in rec.rows][0] == "restore.prune_gone_census_entry.intent"


def test_entry_in_no_census_or_naming_no_path_refuses(tmp_path):
    repo = _repo(tmp_path)
    assert ra.prove_gone_entry("tools/never.py::x", root=repo).proven is False
    assert ra.prove_gone_entry("just prose", root=repo).proven is False
    assert ra._entry_path("tests/**/*.py") is None


# --------------------------------------------------------------------------- #
# 4. restart_stale_daemon — supervisor UP, verdict STALE, pid VERIFIED
# --------------------------------------------------------------------------- #
def _stale_report(module="tools.genesis.daemon", pid=777, verdict="stale"):
    return {"state": "measured", "processes": [{
        "module": module, "pid": pid, "verdict": verdict, "code_version": "abc1234",
        "changed_in_closure": ["tools/genesis/daemon.py"], "changed_count": 1}]}


_SUP_UP = {"state": "up", "pid": 1, "reason": None}


def test_stale_supervised_child_with_matching_cmdline_is_proven():
    p = ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: _SUP_UP,
        staleness_fn=_stale_report,
        cmdline_fn=lambda pid: ["python", "tools/genesis/daemon.py"])
    assert p.proven is True and p.evidence["pid"] == 777


def test_no_supervisor_means_a_kill_not_a_restart():
    p = ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: {"state": "down", "pid": None},
        staleness_fn=_stale_report, cmdline_fn=lambda pid: ["python", "tools/genesis/daemon.py"])
    assert p.proven is False and "kill" in p.reason


def test_unknown_supervisor_unmeasurable_staleness_and_unreadable_cmdline_refuse():
    kw = dict(staleness_fn=_stale_report,
              cmdline_fn=lambda pid: ["python", "tools/genesis/daemon.py"])
    assert ra.prove_stale_daemon("tools.genesis.daemon",
                                 supervisor_fn=lambda: {"state": "unknown", "pid": 1},
                                 **kw).proven is None
    assert ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: _SUP_UP,
        staleness_fn=lambda: {"state": "unmeasurable", "reason": "no git"},
        cmdline_fn=kw["cmdline_fn"]).proven is None
    assert ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: _SUP_UP,
        staleness_fn=_stale_report, cmdline_fn=lambda pid: None).proven is None
    assert ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: _SUP_UP,
        staleness_fn=lambda: _stale_report(verdict="unmeasurable"),
        cmdline_fn=kw["cmdline_fn"]).proven is None


def test_current_process_and_reused_pid_refuse():
    assert ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: _SUP_UP,
        staleness_fn=lambda: _stale_report(verdict="current"),
        cmdline_fn=lambda pid: ["python", "tools/genesis/daemon.py"]).proven is False
    p = ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: _SUP_UP, staleness_fn=_stale_report,
        cmdline_fn=lambda pid: ["python", "-m", "pytest"])
    assert p.proven is False and "reused" in p.reason


def test_a_shell_that_merely_typed_the_name_is_not_the_child():
    p = ra.prove_stale_daemon(
        "tools.genesis.daemon", supervisor_fn=lambda: _SUP_UP, staleness_fn=_stale_report,
        cmdline_fn=lambda pid: ["bash", "-c", "grep tools/genesis/daemon.py"])
    assert p.proven is False


def test_only_supervised_services_can_be_restarted():
    p = ra.prove_stale_daemon(
        "tools.cortex.api", supervisor_fn=lambda: _SUP_UP,
        staleness_fn=lambda: _stale_report(module="tools.cortex.api"),
        cmdline_fn=lambda pid: ["python", "tools/cortex/api.py"])
    assert p.proven is False and "not a supervised service" in p.reason
    # And the two daemons named `daemon` resolve to DIFFERENT services.
    assert ra._service_for_module("tools.genesis.daemon").name == "genesis_daemon"
    assert ra._service_for_module("proposal_genesis.daemon").name == "proposal_genesis"
    assert ra._service_for_module("tools.ci.pr_watcher").name == "pr_watcher"


def test_restart_terminates_gracefully_after_the_intent_row_and_confirms_exit():
    rec = Recorder()
    kills = []
    alive = {"on": True}

    def kill(pid, force):
        kills.append((pid, force, len(rec.rows)))
        alive["on"] = False
        return True

    r = ra.perform(
        "restart_stale_daemon", "tools.genesis.daemon", audit=rec,
        supervisor_fn=lambda: _SUP_UP, staleness_fn=_stale_report,
        cmdline_fn=lambda pid: ["python", "tools/genesis/daemon.py"],
        kill_fn=kill, pid_exists_fn=lambda pid: alive["on"], sleep=lambda s: None)
    assert r["outcome"] == ra.APPLIED
    assert kills == [(777, False, 1)], "terminate (not kill), after exactly one row"
    assert r["applied"]["terminate_sent"] is True and r["confirmed"] is True


def test_a_child_that_does_not_exit_is_applied_unconfirmed_never_applied():
    r = ra.perform(
        "restart_stale_daemon", "tools.genesis.daemon", audit=Recorder(),
        supervisor_fn=lambda: _SUP_UP, staleness_fn=_stale_report,
        cmdline_fn=lambda pid: ["python", "tools/genesis/daemon.py"],
        kill_fn=lambda pid, force: True, pid_exists_fn=lambda pid: True,
        sleep=lambda s: None, wait_seconds=0.0)
    assert r["outcome"] == ra.APPLIED_UNCONFIRMED


# --------------------------------------------------------------------------- #
# 5. The plan acts on nothing and says what it measured
# --------------------------------------------------------------------------- #
def test_plan_reproves_every_candidate_and_acts_on_nothing(tmp_path):
    repo = _repo(tmp_path)
    store = FakeLeases(dict(_META), alive=False)
    rep = ra.plan(repo, leases=store, heartbeating=lambda t: True,
                  staleness_fn=_stale_report, supervisor_fn=lambda: _SUP_UP,
                  cmdline_fn=lambda pid: ["python", "tools/genesis/daemon.py"])
    by_act = {}
    for c in rep["candidates"]:
        by_act.setdefault(c["act"], []).append(c)
    assert [c["proven"] for c in by_act["reap_dead_lease"]] == [False]   # heartbeating
    assert len(by_act["prune_gone_census_entry"]) == 3
    assert by_act["restart_stale_daemon"][0]["proven"] is True
    assert rep["provable"] == 4 and rep["refused"] == 1
    assert store.release_calls == 0
    assert (repo / "args/undeclared_import_census.txt").read_text(encoding="utf-8").count("gone") == 2
    assert rep["staleness_state"] == "measured" and rep["census_files_read"] == 2
    text = ra.render_plan(rep)
    assert "ACTS NOTHING" in text and "staleness=measured" in text


def test_plan_over_an_unmeasured_fleet_says_so(tmp_path):
    rep = ra.plan(tmp_path, leases=FakeLeases(None),
                  staleness_fn=lambda: {"state": "unmeasurable", "reason": "no registry"})
    assert rep["candidates"] == []
    assert rep["staleness_state"] == "unmeasurable"
    assert rep["census_files_read"] == 0
    assert "staleness=unmeasurable" in ra.render_plan(rep)


def test_default_audit_goes_through_log_event_with_the_admitted_type(monkeypatch):
    from tools.audit import audit_logger
    assert ra.AUDIT_EVENT_TYPE in audit_logger.VALID_EVENT_TYPES
    seen = {}

    def fake_log_event(event_type, actor, action, **kw):
        seen.update(event_type=event_type, actor=actor, action=action, **kw)
        return 7
    monkeypatch.setattr(audit_logger, "log_event", fake_log_event)
    assert ra._default_audit("restore.x.intent", {"a": 1}) == 7
    assert seen["event_type"] == ra.AUDIT_EVENT_TYPE
    assert seen["raise_on_error"] is True, "fail-closed: the audit must raise, not return -1"


def test_cli_list_and_apply_require_target(capsys):
    assert ra.main(["--list"]) == 0
    assert "reap_dead_lease" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ra.main(["--apply", "reap_dead_lease"])


def _unused(*_a, **_k):  # keeps SimpleNamespace import honest for future fakes
    return SimpleNamespace()
