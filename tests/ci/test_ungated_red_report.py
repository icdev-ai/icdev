# CUI // SP-CTI
"""The red half of the ungated census: grouping the failures by SHAPE (rem-tst-04).

The census answers "which grandfathered modules are green today?" and the
promotion batches consume that answer. Nothing consumed the other 93 rows, and
the census's own signature table cannot help: it keys on the raw first-failure
line, which carries each test's prose, so 93 reds render as ~80 singletons.

What these tests protect is the part that can silently rot:

  * a classifier that never says "I cannot tell" always finds a bucket, and the
    report then reads as complete when it is guessing;
  * a DB-driver regex that matches anywhere on the line files an ASSERTION whose
    message quotes a docstring as a database error nobody can reproduce;
  * a committed markdown artifact whose counts have drifted from the census it
    claims to summarise is worse than no artifact.

No network, no live DB — every case is a literal recorded by the census run.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CENSUS_JSON = REPO_ROOT / "docs" / "testing" / "ungated_test_census.json"
RED_MD = REPO_ROOT / "docs" / "testing" / "ungated_red_modules.md"

module = importlib.import_module("tools.ci.ungated_test_census")


# --------------------------------------------------------------------------- #
# classify_red — the shape rules, one recorded line each
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # schema-drift: the DDL and the DB the test got have diverged.
        ("E   sqlite3.OperationalError: no such table: rag_chunks", "schema-drift"),
        (
            "E   sqlite3.OperationalError: table nc_traffic_flows has no column named source_zone",
            "schema-drift",
        ),
        # The table name comes BEFORE the word here, which the first draft missed.
        (
            "E   AssertionError: dic_doc_freshness table missing from MINIMAL_ICDEV_SCHEMA",
            "schema-drift",
        ),
        # sql-dialect: PG placeholders reaching SQLite, and the reverse.
        ('E   sqlite3.OperationalError: near "%": syntax error', "sql-dialect"),
        (
            "E   AssertionError: assert 'SELECT snipp...ERE t MATCH ?' == 'SELECT snipp...RE t MATCH %s'",
            "sql-dialect",
        ),
        # import-or-attribute, including the truncated ERROR form.
        (
            "E   ModuleNotFoundError: No module named 'migration'",
            "import-or-attribute",
        ),
        (
            "E   AttributeError: module 'tools.ai_augmentation.engine' has no attribute '_classify'",
            "import-or-attribute",
        ),
        (
            "ERROR tests/test_log_triage.py::TestRun::test_run_no_log_file - ImportError: ...",
            "import-or-attribute",
        ),
        # http-auth: the client was never authenticated. Says nothing about behaviour.
        ("E   assert 401 == 200", "http-auth"),
        (
            'E   AssertionError: Preview failed 403: {"code":"CSRF_FAILED"}',
            "http-auth",
        ),
        # db-error-unspecified: the driver raised, pytest truncated the reason.
        (
            "FAILED tests/test_signal_decay.py::test_get_signals_ranked_ordering - sqlite3...",
            "db-error-unspecified",
        ),
        (
            "FAILED tests/test_cnr_migration_intel.py::test_mi_db_writes_postgresql - psyc...",
            "db-error-unspecified",
        ),
        # assertion, readable and truncated alike.
        ("E   assert False is True", "assertion"),
        (
            "FAILED tests/e2e_alert_test.py::test_async_db_writer_persists_records - Asser...",
            "assertion",
        ),
        (
            "FAILED tests/test_lpx_proxy_reconcile.py::test_no_join_uses_aggregates - asse...",
            "assertion",
        ),
        ("E   Failed: 5 unannotated introspection-PRAGMA probe(s) found", "assertion"),
        # runtime-exception: not an assertion, not a DB driver error.
        ("E   KeyError: 'tables_found'", "runtime-exception"),
        (
            "E   tools.rag.retriever.EmbeddingUnavailableError: no embedding provider is available",
            "runtime-exception",
        ),
        (
            "FAILED tests/test_dcpr_product_registry.py::test_subscribe_and_approve - KeyE...",
            "runtime-exception",
        ),
    ],
)
def test_classify_red_assigns_the_recorded_shape(line, expected):
    assert module.classify_red(line) == expected


def test_a_db_driver_named_inside_an_assertion_message_is_not_a_db_error():
    """The line that made the anchor necessary.

    `tests/test_aca_xp_ledger.py` fails an `assert '"fa_xp_ledger"' in <docstring>`
    where the docstring names the sqlite3 driver. An unanchored driver match files
    it under `db-error-unspecified`, i.e. "re-run it, the reason was truncated" —
    advice that is simply wrong, for a failure whose reason is fully recorded.
    """
    line = (
        "E   assert '\"fa_xp_ledger\"' in 'APPEND_ONLY_TABLES list in "
        "is_append_only_table_modification()\\n    - Direct sqlite3.conn"
    )
    assert module.classify_red(line) == "assertion"


@pytest.mark.parametrize(
    "line",
    [
        "FAILED tests/test_devops_twin_route.py::test_twin_list_empty_returns_200 - ji...",
        "FAILED tests/test_infra_twin_route.py::test_twin_snapshot_summary_rendered - ...",
        "",
    ],
)
def test_a_line_with_no_usable_signature_is_admitted_as_unclassified(line):
    """A classifier that always finds a bucket is not measuring anything."""
    assert module.classify_red(line) == "unclassified"


def test_error_dominant_is_decided_by_the_counts_not_the_prose():
    """No assertion was ever reached, so the fixture is the defect."""
    line = "ERROR tests/test_pipeline_snapshot_db.py::test_create_snapshot_returns_id - F..."
    assert module.classify_red(line, {"error": 11}) == "error-dominant"
    # ...but a named cause beats the count: an ImportError in setup explains
    # itself, and the error tally adds nothing to it.
    assert (
        module.classify_red("ERROR x.py::y - ImportError: ...", {"error": 13})
        == "import-or-attribute"
    )


def test_classify_red_only_ever_returns_a_declared_group():
    keys = {key for key, _title, _why in module.RED_GROUPS}
    assert len(keys) == len(module.RED_GROUPS), "duplicate group key"
    for line in ("", "E   assert 0 == 1", "wat", "ERROR collecting x"):
        assert module.classify_red(line) in keys


# --------------------------------------------------------------------------- #
# red_report — the arithmetic
# --------------------------------------------------------------------------- #
def _synthetic_census():
    return {
        "measured": 5,
        "counts": {"passed": 1, "failed": 2, "timeout": 1, "no-tests": 1},
        "results": [
            {"file": "tests/a.py", "status": "passed", "counts": {"passed": 3},
             "first_failure": ""},
            {"file": "tests/b.py", "status": "failed", "counts": {"failed": 1},
             "first_failure": "E   sqlite3.OperationalError: no such table: t"},
            {"file": "tests/c.py", "status": "failed", "counts": {"failed": 2},
             "first_failure": "E   assert 0 == 1"},
            {"file": "tests/d.py", "status": "timeout", "counts": {},
             "first_failure": "timed out"},
            {"file": "tests/e.py", "status": "no-tests", "counts": {},
             "first_failure": ""},
        ],
    }


def test_red_report_groups_exactly_the_failing_rows():
    red = module.red_report(_synthetic_census())
    assert red["failing_claimed"] == 2
    assert red["failing_grouped"] == 2
    assert red["accounted_for"] is True
    by_key = {g["key"]: g for g in red["groups"]}
    assert [m["file"] for m in by_key["schema-drift"]["modules"]] == ["tests/b.py"]
    assert [m["file"] for m in by_key["assertion"]["modules"]] == ["tests/c.py"]


def test_timeouts_and_no_tests_are_carried_but_never_folded_in():
    """Neither green nor failing. Merging them would break the one checkable claim."""
    red = module.red_report(_synthetic_census())
    grouped_files = {m["file"] for g in red["groups"] for m in g["modules"]}
    assert "tests/d.py" not in grouped_files
    assert "tests/e.py" not in grouped_files
    assert red["other_non_green"]["timeout"] == ["tests/d.py"]
    assert red["other_non_green"]["no-tests"] == ["tests/e.py"]


def test_a_disagreement_with_the_census_count_is_reported_not_swallowed():
    census = _synthetic_census()
    census["counts"]["failed"] = 7  # the census claims more than it carries
    red = module.red_report(census)
    assert red["accounted_for"] is False


def test_every_declared_group_is_rendered_or_named_as_empty():
    """A bucket that silently disappears is how a shape stops being looked for."""
    text = module.render_red_markdown(_synthetic_census(), module.red_report(_synthetic_census()))
    for key, _title, _why in module.RED_GROUPS:
        assert key in text, f"group {key} appears nowhere in the report"


# --------------------------------------------------------------------------- #
# The committed artifacts have to agree with each other
# --------------------------------------------------------------------------- #
def test_the_committed_red_report_matches_the_committed_census():
    """Regenerate from the census and compare to what is on disk.

    Not a tautology: the doc is committed, the census is committed, and this
    fails when either moves without the other.
    """
    assert CENSUS_JSON.exists(), "the census artifact is committed; a skip here would be an UNMEASURED test"
    census = json.loads(CENSUS_JSON.read_text(encoding="utf-8"))
    red = module.red_report(census)
    assert red["accounted_for"], "group counts do not sum to the census failing count"

    assert RED_MD.exists(), "docs/testing/ungated_red_modules.md is missing"
    text = RED_MD.read_text(encoding="utf-8")
    claimed = re.search(r"\*\*(\d+) grouped = (\d+) recorded failing\.\*\*", text)
    assert claimed, "the red report states no headline arithmetic"
    assert int(claimed.group(1)) == red["failing_grouped"]
    assert int(claimed.group(2)) == red["failing_claimed"]

    for group in red["groups"]:
        if not group["count"]:
            continue
        row = f"| {group['count']} | **{group['title']}** (`{group['key']}`) |"
        assert row in text, f"stale count for {group['key']} (expected {group['count']})"


def test_a_red_module_that_is_already_gated_is_named_not_hidden():
    """The census is a SNAPSHOT, and one of its reds has been fixed and gated since.

    `tests/git/test_manifest_merge_rehearsal.py` was red when the census ran and
    was gated by the PR that made it pass (#kax-conflict-11). Asserting the
    intersection is empty would be wrong; filtering those rows out would make the
    report's counts stop matching the census's. So it is reported, by name.
    """
    assert CENSUS_JSON.exists(), "the census artifact is committed; a skip here would be an UNMEASURED test"
    census = json.loads(CENSUS_JSON.read_text(encoding="utf-8"))
    red = module.red_report(census, root=REPO_ROOT)

    reds = {m["file"] for g in red["groups"] for m in g["modules"]}
    gated = set(module.gated_allowlist(REPO_ROOT))
    assert set(red["already_gated"]) == (reds & gated)

    text = RED_MD.read_text(encoding="utf-8")
    assert "## Recorded red here, gated since" in text
    for entry in red["already_gated"]:
        assert f"`{entry}`" in text, f"{entry} is gated and red, and the report does not say so"


def test_the_batch_this_pr_gated_holds_no_red_module():
    """The acceptance criterion, checked rather than asserted in a commit message.

    Scoped to the rem-tst-04 block in `core.txt`, because the file as a whole
    carries entries gated long after the census snapshot.
    """
    census = json.loads(CENSUS_JSON.read_text(encoding="utf-8"))
    red = module.red_report(census, root=REPO_ROOT)
    reds = {m["file"] for g in red["groups"] for m in g["modules"]}

    core = (REPO_ROOT / "args" / "ci_test_files" / "core.txt").read_text(encoding="utf-8")
    _, _, tail = core.partition("# rem-tst-04 —")
    assert tail, "the rem-tst-04 promotion block is missing from core.txt"
    # The block is: header comment, then the promoted entries, then a blank line.
    # Stopping at that blank line matters — the entry gating THIS test file sits
    # further down the same tail, and swallowing it would make the batch 26.
    batch: list[str] = []
    for line in tail.splitlines()[1:]:
        entry = line.strip()
        if entry.startswith("#"):
            continue
        if not entry:
            if batch:
                break
            continue
        batch.append(entry)
    assert len(batch) == 25, f"the batch is documented as 25 modules, found {len(batch)}"
    assert not (set(batch) & reds), "a red module was promoted"

    by_file = {r["file"]: r for r in census["results"]}
    for entry in batch:
        assert by_file[entry]["status"] == "passed", f"{entry} was not green in the census"
