#!/usr/bin/env python3
# CUI // SP-CTI
"""Will two tasks fight over the same file? Asked at SEED time. (rem-hyg-07)

THE GAP
=======
``tools/ci/pr_watcher.py`` already has a sibling-conflict guard —
``_open_pr_files()`` plus ``hold_on_sibling_conflict`` — but it runs on OPEN
PRs, which is after both sessions have already built. By then the cheapest
outcome is a rebase and the usual one is a binned branch: #1684 dispatched a
producer and its consumer together, the loser's PR was unlandable, and 1,058
lines were discarded. The prevention has to happen earlier, where the collision
is still two rows in a batch rather than two branches with work on them.

MEASURED ON THE LIVE BOARD 2026-08-16: of 44 non-terminal tasks, 39 named at
least one non-coordination source file in their description, and there were 54
pairs sharing a file with NO dependency path between them — 16 of which were
dispatchable simultaneously. Two were serialized by hand that day
(``kpr-stale-02`` vs ``kpr-watch-01`` on ``tools/ci/pr_watcher.py``;
``rem-cap-05`` vs ``rem-cap-01`` on
``tools/awareness/capability_consumption.py``) and a third — a four-way
contention on ``tools/awareness/capability_consumption.py`` — had already turned
PR #1730 DIRTY against main.

WHAT IS HONESTLY MEASURABLE, AND WHAT IS NOT
============================================
Distinguishing "this task will WRITE this file" from "this task MENTIONS this
file" is the whole difficulty, and prose does not carry that distinction
reliably. This module therefore reports two grades of evidence and never blurs
them:

* ``prose`` — paths extracted from the task description. Available at SEED time,
  which is the only time that helps, and *inherently a heuristic*. Three
  observed false-positive classes are suppressed (see :func:`classify_mention`);
  there is no claim that the list is complete. A prose finding is a prompt to
  look, not a verdict.
* ``branch`` — ``git diff --name-only origin/main...kanban/<task_id>``, the
  files the task ACTUALLY changed. Exact, and worth strictly more than any
  amount of prose parsing — but it only exists once a branch does, so it is a
  dispatch-time signal, never a seed-time one. Enable with ``--from-branches``.

Both are offered because neither is sufficient: prose is early and fuzzy, branch
is precise and late. Where a task has a branch, its branch paths REPLACE its
prose paths rather than joining them, because the exact answer should not be
diluted by the guess.

WHY NOT COMPARE THE TWO BRANCHES TO EACH OTHER
==============================================
``git merge-tree`` between two task branch tips reports conflicts the forge will
never see, because the forge merges each branch into MAIN in sequence, not into
each other. Measured 2026-08-16: ``hcx-live-02`` vs ``hcx-live-03`` reported
CONFLICT, while against main ``hcx-live-03`` was CLEAN and only ``hcx-live-02``
was dirty. Every branch here is diffed against ``origin/main`` and nothing else.

WHAT IT DOES NOT DO
===================
Refuses nothing. ``create_tasks`` calls :func:`check_batch` as a REPORT, in the
same shape as the three checks already there. Arming it needs a fire-rate
survey first, exactly as rem-hyg-03/04 do for the identity check — CLAUDE.md is
explicit that a check enabled without a survey is unmeasured rather than proven.

Usage::

    python -m tools.kanban.lane_conflicts --json
    python -m tools.kanban.lane_conflicts                  # table by shared file
    python -m tools.kanban.lane_conflicts --live-only
    python -m tools.kanban.lane_conflicts --from-branches
    python -m tools.kanban.lane_conflicts --task rem-hyg-07

    from tools.kanban.lane_conflicts import check_batch
    check_batch([{"id": "x-y-01", "description": "..."}])   # -> [finding]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 — fixed argv, no shell, read-only git plumbing
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Resolved from __file__ and NEVER from os.getcwd(), because
# seeding and dispatch both happen from worktrees.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.git.coordination_paths import is_coordination_path  # noqa: E402
from tools.kanban.deps import effective_dep_ids  # noqa: E402
from tools.kanban.gates import is_manual_gate  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Board vocabulary
# ────────────────────────────────────────────────────────────────────────────

#: A task in one of these states will never be built again, so it cannot race.
#: ``decomposed`` is here alongside the two real terminal states because
#: ``_deps_satisfied`` treats it as satisfied — a decomposed parent has handed
#: its work to children and does nothing itself.
TERMINAL_STATUSES = frozenset({"done", "failed", "decomposed"})

#: Statuses meaning "a session is holding this task RIGHT NOW". Work exists.
ACTIVE_STATUSES = frozenset({
    "in_progress", "pr_opened", "ci_failed", "merge_conflict",
    "changes_requested", "token_exhausted",
})

#: Statuses the promoter/dispatcher draws from. Combined with satisfied
#: dependencies this is "will be picked up on the next cycle".
QUEUED_STATUSES = frozenset({"backlog", "scheduled"})

#: ``suggested`` is a QUARANTINE state, not a queue: nothing dispatches out of it
#: without a human promoting it first, so a suggested task cannot race today.
#: Named rather than lumped into "blocked" so a reader can see why.
QUARANTINE_STATUSES = frozenset({"suggested"})

#: A pair whose members can both be built right now. THE finding.
SEVERITY_LIVE = "live"
#: A pair that shares a file but where at least one side cannot be dispatched
#: yet. Real contention, but not today's. Reporting the two identically is how a
#: live finding gets lost in latent noise.
SEVERITY_LATENT = "latent"

#: Evidence grades — see the module docstring. Never merged.
EVIDENCE_PROSE = "prose"
EVIDENCE_BRANCH = "branch"


# ────────────────────────────────────────────────────────────────────────────
# Path extraction from prose
# ────────────────────────────────────────────────────────────────────────────

#: Extensions that make a slash-bearing token a source path rather than prose.
#: Deliberately a closed list: an open one matches version numbers ("qwen3.5"),
#: percentages and money, all of which appear in these descriptions.
_PATH_EXTENSIONS = (
    "py", "md", "yaml", "yml", "json", "html", "txt", "sql",
    "js", "ts", "css", "toml", "ini", "cfg", "sh", "xml",
)

#: A path must contain at least one ``/``. A bare ``CLAUDE.md`` is far more often
#: a citation of the rules than a claim to edit them, and requiring the slash is
#: a cheaper, more predictable rule than trying to tell those apart.
PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.\-/])"
    r"((?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+\.(?:" + "|".join(_PATH_EXTENSIONS) + r"))"
)

#: Programs whose argument is a file to RUN, not a file to edit.
_RUNNER_TOKENS = (
    "python3", "python", "pytest", "behave", "ruff", "bandit", "black",
    "mypy", "bash", "sh", "node", "npm", "npx", "gh", "git", "icdev", "pip",
)

#: A runner token followed only by flags/paths up to the mention. Anchored to the
#: end of the preceding text so ``python tools/x.py`` matches and a sentence that
#: merely says the word "python" three clauses earlier does not.
_COMMAND_PREFIX = re.compile(
    r"(?:^|[\s`(])(?:" + "|".join(_RUNNER_TOKENS) + r")\b"
    r"(?:\s+-{1,2}[A-Za-z0-9][\w\-]*)*"
    r"(?:\s+[A-Za-z0-9_.\-/]+)*\s*$"
)

#: ``tools/ci/red_first_gate.py --gate`` inside backticks with no ``python`` in
#: front. Six of the seven live mentions of that file carried the runner word;
#: the seventh did not, and a trailing CLI flag is what identifies it.
_TRAILING_FLAG = re.compile(r"^\s+-{1,2}[A-Za-z]")

#: Prefixes whose files are REFERENCE material by default — a memo is cited far
#: more often than it is edited. Overridden per-mention by a write verb on the
#: same line, so "OUTPUT: commit docs/testing/ungated_test_census.json" is still
#: read as a claim while "Read docs/testing/ungated_test_census.json from
#: rem-tst-01" is not.
_REFERENCE_PREFIXES = ("docs/", "memory/", "context/", "hardprompts/", "goals/")

#: An EVIDENCE paragraph: a past observation that enumerates files as DATA.
#:
#: This board writes its evidence under an all-caps marker — "MEASURED
#: 2026-08-16 across tools/: 94 files contain a raw INSERT INTO kanban_tasks…",
#: "MEASURED ON THE LIVE BOARD 2026-08-16: … rem-cap-05 vs rem-cap-01 on
#: tools/awareness/capability_consumption.py". Every path in such a sentence is
#: a specimen of the finding, not a file the task will open.
#:
#: MEASURED against the live board 2026-08-16: without this rule, all three
#: ``live`` findings were false — five of rem-hyg-05's census specimens and
#: three of rem-hyg-07's, including one pairing rem-hyg-07 against hcx-live-02
#: over a file rem-hyg-07 only CITES. A report whose top severity is 0/3 correct
#: is one nobody reads twice, which is the failure mode this check exists to
#: avoid.
#:
#: Required in CAPS at the start of the line, which is the whole discriminator:
#: "measured" lower-case mid-sentence is ordinary prose, while a capitalised
#: marker in column 0 is this board's section heading for observation.
#:
#: Deliberately NOT overridable by a write verb, unlike the reference prefixes
#: above. An evidence paragraph narrates writes that have ALREADY happened
#: ("added", "landed", "regrew"), so honouring a write verb here would defeat
#: the rule on exactly the sentences it was built for. Claims live under ``DO:``.
_EVIDENCE_LINE = re.compile(
    r"^\s*[-*\d.)\s]*"
    r"(?:MEASURED|OBSERVED|SURVEYED|BASELINE|EVIDENCE|CONTEXT|BACKGROUND|RISK)\b"
)

#: "Near enough to govern the mention that follows": up to 60 characters, none
#: of which may START a sentence break. Shared by :data:`_CROSS_REF` and
#: :data:`_PRECEDENT` so there is ONE definition of proximity rather than two
#: that drift.
#:
#: A sentence break is a period followed by WHITESPACE, never a bare period —
#: the distinction is load-bearing, because every path this module looks at
#: contains a dot. Excluding the dot itself was the obvious first cut and it
#: silently truncated the window at ".txt", so "Follow args/ci_test_backlog.txt +
#: args/test_gating_gate.yaml" suppressed the first path and claimed the second.
_NEAR = r"(?:(?!\.\s)[^\n]){0,60}$"

#: An explicit CROSS-REFERENCE — "see X", "per X", "as documented in X".
#:
#: The task brief named citations as a false-positive class using a ``docs/``
#: example, and :data:`_REFERENCE_PREFIXES` handles that shape. This is the same
#: act pointed at a CODE path, which no prefix can catch. Observed live on
#: rem-hyg-04: "NEVER a shell operator and never a config key the code does not
#: read — see the inert hook_points: block in args/extension_config.yaml". That
#: sentence cites hcx-live-02's finding as a cautionary example, and reading it
#: as a claim made the two tasks the board's only ``live`` pair over a file
#: rem-hyg-04 never opens.
#:
#: Bounded to 60 characters and forbidden from crossing a sentence break, so the
#: marker has to govern THIS mention — a "see" three sentences earlier does not
#: silence a claim made later. Character-bounded rather than word-bounded on
#: purpose: "see the inert hook_points: block in" is only four words but carries
#: a colon, and a clause-splitting rule loses the marker on exactly that string.
_CROSS_REF = re.compile(
    r"\b(?:see(?:\s+also)?|per|as\s+(?:described|documented|noted|defined|shown)\s+in)"
    r"\b" + _NEAR,
    re.IGNORECASE,
)

#: A PRECEDENT reference — "do the new thing the way this existing thing is
#: done". The file named is a model to imitate somewhere else, not a file to
#: edit. Observed live on rem-hyg-05: "Follow args/ci_test_backlog.txt +
#: args/test_gating_gate.yaml: the census only ever SHRINKS and the ceiling may
#: only go DOWN" — a sentence that pairs rem-hyg-05 against rem-tst-01 over two
#: files rem-hyg-05 has no intention of touching.
#:
#: Uses the same bounded window as :data:`_CROSS_REF` — one definition of "close
#: enough to govern this mention", applied twice — and for the same reason it is
#: measured in CHARACTERS. A word-counted window missed the SECOND path of
#: "Follow args/ci_test_backlog.txt + args/test_gating_gate.yaml", because a path
#: is not a word: the `/` and `.` in the first one broke the count and the marker
#: stopped reaching. One marker governs a LIST of precedents, which is how this
#: board writes them.
#:
#: "mirror" is excluded on purpose: it is already a write verb ("mirror the
#: module to icdev/"), and a token cannot be evidence for both readings.
_PRECEDENT = re.compile(
    r"\b(?:follow(?:s|ing|ed)?|same\s+(?:shape|way|pattern|form)\s+as"
    r"|like|as\s+in|modell?ed\s+on|precedent\s+(?:of|in)|analogous\s+to"
    r"|cf\.?|compare)\b" + _NEAR,
    re.IGNORECASE,
)

#: Verbs that turn a reference-class mention back into a claim. Matched anywhere
#: on the SAME LINE, which is the window these descriptions actually use — a
#: ±N-character window straddles unrelated sentences and starts inventing claims.
_WRITE_VERBS = re.compile(
    r"\b(?:writ(?:e|es|ing|ten)|commit(?:s|ted)?|creat(?:e|es|ing|ed)|add(?:s|ed|ing)?"
    r"|updat(?:e|es|ing|ed)|land(?:s|ed|ing)?|emit(?:s|ted)?|generat(?:e|es|ing|ed)"
    r"|record(?:s|ed|ing)?|produc(?:e|es|ing|ed)|extend(?:s|ed|ing)?|output|append(?:s|ed|ing)?"
    r"|rewrit(?:e|es|ing|ten)|delet(?:e|es|ing|ed)|mirror(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)

#: An explicit instruction NOT to touch a file. Observed live: ``rem-tst-01``
#: says "Do NOT change args/ci_test_files/core.txt or args/ci_test_backlog.txt in
#: this task" — the two files rem-tst-02 genuinely does edit. Counting that as a
#: claim manufactures a collision out of a sentence written to PREVENT one.
_NEGATION = re.compile(
    r"\b(?:do\s+not|don'?t|never|without|rather\s+than|instead\s+of|not)\s+"
    r"(?:\w+\s+){0,3}?"
    r"(?:chang\w*|edit\w*|touch\w*|modif\w*|writ\w*|updat\w*|add\w*|append\w*)\b"
    r"[^\n]{0,80}$",
    re.IGNORECASE,
)

# Mention classes.
MENTION_CLAIM = "claim"
MENTION_COMMAND = "command"
MENTION_CITATION = "citation"
MENTION_NEGATED = "negated"
MENTION_COORDINATION = "coordination"
MENTION_EVIDENCE = "evidence"
MENTION_PRECEDENT = "precedent"

#: Only this one means "this task intends to edit the file".
CLAIMING_MENTIONS = (MENTION_CLAIM,)


@dataclass(frozen=True)
class Mention:
    """One occurrence of a path in a description, and why it was or was not kept."""

    path: str
    kind: str
    line: str


def _line_around(text: str, start: int, end: int) -> Tuple[str, str, str]:
    """The line containing ``text[start:end]``, split into (before, match, after)."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    if le == -1:
        le = len(text)
    return text[ls:start], text[start:end], text[end:le]


def classify_mention(path: str, before: str, after: str) -> str:
    """Why this occurrence is, or is not, a claim to edit ``path``.

    Six suppressions, each for a false-positive class OBSERVED on the live
    board — none of them invented ahead of the evidence:

    * ``coordination`` — the file is one MANY branches legitimately co-edit.
      Delegated to ``tools/git/coordination_paths.py``, which is the SAME list
      ``pr_watcher``'s merge-time guard uses; a second copy is how the two
      checks come to disagree.
    * ``command`` — the path sits inside a shell command. Nearly every
      description on the board ends with ``python tools/ci/red_first_gate.py
      --gate`` and similar; those are instructions to RUN a tool, not to edit it.
    * ``negated`` — the sentence says explicitly not to touch the file.
    * ``evidence`` — the path is a specimen inside a ``MEASURED``/``OBSERVED``
      paragraph. Eight of the board's nine spurious code-path claims were this,
      including one that pitted this very task against ``hcx-live-02`` over a
      file it does nothing but cite.
    * ``precedent`` — "Follow ``args/ci_test_backlog.txt``" names a model to
      imitate elsewhere, not a file to open.
    * ``citation`` — either an explicit cross-reference ("see X", "per X") or a
      ``docs/`` (``memory/``, ``context/``…) path with no write verb on its
      line. Both are the act of pointing at a file rather than claiming it; the
      prefix rule alone cannot see a cross-reference aimed at a CODE path.

    Order matters where two could fire: ``negated`` and ``evidence`` are settled
    before the reference-prefix rule, because both are stronger statements about
    the sentence than "this path looks like a doc" is.

    A path mentioned five times is a claim if ANY ONE occurrence survives, so a
    task that both runs a tool and edits it is still credited with the edit.
    """
    if is_coordination_path(path):
        return MENTION_COORDINATION
    if _COMMAND_PREFIX.search(before) or _TRAILING_FLAG.match(after):
        return MENTION_COMMAND
    if _NEGATION.search(before):
        return MENTION_NEGATED
    if _EVIDENCE_LINE.match(before):
        return MENTION_EVIDENCE
    if _PRECEDENT.search(before):
        return MENTION_PRECEDENT
    if _CROSS_REF.search(before):
        return MENTION_CITATION
    if path.startswith(_REFERENCE_PREFIXES) and not _WRITE_VERBS.search(before + after):
        return MENTION_CITATION
    return MENTION_CLAIM


def mentions(text: str) -> List[Mention]:
    """Every path occurrence in ``text``, classified. Order preserved."""
    out: List[Mention] = []
    body = text or ""
    for m in PATH_PATTERN.finditer(body):
        path = m.group(1).replace("\\", "/")
        before, _, after = _line_around(body, m.start(1), m.end(1))
        out.append(Mention(path=path, kind=classify_mention(path, before, after),
                           line=(before + m.group(1) + after).strip()))
    return out


def claimed_paths(text: str) -> Set[str]:
    """The set of files a description appears to claim it will EDIT.

    A heuristic, and the module docstring says so plainly. Prefer
    :func:`branch_paths` wherever a branch exists.
    """
    return {m.path for m in mentions(text) if m.kind in CLAIMING_MENTIONS}


# ────────────────────────────────────────────────────────────────────────────
# Path extraction from a branch (exact, but late)
# ────────────────────────────────────────────────────────────────────────────

def _git(args: Sequence[str], *, runner=None) -> Optional[str]:
    """Read-only git, or None. Every caller here is advisory and fails open."""
    run = runner or subprocess.run
    try:
        proc = run(  # nosec B603 — fixed argv, shell=False
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("lane_conflicts: git %s failed (%s)", " ".join(args), exc)
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    return proc.stdout or ""


def branch_paths(
    task_id: str,
    *,
    base: str = "origin/main",
    runner=None,
) -> Optional[Set[str]]:
    """Files the task's branch changed relative to ``base``, or None if no branch.

    ``origin/main...kanban/<task_id>`` — three dots, so the diff is against the
    MERGE BASE and the answer is "what this branch adds", not "how this branch
    differs from whatever main has done since".

    Never diffs one task branch against another. ``git merge-tree`` between two
    branch tips reports conflicts the forge will never see, because the forge
    merges each branch into main in sequence: measured 2026-08-16, hcx-live-02 vs
    hcx-live-03 reported CONFLICT while against main hcx-live-03 was CLEAN.

    Returns ``None`` (not an empty set) when the branch does not exist — "no
    branch yet" and "a branch that changed nothing" are different facts and the
    caller has to be able to tell them apart.
    """
    ref = f"{base}...kanban/{task_id}"
    if _git(["rev-parse", "--verify", "--quiet", f"kanban/{task_id}"], runner=runner) is None:
        if _git(["rev-parse", "--verify", "--quiet", f"origin/kanban/{task_id}"],
                runner=runner) is None:
            return None
        ref = f"{base}...origin/kanban/{task_id}"
    out = _git(["diff", "--name-only", ref], runner=runner)
    if out is None:
        return None
    return {
        line.strip().replace("\\", "/")
        for line in out.splitlines()
        if line.strip() and not is_coordination_path(line.strip())
    }


# ────────────────────────────────────────────────────────────────────────────
# The board
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class Task:
    """One board row, reduced to what a contention question needs."""

    id: str
    title: str = ""
    description: str = ""
    status: str = "backlog"
    depends_on: Set[str] = field(default_factory=set)
    paths: Set[str] = field(default_factory=set)
    evidence: str = EVIDENCE_PROSE

    @property
    def is_gate(self) -> bool:
        return bool(is_manual_gate(self.id, self.title))

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def load_board(conn=None) -> Tuple[List[Task], Dict[str, Set[str]], Dict[str, str]]:
    """``(non_terminal_tasks, dep_edges, all_statuses)`` read from the live board.

    ``dep_edges`` spans EVERY row, terminal ones included: A depends on B depends
    on C serializes A behind C even when B is already done, and dropping the
    terminal middle would break the chain.

    Both dependency mechanisms are read — the scalar ``depends_on_task_id`` and
    the ``kanban_task_deps`` junction — but the edge set is what
    ``tools.kanban.deps`` says actually GATES, not the union of the two. That
    distinction is load-bearing HERE, not merely cosmetic: an edge suppresses a
    conflict, so keeping a scalar the junction graph superseded would report two
    tasks as safely serialized that the dispatcher will now run at the same time
    (kpr-fix-02). This module exists to catch exactly that race.
    """
    owns = conn is None
    if owns:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, title, description, status, depends_on_task_id "
            "FROM kanban_tasks"
        ).fetchall()]
        titles = {str(r["id"]): str(r.get("title") or "") for r in rows}
        try:
            junction = [dict(r) for r in conn.execute(
                "SELECT task_id, depends_on_id FROM kanban_task_deps"
            ).fetchall()]
        except Exception as exc:  # noqa: BLE001 — the junction may not exist yet
            logger.debug("lane_conflicts: kanban_task_deps unreadable (%s)", exc)
            junction = []
    finally:
        if owns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    edges: Dict[str, Set[str]] = {}
    statuses: Dict[str, str] = {}
    junction_by_task: Dict[str, List[str]] = {}
    for j in junction:
        junction_by_task.setdefault(str(j["task_id"]), []).append(
            str(j["depends_on_id"])
        )
    for r in rows:
        tid = str(r["id"])
        statuses[tid] = str(r.get("status") or "")
        scalar = r.get("depends_on_task_id")
        gating = effective_dep_ids(
            scalar,
            junction_by_task.get(tid, ()),
            scalar_is_gate=bool(scalar)
            and is_manual_gate(scalar, titles.get(str(scalar))),
        )
        if gating:
            edges.setdefault(tid, set()).update(gating)

    tasks = [
        Task(
            id=str(r["id"]),
            title=str(r.get("title") or ""),
            description=str(r.get("description") or ""),
            status=str(r.get("status") or ""),
            depends_on=set(edges.get(str(r["id"]), set())),
        )
        for r in rows
        if str(r.get("status") or "") not in TERMINAL_STATUSES
    ]
    return tasks, edges, statuses


def dependency_closure(edges: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    """``{task: every task it transitively depends on}``.

    Iterative rather than recursive so a cycle in the board data — which nothing
    forbids — saturates and stops instead of blowing the stack.
    """
    closure: Dict[str, Set[str]] = {k: set(v) for k, v in edges.items()}
    changed = True
    while changed:
        changed = False
        for node, deps in closure.items():
            grown = set(deps)
            for d in deps:
                grown |= closure.get(d, set())
            grown.discard(node)
            if grown != deps:
                closure[node] = grown
                changed = True
    return closure


def is_serialized(a: str, b: str, closure: Dict[str, Set[str]]) -> bool:
    """True when a dependency path runs between the two, in EITHER direction."""
    return b in closure.get(a, ()) or a in closure.get(b, ())


def dispatch_state(task: Task, statuses: Dict[str, str]) -> str:
    """``active`` | ``ready`` | ``blocked`` — can this task be built right now?

    ``ready`` re-derives what ``promote_backlog_to_scheduled`` decides: every
    GATING dependency — the set ``load_board`` already narrowed via
    ``tools.kanban.deps`` — must be ``done`` or ``decomposed``. A manual gate is never ready — that is the point of a gate —
    and a ``suggested`` row is in quarantine, not in a queue.
    """
    if task.status in ACTIVE_STATUSES:
        return "active"
    if task.is_gate or task.status in QUARANTINE_STATUSES:
        return "blocked"
    if task.status not in QUEUED_STATUSES:
        return "blocked"
    for dep in task.depends_on:
        if statuses.get(dep) not in ("done", "decomposed"):
            return "blocked"
    return "ready"


# ────────────────────────────────────────────────────────────────────────────
# The check
# ────────────────────────────────────────────────────────────────────────────

def resolve_paths(
    tasks: Iterable[Task],
    *,
    from_branches: bool = False,
    base: str = "origin/main",
    runner=None,
) -> None:
    """Fill ``task.paths`` / ``task.evidence`` in place.

    With ``from_branches`` a task that HAS a branch uses the branch's file list
    and discards its prose guess entirely: the exact answer must not be diluted
    by the heuristic one. A task with no branch keeps prose, and says so.
    """
    for task in tasks:
        if from_branches:
            actual = branch_paths(task.id, base=base, runner=runner)
            if actual is not None:
                task.paths = actual
                task.evidence = EVIDENCE_BRANCH
                continue
        task.paths = claimed_paths(task.description)
        task.evidence = EVIDENCE_PROSE


def find_conflicts(
    tasks: Sequence[Task],
    closure: Dict[str, Set[str]],
    statuses: Dict[str, str],
) -> List[dict]:
    """Every unserialized pair sharing a claimed file, ranked live before latent.

    Gate sentinels are dropped before pairing: a ``<prefix>-gate-<n>`` row is
    never built, so a file path inside its ``RISK:`` description is not
    contention. ``tools/kanban/gates.py::is_manual_gate`` is the predicate, so
    this cannot disagree with the dispatcher about what a gate is.
    """
    live = [t for t in tasks if not t.is_gate and not t.is_terminal and t.paths]
    states = {t.id: dispatch_state(t, statuses) for t in live}

    out: List[dict] = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            shared = a.paths & b.paths
            if not shared or is_serialized(a.id, b.id, closure):
                continue
            both_dispatchable = (
                states[a.id] in ("active", "ready")
                and states[b.id] in ("active", "ready")
            )
            out.append({
                "tasks": sorted([a.id, b.id]),
                "shared_files": sorted(shared),
                "severity": SEVERITY_LIVE if both_dispatchable else SEVERITY_LATENT,
                "dispatch_state": {a.id: states[a.id], b.id: states[b.id]},
                "status": {a.id: a.status, b.id: b.status},
                "evidence": {a.id: a.evidence, b.id: b.evidence},
                "detail": (
                    f"{a.id} and {b.id} both claim "
                    f"{', '.join(sorted(shared))} and neither depends on the "
                    f"other, so nothing serializes them. "
                    + (
                        "Both are dispatchable now — the loser's PR is the one "
                        "that gets rebased or binned."
                        if both_dispatchable else
                        f"Latent: {a.id} is {states[a.id]}, {b.id} is "
                        f"{states[b.id]}, so they cannot race yet."
                    )
                ),
            })
    out.sort(key=lambda c: (c["severity"] != SEVERITY_LIVE,
                            -len(c["shared_files"]), c["tasks"]))
    return out


def report(
    *,
    conn=None,
    from_branches: bool = False,
    base: str = "origin/main",
    live_only: bool = False,
    task_filter: Optional[str] = None,
    runner=None,
) -> dict:
    """The whole check, board-wide. Never raises: every caller is advisory."""
    try:
        tasks, edges, statuses = load_board(conn)
    except Exception as exc:  # noqa: BLE001 — fail OPEN
        logger.warning("lane_conflicts: board unreadable (%s) — NOT measured", exc)
        return {"measured": False, "reason": str(exc), "conflicts": [],
                "task_count": 0, "counts": {}}

    resolve_paths(tasks, from_branches=from_branches, base=base, runner=runner)
    conflicts = find_conflicts(tasks, dependency_closure(edges), statuses)

    if task_filter:
        conflicts = [c for c in conflicts if task_filter in c["tasks"]]
    if live_only:
        conflicts = [c for c in conflicts if c["severity"] == SEVERITY_LIVE]

    return {
        "measured": True,
        "task_count": len(tasks),
        "tasks_claiming_files": sum(1 for t in tasks if t.paths and not t.is_gate),
        "evidence_mode": EVIDENCE_BRANCH if from_branches else EVIDENCE_PROSE,
        "counts": {
            SEVERITY_LIVE: sum(1 for c in conflicts if c["severity"] == SEVERITY_LIVE),
            SEVERITY_LATENT: sum(1 for c in conflicts if c["severity"] == SEVERITY_LATENT),
        },
        "conflicts": conflicts,
    }


# ────────────────────────────────────────────────────────────────────────────
# Seed-time entry point
# ────────────────────────────────────────────────────────────────────────────

def check_batch(task_specs: Iterable[dict], *, conn=None) -> List[dict]:
    """Findings for a batch about to be seeded — the ``create_tasks`` entry point.

    Compares each incoming spec against BOTH the rest of its own batch and every
    non-terminal row already on the board, because either is a session that can
    be dispatched alongside it.

    Only the scalar ``depends_on_task_id`` is available for the new rows: the
    ``kanban_task_deps`` junction is populated after the insert, so a batch that
    serializes itself through the junction alone reads here as unserialized. That
    is a false-positive direction, which is the safe one for a report-only check
    — and it disappears the moment the rows exist, when the board-wide CLI sees
    both mechanisms.

    Returns ``[]`` on any failure. Raises nothing: seeding must never break on an
    advisory check.
    """
    specs = [s for s in task_specs if isinstance(s, dict)]
    if not specs:
        return []
    try:
        board, edges, statuses = load_board(conn)
    except Exception as exc:  # noqa: BLE001 — fail OPEN
        logger.debug("lane_conflicts: board unreadable at seed time (%s)", exc)
        return []

    incoming: List[Task] = []
    for spec in specs:
        tid = str(spec.get("id") or "").strip()
        if not tid:
            continue
        dep = spec.get("depends_on_task_id")
        deps = {str(dep)} if dep else set()
        incoming.append(Task(
            id=tid,
            title=str(spec.get("title") or ""),
            description=str(spec.get("description") or ""),
            status=str(spec.get("status") or "backlog"),
            depends_on=deps,
        ))
        statuses.setdefault(tid, str(spec.get("status") or "backlog"))
        if deps:
            edges.setdefault(tid, set()).update(deps)

    if not incoming:
        return []

    known = {t.id for t in incoming}
    combined = incoming + [t for t in board if t.id not in known]
    resolve_paths(combined)
    conflicts = find_conflicts(combined, dependency_closure(edges), statuses)
    # Only pairs involving something being seeded — a pre-existing collision
    # between two rows already on the board is the CLI's finding, not the
    # seeder's, and reporting it here would make every batch look guilty.
    return [c for c in conflicts if known & set(c["tasks"])]


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────

def _render_table(result: dict) -> str:
    if not result.get("measured"):
        return f"lane_conflicts: NOT MEASURED — {result.get('reason', 'board unreadable')}"

    lines = [
        f"Sibling file contention — {result['task_count']} non-terminal task(s), "
        f"{result['tasks_claiming_files']} claiming at least one file "
        f"(evidence: {result['evidence_mode']})",
        f"  {result['counts'][SEVERITY_LIVE]} live, "
        f"{result['counts'][SEVERITY_LATENT]} latent",
        "",
    ]
    if not result["conflicts"]:
        lines.append("  no unserialized pairs share a non-coordination file.")
        return "\n".join(lines)

    by_file: Dict[str, List[dict]] = {}
    for c in result["conflicts"]:
        for f in c["shared_files"]:
            by_file.setdefault(f, []).append(c)

    def _rank(item):
        path, pairs = item
        return (not any(p["severity"] == SEVERITY_LIVE for p in pairs), -len(pairs), path)

    for path, pairs in sorted(by_file.items(), key=_rank):
        lines.append(f"{path}")
        for c in sorted(pairs, key=lambda p: (p["severity"] != SEVERITY_LIVE, p["tasks"])):
            a, b = c["tasks"]
            lines.append(
                f"  [{c['severity']:>6}] {a} ({c['dispatch_state'][a]}/{c['status'][a]})"
                f"  <->  {b} ({c['dispatch_state'][b]}/{c['status'][b]})"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect sibling file contention between kanban tasks "
                    "at seed/dispatch time (rem-hyg-07).",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--live-only", action="store_true",
                        help="only pairs where BOTH tasks are dispatchable now")
    parser.add_argument("--from-branches", action="store_true",
                        help="use `git diff origin/main...kanban/<id>` where a branch "
                             "exists, instead of parsing the description")
    parser.add_argument("--task", help="only pairs involving this task id")
    parser.add_argument("--base", default="origin/main",
                        help="branch-mode diff base (default: origin/main). Each "
                             "branch is compared to THIS, never to another branch.")
    args = parser.parse_args(argv)

    result = report(
        from_branches=args.from_branches,
        base=args.base,
        live_only=args.live_only,
        task_filter=args.task,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(_render_table(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
