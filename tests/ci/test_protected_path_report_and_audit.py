# CUI // SP-CTI
"""kpr-watch-10: two defects in the protected-path guard, both found in production.

kpr-watch-05 shipped a guard that refuses to auto-merge a PR touching
`pr_watcher.py` and friends. Checked against the live board 2026-08-19, it was
refusing correctly and getting two things wrong around it.

1. THE REPORT COULD NOT SEE THE POLICY THE MERGER HAD. `merge_readiness --json`
   reported PRs 1818/1819/1820 as `linked` while the watcher was actively
   refusing them as `protected_path`. The rung was never evaluated there: the
   CLI fetched `files` but never passed `protected_paths`. That is precisely the
   failure the shared classifier exists to prevent — the module's own docstring
   promises "the report can never describe a merge policy the merger does not
   have" — and anyone asking "why is this not merging?" got a misleading answer.

2. THE HOLD RE-AUDITED EVERY POLL. 161 `pr_watcher.protected_path_hold` rows in
   59 minutes for two PRs. `audit_trail` is append-only and the watcher polls
   continuously, so a PR held over a weekend writes thousands of identical rows
   and the signal is buried in its own repetition. A hold is an EVENT.

The second one has no inherited excuse: `behind_main_hold`, the branch this
pattern was copied from, has 0 rows lifetime.
"""
from __future__ import annotations

import pathlib

import yaml

import tools.ci.merge_readiness as mr
import tools.ci.pr_watcher as pw

REPO = pathlib.Path(__file__).resolve().parents[2]
URL = "https://github.com/o/r/pull/1"


def _green(url=URL, files=("tools/ci/pr_watcher.py",)):
    pr = {
        "url": url, "number": 1, "state": "OPEN", "isDraft": False,
        "baseRefName": "main", "mergeable": "MERGEABLE", "labels": [],
        "reviews": [], "statusCheckRollup": [{"name": "T", "conclusion": "SUCCESS"}],
    }
    if files is not None:
        pr["files"] = [{"path": p} for p in files]
    return pr


# ── 1. the report evaluates the merger's rung ──────────────────────────────
def test_a_protected_pr_is_not_reported_as_merely_linked():
    """The exact live symptom: the watcher refused it, the report said `linked`."""
    report = mr.build_report(
        [_green()], default_branch="main", linked_urls=[URL],
        protected_paths=["tools/ci/pr_watcher.py"])
    assert report["prs"][0]["state"] == mr.PROTECTED_PATH
    assert report["prs"][0]["ready"] is False


def test_without_the_list_the_old_answer_stands():
    """Back-compat: an unconfigured report must behave exactly as before, or
    every caller starts seeing a state it has never handled."""
    report = mr.build_report([_green()], default_branch="main", linked_urls=[URL])
    assert report["prs"][0]["state"] == mr.LINKED


def test_an_unprotected_pr_is_unaffected():
    report = mr.build_report(
        [_green(files=("README.md",))], default_branch="main",
        protected_paths=["tools/ci/pr_watcher.py"])
    assert report["prs"][0]["state"] == mr.READY


# ── the files field, and the honest unknown ────────────────────────────────
def test_changed_files_reads_the_gh_shape():
    assert mr._changed_files(_green(files=("a.py", "b.py"))) == ["a.py", "b.py"]


def test_a_record_with_no_files_key_is_None_not_empty():
    """None fails CLOSED at the rung; [] would read as "touches nothing" and
    make the report MORE optimistic than the merger, which is the direction that
    matters."""
    assert mr._changed_files(_green(files=None)) is None
    assert mr._changed_files({"url": URL, "files": []}) == []


def test_an_unreadable_record_still_fails_closed_in_the_report():
    report = mr.build_report(
        [_green(files=None)], default_branch="main",
        protected_paths=["tools/ci/pr_watcher.py"])
    assert report["prs"][0]["state"] == mr.PROTECTED_PATH
    assert "could not be determined" in report["prs"][0]["reason"]


# ── one list, read from the merger's own config ────────────────────────────
def test_the_report_reads_the_WATCHERS_config():
    """Two lists would drift, and a report naming a different set than the
    merger enforces is the defect this whole card is about."""
    paths = mr.load_protected_paths()
    shipped = yaml.safe_load(
        (REPO / "args/pr_watcher_config.yaml").read_text(encoding="utf-8")) or {}
    assert paths == [str(p).strip() for p in (shipped.get("protected_paths") or [])]
    assert "tools/ci/pr_watcher.py" in paths


def test_a_missing_config_degrades_to_no_protection_not_a_crash(tmp_path):
    assert mr.load_protected_paths(tmp_path / "nope.yaml") == []


# ── 2. the hold is an event, not a heartbeat ───────────────────────────────
class _Watcher(pw.PRWatcher):
    def __init__(self, *, already_held: bool):
        super().__init__(config={"auto_merge_enabled": True,
                                 "protected_paths": ["tools/ci/pr_watcher.py"]})
        self._already = already_held
        self.audits = []

    def _open_pr_index(self):
        return {URL: {"files": {"tools/ci/pr_watcher.py"},
                      "mergeable": "MERGEABLE", "draft": False}}

    def _count_audit_actions(self, task_id, actions, pr_url=None):
        assert actions == ("pr_watcher.protected_path_hold",)
        return 1 if self._already else 0

    def _audit(self, action):
        self.audits.append(action)


def test_the_first_hold_is_audited_and_warned(caplog):
    w = _Watcher(already_held=False)
    pw.logger.propagate = True
    with caplog.at_level("WARNING", logger=pw.logger.name):
        assert w._refuse_protected(URL) == ["tools/ci/pr_watcher.py"]
    assert len(w.audits) == 1
    assert any("REFUSING to merge" in r.getMessage() for r in caplog.records)


def test_a_standing_hold_is_NOT_audited_again():
    """161 rows in 59 minutes was the measurement. One PR, one hold, one row."""
    w = _Watcher(already_held=True)
    assert w._refuse_protected(URL) == ["tools/ci/pr_watcher.py"]
    assert w.audits == [], "a standing hold must not write a second audit row"


def test_a_standing_hold_still_REFUSES():
    """Dedupe must apply to the RECORD, never to the refusal. Skipping the audit
    must not turn into skipping the guard."""
    for already in (False, True):
        assert _Watcher(already_held=already)._refuse_protected(URL), \
            "the hit list is the refusal — it must be returned either way"


def test_a_standing_hold_still_says_so_at_debug(caplog):
    """A held PR that stops appearing anywhere is how AWAITING MERGE went quiet
    in the first place. Quieter, never silent."""
    w = _Watcher(already_held=True)
    pw.logger.propagate = True
    with caplog.at_level("DEBUG", logger=pw.logger.name):
        w._refuse_protected(URL)
    assert any("still refusing" in r.getMessage() for r in caplog.records)


def test_the_dedupe_reads_the_audit_not_a_memory_set():
    """An in-memory set would restart the storm on every daemon restart —
    exactly when it would be least noticed."""
    import inspect

    src = inspect.getsource(pw.PRWatcher._protected_already_held)
    assert "_count_audit_actions" in src


def test_the_sweep_branch_dedupes_too():
    """Two writers, one fix. The unlinked sweep was the LOUDER of the two —
    it reaches this branch every poll for as long as the PR sits there."""
    import inspect

    src = inspect.getsource(pw)
    i = src.index("elif verdict.state == PROTECTED_PATH:")
    assert "_protected_already_held" in src[i:i + 900], (
        "the sweep must dedupe its audit as well, or the fix covers one path")
