"""Wave-parallel dispatch must not reorder a template that never asked for it (hgx-vv-01).

`hgx-par-01` swapped the workflow runner's eager `_resolve_dag` walk for a
prepared `TopologicalSorter` driven by `get_ready()`/`done()`. Backward
compatibility rested on a claim written as a comment in the runner:

    At max_parallel == 1 this walks the graph in exactly `_resolve_dag` order.

These tests pin that claim to the templates actually on disk, so a future change
to the dispatch loop cannot quietly reorder 61 shipped workflows. The comparison
itself lives in `tools/studio/dispatch_parity.py` — this file is the gate.
"""

from __future__ import annotations

import pytest

from tools.studio import dispatch_parity
from tools.studio.workflow_runner import _MAX_PARALLEL_CAP, _max_parallel


def test_every_shipped_template_dispatches_in_baseline_order():
    """The whole gallery, both roots — no template's executed order changed."""
    report = dispatch_parity.run_check()
    assert report["templates_compared"] > 0, (
        f"no templates were compared; roots resolved to {report['template_roots']} "
        "— a check that silently compares nothing is not a check"
    )
    assert report["identical"], (
        "wave-parallel dispatch reordered a template that does not declare "
        f"max_parallel: {[d['template'] for d in report['diverged']]}"
    )


def test_templates_are_found_under_both_roots():
    """A resolver typo that finds one root would hide every divergence in the other."""
    report = dispatch_parity.run_check()
    # The FORGE composer set and the Studio gallery are both non-empty today; the
    # floor is deliberately low so adding or retiring a template is not a failure.
    assert report["templates_found"] >= 40, (
        f"only {report['templates_found']} templates found — expected both "
        f"{report['template_roots']} to resolve"
    )


def test_a_template_declaring_no_max_parallel_runs_one_step_at_a_time():
    """DEFAULT 1 is the property the whole parity argument rests on."""
    assert _max_parallel({}) == 1
    assert _max_parallel({"max_parallel": None}) == 1
    # Degrade to the nearest legal value rather than failing the run.
    assert _max_parallel({"max_parallel": "not-a-number"}) == 1
    assert _max_parallel({"max_parallel": 0}) == 1
    assert _max_parallel({"max_parallel": -4}) == 1
    # A template typo cannot spawn a thread per step.
    assert _max_parallel({"max_parallel": 500}) == _MAX_PARALLEL_CAP


@pytest.mark.parametrize(
    "steps",
    [
        # Linear chain.
        [{"id": "a"}, {"id": "b", "depends_on": ["a"]}, {"id": "c", "depends_on": ["b"]}],
        # Fan-out then join — the shape parallel dispatch exists for.
        [
            {"id": "root"},
            {"id": "left", "depends_on": ["root"]},
            {"id": "right", "depends_on": ["root"]},
            {"id": "join", "depends_on": ["left", "right"]},
        ],
        # Two independent roots: nothing orders them but declaration order.
        [{"id": "one"}, {"id": "two"}, {"id": "after", "depends_on": ["one"]}],
        # A dangling depends_on target is retired without executing, in BOTH
        # generations — it must not shift the surviving steps.
        [{"id": "a", "depends_on": ["ghost"]}, {"id": "b", "depends_on": ["a"]}],
    ],
)
def test_dispatch_matches_baseline_on_representative_shapes(steps):
    """Hand-built graphs, so the property holds for templates not yet written."""
    baseline = [s for s in dispatch_parity.baseline_order(steps)
                if s in {step["id"] for step in steps}]
    assert dispatch_parity.dispatch_order(steps, max_parallel=1) == baseline


def test_parity_module_is_mirrored_into_the_package():
    """`tools/studio` and `icdev/tools/studio` are full copies, not shims."""
    repo_root = dispatch_parity._REPO_ROOT
    root_copy = repo_root / "tools" / "studio" / "dispatch_parity.py"
    package_copy = repo_root / "icdev" / "tools" / "studio" / "dispatch_parity.py"
    assert root_copy.is_file(), f"missing {root_copy}"
    assert package_copy.is_file(), f"missing {package_copy}"
    assert root_copy.read_bytes() == package_copy.read_bytes(), (
        "tools/studio/dispatch_parity.py and its icdev/ mirror have drifted"
    )
