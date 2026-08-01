# CUI // SP-CTI
"""DIC runtime SQL must author %s, not lean on translate_sql's ? shim.

PostgreSQL is primary and psycopg2 wants %s. storage.translate_sql rewrites `?`
at runtime, and its own comment says that must not become load-bearing:

    # WARNING: this translation masks source code that uses SQLite-style ?
    # placeholders in runtime modules. Log so the silent translation is
    # visible in server logs and doesn't become a hidden load-bearing shim.

It was load-bearing for 71 sites in these three modules. This keeps them clean.

Scoped to DIC on purpose. Repo-wide the debt is ~1275 lines across ~369 files,
and pg_portability_linter's baseline is keyed on (file, line, pattern) — a
baseline that large would turn every line-shifting edit into a "new" finding and
break CI. That is a separate decision with a separate instrument; it is not a
reason to let these three files rot back.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The modules converted alongside this test.
CLEAN_MODULES = [
    "tools/document_intelligence/blueprint.py",
    "tools/document_intelligence/analytics_engine.py",
    "tools/document_intelligence/explorer.py",
    # already clean before the conversion — keep them that way
    "tools/document_intelligence/search_engine.py",
    "tools/document_intelligence/freshness_engine.py",
    "tools/document_intelligence/acoic.py",
    "tools/document_intelligence/handoff.py",
    "tools/document_intelligence/ingest_orchestrator.py",
]

_PH = re.compile(r"(?<![\w?])\?(?![\w?])")
_SQLW = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|WHERE|VALUES|SET|LIMIT)\b", re.I
)
# ",".join(["?" for _ in ids])  /  ",".join("?" * n) — dynamic IN-lists. These
# carry no SQL keyword, so a keyword scan never sees them.
_JOIN_PH = re.compile(r"\.join\(\s*[\[\(]?\s*[\"']\?[\"']")


def _sql_strings(path: Path):
    """Yield (lineno, text) for SQL-looking string literals AND f-strings.

    f-strings are JoinedStr, not Constant — an ast.Constant walk silently misses
    `f"... LIMIT ?"`, which is exactly how two survived the first conversion pass.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
        else:
            continue
        if text and _SQLW.search(text):
            yield node.lineno, text


@pytest.mark.parametrize("rel", CLEAN_MODULES)
def test_no_question_mark_placeholders_in_sql(rel):
    path = REPO_ROOT / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present")
    offenders = [(ln, t[:70]) for ln, t in _sql_strings(path) if _PH.search(t)]
    assert not offenders, (
        f"{rel} authors SQLite-style ? placeholders; PG is primary and "
        f"translate_sql's rewrite must not be load-bearing:\n"
        + "\n".join(f"  line {ln}: {t!r}" for ln, t in offenders)
    )


@pytest.mark.parametrize("rel", CLEAN_MODULES)
def test_no_dynamic_question_mark_placeholder_lists(rel):
    """`ph = ",".join(["?" for _ in ids])` builds an IN-list out of ? on a line
    with no SQL keyword — invisible to any keyword scan."""
    path = REPO_ROOT / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present")
    offenders = [
        (i, line.strip()[:70])
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _JOIN_PH.search(line)
    ]
    assert not offenders, (
        f"{rel} builds a placeholder list from '?':\n"
        + "\n".join(f"  line {i}: {t}" for i, t in offenders)
    )


def test_display_question_marks_are_not_collateral():
    """blueprint.py uses "?" as user-facing text meaning "unknown" — a blind
    ? -> %s replace would print "%s" to a reader. These must survive, and the
    checks above must not flag them."""
    src = (REPO_ROOT / "tools" / "document_intelligence" / "blueprint.py").read_text(encoding="utf-8")
    assert 'source_map.get((r.doc_id, r.page), "?")' in src, "unknown-source marker lost"
    assert "r.page or '?'" in src, "unknown-page marker lost"
