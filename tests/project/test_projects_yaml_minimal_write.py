# CUI // SP-CTI
"""The sync writer must not rewrite what it did not change (autonomy-dep-03).

THE INCIDENT. `args/projects.yaml` is TRACKED, and `code_reload.pull_if_safe`
refuses to pull when an incoming file is also locally modified — correctly,
since pulling over a modified file destroys work on a shared checkout. Measured
2026-08-21, the live deployment had been frozen 22 commits behind origin/main:
170 files incoming, 11 locally modified, and exactly ONE overlap — this file.
Every merged fix was absent from the running services while every board and CI
signal stayed green.

WHY THIS FILE. `kanban_project_sync` ran `yaml.dump` over the WHOLE document,
and that round-trip is not stable: it reflows block scalars, quoting and line
wrapping. Measured on the live file, a write that changed NOTHING semantically
still produced +2,174 / -1,599 lines, and adding one project rewrote all 165.
The writer runs on every dashboard task creation, and every card registration
edits this file upstream — so the local side was re-dirtied continuously while
the incoming side changed constantly, and a correct, transient refusal became a
permanent freeze.

THE PROPERTY THAT MATTERS, and the first test below: composing with NOTHING
changed must return the original bytes. Everything else follows from it — a diff
proportional to the change is reviewable, committable, and a commit is what
clears the block.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.project.kanban_project_sync import (  # noqa: E402
    _split_project_blocks,
    compose,
)

SAMPLE = """# CUI // SP-CTI
#
# Auto-managed by tools/project/kanban_project_sync.py

projects:
- key: alpha
  name: Alpha
  description: 'A description that the dumper would happily reflow onto
    several lines of its own choosing.'
  task_prefix: alpha-
  epics:
  - key: gate
    title: Manual Gate
    priority: critical
- key: beta
  name: Beta
  task_prefix: beta-
  epics:
  - key: one
    title: One
    priority: medium
"""


def _data(text):
    return yaml.safe_load(text)


# --------------------------------------------------------------------------- #
# 1. THE property
# --------------------------------------------------------------------------- #
def test_composing_with_nothing_changed_returns_the_original_bytes():
    """The whole fix in one assertion. The previous writer failed this by
    +2,174 / -1,599 on the live file."""
    assert compose(_data(SAMPLE), SAMPLE, set()) == SAMPLE


def test_an_unchanged_project_is_written_back_verbatim():
    """Not "equivalently" — verbatim. A reflowed block scalar is a diff, and a
    diff on this file is what blocks the deployment."""
    out = compose(_data(SAMPLE), SAMPLE, {"beta"})
    assert "  description: 'A description that the dumper would happily reflow onto\n" in out


# --------------------------------------------------------------------------- #
# 2. A change is rendered, and stays proportional
# --------------------------------------------------------------------------- #
def test_adding_an_epic_touches_only_that_project():
    import copy

    data = copy.deepcopy(_data(SAMPLE))
    beta = [p for p in data["projects"] if p["key"] == "beta"][0]
    beta["epics"].append({"key": "two", "title": "Two", "priority": "medium"})

    out = compose(data, SAMPLE, {"beta"})
    assert "key: two" in out
    # alpha's text is untouched, including the scalar the dumper would reflow.
    assert SAMPLE[SAMPLE.index("- key: alpha"):SAMPLE.index("- key: beta")] in out


def test_a_new_project_is_appended_without_disturbing_the_rest():
    import copy

    data = copy.deepcopy(_data(SAMPLE))
    data["projects"].insert(0, {"key": "gamma", "name": "Gamma",
                                "task_prefix": "gamma-"})

    out = compose(data, SAMPLE, {"gamma"})
    assert "- key: gamma" in out
    assert out.index("- key: gamma") < out.index("- key: alpha"), (
        "insertion order was not preserved"
    )
    assert SAMPLE[SAMPLE.index("- key: alpha"):] in out


def test_the_result_still_parses_to_the_intended_data():
    """Preserving text is worthless if it changes meaning."""
    import copy

    data = copy.deepcopy(_data(SAMPLE))
    data["projects"][1]["epics"].append({"key": "two", "title": "Two",
                                         "priority": "medium"})
    assert yaml.safe_load(compose(data, SAMPLE, {"beta"})) == data


# --------------------------------------------------------------------------- #
# 3. The splitter
# --------------------------------------------------------------------------- #
def test_the_splitter_keeps_the_preamble_and_one_block_per_project():
    preamble, blocks = _split_project_blocks(SAMPLE)
    assert preamble.endswith("projects:\n")
    assert set(blocks) == {"alpha", "beta"}
    assert blocks["alpha"].startswith("- key: alpha")
    assert "- key: beta" not in blocks["alpha"], "blocks bled into each other"


def test_reassembling_untouched_blocks_reproduces_the_file():
    preamble, blocks = _split_project_blocks(SAMPLE)
    assert preamble + blocks["alpha"] + blocks["beta"] == SAMPLE


def test_an_empty_file_still_composes():
    """Bootstrap: no original text at all must not crash or lose the header."""
    out = compose({"projects": [{"key": "solo", "name": "Solo",
                                 "task_prefix": "solo-"}]}, "", {"solo"})
    assert "projects:" in out and "- key: solo" in out
    assert yaml.safe_load(out)["projects"][0]["key"] == "solo"


def test_a_project_absent_from_the_original_text_is_rendered_not_dropped():
    """It has no block to preserve, so it must be rendered — silently dropping
    it would lose a card the board knows about."""
    import copy

    data = copy.deepcopy(_data(SAMPLE))
    data["projects"].append({"key": "delta", "name": "Delta",
                             "task_prefix": "delta-"})
    out = compose(data, SAMPLE, set())        # NOT listed as changed
    assert "- key: delta" in out, "a project with no existing block was dropped"
