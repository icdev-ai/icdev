# CUI // SP-CTI
"""An opaque machine id must not invent a project card (rem-hyg-08).

`tools/project/kanban_project_sync.py` auto-registers a project card, and its
epics, from any task id shaped `<prefix><epic>-<N>`. It tested the tail with
`parts[-1].isdigit()` and NO bound on its length — and a hex token is all digits
roughly 2% of the time.

Of the 416 opaque `task-<hex>` rows on the live board, THREE ended in an
all-digit hex segment:

    task-0a4389596f-79141324
    task-3bc9eb0918-12704769
    task-3bc9eb0918-79410283

Those three parsed as prefix=`task-`, epic=<hex parent id>, N=<hex tail>, so the
sync invented two "epics" named after hex parent ids and registered a whole
"Task Project" card. Its epic LIKE patterns then claimed 83 rows while the other
333 matched nothing, producing a coverage warning nobody could resolve: the
warning's own advice ("fix the ids or add the missing epic") is wrong for this
namespace, because `task-<hex>` is what the dashboard's create-task API and
`awareness/suggested_card_writer` generate and it was never card work.

The regression this file exists to prevent is the SECOND half: the obvious fix —
gating on `task_identity.classify_shape`, which draws almost this same line — was
measured and would have killed three LEGITIMATE epics whose ids carry machine
tails. `test_a_machine_tail_does_not_disqualify_a_real_namespace` is that case,
and it must keep passing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.project.kanban_project_sync import (  # noqa: E402
    _is_hex_token,
    _parse_task_id,
)

#: The committed registry. Read once, with NO skip guard: `pyyaml` is a hard
#: ICDEV dependency and `args/projects.yaml` is a committed file, so a skip here
#: could only ever hide a broken checkout — and a gated test that skips still
#: reports as coverage while asserting nothing.
def _load_registry() -> dict:
    import yaml

    path = ROOT / "args" / "projects.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


#: The three live ids that caused the defect. Verbatim from the board.
CULPRITS = (
    "task-0a4389596f-79141324",
    "task-3bc9eb0918-12704769",
    "task-3bc9eb0918-79410283",
)


@pytest.mark.parametrize("task_id", CULPRITS)
def test_a_hex_tail_that_is_all_digits_registers_nothing(task_id: str):
    """The defect, in one assertion: these must yield NO prefix and NO epic."""
    assert _parse_task_id(task_id) is None


@pytest.mark.parametrize(
    ("task_id", "expected"),
    [
        # A machine tail is NOT disqualifying — the epic segment decides.
        ("ci-fix-27889336050", ("ci-", "fix")),      # a GitHub Actions run id
        ("mc-reflex-58733561", ("mc-", "reflex")),
        ("cdh-gap-39963587", ("cdh-", "gap")),
    ],
)
def test_a_machine_tail_does_not_disqualify_a_real_namespace(task_id, expected):
    """THE REGRESSION GUARD for the fix that was measured and rejected.

    Gating this module on `task_identity.classify_shape` — which asks whether a
    ROW is card work, and bounds the tail to 1-3 digits — would have killed
    these three epics. They are real: `ci-fix` and `mc-reflex` were already
    registered in args/projects.yaml, and their prefix+epic namespace is
    meaningful even though the tail is an identifier rather than a sequence
    number. The two predicates answer different questions and must not be
    merged.
    """
    assert _parse_task_id(task_id) == expected


@pytest.mark.parametrize(
    ("task_id", "expected"),
    [
        ("sim-l0-01", ("sim-", "l0")),
        ("dt-iqe-03", ("dt-", "iqe")),
        ("ad710-macro-01", ("ad710-", "macro")),
        ("crx-test-05", ("crx-", "test")),
        ("rem-hyg-08", ("rem-", "hyg")),
    ],
)
def test_ordinary_card_ids_still_parse(task_id, expected):
    """The fix must not narrow the ordinary case it exists to serve."""
    assert _parse_task_id(task_id) == expected


@pytest.mark.parametrize(
    "task_id",
    [
        "task-0024fa5a55",   # the plain opaque shape: only two segments
        "task-00a8a39508",
        "not-an-id",
        "",
        "onlyone",
    ],
)
def test_non_card_ids_register_nothing(task_id: str):
    assert _parse_task_id(task_id) is None


class TestHexTokenPredicate:
    """`_is_hex_token` decides whether an "epic key" is a name or an id fragment."""

    @pytest.mark.parametrize("key", ["0a4389596f", "3bc9eb0918", "12345678", "79141324"])
    def test_hex_fragments_are_tokens(self, key: str):
        assert _is_hex_token(key) is True

    @pytest.mark.parametrize(
        "key", ["fix", "reflex", "gap", "iqe", "macro", "l0", "hyg", "test", "auth"]
    )
    def test_real_epic_names_are_not_tokens(self, key: str):
        assert _is_hex_token(key) is False

    @pytest.mark.parametrize("key", ["deadbeef", "decade", "facade", "added", "abcdef"])
    def test_a_hex_legal_word_with_no_digit_is_a_name(self, key: str):
        """The digit lookahead is what saves these.

        `deadbeef` is eight hex-legal characters and would otherwise be
        classified as an id fragment, permanently un-registering any epic
        somebody named that way.
        """
        assert _is_hex_token(key) is False

    def test_a_short_hex_string_is_a_name(self):
        """Seven characters or fewer stays a name: real epic keys are short, and
        a uuid4 fragment is not. The bound is what keeps `l0` and `db` safe."""
        assert _is_hex_token("0a43895") is False
        assert _is_hex_token("0a438959") is True


def test_the_committed_registry_has_no_hex_token_epics():
    """The registry itself must stay clean.

    Measured when this shipped: of 1,602 registered epics exactly two were hex
    tokens, both on the bogus auto-registered `task` card, and both are removed
    by this change. A new one appearing means the sync regressed or somebody
    hand-added one.
    """
    data = _load_registry()
    offenders = [
        f"{project.get('key')}:{epic.get('key')}"
        for project in (data.get("projects") or [])
        for epic in (project.get("epics") or [])
        if _is_hex_token(str(epic.get("key", "")))
    ]
    assert not offenders, (
        "epic keys that are hex id fragments, not names — each one makes its "
        "card count an arbitrary slice of an opaque namespace and warn forever "
        f"about the rest: {offenders}"
    )


def test_the_opaque_task_namespace_owns_no_card():
    """`task-` must not be a registered project.

    The 416 `task-<hex>` rows are opaque machine ids counted by no card, which
    is correct — `task_identity` already treats an unclaimed opaque id as
    reportable but never enforceable. A card for this prefix can only ever be a
    subset with a permanent warning attached.
    """
    data = _load_registry()
    prefixes = {str(p.get("task_prefix", "")) for p in (data.get("projects") or [])}
    assert "task-" not in prefixes


def test_the_icdev_mirror_carries_the_same_rule():
    """`tools.` and `icdev.tools.` are SEPARATE module objects, and the fix
    landed in only one of them.

    The dashboard imports `tools.project.kanban_project_sync`, but a
    wheel-installed deployment reaches `icdev.tools.…` — so a fix applied to one
    copy leaves the other still inventing cards, on precisely the deployment
    nobody is watching. `mirror_parity` catches whole-file drift; this catches
    the behaviour, which is what actually matters.
    """
    import importlib

    mirror = importlib.import_module("icdev.tools.project.kanban_project_sync")
    for task_id in CULPRITS:
        assert mirror._parse_task_id(task_id) is None, task_id
    assert mirror._parse_task_id("ci-fix-27889336050") == ("ci-", "fix")
    assert mirror._parse_task_id("sim-l0-01") == ("sim-", "l0")
