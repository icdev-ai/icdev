# CUI // SP-CTI
"""flx-ci-02 -- there is exactly ONE pre-apply gate, and this test says which.

TWO gates sat side by side in ``tools/infra_canvas/`` and nothing in the tree
said which one was the gate. flx-ci-01 needed one and had to NAME it in a
comment rather than choose, because a job that quietly picks one of a duplicate
pair blesses the pair.

WHAT WAS MEASURED BEFORE THE LOSER WAS DELETED (2026-09-05, at 81ef92024):

  * ``pre_apply_gate.check_plan`` had ZERO runtime callers. Every reference in
    the tree was its own docstring, one UNGATED test file, or a manifest row.
  * It could not tell a COMPLIANT plan from a VIOLATING one. Over the two
    flx-ci-01 fixtures it returned the identical verdict both times --
    ``passed=False``, 6 violations, score 53.8 -- while ``run_gate`` returned
    ``pass`` and ``fail`` respectively.
  * WHY, and this is the whole argument: its rules are ESTATE-COMPLETENESS
    questions ("is there a KMS service in this design?", "is there an IAM
    provider?") asked of a plan DELTA. It returned ``passed=True`` only for a
    plan that itself contained KMS + IAM + Secrets Manager -- i.e. only when
    the plan IS the entire estate. Adding one bucket to an estate that already
    had all three failed with four CAT1s.
  * The rulebook was NOT lost by deleting the wrapper.
    ``infra_engine.assess_infra_design`` is consumed live by
    ``tools/infra_canvas/blueprint.py`` at three sites, over the full design
    graph -- which is the input those rules were written for.

Full derivation: ``docs/audits/flx-ci-02-two-preapply-gates.md``.

This test is the standing guard. It fails if a second pre-apply gate appears
under ``tools/infra_canvas/`` in either tree, and it fails if the survivor
stops being the module the floci job names.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The one surviving gate, relative to each tree root.
SURVIVOR_RELPATH = "infra_canvas/preapply_gate.py"

#: Both trees ship the gate; a duplicate may not hide in either.
TREE_ROOTS = ("tools", "icdev/tools")

#: Anything matching this under infra_canvas/ is a pre-apply gate by name.
#: Deliberately loose -- `pre_apply_gate`, `preapply_gate`, `pre-apply_gate`
#: and `pre_apply_gate_v2` all match, because the defect this guards against
#: is a NEAR-miss spelling of the survivor, not an exact duplicate.
_GATE_NAME = re.compile(r"pre[_-]?apply[_-]?gate.*\.py$", re.IGNORECASE)


def _gate_modules(tree: str) -> list[str]:
    """Every pre-apply-gate-shaped module under one tree's infra_canvas/."""
    canvas = REPO_ROOT / tree / "infra_canvas"
    if not canvas.is_dir():
        pytest.fail(f"{tree}/infra_canvas/ is missing; the gate has no home")
    return sorted(
        p.name for p in canvas.iterdir()
        if p.is_file() and _GATE_NAME.search(p.name)
    )


@pytest.mark.parametrize("tree", TREE_ROOTS)
def test_exactly_one_preapply_gate_module(tree: str) -> None:
    """A second pre-apply gate under infra_canvas/ fails this test.

    A SHIM DOES NOT SATISFY THIS. A shim over a gate is a second gate with a
    redirect: it still gives a caller two names to import, two things to grep
    for, and two places a future edit can land. The check is on the FILE, so a
    re-export module fails exactly like a reimplementation.
    """
    found = _gate_modules(tree)
    assert found == ["preapply_gate.py"], (
        f"{tree}/infra_canvas/ must hold exactly one pre-apply gate "
        f"('preapply_gate.py'); found {found}. Two gates that take the same "
        "input and answer the same question is the defect flx-ci-02 removed -- "
        "if this is deliberate, the other one needs a different NAME and a "
        "docstring saying what different question it answers."
    )


@pytest.mark.parametrize("tree", TREE_ROOTS)
def test_the_survivor_exists_and_exports_run_gate(tree: str) -> None:
    """The survivor is the IQE-over-plan-delta gate, not something renamed onto it."""
    src = (REPO_ROOT / tree / SURVIVOR_RELPATH).read_text(encoding="utf-8")
    assert "def run_gate(" in src, (
        f"{tree}/{SURVIVOR_RELPATH} must export run_gate() -- that is the name "
        "twin_core/adapters/idc.py::simulate_delta and tools/ci/floci_iac_gate.py "
        "both call"
    )


def test_no_doc_points_a_reader_at_the_deleted_gate() -> None:
    """Nothing may hand a reader a PATH to the module flx-ci-02 deleted.

    CLAUDE.md: never document a command whose file does not exist -- an agent
    reading it will confidently run it and burn a cycle deciding whether the
    tree is broken or the doc is. The three places that NAMED the loser while
    it lived (the floci workflow, the floci gate module, the survivor's own
    docstring) are the exact places most likely to keep naming it after it is
    gone.

    THE PREDICATE IS THE FULL PATH, not the bare module name, and that is
    deliberate. Every one of these files now carries prose EXPLAINING the
    deletion, and that prose has to be able to say `pre_apply_gate.py` out
    loud -- a rule that forbade the name would forbid the explanation, and the
    next reader would find a deletion with no recorded reason. What must never
    reappear is the actionable form: a path a reader can copy and run.
    """
    dead_path = "tools/infra_canvas/pre_apply_gate.py"

    for tree in TREE_ROOTS:
        assert not (REPO_ROOT / tree / "infra_canvas" / "pre_apply_gate.py").exists(), (
            f"{tree}/infra_canvas/pre_apply_gate.py is back. It was deleted by "
            "flx-ci-02 for cause -- see this module's docstring, and "
            "docs/audits/flx-ci-02-two-preapply-gates.md, before restoring it."
        )

    offenders: list[str] = []
    for rel in (
        ".github/workflows/floci-iac-gate.yml",
        "tools/ci/floci_iac_gate.py",
        "icdev/tools/ci/floci_iac_gate.py",
        "tools/infra_canvas/preapply_gate.py",
        "icdev/tools/infra_canvas/preapply_gate.py",
        "tools/manifest/design-canvases.md",
        "icdev/tools/manifest/design-canvases.md",
    ):
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if dead_path in line:
                offenders.append(f"{rel}:{i}: {line.strip()[:100]}")

    assert not offenders, (
        f"these lines hand a reader a path to {dead_path}, which does not "
        "exist:\n  " + "\n  ".join(offenders)
    )
