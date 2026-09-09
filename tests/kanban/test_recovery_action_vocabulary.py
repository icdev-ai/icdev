# CUI // SP-CTI
"""ONE statement of "which pr_watcher actions are recovery evidence" (autonomy-act-05).

THE DEFECT. ``recovery_summary.AUDIT_ACTIONS`` was exported precisely so the
panel's SQL and the classifier could not drift apart -- and exactly ONE of the
three readers took it. ``detector_findings.recovery_rows`` and
``claims._recovery_rows``, the two that FILE AND CLEAR CARDS, each kept a
hand-written four-value literal, so rmf-disc-01's widening reached the surface a
human reads and never reached the readers that act.

A BEHAVIOURAL TEST CANNOT HOLD THIS. Both spellings return audit rows and the
narrow one returns a strict subset, so every assertion about a fixture with only
``resume`` rows passes for both. The drift is only visible in the SOURCE, so the
source is what is read: these tests parse the modules' ASTs.

TWO RULES:
  1. the three recovery readers reach ``AUDIT_ACTIONS`` and hold no literal;
  2. a FOURTH module that bundles the recovery vocabulary must declare it as a
     module-level NAMED constant and be enumerated below with a written reason.
     ``sibling_overlap`` and ``protected_conflict_survey`` already comply -- they
     answer DIFFERENT questions (conflict episodes, ladder episodes) and say so.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ONLY ``AUDIT_ACTIONS`` at module level, on purpose. The two rules that carry
# this test's finding -- no literal in a reader, no fourth reader -- need
# nothing else, and importing the newer ``ATTEMPT_KINDS``/``REFUND_KINDS`` here
# would turn every run against a tree predating them into a COLLECTION ERROR.
# A collection error is a weaker RED than the assertion: it records that a
# constant is new, not that the readers had drifted apart.
from tools.dashboard.recovery_summary import AUDIT_ACTIONS

REPO = Path(__file__).resolve().parents[2]

#: Every ``pr_watcher.<action>`` literal that carries a recovery verdict. Derived
#: from the declaration, never respelled -- a ninth action added there joins this
#: scan for free.
VOCABULARY = frozenset(AUDIT_ACTIONS)

#: The three readers of the question "did pr_watcher recover this PR". Each must
#: reach the ONE constant.
READERS = (
    "tools/dashboard/app.py",
    "tools/kanban/detector_findings.py",
    "tools/awareness/claims.py",
)

#: THE DECLARATION and THE WRITER. The first defines the vocabulary; the second
#: emits the rows and necessarily names them.
DECLARERS = (
    "tools/dashboard/recovery_summary.py",
    "tools/ci/pr_watcher.py",
)

#: A module that legitimately names some of the vocabulary for a DIFFERENT
#: question, each with the reason it differs. Adding an entry is a deliberate,
#: reviewable act -- which is the point: two UNNAMED literals that silently
#: disagree is the defect, a named constant with a stated reason is not.
DECLARED_EXCEPTIONS = {
    "tools/kanban/sibling_overlap.py": (
        "CONFLICT_ACTIONS -- the rows a real merge CONFLICT leaves behind, not a "
        "recovery verdict. It deliberately excludes merge/refunds."
    ),
    "tools/ci/protected_conflict_survey.py": (
        "LADDER_ACTIONS -- the rungs an EPISODE is made of. `wait` is excluded "
        "with a written reason and `merge` is not a rung."
    ),
    "tools/kanban/recovery_action_survey.py": (
        "LEGACY_AUDIT_ACTIONS -- the pre-autonomy-act-05 rule, copied verbatim so "
        "the survey can REPLAY it. Labelled history; never the shipped rule."
    ),
}


def _module_paths(relpath: str) -> list[Path]:
    """The repo copy and its icdev/ mirror, whichever exist."""
    out = [REPO / relpath]
    mirror = REPO / "icdev" / relpath
    if mirror.exists():
        out.append(mirror)
    return [p for p in out if p.exists()]


def _tree(path: Path) -> ast.Module:
    # ``utf-8-sig`` because a handful of first-party modules carry a BOM, and a
    # scan that cannot READ a file reports it clean -- the failure mode this
    # whole test exists to refuse.
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """id() of every Constant that is a docstring -- prose is not a literal."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _parents(tree: ast.Module) -> dict:
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


def _module_level_constant_collections(tree: ast.Module) -> set[int]:
    """id() of every collection that IS a module-level UPPER_CASE assignment."""
    out: set[int] = set()
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id.isupper() for t in targets):
            if isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                out.add(id(node.value))
    return out


def _vocabulary_literals(path: Path) -> list[tuple[int, str, str]]:
    """Action names a module STATES for itself, with line number and context.

    THREE DISCRIMINATIONS, and each was measured rather than reasoned:

    SUBSTRING, NOT EQUALITY. The defect's actual shape was an inline SQL
    fragment -- ``"WHERE action IN ('pr_watcher.rebase','pr_watcher.resume',"``
    -- which is ONE string constant equal to no vocabulary member. An equality
    scan reports it clean, and a rule that cannot see the incident it was
    written for is not a rule.

    PROSE IS NOT A LITERAL, the ``args/model_id_gate.yaml`` precedent. A reason
    string reading "a pr_watcher.merge landed after the escalation" states no row
    set; ``detector_findings`` carries four such messages and flagging them would
    make the rule refuse routine work on its first run.

    A NAMED MODULE-LEVEL CONSTANT IS NOT A LITERAL EITHER. That is the card's
    second acceptable answer -- a DISTINCT named constant carrying a written
    reason for differing -- and it is what ``watcher_outcome_rows`` needs: it
    asks a genuinely different question (did a merge land AFTER the escalation)
    and must NOT widen with the recovery row set.
    """
    tree = _tree(path)
    skip = _docstring_nodes(tree)
    parents = _parents(tree)
    named = _module_level_constant_collections(tree)
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if (not isinstance(node, ast.Constant) or not isinstance(node.value, str)
                or id(node) in skip):
            continue
        found = sorted(a for a in VOCABULARY if a in node.value)
        if not found:
            continue
        parent = parents.get(id(node))
        upper = node.value.upper()
        if isinstance(parent, (ast.Tuple, ast.List, ast.Set)):
            if id(parent) in named:
                continue                      # a declaration, not a restatement
            context = "inline collection"
        elif any(k in upper for k in ("SELECT ", "WHERE ", " IN (", "IN (")):
            context = "sql"
        else:
            continue                          # prose
        hits.extend((getattr(node, "lineno", 0), a, context) for a in found)
    return hits


def _first_party_modules() -> list[Path]:
    roots = [REPO / "tools", REPO / "icdev" / "tools"]
    out: list[Path] = []
    for root in roots:
        if root.exists():
            out.extend(sorted(root.rglob("*.py")))
    return out


def _relpath(path: Path) -> str:
    rel = path.relative_to(REPO).as_posix()
    return rel[len("icdev/"):] if rel.startswith("icdev/tools/") else rel


# --------------------------------------------------------------------------- #
# Rule 1 -- the three readers reach the ONE constant and hold no literal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("relpath", READERS)
def test_reader_holds_no_action_literal(relpath):
    for path in _module_paths(relpath):
        hits = _vocabulary_literals(path)
        assert not hits, (
            f"{_relpath(path)} states a pr_watcher recovery action for itself at "
            f"{hits} (line, action, context). There is ONE statement of which "
            "actions are recovery evidence: recovery_summary.AUDIT_ACTIONS -- "
            "import it. If this reader asks a DIFFERENT question, declare a "
            "module-level UPPER_CASE constant with the reason it differs."
        )


@pytest.mark.parametrize("relpath", READERS)
def test_reader_imports_the_declaration(relpath):
    for path in _module_paths(relpath):
        tree = _tree(path)
        found = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom)
                    and (node.module or "").endswith("dashboard.recovery_summary")
                    and any(a.name == "AUDIT_ACTIONS" for a in node.names)):
                found = True
        assert found, (
            f"{_relpath(path)} does not import AUDIT_ACTIONS from "
            "tools.dashboard.recovery_summary. A reader of the recovery row set "
            "must reach the declaration, not restate it."
        )


def test_derived_recovery_side_shares_the_vocabulary_not_the_reduction():
    """claims._derived_recoveries reads the vocabulary and NOT summarize_recovery.

    The claim_verifier rule is that the two derivations must not share the
    REDUCTION. The vocabulary is a fact about pr_watcher's writer, and a
    derivation over a DIFFERENT population is a different claim, not an
    independent check of the same one.
    """
    for path in _module_paths("tools/awareness/claims.py"):
        tree = _tree(path)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_derived_recoveries")
        # NAMES, not the raw dump: this function's own docstring EXPLAINS why it
        # does not call summarize_recovery, and a substring scan would flag that
        # explanation. The same trap dwr-ev-03's AST test records.
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom):
                names.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        assert {"ATTEMPT_KINDS", "REFUND_KINDS"} <= names, (
            f"{_relpath(path)}::_derived_recoveries must read the declared attempt "
            "vocabulary, not a private copy of it."
        )
        assert "summarize_recovery" not in names, (
            f"{_relpath(path)}::_derived_recoveries must NOT call summarize_recovery "
            "-- the independent side may share the vocabulary, never the reduction."
        )


def test_audit_actions_is_derived_from_the_kind_tuples():
    from tools.dashboard.recovery_summary import ATTEMPT_KINDS, REFUND_KINDS

    assert set(AUDIT_ACTIONS) == {
        f"pr_watcher.{k}" for k in (*ATTEMPT_KINDS, *REFUND_KINDS, "escalate", "merge")
    }


# --------------------------------------------------------------------------- #
# Rule 2 -- a FOURTH reader may not hard-code the vocabulary
# --------------------------------------------------------------------------- #
def test_no_fourth_reader_hardcodes_the_action_vocabulary():
    """Any other module naming >=2 recovery actions is a declared exception.

    ONE literal is a single row filter or a log-message match, not a competing
    vocabulary; TWO OR MORE is a reader restating the row set. Every current
    exception declares a module-level NAMED constant AND carries a written
    reason here -- which is what the acceptance criterion's second branch asks
    for and what an unnamed literal can never provide.
    """
    offenders: list[str] = []
    for path in _first_party_modules():
        rel = _relpath(path)
        if rel in DECLARERS or rel in DECLARED_EXCEPTIONS or rel in READERS:
            continue
        hits = _vocabulary_literals(path)
        if len({v for _, v, _ in hits}) >= 2:
            offenders.append(f"{rel} -> {sorted({v for _, v, _ in hits})}")
    assert not offenders, (
        "These modules restate the pr_watcher recovery vocabulary:\n  "
        + "\n  ".join(offenders)
        + "\n\nIf it answers 'did pr_watcher recover this', import "
          "recovery_summary.AUDIT_ACTIONS. If it answers a DIFFERENT question, "
          "declare a module-level NAMED constant and add it to "
          "DECLARED_EXCEPTIONS in this file with the reason it differs."
    )


@pytest.mark.parametrize("relpath,reason", sorted(DECLARED_EXCEPTIONS.items()))
def test_declared_exception_uses_a_named_module_constant(relpath, reason):
    """An exception is a NAMED constant, never an inline literal in a query."""
    assert reason.strip(), f"{relpath} needs a written reason"
    paths = _module_paths(relpath)
    assert paths, f"{relpath} does not exist -- prune the exception"
    for path in paths:
        tree = _tree(path)
        named: list[str] = []
        for node in tree.body:
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, ast.AnnAssign) else [])
            if not any(isinstance(t, ast.Name) and t.id.isupper() for t in targets):
                continue
            value = node.value
            if isinstance(value, (ast.Tuple, ast.List, ast.Set)) and any(
                    isinstance(e, ast.Constant) and e.value in VOCABULARY
                    for e in value.elts):
                named.extend(t.id for t in targets if isinstance(t, ast.Name))
        assert named, (
            f"{_relpath(path)} names recovery actions but not as a module-level "
            "UPPER_CASE constant. An unnamed literal carries no reason for "
            "differing, which is the whole defect."
        )


# --------------------------------------------------------------------------- #
# Positive controls -- a scanner that stopped scanning also reports clean
# --------------------------------------------------------------------------- #
def test_scanner_fires_on_the_incident_shape(tmp_path):
    """The exact inline SQL the two readers carried before autonomy-act-05."""
    mod = tmp_path / "fourth_reader.py"
    mod.write_text(
        "SQL = (\n"
        "    \"SELECT action FROM audit_trail \"\n"
        "    \"WHERE action IN ('pr_watcher.rebase','pr_watcher.resume',\"\n"
        "    \"'pr_watcher.escalate','pr_watcher.merge')\"\n"
        ")\n",
        encoding="utf-8",
    )
    hits = _vocabulary_literals(mod)
    assert {a for _, a, _ in hits} >= {"pr_watcher.rebase", "pr_watcher.resume",
                                       "pr_watcher.escalate", "pr_watcher.merge"}
    assert {c for _, _, c in hits} == {"sql"}


def test_scanner_fires_on_an_inline_collection(tmp_path):
    """A tuple built at a call site is a restatement; only a MODULE constant is not."""
    mod = tmp_path / "inline.py"
    mod.write_text(
        "def go(conn):\n"
        "    return conn.execute('...', ('pr_watcher.resume', 'pr_watcher.merge'))\n",
        encoding="utf-8",
    )
    hits = _vocabulary_literals(mod)
    assert {c for _, _, c in hits} == {"inline collection"}


def test_scanner_ignores_prose_and_named_constants(tmp_path):
    """Two things that are NOT restatements, or the rule refuses routine work."""
    mod = tmp_path / "clean.py"
    mod.write_text(
        "#: a distinct question, with its reason\n"
        "OUTCOME_ACTIONS = ('pr_watcher.escalate', 'pr_watcher.merge')\n"
        "\n"
        "def why():\n"
        "    return 'a pr_watcher.merge landed after the escalation'\n",
        encoding="utf-8",
    )
    assert _vocabulary_literals(mod) == []
