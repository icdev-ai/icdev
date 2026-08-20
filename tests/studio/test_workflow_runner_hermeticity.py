# CUI // SP-CTI
"""A stubbed workflow run must not open a database connection (hgx-park-02).

`test_workflow_parallel.py` opens by promising its tests "must not need a seeded
database", and `_stub_persistence` is how it keeps that promise. The promise was
silently broken and nothing noticed.

hgx-park-01 replaced this pair inside the human-gate branch of `_worker`

    _update_step_record(step_run_id, result)         # the GATE row
    _update_run_status(run_id, "awaiting_approval")  # the RUN row

with one atomic `_park_for_approval`, closing a real race — and in doing so
swapped TWO seams the stub list already covered for ONE it did not. Every
shipped template carrying a `node_type: human` step then reached the live
database from a test that declares it needs none.

It failed the way a filesystem dependency always fails: invisibly, and somewhere
else. It PASSED wherever an ambient `data/icdev.db` happened to hold the studio
tables — every developer machine, and CI right up until the merge — and FAILED
where none did. Against an empty database 43 of the 62 shipped templates fail.
`main` went red on the merge commit, and the `Test` check on the PR that caused
it had reported SUCCESS. The suite had been describing the developer's
filesystem rather than the dispatch order it claims to pin.

WHY THIS IS A SEPARATE FILE, AND WHY IT ARMS THE SEAM RATHER THAN ENUMERATING
IT. Adding `_park_for_approval` to the stub list fixes today's break, and a test
that then asserts the list contains it only restates the fix — it would pass
against the broken tree the moment the fix is applied, so it can never have gone
RED. Patching `get_connection` to RAISE asks the question the enumeration cannot:
did anything at all reach persistence? That covers the writer nobody has added
yet, which is the case that actually recurs. It lives outside the harness module
so that applying this file to an unfixed tree does not also apply the fix to it,
which is what makes the red-first proof meaningful here.
"""

from __future__ import annotations

# Reuse the harness rather than rebuild it: a second copy of `_stub_persistence`
# would drift from the one under test, and drift is the defect being guarded.
from tests.studio.test_workflow_parallel import _run, runner

#: A minimal human gate. `node_type: human` is the branch that parks, and
#: parking is the only path that reaches `_park_for_approval`.
HUMAN_GATE = """
steps:
  - id: step_1
    name: Approval
    node_type: human
    tool: ""
    depends_on: []
  - id: step_2
    name: After
    tool: tools/x.py
    depends_on: [step_1]
"""


def test_a_stubbed_run_never_opens_a_database_connection(monkeypatch):
    """Arm the seam: if ANY persistence path escapes `_stub_persistence`, the
    connection attempt raises here instead of silently depending on whatever
    the ambient database happens to contain."""
    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "a stubbed run opened a database connection — a persistence seam "
            "is missing from test_workflow_parallel._stub_persistence()"
        )

    monkeypatch.setattr(runner, "get_connection", _refuse)
    recorder, _events = _run(monkeypatch, HUMAN_GATE)

    # The gate is stubbed approved, so the dependent step must still dispatch.
    # Asserting the order (not merely "no exception") keeps this honest: a stub
    # that swallowed the park would satisfy the connection check while breaking
    # the run.
    assert recorder.order == ["step_1", "step_2"]

