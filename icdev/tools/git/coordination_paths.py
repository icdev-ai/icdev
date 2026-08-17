# CUI // SP-CTI
"""Which repo paths are safe for two branches to touch at once? (rem-hyg-07)

THE SINGLE SOURCE
=================
``tools/ci/pr_watcher.py`` curated this list first, for the sibling-conflict
guard that runs on OPEN PRs. ``tools/kanban/lane_conflicts.py`` asks the same
question at SEED and DISPATCH time, and the two must not be allowed to disagree:
a second divergent copy is worse than none, because the seed-time check would
report a collision the merge-time check then waves through (or the reverse), and
a human reading either one could not tell which was current.

So the list lives here and both import it. ``pr_watcher`` keeps its private
``_is_additive_path`` / ``_is_generated_path`` aliases pointing at these
functions — the names are load-bearing for its tests.

Keep it import-free, like ``tools/kanban/gates.py``: every caller — CLI,
watcher, seeder, Flask route — must be able to import it without pulling in a
dependency tree.

WHAT "SAFE TO CO-EDIT" ACTUALLY MEANS
=====================================
Two different reasons, deliberately kept apart because they are not the same
claim:

* **coordination** — a file MANY branches legitimately append to. Only the
  manifest shards and ``args/ci_test_files/`` are literally union-merged; the
  rest is a heuristic about how the file is edited. See
  :data:`COORDINATION_PATH_MARKERS`.
* **generated** — a file a generator produces and a human never arbitrates.
  Re-running the generator over the merged tree yields the correct content, so
  there is nothing to serialize. See :data:`GENERATED_PATH_MARKERS`.

Usage::

    from tools.git.coordination_paths import is_coordination_path

    if is_coordination_path("tools/manifest/kanban.md"):
        ...   # two branches here is normal, not a collision
"""

from __future__ import annotations

# Coordination files that MANY task branches legitimately co-edit (manifest
# shards, append-only-table registry, nav/registry configs, conftest schema).
# Two PRs touching these is normal, not a collision — exclude them from the
# sibling-conflict check so it only fires on genuine same-source-file races
# (e.g. two branches each creating a different tools/cortex/blueprint.py). See
# the merge-conflict-hotspots prevention notes.
#
# "Union-merged" is only literally true for three of these: `.gitattributes`
# declares `tools/manifest*` (kax-conflict-03), `args/ci_test_files/*.txt`
# (kax-conflict-07) and `docs/reference/commands.md` (kax-conflict-11)
# `merge=union`, so concurrent appends there resolve without a human IN GIT.
#
# NOT ON THE FORGE (kpr-watch-07). GitHub does not apply `.gitattributes` merge
# drivers when it computes PR mergeability, so two PRs appending to a union path
# still both go CONFLICTING/DIRTY and each needs a rebase. Measured 2026-08-17:
# nine of ten open PRs were DIRTY and a without-union merge reproduced the
# forge's verdict on ten of ten. #1777 merging that morning re-DIRTIED all four
# of its siblings at once. So excluding these paths does NOT mean two PRs sharing
# one can both sail through — it means the watcher declines to serialise them and
# lets `pr_watcher`'s rebase recovery clean up after each merge instead.
#
# THAT IS THE RIGHT TRADE, AND IT WAS MEASURED RATHER THAN ASSUMED.
# `python tools/ci/sibling_hold_survey.py` replays real PRs under both postures.
# Over 120 merged PRs on 2026-08-17:
#
#     posture   held at their own merge moment   max clique
#     current   35/120  (29.2%)                   5
#     widened   78/120  (65.0%)                  13
#
# Widening more than doubles the hold rate and produces cliques of 13, each step
# of which costs a full CI cycle — hours of strictly serial merging, for no
# throughput gain, because the board already drains at one merge per CI cycle
# either way. Do NOT move these entries; re-run the survey if you think the
# balance has changed.
#
# What the survey DID find worth fixing is one level up, in the tie-break rather
# than in this list: it keyed on PR NUMBER alone, so a PR held behind a draft or
# CONFLICTING sibling waited for a queue slot that never came free. See
# `pr_watcher._wins_sibling_tiebreak`.
# The remaining paths are structured config/code, where union would produce
# duplicate keys or broken syntax — they are excluded from the sibling check as
# a heuristic about how they are edited, NOT because git resolves them
# automatically. Adding a path here does not make it auto-mergeable.
#
# That gap is not hypothetical: `docs/reference/commands.md` sat in this tuple
# with no union rule behind it, so the watcher stopped serializing PRs that
# shared it while git still conflicted on every one. Two of the six conflicts
# resolved by hand on 2026-08-16 were exactly that. When you add a path here,
# decide explicitly which of the two claims you are making.
#
# `args/ci_test_files/` is the second entry that is literally union-merged
# (kax-conflict-07). It holds the pytest allowlists that used to be a
# line-continuation chain inside .github/workflows/icdev-ci.yml. That inlining
# is what deadlocked the board on 2026-08-09: five open PRs each appended a test
# path to the same hand-written workflow, so the guard made each a sibling of
# every other and refused all five. Note what is NOT listed here — the workflow
# itself. It carries real job definitions, and two PRs editing a job's `run:`
# block is a genuine collision worth serializing; only the additive list moved
# out, so only the list is excluded.
COORDINATION_PATH_MARKERS = (
    "tools/manifest/",
    "tools/manifest.md",
    "args/ci_test_files/",
    ".claude/hooks/pre_tool_use.py",
    "tools/dashboard/templates/base.html",
    ".claude/commands/start.md",
    "args/component_registry.yaml",
    "args/projects.yaml",
    "args/genesis_config.yaml",
    "tests/conftest.py",
    "docs/reference/commands.md",
)

#: Substrings marking a DERIVED artifact — a file produced by a generator and
#: checked in, never hand-edited.
#:
#: These are excluded for a different reason than the coordination files above.
#: A coordination file is safe to co-edit because it union-merges. A generated
#: file is safe because a conflict in it is not a disagreement at all: re-running
#: the generator over the merged tree produces the correct content, so there is
#: nothing for a human to arbitrate and nothing for a serialized merge to protect.
#:
#: WHY THIS EXISTS. On 2026-08-09 a single generated file —
#: docs/research/external-benchmark-map.generated.md — deadlocked the entire
#: board. Every branch that added a module regenerated it, so every open PR
#: touched it, so hold_on_sibling_conflict made every PR a sibling of every other
#: and refused all six. The guard was behaving correctly; the input made it
#: total. The daemons were healthy the whole time, which is what made it read as
#: "the dispatcher is broken". One shared generated file is enough to stop
#: everything, so the class is excluded rather than that one path.
GENERATED_PATH_MARKERS = (
    ".generated.",
    "/generated/",
)


def normalize(path: str) -> str:
    """Backslashes to forward slashes — Windows paths compare against POSIX markers."""
    return (path or "").replace("\\", "/")


def is_generated_path(path: str) -> bool:
    """True when `path` is a generator-produced artifact (regenerate, don't merge)."""
    p = normalize(path)
    return any(marker in p for marker in GENERATED_PATH_MARKERS)


def is_coordination_path(path: str) -> bool:
    """True when `path` is safe for two branches to touch at once.

    Either union-merged coordination state, or a derived artifact whose conflicts
    are resolved by regeneration rather than by arbitration.
    """
    p = normalize(path)
    if is_generated_path(p):
        return True
    return any(marker in p for marker in COORDINATION_PATH_MARKERS)
