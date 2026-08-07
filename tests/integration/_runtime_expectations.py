# CUI // SP-CTI
"""What the seeded runtime telemetry contains, and how to compare two readings.

Kept out of ``conftest.py`` so both test modules import the same constants
rather than each asserting against whatever the other reader returned — a
comparison of two readers is only meaningful if a third party says what the
right answer is. Imported by name (``from _runtime_expectations import ...``),
which works because pytest puts this directory on ``sys.path`` when it loads
the sibling ``conftest.py``; the same convention as ``_sql_compat`` in
``tests/``.
"""
from __future__ import annotations

#: What the `seeded` fixture writes, as (surface, name) -> (calls, errors).
EXPECTED = {
    ("mcp", "rag_search"): (4, 1),
    ("mcp", "kg_search"): (1, 0),
    ("agent", "builder"): (1, 0),
    ("persona", "analyst"): (1, 0),
}

#: The one group with no completed call. Its avg/max must stay NULL everywhere.
STILL_RUNNING = ("persona", "analyst")

#: Total calls/errors across :data:`EXPECTED`.
TOTAL_CALLS = sum(calls for calls, _ in EXPECTED.values())
TOTAL_ERRORS = sum(errors for _, errors in EXPECTED.values())

#: The fields both readers must agree on. ``error_rate`` is panel-only — the
#: CLI shows counts, not a rate — so it is excluded here and checked separately.
SHARED_FIELDS = ("surface", "name", "calls", "errors", "avg_ms", "max_ms")


def by_key(rows):
    """Rollup rows keyed by (surface, name).

    Compared as a mapping rather than as a list because both readers order by
    call count and the seed leaves three names tied at one call each — list
    equality would be asserting a tie-break neither reader promises.
    """
    return {(row["surface"], row["name"]): row for row in rows}


def shared(row):
    """Just the fields the CLI and the panel are both responsible for."""
    return {field: row[field] for field in SHARED_FIELDS}
