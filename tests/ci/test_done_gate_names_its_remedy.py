# CUI // SP-CTI
"""kpr-watch-21 — a refusal states a condition AND names its remedy.

`pr_watcher._enforced_done_ok` refused 10,542 times with "enforced gate: awaiting
ICDEV done-verification": accurate, and actionless. Nothing writes a
`kanban_verifications` row except a dispatch, so the verification is something an
operator RUNS (`cli.py --reverify <id>`), and the refusal never said so.

Both halves are asserted here, and the first one is the important one:

* **the CONDITION is unchanged** — same predicate, same fail-closed posture. Every
  input that refused before still refuses, and only `pass`/`passed`/`bypassed`
  passes. A card that "improves a message" must not be able to widen a gate.
* **the refusal names what to run** — and names the RIGHT thing per branch.
  `--reverify` is wrong for `review_passed=false` (`reverify_is_allowed` refuses
  it, because a fresh NULL verdict would launder a conformance failure), so that
  branch must NOT advertise it.

The `land.py` audit (docs/reference/cards/kpr-watch-21.md) is pinned at the bottom:
the refusal-site count, so a new rung cannot be added without the audit being
re-read, and the four rungs whose remedy this card added.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

prw = importlib.import_module("tools.ci.pr_watcher")
land = importlib.import_module("tools.kanban.land")
cli = importlib.import_module("tools.kanban.cli")

TASK = "kpr-watch-21"
PR = "https://github.com/icdev-ai/ICDev/pull/2280"


class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Cur(self._row)


def _gate(row, monkeypatch, *, enforce="1"):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", enforce)
    return prw._enforced_done_ok(lambda: _Conn(row), TASK)


# ── half one: the CONDITION did not move ────────────────────────────────────

@pytest.mark.parametrize("row, expected_ok", [
    (None, False),                                       # never verified
    ({"result": "failed", "review_passed": None}, False),
    ({"result": "passed", "review_passed": 0}, False),   # conformance failed
    ({"result": "pending", "review_passed": None}, False),
    ({"result": "", "review_passed": None}, False),
    ({"result": "passed", "review_passed": None}, True),
    ({"result": "pass", "review_passed": 1}, True),
    ({"result": "bypassed", "review_passed": None}, True),
])
def test_the_gate_still_fires_on_the_same_input(row, expected_ok, monkeypatch):
    ok, reason = _gate(row, monkeypatch)
    assert ok is expected_ok, reason


def test_enforcement_off_is_still_the_only_blanket_pass(monkeypatch):
    ok, reason = _gate(None, monkeypatch, enforce="0")
    assert ok is True and reason == "enforcement off"


def test_an_unreadable_store_still_holds_the_merge(monkeypatch):
    """Fail-closed: the reason gained a sentence, not a pass."""
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")

    def _boom():
        raise RuntimeError("connection refused")

    ok, reason = prw._enforced_done_ok(_boom, TASK)
    assert ok is False
    assert "connection refused" in reason


def test_every_refusal_keeps_the_prefix_merge_stall_classifies_on(monkeypatch):
    """`merge_stall.DEFAULT_HOLD_PATTERNS` matches on "enforced gate:"."""
    ms = importlib.import_module("tools.ci.merge_stall")
    for row in (None,
                {"result": "failed", "review_passed": None},
                {"result": "passed", "review_passed": 0},
                {"result": "pending", "review_passed": None}):
        ok, reason = _gate(row, monkeypatch)
        assert ok is False
        assert reason.startswith("enforced gate:"), reason
        assert any(pat in reason.lower() and cause == ms.CAUSE_DONE_GATE
                   for pat, cause in ms.DEFAULT_HOLD_PATTERNS), reason


# ── half two: the refusal names the remedy ──────────────────────────────────

@pytest.mark.parametrize("row", [
    None,                                                # the 10,542-row case
    {"result": "failed", "review_passed": None},
    {"result": "pending", "review_passed": None},
])
def test_the_refusal_names_reverify_with_this_task_id(row, monkeypatch):
    ok, reason = _gate(row, monkeypatch)
    assert ok is False
    assert "--reverify" in reason, reason
    # the TASK ID, not a placeholder: a reader must be able to paste it
    assert f"--reverify {TASK}" in reason, reason
    assert "tools/kanban/cli.py" in reason, reason


def test_the_missing_row_refusal_says_it_will_not_arrive_on_its_own(monkeypatch):
    """"awaiting" reads as *wait*, and waiting never clears it."""
    _ok, reason = _gate(None, monkeypatch)
    assert "awaiting ICDEV done-verification" in reason, reason
    assert "will not arrive" in reason, reason


def test_a_conformance_failure_does_NOT_advertise_reverify(monkeypatch):
    """`reverify_is_allowed` refuses this case; the refusal must not send a
    reader to a command designed to refuse them."""
    ok, reason = _gate({"result": "passed", "review_passed": 0}, monkeypatch)
    assert ok is False
    assert "review_passed=false" in reason
    assert "NOT clear this" in reason, reason
    assert "--force-done" in reason, reason
    # and the module it defers to genuinely refuses
    allowed, why = prw.reverify_is_allowed({"review_passed": 0}, allow_when_missing=True)
    assert allowed is False, why


def test_an_unreadable_store_is_not_sold_as_a_reverify_case(monkeypatch):
    """--reverify reads the SAME store; naming it here is a dead end."""
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")

    def _boom():
        raise RuntimeError("connection refused")

    _ok, reason = prw._enforced_done_ok(_boom, TASK)
    assert "--reverify" not in reason, reason
    assert "storage fault" in reason, reason


def test_reverify_remedy_is_one_sentence_stated_once():
    """The remedy text has a single home, so the four call sites cannot drift."""
    remedy = prw.reverify_remedy("abc-01")
    assert "python tools/kanban/cli.py --reverify abc-01" in remedy
    assert "--dry-run" in remedy


# ── the CLI names it BEFORE the refusal ─────────────────────────────────────

def test_the_merge_help_text_points_at_reverify():
    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    if parser is None:
        src = Path(cli.__file__).read_text(encoding="utf-8")
        merge_help = re.search(r'"--merge", action="store_true",\s*help=(.*?)\)\n',
                               src, re.S)
        assert merge_help, "could not locate the --merge help text"
        text = merge_help.group(1)
    else:  # pragma: no cover - build_parser does not exist today
        text = next(a.help for a in parser._actions if "--merge" in a.option_strings)
    assert "--reverify" in text, text
    assert "done-verification" in text, text


def test_the_module_docstring_shows_the_two_command_sequence():
    doc = cli.__doc__ or ""
    assert "--reverify zig-ext-08" in doc
    assert "awaiting ICDEV done-verification" in doc


# ── the land.py audit is pinned ─────────────────────────────────────────────

#: Every `_refusal(...)` return in tools/kanban/land.py was read once, and the
#: verdict per rung lives in docs/reference/cards/kpr-watch-21.md. This count is
#: the tripwire: adding an 18th refusal without re-reading that audit fails here.
LAND_REFUSAL_SITES = 17


def test_the_land_refusal_census_matches_the_audit():
    src = Path(land.__file__).read_text(encoding="utf-8")
    assert src.count("return _refusal(") == LAND_REFUSAL_SITES, (
        "tools/kanban/land.py gained or lost a refusal — re-read "
        "docs/reference/cards/kpr-watch-21.md and update the audit table"
    )


@pytest.mark.parametrize("marker, remedy", [
    ("is not the default branch", "gh pr edit"),
    ("CI is red", "gh pr checks"),
    ("no conclusive successful check rollup", "gh pr checks"),
    ("shares source file(s) with", "lowest-numbered mergeable sibling"),
])
def test_the_four_rungs_this_card_fixed_name_their_command(marker, remedy):
    """Source-level, because these reasons are f-string literals on refusal paths
    that need a live forge to reach. The behaviour they gate is covered by
    tests/kanban/test_cli_merge_landing.py, which still asserts each marker."""
    src = Path(land.__file__).read_text(encoding="utf-8")
    idx = src.find(marker)
    assert idx > 0, f"refusal {marker!r} is gone — the audit is stale"
    window = src[idx:idx + 600]
    assert remedy in window, f"{marker!r} no longer names its remedy ({remedy!r})"
