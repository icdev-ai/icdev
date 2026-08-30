# CUI // SP-CTI
"""A `done` task must have an ARTIFACT, and a citation is not one (kpr-rvfy-04).

Two mechanisms put five ``ftp-*`` tasks into ``done`` on 2026-08-29 with their
deliverables absent from ``origin/main``, and each gets its own class here:

  * a task id present on main ONLY inside a source comment — a forward
    reference naming the card that WILL do the work — must never read as a
    landing (:class:`TestFileContentIsNotALanding`);
  * a task nothing ever dispatched, with no branch and nothing to merge, must
    not be completable by any automatic path
    (:class:`TestDeliveryEvidenceGate`).

Every git-backed test builds a REAL repository in ``tmp_path`` and runs real
git. The defect is about what git says, so a mocked git would prove only that
the mock returns what the test wrote into it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.kanban import artifact_evidence as ae  # noqa: E402
from tools.kanban import landed_check as lc  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repo with an ``origin/main`` that is a real remote-tracking ref."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["git", "clone", str(origin), str(work)],
                   capture_output=True, check=True, timeout=60)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "-u", "origin", "main")
    return work


class TestFileContentIsNotALanding:
    """A forward reference in a comment is evidence the work has NOT happened."""

    def test_comment_mention_is_classified_file_content_and_never_landed(self, repo):
        # The exact shape measured on ICDEV[FT]'s main: the env wizard names the
        # token an auth module that does not exist yet will consume.
        (repo / "setup_ft.py").write_text(
            "# FIN_API_TOKEN is consumed by the API auth middleware (ftp-prd-08)\n"
            "TOKEN_ENV = 'FIN_API_TOKEN'\n",
            encoding="utf-8",
        )
        _git(repo, "add", "-A")
        # The COMMIT MESSAGE deliberately does not name the task: the only
        # trace of ftp-prd-08 anywhere is the comment.
        _git(repo, "commit", "-m", "chore: name the token the wizard writes")
        _git(repo, "push", "origin", "main")

        report = lc.check_file_content_bulk(["ftp-prd-08"], repo_root=repo)["ftp-prd-08"]
        assert report["checked"] is True
        assert report["files"] == ["setup_ft.py"]
        assert report["confidence"] == lc.CONFIDENCE_FILE_CONTENT
        # The whole point: found, and still not a landing.
        assert report["landed"] is False
        assert report["referenced"] is True

    def test_file_content_can_never_satisfy_a_gate(self):
        """The tier is absent from the blocking set, and named as a citation."""
        assert lc.CONFIDENCE_FILE_CONTENT not in lc.BLOCKING_CONFIDENCE
        assert lc.CONFIDENCE_FILE_CONTENT in lc.NON_LANDING_CONFIDENCE
        # It ranks below `body`, which is itself already advisory-only.
        assert (lc._CONFIDENCE_RANK[lc.CONFIDENCE_FILE_CONTENT]
                < lc._CONFIDENCE_RANK[lc.CONFIDENCE_BODY])

    def test_a_comment_mention_does_not_complete_the_task(self, repo):
        """End to end: the id is on main, the DELIVERABLE is not, done is refused.

        This is the card's whole claim in one assertion. ``git grep ftp-prd-08
        origin/main`` returns a hit, and every honest question about the task
        still answers "not delivered".
        """
        (repo / "setup_ft.py").write_text(
            "# see ftp-prd-08 for the auth middleware that will read this\n",
            encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "chore: wizard note")
        _git(repo, "push", "origin", "main")

        # The naive survey finds it...
        grep = _git(repo, "grep", "-l", "ftp-prd-08", "origin/main")
        assert grep.returncode == 0 and "setup_ft.py" in grep.stdout

        # ...and every gate says no.
        assert lc.check_landed_bulk(
            ["ftp-prd-08"], repo_root=repo)["ftp-prd-08"]["landed"] is False
        artifact = ae.artifact_report(
            "ftp-prd-08",
            title="PRD - API auth",
            description="FIX: (1) new ft_api/auth.py + Starlette middleware.",
            repo_root=repo, ref="origin/main",
        )
        assert artifact["declared"] == ["ft_api/auth.py"]
        assert artifact["state"] == ae.STATE_ABSENT
        assert artifact["missing"] == ["ft_api/auth.py"]


class TestDeclaredArtifacts:
    """Only a path the card says it will CREATE is a deliverable."""

    def test_creation_marker_is_required(self):
        declared = ae.declared_artifacts(
            "EZB - layman UI",
            "The chip is hardcoded in ui/src/routes/ops.tsx. Add "
            "GET /api/config/runtime to ft_api/routers/config.py; new "
            "ui/src/lib/glossary.ts exporting GLOSSARY.",
        )
        # `ui/src/routes/ops.tsx` is CITED, never declared — it already exists,
        # and counting it would make the card report `present` for free.
        assert "ui/src/routes/ops.tsx" not in declared
        assert "ui/src/lib/glossary.ts" in declared

    def test_a_substring_verb_does_not_count_as_a_marker(self):
        assert ae.declared_artifacts("", "renew tools/foo/bar.py") == []
        assert ae.declared_artifacts("", "address tools/foo/bar.py") == []
        assert ae.declared_artifacts("", "new tools/foo/bar.py") == ["tools/foo/bar.py"]

    def test_shared_registries_are_never_a_deliverable(self):
        assert ae.declared_artifacts("", "add a row to tools/manifest.md") == []

    def test_a_card_declaring_nothing_is_unmeasurable_not_clean(self, repo):
        report = ae.artifact_report(
            "x-y-01", title="tighten a threshold",
            description="lower max_behind_commits from 12 to 10.",
            repo_root=repo, ref="origin/main",
        )
        assert report["state"] == ae.STATE_UNMEASURABLE
        assert report["declared"] == []
        assert "nothing to verify" in report["reason"]


class TestArtifactReportStates:
    def test_present_partial_absent(self, repo):
        (repo / "pkg").mkdir()
        (repo / "pkg" / "there.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add one of two")
        _git(repo, "push", "origin", "main")

        both = ae.artifact_report(
            "t-1", "", "new pkg/there.py and new pkg/missing.py",
            repo_root=repo, ref="origin/main")
        assert both["state"] == ae.STATE_PARTIAL

        present = ae.artifact_report(
            "t-2", "", "new pkg/there.py", repo_root=repo, ref="origin/main")
        assert present["state"] == ae.STATE_PRESENT

        absent = ae.artifact_report(
            "t-3", "", "new pkg/missing.py", repo_root=repo, ref="origin/main")
        assert absent["state"] == ae.STATE_ABSENT

    def test_a_gitignored_path_is_never_a_finding(self, repo):
        """MEASURED false positive: ftl-sched-03 / args/ft_scheduler.local.yaml.

        A path git is told never to track cannot be on any branch, so "it is not
        on main" says nothing about the card. It is kept out of BOTH `present`
        and `missing`.
        """
        (repo / ".gitignore").write_text("args/*.local.yaml\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "chore: ignore local overrides")
        _git(repo, "push", "origin", "main")

        report = ae.artifact_report(
            "ftl-sched-03", "", "new args/ft_scheduler.local.yaml",
            repo_root=repo, ref="origin/main")
        assert report["state"] == ae.STATE_UNMEASURABLE
        assert report["ignored"] == ["args/ft_scheduler.local.yaml"]
        assert report["missing"] == []
        assert "gitignored" in report["reason"]

    def test_a_uniquely_resolving_relative_path_is_present(self, repo):
        """MEASURED false positive: ftl-val-05 / families/__init__.py.

        The card wrote the path relative to a subdirectory; the file is on main
        at icdev_fin/backtest/families/__init__.py.
        """
        deep = repo / "pkg" / "backtest" / "families"
        deep.mkdir(parents=True)
        (deep / "__init__.py").write_text("", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat: families")
        _git(repo, "push", "origin", "main")

        report = ae.artifact_report(
            "ftl-val-05", "", "new families/__init__.py",
            repo_root=repo, ref="origin/main")
        assert report["state"] == ae.STATE_PRESENT
        assert report["resolved_relative"] == [
            {"declared": "families/__init__.py",
             "found": "pkg/backtest/families/__init__.py"}
        ], "the move must be RECORDED, not silently applied"

    def test_an_ambiguous_relative_path_does_not_resolve(self, repo):
        """Two candidates is a guess, and a guess is not evidence."""
        for parent in ("a", "b"):
            d = repo / parent / "families"
            d.mkdir(parents=True)
            (d / "__init__.py").write_text("", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat: two families")
        _git(repo, "push", "origin", "main")

        report = ae.artifact_report(
            "t-amb", "", "new families/__init__.py",
            repo_root=repo, ref="origin/main")
        assert report["state"] == ae.STATE_ABSENT
        assert report["missing"] == ["families/__init__.py"]
        assert report["resolved_relative"] == []

    def test_a_genuinely_missing_path_is_still_a_finding(self, repo):
        """The narrowings must not swallow the case the survey exists for."""
        report = ae.artifact_report(
            "ftp-prd-08", "", "new ft_api/auth.py",
            repo_root=repo, ref="origin/main")
        assert report["state"] == ae.STATE_ABSENT
        assert report["missing"] == ["ft_api/auth.py"]
        assert report["ignored"] == []

    def test_unresolvable_ref_is_unmeasurable_not_absent(self, repo):
        report = ae.artifact_report(
            "t-4", "", "new pkg/whatever.py",
            repo_root=repo, ref="origin/no-such-branch")
        assert report["state"] == ae.STATE_UNMEASURABLE
        assert "not resolvable" in report["reason"]
        # An unreadable ref must not populate `missing` — that would render as a
        # finding on a repo nobody could look at.
        assert report["missing"] == []


class TestDeliveryEvidenceGate:
    """A task nothing built must not be completable by an automatic path."""

    def test_no_dispatch_and_no_branch_is_a_measured_absence(self, repo):
        ev = ae.delivery_evidence(
            "ftp-prd-11", repo_root=repo, base_branch="main", dispatched=False)
        assert ev["has_evidence"] is False
        assert ev["branches"] == []
        assert ev["commits_ahead"] == 0

    def test_a_branch_alone_is_enough_evidence(self, repo):
        _git(repo, "branch", "kanban/ftp-prd-11")
        ev = ae.delivery_evidence(
            "ftp-prd-11", repo_root=repo, base_branch="main", dispatched=False)
        assert ev["has_evidence"] is True
        assert any("ftp-prd-11" in b for b in ev["branches"])

    def test_a_dispatch_record_alone_is_enough_evidence(self, repo):
        ev = ae.delivery_evidence(
            "ftp-prd-11", repo_root=repo, base_branch="main", dispatched=True)
        assert ev["has_evidence"] is True

    def test_unread_dispatch_record_is_unmeasurable_never_false(self, repo):
        """`None` in, never `False` out: fail-open is the whole safety story."""
        ev = ae.delivery_evidence(
            "ftp-prd-11", repo_root=repo, base_branch="main", dispatched=None)
        assert ev["has_evidence"] is None

    def test_unresolvable_base_branch_is_unmeasurable(self, repo):
        ev = ae.delivery_evidence(
            "ftp-prd-11", repo_root=repo, base_branch="no-such-branch",
            dispatched=False)
        assert ev["has_evidence"] is None
        assert "not resolvable" in ev["reason"]

    def test_gate_refuses_an_automatic_done_with_no_evidence(self, monkeypatch, repo):
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: False)

        reason = k.done_delivery_refusal("ftp-prd-11", actor="scheduler")
        assert reason, "an automatic done with no delivery evidence must be refused"
        assert "no delivery evidence" in reason

    def test_gate_allows_a_human_completion(self, monkeypatch, repo):
        """A `cli`/`manual` done is a decision. This gate does not judge it."""
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: False)

        for actor in sorted(k._DELIVERY_EVIDENCE_EXEMPT_ACTORS):
            assert k.done_delivery_refusal("ftp-prd-11", actor=actor) == ""

    def test_gate_allows_when_the_board_cannot_be_read(self, monkeypatch, repo):
        """Unmeasurable allows. An unreachable board must not wedge the queue."""
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: None)

        assert k.done_delivery_refusal("ftp-prd-11", actor="scheduler") == ""

    def test_a_parent_whose_children_are_all_done_is_not_refused(
            self, monkeypatch, repo):
        """A gate sentinel's delivery evidence is its children's.

        12 of the 29 fires in the survey were exactly this: a
        ``<prefix>-gate-00`` auto-closed by ``state_machine.auto_close_parent``,
        never dispatched and with no branch, because it never had work of its
        own to do.
        """
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: False)
        monkeypatch.setattr(k, "_children_all_done", lambda tid: True)

        assert k.done_delivery_refusal("pkg-gate-00", actor="startup_backfill") == ""

    def test_a_parent_with_unfinished_children_is_still_refused(
            self, monkeypatch, repo):
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: False)
        monkeypatch.setattr(k, "_children_all_done", lambda tid: False)
        monkeypatch.setattr(k, "_work_already_landed", lambda tid: (False, "not there"))

        assert k.done_delivery_refusal("pkg-gate-00", actor="startup_backfill")

    def test_work_already_on_main_is_not_refused(self, monkeypatch, repo):
        """A merged PR whose branch was deleted still delivered its work."""
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: False)
        monkeypatch.setattr(k, "_children_all_done", lambda tid: None)
        monkeypatch.setattr(k, "_work_already_landed",
                            lambda tid: (True, "merge_ref evidence"))

        assert k.done_delivery_refusal("xcore-compat-01", actor="pr_watcher") == ""

    def test_an_unanswerable_landed_check_is_not_refused(self, monkeypatch, repo):
        """`None` from the landed check allows — it could not be answered."""
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: False)
        monkeypatch.setattr(k, "_children_all_done", lambda tid: None)
        monkeypatch.setattr(k, "_work_already_landed", lambda tid: (None, "no git"))

        assert k.done_delivery_refusal("x-y-01", actor="scheduler") == ""

    def test_the_pre_dispatch_resolver_declares_itself(self):
        """Its completion claims "nothing to build", not "work delivered"."""
        from tools.genesis.reflexes import kanban as k

        assert "pre_dispatch_resolver" in k._DELIVERY_EVIDENCE_EXEMPT_ACTORS
        source = Path(k.__file__).read_text(encoding="utf-8")
        assert 'actor="pre_dispatch_resolver"' in source, (
            "the auto-resolve path must complete under its own actor, not the "
            "scheduler's — 18 of the survey's fires were this path unlabelled"
        )

    def test_the_exempt_set_did_not_grow_a_catch_all(self):
        """The set is closed at four, and a fifth needs its own survey."""
        from tools.genesis.reflexes import kanban as k

        assert k._DELIVERY_EVIDENCE_EXEMPT_ACTORS == frozenset({
            "manual", "cli", "operator", "pre_dispatch_resolver",
        })
        # `scheduler` and `pr_watcher` are the population this gate is FOR.
        assert "scheduler" not in k._DELIVERY_EVIDENCE_EXEMPT_ACTORS
        assert "pr_watcher" not in k._DELIVERY_EVIDENCE_EXEMPT_ACTORS

    def test_gate_stands_down_on_the_env_toggle(self, monkeypatch, repo):
        from tools.genesis.reflexes import kanban as k

        monkeypatch.setattr(k, "_task_repo_root", lambda tid: repo)
        monkeypatch.setattr(k, "_task_base_branch", lambda tid: "main")
        monkeypatch.setattr(k, "_has_dispatch_record", lambda tid: False)
        monkeypatch.setenv(k._DELIVERY_EVIDENCE_ENV, "0")

        assert k.done_delivery_refusal("ftp-prd-11", actor="scheduler") == ""


class TestSurveyHonesty:
    """`unmeasurable` is never folded into a clean result."""

    def test_an_unreadable_board_is_unmeasurable_not_clean(self, monkeypatch):
        monkeypatch.setattr(ae, "_done_tasks",
                            lambda **kw: ([], "board query failed: boom"))
        result = ae.survey()
        assert result["state"] == ae.STATE_UNMEASURABLE
        assert result["findings"] == []
        assert "boom" in result["reason"]
        # No rate is invented over an empty denominator.
        assert result["artifact_present_pct"] is None

    def test_a_board_of_undeclared_cards_reports_no_rate(self, monkeypatch, repo):
        monkeypatch.setattr(ae, "_task_repo", lambda tid: (repo, "main"))
        monkeypatch.setattr(ae, "_done_tasks", lambda **kw: (
            [{"id": "a-b-01", "title": "t", "description": "tighten a number",
              "completed_at": "", "updated_at": ""}], ""))
        result = ae.survey()
        assert result["state"] == "measured"
        assert result["counts"][ae.STATE_UNMEASURABLE] == 1
        assert result["measurable_tasks"] == 0
        assert result["artifact_present_pct"] is None
        assert "not a clean bill of health" in ae._render(result)

    def test_a_missing_artifact_becomes_a_finding(self, monkeypatch, repo):
        monkeypatch.setattr(ae, "_task_repo", lambda tid: (repo, "main"))
        monkeypatch.setattr(ae, "_done_tasks", lambda **kw: (
            [{"id": "ftp-prd-07", "title": "alerts",
              "description": "new icdev_fin/fathomdesk/alert_delivery.py",
              "completed_at": "", "updated_at": ""}], ""))
        result = ae.survey()
        assert result["state"] == "measured"
        assert result["counts"][ae.STATE_ABSENT] == 1
        assert [f["task_id"] for f in result["findings"]] == ["ftp-prd-07"]
        rendered = ae._render(result)
        assert "FINDINGS" in rendered
        # A finding carries the command that re-derives it.
        assert "cat-file -e" in rendered

    def test_survey_reports_and_never_gates(self):
        """No `--gate`: this measures the BOARD, not a diff (kpr-fix-03)."""
        source = Path(ae.__file__).read_text(encoding="utf-8")
        assert '"--gate"' not in source
        assert "'--gate'" not in source
