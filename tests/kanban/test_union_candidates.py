# CUI // SP-CTI
"""Nothing consumed `pr_watcher.union_refused`, and the rung had resolved TWO
conflicts in its lifetime (kpr-watch-22).

37 refusals in twelve hours, 33 of them `undeclared:`, against 2 lifetime
resolutions. `pr_watcher.union_refused` had exactly ONE mention outside its
writer and it was a docstring, so the only way an undeclared append-shaped file
was ever discovered was a human noticing a stalled PR and reading audit rows by
hand. This registers `union_candidate` as a SIXTH detector on the act-02 path.

THE SIX THINGS PINNED:
  1. ATTRIBUTION IS TO THE UNDECLARED MEMBER. A refusal naming
     {declared, undeclared} is evidence about the UNDECLARED file only, and a
     file `match_declaration` accepts can NEVER be a candidate. The regression
     case is the 4 `CLAUDE.md` rows measured on 2026-09-12: declared since
     kpr-watch-14, named in refusals it did not cause.
  2. A CANDIDATE WHOSE SURVEY CANNOT BE PRODUCED FILES NOTHING. `lost_content`
     is None -- never 0 -- and a hunk that was `refused` or `unanchorable`
     COMPARED NOTHING, so a file whose every hunk is one of those is
     `unmeasurable`, not clean. That is the live shape of
     `tools/canvas_compliance/posture.py`.
  3. SHAPE IS REPORTED, NOT GUESSED, and not read off the path: append-shaped
     means every observed hunk is a pure insertion on both sides with an EMPTY
     base region; anything else is `rewrites_base`, carried with its count.
  4. THE CARD PROPOSES, THE HUMAN DECLARES. Nothing writes
     `union_resolver.files` -- asserted over the AST, not by grep, because an
     actuator that edits its own guardrail is the tier `restore_acts`
     deliberately does not have.
  5. DEDUPE IS ON THE FILE: one finding per (detector, file, fingerprint).
  6. UNMEASURABLE CLEARS NOTHING -- an unreadable corpus, an EMPTY corpus and
     an unreadable git history are each `unmeasurable`, never `clean`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import detector_findings as df  # noqa: E402
from tools.kanban import union_candidates as uc  # noqa: E402

DECLARED = [{"path": "CLAUDE.md", "rules": ["keep_both_blocks"]},
            {"path": "tools/*/blueprint.py", "rules": ["keep_both_blocks"]}]

#: The mixed set measured on the live board on 2026-09-12: one declared file
#: (named in refusals it did not cause) and one undeclared file (which caused
#: every one of them).
MIXED_REASON = ("refused: files=['CLAUDE.md', 'tests/airgap/test_artifact_freshness.py'] "
                "rules=[] verifiers=[] -- undeclared: "
                "tests/airgap/test_artifact_freshness.py matches no "
                "union_resolver.files entry")


def _row(reason=MIXED_REASON, task_id="kpr-watch-20", created_at="2026-09-12T20:59:31"):
    return {"task_id": task_id, "reason": reason, "created_at": created_at,
            "pr_url": "https://example.invalid/pr/1"}


def _hunk(verdict="exact", base_lines=0, lost=0):
    return {"verdict": verdict, "base_lines": base_lines, "main_lines": 3,
            "card_lines": 4, "lost": lost, "extra": 0, "invented": 0}


def _survey(hunks, *, merges=4, conflicting=1):
    """A stand-in for `artifact_pin_union_survey.survey` over ONE file."""
    def _fn(files, rules, root=None, ref="origin/main"):
        return {"ref": ref, "rules": list(rules), "files": list(files),
                "reports": [{"file": files[0], "measurable": bool(hunks),
                             "merge_commits_touching_file": merges,
                             "skipped": {}, "conflicting": conflicting,
                             "file_verdicts": {},
                             "cases": [{"sha": "abc123def", "subject": "merge",
                                        "hunks": list(hunks)}] if hunks else []}]}
    return _fn


# -- 1. attribution is to the UNDECLARED member ------------------------------
def test_a_declared_file_in_a_mixed_set_is_collateral_never_a_candidate():
    """THE REGRESSION CASE. Four refusals naming {CLAUDE.md, <undeclared>} are
    four data points about the undeclared file and ZERO about CLAUDE.md."""
    out = uc.attribute([_row() for _ in range(4)], DECLARED)

    assert out["candidates"] == {"tests/airgap/test_artifact_freshness.py": 4}
    assert out["collateral"] == {"CLAUDE.md": 4}
    assert "CLAUDE.md" not in out["candidates"]


def test_a_refusal_naming_two_undeclared_files_counts_both():
    """The resolver raises at the FIRST undeclared file it reaches, so its own
    message names one and the set costs both. The set is re-walked."""
    reason = ("refused: files=['CLAUDE.md', 'args/a.yaml', 'args/b.yaml'] rules=[] "
              "verifiers=[] -- undeclared: args/a.yaml matches no "
              "union_resolver.files entry")
    out = uc.attribute([_row(reason=reason)], DECLARED)

    assert out["candidates"] == {"args/a.yaml": 1, "args/b.yaml": 1}


def test_a_mirror_pair_in_one_set_is_one_row_not_two():
    """`icdev/tools/x.py` and `tools/x.py` fold onto ONE declaration, so a set
    naming both is ONE refusal that file caused."""
    reason = ("refused: files=['icdev/tools/foo/bar.py', 'tools/foo/bar.py'] rules=[] "
              "verifiers=[] -- undeclared: tools/foo/bar.py matches no entry")
    out = uc.attribute([_row(reason=reason)], DECLARED)

    assert out["candidates"] == {"tools/foo/bar.py": 1}


def test_a_refusal_with_no_file_set_is_counted_not_dropped():
    out = uc.attribute([_row(reason="refused: verifier failed"), _row()], DECLARED)

    assert out["unparsed_rows"] == 1
    assert out["rows"] == 2


def test_a_declared_file_can_never_reach_the_candidate_list():
    """The whole-report path, not just `attribute`: a candidate is re-asked of
    the shipped matcher at the point of use."""
    report = uc.candidates(rows=[_row() for _ in range(4)], declarations=DECLARED,
                           survey_fn=_survey([_hunk()]))

    assert [c["file"] for c in report["candidates"]] == [
        "tests/airgap/test_artifact_freshness.py"]
    assert report["collateral"] == {"CLAUDE.md": 4}


# -- 2. a survey that compared nothing files nothing -------------------------
def test_a_hunk_that_was_refused_compared_nothing_so_the_file_is_unmeasurable():
    """`tools/canvas_compliance/posture.py`'s live shape: ONE conflict hunk,
    `refused`, and therefore a `lost_content` of 0 that means NOT MEASURED."""
    out = uc.assess("tools/canvas_compliance/posture.py",
                    survey_fn=_survey([_hunk("refused", base_lines=7)]))

    assert out["state"] == uc.STATE_UNMEASURABLE
    assert out["lost_content"] is None, "None, never 0, when nothing was compared"
    assert out["decisive_hunks"] is None
    assert out["recommendation"] == uc.RECOMMEND_DO_NOT


def test_an_unanchorable_hunk_compared_nothing_either():
    out = uc.assess("args/x.yaml", survey_fn=_survey([_hunk("unanchorable")]))

    assert out["state"] == uc.STATE_UNMEASURABLE
    assert out["lost_content"] is None


def test_a_file_with_no_conflicting_merge_is_unmeasurable_and_says_which():
    out = uc.assess("tools/security/row_security.py",
                    survey_fn=_survey([], merges=0, conflicting=0))

    assert out["state"] == uc.STATE_UNMEASURABLE
    assert "no conflicting merge" in out["reason"]
    assert out["lost_content"] is None


def test_an_unmeasurable_candidate_files_no_finding():
    report = uc.candidates(rows=[_row()], declarations=DECLARED,
                           survey_fn=_survey([_hunk("refused", base_lines=7)]))

    assert report["candidates"][0]["state"] == uc.STATE_UNMEASURABLE
    assert df.union_candidate_findings(report) == []


# -- 3. shape is reported, not guessed ---------------------------------------
def test_every_hunk_a_pure_insertion_is_append_shaped():
    out = uc.shape_of([_hunk(base_lines=0), _hunk(base_lines=0)])

    assert out["shape"] == uc.SHAPE_APPEND
    assert out["hunks_rewriting_base"] == 0


def test_any_hunk_rewriting_base_lines_reads_as_rewrites_base_with_its_count():
    """1-of-15 (CLAUDE.md, correctly declared) and 1-of-1 (a source module) are
    the same flag and different facts, so the count travels with it."""
    out = uc.shape_of([_hunk(base_lines=0)] * 14 + [_hunk(base_lines=6)])

    assert out["shape"] == uc.SHAPE_REWRITES_BASE
    assert (out["hunks_rewriting_base"], out["hunks"]) == (1, 15)


def test_shape_is_reported_even_when_nothing_was_compared():
    """A source module that happens to collide must READ as rewrites_base, so a
    reviewer sees the difference the evidence turns on -- even though the file
    is unmeasurable and files nothing."""
    out = uc.assess("tools/canvas_compliance/posture.py",
                    survey_fn=_survey([_hunk("refused", base_lines=7)]))

    assert out["shape"] == uc.SHAPE_REWRITES_BASE
    assert out["hunks_rewriting_base"] == 1


def test_a_rewrites_base_file_is_still_recommended_when_nothing_was_lost():
    """CALIBRATION. `CLAUDE.md` (1 of 15) and `args/pinned_artifacts.yaml`
    (1 of 2) both have a base-touching hunk and were BOTH correctly declared by
    hand. "Append-shaped or nothing" would have declined two of the three
    correct declarations, so the shape is the reviewer's input and not a veto."""
    out = uc.assess("CLAUDE.md",
                    survey_fn=_survey([_hunk(base_lines=0)] * 14 + [_hunk("refused", 6)]))

    assert out["shape"] == uc.SHAPE_REWRITES_BASE
    assert out["recommendation"] == uc.RECOMMEND_DECLARE
    assert "READ THEM before declaring" in out["reason"]


def test_lost_content_refuses_the_declaration():
    out = uc.assess("tools/foo.py",
                    survey_fn=_survey([_hunk(), _hunk("union_lost_content", lost=9)]))

    assert out["lost_content"] == 9
    assert out["recommendation"] == uc.RECOMMEND_DO_NOT
    assert "DROPS 9 line(s)" in out["reason"]


# -- 4. the card proposes, the human declares --------------------------------
def test_a_recommended_candidate_carries_the_exact_one_line_declaration():
    report = uc.candidates(rows=[_row()], declarations=DECLARED,
                           survey_fn=_survey([_hunk(), _hunk()]))
    entry = report["candidates"][0]

    assert entry["declaration"] == (
        "  - {path: tests/airgap/test_artifact_freshness.py, "
        "rules: [keep_both_blocks, adjacent_edits]}")
    findings = df.union_candidate_findings(report)
    assert len(findings) == 1
    assert entry["declaration"] in findings[0]["advice"]
    assert findings[0]["title"].startswith("union rung refuses")


def test_a_refused_candidate_offers_no_declaration_line():
    report = uc.candidates(rows=[_row()], declarations=DECLARED,
                           survey_fn=_survey([_hunk("union_lost_content", lost=3)]))
    entry = report["candidates"][0]

    assert entry["declaration"] is None
    (finding,) = df.union_candidate_findings(report)
    assert "DO NOT DECLARE IT" in finding["advice"]
    assert "```yaml" not in finding["advice"]


def test_the_declaration_line_is_valid_yaml_naming_the_surveyed_rules():
    import yaml

    line = uc.declaration_line("args/x.yaml", ("keep_both_blocks", "adjacent_edits"))
    (entry,) = yaml.safe_load(line)

    assert entry == {"path": "args/x.yaml",
                     "rules": ["keep_both_blocks", "adjacent_edits"]}


@pytest.mark.parametrize("module", ["tools/kanban/union_candidates.py"])
def test_nothing_writes_the_declaration_config(module):
    """AN ACTUATOR NEVER EDITS ITS OWN GUARDRAIL. Asserted over the AST rather
    than by grepping for a filename, so a write reached by any spelling of the
    path is still caught: no write-mode `open`, no `write_text`/`write_bytes`,
    no `yaml.dump`/`safe_dump`, no `shutil.copy*`, no `os.replace`.
    """
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in {"write_text", "write_bytes", "dump", "safe_dump", "replace",
                    "copy", "copy2", "copyfile", "rename", "unlink", "mkdir"}:
            offenders.append(f"{name}() at line {node.lineno}")
        if name == "open":
            mode = ""
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            if any(ch in mode for ch in "wax+"):
                offenders.append(f"open(mode={mode!r}) at line {node.lineno}")
    assert not offenders, f"{module} writes: {offenders}"


def test_the_detector_reads_the_config_only_through_the_shipped_loader():
    """The one seam. A second reader would be a second answer to "is this file
    declared", which is the drift autonomy-act-05 records."""
    source = (ROOT / "tools/kanban/union_candidates.py").read_text(encoding="utf-8")

    assert "load_declared_rules" in source
    assert "pr_watcher_config.yaml" not in source, (
        "the config path belongs to union_resolver.config_path(), not to a second literal")


# -- 5. dedupe is on the FILE ------------------------------------------------
def test_one_finding_per_file_and_the_fingerprint_is_the_verdict():
    report = uc.candidates(rows=[_row(), _row()], declarations=DECLARED,
                           survey_fn=_survey([_hunk()]))
    (finding,) = df.union_candidate_findings(report)

    assert finding["subject"] == "tests/airgap/test_artifact_freshness.py"
    assert finding["fingerprint"] == f"{uc.SHAPE_APPEND}|{uc.RECOMMEND_DECLARE}"
    assert finding["detector"] == df.DETECTOR_UNION_CANDIDATE
    # Same file, same verdict, a later run: the SAME finding.
    again = df.union_candidate_findings(report)[0]
    assert again["finding_id"] == finding["finding_id"]


def test_the_fingerprint_is_not_declared_set_valued():
    """`<shape>|<recommendation>` is TWO VERDICTS ABOUT ONE FILE, not two
    independently resolvable members -- the test the constant's own comment
    demands before a detector joins that set."""
    assert df.DETECTOR_UNION_CANDIDATE not in df.SET_VALUED_FINGERPRINT_DETECTORS


def test_the_detector_is_registered_and_has_a_blurb():
    assert df.DETECTOR_UNION_CANDIDATE in df.DETECTORS
    assert df.DETECTOR_UNION_CANDIDATE in df.DEFAULT_RUNNERS
    assert df.DETECTOR_BLURB[df.DETECTOR_UNION_CANDIDATE]
    spec = df.build_spec(
        df.union_candidate_findings(
            uc.candidates(rows=[_row()], declarations=DECLARED,
                          survey_fn=_survey([_hunk()])))[0],
        seen_count=1, first_seen_at=None, revision=1, seed_status="suggested")
    assert spec["title"].startswith("[UNION-CANDIDATE]")
    from tools.kanban.task_factory import VALID_TASK_TYPES
    assert spec["task_type"] in VALID_TASK_TYPES
    assert spec["status"] == "suggested", "the card PROPOSES; it is not dispatched"


# -- 6. unmeasurable clears nothing ------------------------------------------
def _run_over(report, monkeypatch, cfg=None):
    """`run_union_candidate` over a prepared report. The runner imports
    `candidates` at CALL time, so the module attribute is the seam."""
    monkeypatch.setattr(uc, "candidates", lambda **_kw: report)
    return df.run_union_candidate(object(), cfg or {"window_hours": 720})


def test_an_empty_corpus_is_unmeasurable_never_clean(monkeypatch):
    """The rung may be idle, the watcher may be down, or the audit writer may
    be bypassed. None of those is "no file needs declaring"."""
    report = uc.candidates(rows=[], declarations=DECLARED)

    assert report["measurable"] is False
    assert "no pr_watcher.union_refused rows" in report["reason"]
    assert _run_over(report, monkeypatch)["state"] == df.RUN_UNMEASURABLE


def test_an_unreadable_corpus_is_unmeasurable(monkeypatch):
    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError("board unreachable")

    report = uc.candidates(conn=_Boom(), window_hours=720)

    assert report["measurable"] is False
    assert "unreadable" in report["reason"]
    assert report["rows"] is None, "None, never 0, when the corpus could not be read"
    assert _run_over(report, monkeypatch)["state"] == df.RUN_UNMEASURABLE


def test_an_unreadable_git_history_makes_the_whole_run_unmeasurable(monkeypatch):
    """Otherwise every candidate reports zero conflicting merges and a finding
    filed on a previous cycle is CLEARED by a run that looked at nothing."""
    def _blind(files, rules, root=None, ref="origin/main"):
        return {"reports": [{"file": files[0], "measurable": False,
                             "merge_commits_touching_file": None,
                             "skipped": {}, "conflicting": None, "cases": []}]}

    report = uc.candidates(rows=[_row()], declarations=DECLARED, survey_fn=_blind)

    assert report["candidates"][0]["git_readable"] is False
    out = _run_over(report, monkeypatch)
    assert out["state"] == df.RUN_UNMEASURABLE
    assert "git merge history is unreadable" in out["reason"]


def test_the_run_is_clean_when_every_candidate_is_unmeasurable(monkeypatch):
    """The LIVE shape on 2026-09-12: 118 refusal rows, 9 undeclared files, and
    every one of them a source module with no comparable hunk. The run measured
    fine -- git was readable, the corpus was read -- it simply proposes
    nothing, and the declined candidates are NAMED in the summary."""
    report = uc.candidates(rows=[_row()], declarations=DECLARED,
                           survey_fn=_survey([_hunk("refused", base_lines=4)]))
    out = _run_over(report, monkeypatch)

    assert report["candidates"][0]["git_readable"] is True
    assert out["state"] == df.RUN_CLEAN
    assert out["findings"] == []
    assert [c["file"] for c in out["summary"]["unmeasurable_candidates"]] == [
        "tests/airgap/test_artifact_freshness.py"]


def test_a_measurable_run_reports_the_finding_and_the_collateral(monkeypatch):
    report = uc.candidates(rows=[_row()], declarations=DECLARED,
                           survey_fn=_survey([_hunk(), _hunk()]))
    out = _run_over(report, monkeypatch)

    assert out["state"] == df.RUN_FINDINGS
    assert len(out["findings"]) == 1
    assert out["summary"]["collateral"] == {"CLAUDE.md": 1}
    assert out["summary"]["rows"] == 1
