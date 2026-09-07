# CUI // SP-CTI
"""mfx-mrg-04: a protected-path PR lands through the DOOR, with an audited
human reason — never by standing the guard down.

kpr-watch-05's refusal is CORRECT and stays: a watcher that auto-merges a
change to itself runs the next cycle on the new, wrong rule with no human in
the path. THE PROBLEM WAS THE ONLY AVAILABLE OVERRIDE. Measured 2026-09-05/06,
three PRs needed a human merge and got one — mfx-mrg-01 (#2064), mfx-boot-01
(#2066), mfx-sib-03 (#2070) — every one touching ``tools/ci/pr_watcher.py``.
The sole way through was ``ICDEV_GH_PR_MERGE_GUARD=0`` plus a raw
``gh pr merge``, which stands the PreToolUse guard down for EVERY kanban PR in
that shell and runs NONE of ``land.py``'s thirteen checks — including
``ci_green``, which is stricter than branch protection (it refuses a failed
check, a check STILL RUNNING, and an EMPTY rollup). In that same session a
merge attempt on #2070 was refused by GitHub's own branch policy because 12
checks were still running; the blunt override would have taken exactly that
safety away.

Against the pre-change tree ``--protected-ok`` does not exist, ``_auto_merge``
takes no ``protected_ok`` keyword and ``kanban.protected_merge_override`` is not
an admitted audit event type, so this whole file is the recorded RED.

The two tests that matter most are the ones that keep the door NARROW: every
other gate still runs, and no autonomous path can ever set the flag.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib

import pytest

cli = importlib.import_module("tools.kanban.cli")
land = importlib.import_module("tools.kanban.land")
pw = importlib.import_module("tools.ci.pr_watcher")
audit_logger = importlib.import_module("tools.audit.audit_logger")

REPO = pathlib.Path(__file__).resolve().parents[2]
PR = "https://github.com/o/r/pull/1"
GUARDED = ["tools/ci/pr_watcher.py", "args/pr_watcher_config.yaml"]


class _Proc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


class _Watcher(pw.PRWatcher):
    """Watcher with the forge stubbed: one open PR with a known file set.

    ``events`` records every audit write AND every ``gh pr merge`` invocation in
    ORDER, so a test can assert the intent row PRECEDES the merge rather than
    merely coexisting with it.
    """

    def __init__(self, files, *, merge_rc=0, audit_raises=False, **kw):
        super().__init__(config={"auto_merge_enabled": True,
                                 "protected_paths": GUARDED}, **kw)
        self._files = files
        self._merge_rc = merge_rc
        self._audit_raises = audit_raises
        self.events: list = []
        self._auto_merge_runner = self._run
        self._log_event = self._fake_log_event

    def _run(self, cmd, **_kw):
        self.events.append(("merge", tuple(cmd)))
        return _Proc(self._merge_rc, "gh said no")

    def _fake_log_event(self, event_type, actor, action, details=None, **kw):
        if self._audit_raises:
            raise RuntimeError("audit_trail CHECK refused the event type")
        self.events.append(("audit", event_type, actor, action, details, kw))
        return 1

    def _open_pr_index(self, repo=None):
        if self._files is None:
            return {}
        return {PR: {"files": set(self._files), "mergeable": "MERGEABLE",
                     "draft": False}}

    def _audit(self, action):           # the watcher's own best-effort trail
        pass

    @property
    def audits(self):
        return [e for e in self.events if e[0] == "audit"]

    @property
    def merges(self):
        return [e for e in self.events if e[0] == "merge"]


@pytest.fixture()
def watcher(monkeypatch):
    """Route ``audit_logger.log_event`` at the watcher under test.

    ``_audit_protected_override`` imports ``log_event`` inside the function, so
    the MODULE attribute is what has to move — patching a bound method would
    never be reached.
    """
    holder: dict = {}

    def _dispatch(*a, **kw):
        w = holder.get("w")
        assert w is not None, "log_event called with no watcher under test"
        return w._log_event(*a, **kw)

    monkeypatch.setattr(audit_logger, "log_event", _dispatch)

    def _make(files, **kw):
        w = _Watcher(files, **kw)
        holder["w"] = w
        return w
    return _make


# ── the control: nothing about the refusal changes ─────────────────────────
def test_without_the_flag_a_protected_pr_is_still_refused(watcher):
    w = watcher(["tools/ci/pr_watcher.py"])
    assert w._auto_merge(PR) is False
    assert w.merges == [], "refused, and no merge was attempted"


def test_the_flag_defaults_off():
    p = inspect.signature(pw.PRWatcher._auto_merge).parameters["protected_ok"]
    assert p.default is False
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
        "keyword-only, so no positional caller can set it by accident")


# ── the door ───────────────────────────────────────────────────────────────
def test_the_override_merges_a_protected_pr(watcher):
    w = watcher(["tools/ci/pr_watcher.py"])
    assert w._auto_merge(PR, protected_ok=True,
                         override_reason="reviewed by hand",
                         task_id="t-1") is True
    assert w.merges, "the merge must actually be attempted"


def test_the_override_needs_a_written_reason(watcher):
    """The ``--force-done --reason`` precedent: refuse, never a default string."""
    for empty in ("", "   "):
        w = watcher(["tools/ci/pr_watcher.py"])
        assert w._auto_merge(PR, protected_ok=True,
                             override_reason=empty) is False
        assert w.merges == []
        assert w.audits == [], "an unreasoned override must not even be audited"


def test_the_audit_precedes_the_merge_and_quotes_the_reason(watcher):
    w = watcher(["tools/ci/pr_watcher.py"])
    reason = "this card changes _auto_merge itself; 12 checks green"
    assert w._auto_merge(PR, protected_ok=True, override_reason=reason,
                         task_id="mfx-mrg-04") is True
    kinds = [e[0] for e in w.events]
    assert kinds[0] == "audit", "the intent row must be written BEFORE the merge"
    assert "merge" in kinds and kinds.index("audit") < kinds.index("merge")

    _, event_type, actor, action, details, kw = w.events[0]
    assert event_type == "kanban.protected_merge_override"
    assert action == "protected_merge_override.intent"
    assert actor == "pr_watcher"
    assert kw.get("raise_on_error") is True, (
        "fail-closed: no row, no merge — an unaudited override of a "
        "self-protection control is indistinguishable from the defect it guards")
    # The paths the PR ACTUALLY hit, not the configured list.
    assert details["protected_paths_hit"] == ["tools/ci/pr_watcher.py"]
    assert "args/pr_watcher_config.yaml" not in details["protected_paths_hit"]
    assert details["reason"] == reason, "the reason is recorded VERBATIM"
    assert details["task_id"] == "mfx-mrg-04"
    assert details["pr_url"] == PR


def test_no_audit_row_no_merge(watcher):
    """The fail-closed leg. On a PostgreSQL board that has not run migration
    20260906120818 the CHECK refuses the event type and EVERY override is
    refused — the correct reading, not an obstacle."""
    w = watcher(["tools/ci/pr_watcher.py"], audit_raises=True)
    assert w._auto_merge(PR, protected_ok=True, override_reason="why") is False
    assert w.merges == [], "an override that could not be audited must not merge"


def test_the_outcome_is_recorded_too(watcher):
    """prove -> audit -> apply -> CONFIRM. A refused merge must not read the
    same as a landed one."""
    w = watcher(["tools/ci/pr_watcher.py"])
    assert w._auto_merge(PR, protected_ok=True, override_reason="why") is True
    assert [a[3] for a in w.audits] == ["protected_merge_override.intent",
                                        "protected_merge_override.merged"]

    w2 = watcher(["tools/ci/pr_watcher.py"], merge_rc=1)
    assert w2._auto_merge(PR, protected_ok=True, override_reason="why") is False
    assert [a[3] for a in w2.audits] == ["protected_merge_override.intent",
                                         "protected_merge_override.not_merged"]


def test_the_confirm_row_can_never_refuse_a_merge_that_happened(watcher):
    """Best-effort by design: the merge has already landed by then, so a raise
    there would only lose the record of it."""
    w = watcher(["tools/ci/pr_watcher.py"])
    calls = {"n": 0}
    real = w._log_event

    def _second_one_explodes(*a, **kw):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("audit write failed")
        return real(*a, **kw)

    w._log_event = _second_one_explodes
    assert w._auto_merge(PR, protected_ok=True, override_reason="why") is True


def test_the_flag_overrides_nothing_on_an_unprotected_pr(watcher):
    """A PR touching no protected path is an ordinary merge — nothing was
    overridden, so nothing is audited as an override."""
    w = watcher(["README.md"])
    assert w._auto_merge(PR, protected_ok=True, override_reason="why") is True
    assert w.audits == []


def test_a_pr_absent_from_the_listing_is_still_treated_as_protected(watcher):
    """``_protected_hits`` is fail-closed: a PR it cannot see reads as
    protected. The override must honour that rather than slipping through as
    'no hits' — so it still demands a reason, and still audits."""
    w = watcher(None)
    assert w._auto_merge(PR, protected_ok=True, override_reason="") is False
    assert w._auto_merge(PR, protected_ok=True, override_reason="why") is True
    assert [a[3] for a in w.audits][0] == "protected_merge_override.intent"


# ── the hold label is a DIFFERENT guard and is NOT overridden ──────────────
def test_a_hold_label_still_refuses(watcher):
    """One rung, and exactly one. A human put that label there."""
    w = watcher(["tools/ci/pr_watcher.py"])
    assert w._auto_merge(PR, state={"labels": [{"name": "blocked"}]},
                         protected_ok=True, override_reason="why") is False
    assert w.merges == []


# ── the autonomous paths, pinned STRUCTURALLY ──────────────────────────────
def _auto_merge_call_sites(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_auto_merge"]


def test_no_call_site_in_the_watcher_ever_sets_the_flag():
    """READ THE AST, not the behaviour. A behavioural test over today's callers
    passes for a future edit that threads the flag through the poll loop — and
    that one edit turns this door back into the standing hazard kpr-watch-05
    closed. Same discipline ``tools/ci/resume_delivery.py`` is pinned with.

    BOTH SPELLINGS, because a wheel reads the ``icdev/`` copy. Absent files are
    skipped by the LOOP rather than by ``pytest.skip`` — a skipped test
    satisfies the coverage claim while asserting nothing, and ``checked``
    below refuses the case where neither copy was read.
    """
    checked = []
    for rel in ("tools/ci/pr_watcher.py", "icdev/tools/ci/pr_watcher.py"):
        path = REPO / rel
        if not path.exists():
            continue
        sites = _auto_merge_call_sites(path)
        assert sites, f"{rel}: expected the watcher to call _auto_merge"
        for call in sites:
            names = {kw.arg for kw in call.keywords if kw.arg}
            assert "protected_ok" not in names, (
                f"{rel}:{call.lineno} passes protected_ok — the poll loop and "
                "the unlinked sweep must never authorise a protected-path merge")
            assert "override_reason" not in names, f"{rel}:{call.lineno}"
        checked.append(rel)
    assert checked, "neither copy of the watcher was read — this asserted nothing"


def test_land_is_the_only_module_that_threads_it():
    """Exactly one runtime call site may set it, and it is the CLI's lander."""
    threading = []
    for path in sorted((REPO / "tools").rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "protected_ok" not in src:
            continue
        for call in _auto_merge_call_sites(path):
            if any(kw.arg == "protected_ok" for kw in call.keywords if kw.arg):
                threading.append(path.relative_to(REPO).as_posix())
    assert threading == ["tools/kanban/land.py"], (
        f"only land.py may thread protected_ok into _auto_merge; got {threading}")


# ── every other gate still runs ────────────────────────────────────────────
class _LandWatcher:
    """The land.py seam: only what land.py reuses, plus a merge recorder."""

    def __init__(self, state, config=None):
        self.config = {"auto_merge_enabled": True,
                       "auto_merge_require_approval": False}
        self.config.update(config or {})
        self._state = state
        self.merge_calls: list = []

    def _fetch_state(self, _url):
        if self.merge_calls:
            return {**self._state, "state": "MERGED"}
        return self._state

    def _default_branch(self):
        return "main"

    def _auto_merge(self, pr_url, state=None, **kw):
        self.merge_calls.append((pr_url, kw))
        return True


_GREEN = {
    "state": "OPEN", "baseRefName": "main", "mergeable": "MERGEABLE",
    "isDraft": False, "reviews": [],
    "statusCheckRollup": [{"name": "Test", "conclusion": "SUCCESS"}],
}


@pytest.fixture()
def one_pr_task(monkeypatch):
    monkeypatch.setattr(pw, "list_pr_tasks",
                        lambda _c, task_id=None: [{"id": task_id, "pr_url": PR}])
    monkeypatch.setattr(pw, "_enforced_done_ok", lambda _c, _t: (True, "off"))


@pytest.mark.parametrize("state,fragment", [
    ({**_GREEN, "statusCheckRollup": [{"name": "T", "conclusion": "FAILURE"}]},
     "CI is red"),
    ({**_GREEN, "statusCheckRollup": []}, "CI is not green"),
    ({**_GREEN, "mergeable": "CONFLICTING"}, "CONFLICTING"),
    ({**_GREEN, "baseRefName": "feat/x"}, "not the default branch"),
    ({**_GREEN, "state": "CLOSED"}, "not OPEN"),
])
def test_the_other_twelve_checks_still_refuse(one_pr_task, state, fragment):
    """--protected-ok overrides ONE rung. ``ci_green`` is STRICTER than branch
    protection — it refuses a failed check, a check STILL RUNNING, and an EMPTY
    rollup — and ``ICDEV_GH_PR_MERGE_GUARD=0`` + a raw ``gh pr merge`` took
    exactly that away."""
    w = _LandWatcher(state)
    verdict = land.land("t-1", get_conn=lambda: None, watcher=w,
                        sleeper=lambda _s: None, protected_ok=True,
                        override_reason="why")
    assert verdict["ok"] is False and verdict["merged"] is False
    assert fragment in verdict["reason"]
    assert w.merge_calls == [], "a failing gate must be refused before the flag"


def test_land_passes_the_reason_down(one_pr_task):
    w = _LandWatcher(dict(_GREEN))
    verdict = land.land("t-1", get_conn=lambda: None, watcher=w,
                        sleeper=lambda _s: None, protected_ok=True,
                        override_reason="reviewed by hand")
    assert verdict["merged"] is True
    _, kw = w.merge_calls[0]
    assert kw["protected_ok"] is True
    assert kw["override_reason"] == "reviewed by hand"
    assert kw["task_id"] == "t-1"


def test_land_defaults_the_flag_off(one_pr_task):
    w = _LandWatcher(dict(_GREEN))
    land.land("t-1", get_conn=lambda: None, watcher=w, sleeper=lambda _s: None)
    _, kw = w.merge_calls[0]
    assert kw["protected_ok"] is False


# ── the CLI door ───────────────────────────────────────────────────────────
def test_cli_refuses_protected_ok_without_a_reason(capsys):
    rc = cli.cmd_set_status(
        ["t-1"], "done", False, merge=True, protected_ok=True, reason="",
        lander=lambda *a, **k: pytest.fail("the lander must never be reached"))
    assert rc == 1
    assert "--protected-ok requires --reason" in capsys.readouterr().err


def test_cli_refuses_protected_ok_without_merge(capsys):
    rc = cli.cmd_set_status(["t-1"], "done", False, protected_ok=True,
                            reason="why")
    assert rc == 1
    assert "--protected-ok only applies" in capsys.readouterr().err


def test_cli_threads_the_flag_and_the_reason():
    seen: dict = {}

    def _lander(task_id, **kw):
        seen.update(task=task_id, **kw)
        return {"ok": True, "merged": False, "reason": "preflight passed",
                "checks": [], "pr_url": PR}

    rc = cli.cmd_set_status(["t-1"], "done", False, merge=True, dry_run=True,
                            protected_ok=True, reason="  reviewed by hand  ",
                            lander=_lander)
    assert rc == 0
    assert seen["protected_ok"] is True
    assert seen["override_reason"] == "reviewed by hand"


def test_the_cli_flag_is_declared_and_off_by_default():
    src = (REPO / "tools/kanban/cli.py").read_text(encoding="utf-8")
    assert '"--protected-ok"' in src
    assert 'dest="protected_ok"' in src and 'action="store_true"' in src
    assert "protected_ok=args.protected_ok" in src, (
        "declared but never threaded is the defect this platform ships most")


# ── the vocabulary ─────────────────────────────────────────────────────────
def test_the_event_type_is_admitted():
    """A type the deployed CHECK does not admit is rejected on log_event's first
    line — and with raise_on_error=True that refuses the merge."""
    assert "kanban.protected_merge_override" in audit_logger.VALID_EVENT_TYPES
    assert ("kanban.protected_merge_override"
            in audit_logger.event_type_check_sql())


def test_a_migration_rebuilds_the_check():
    d = REPO / "tools/db/migrations"
    hits = [p for p in d.glob("*protected_merge_override*") if p.is_dir()]
    assert hits, "the CHECK on an existing PG database must be rebuilt too"
    assert (hits[0] / "up.py").exists(), (
        "a migration directory with neither up.sql nor up.py is skipped "
        "SILENTLY and never runs at all")
