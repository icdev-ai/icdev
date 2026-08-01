#!/usr/bin/env python3
# CUI // SP-CTI
"""Guard test — runtime SQL under tools/boundary_canvas/ must use %s, not ?.

The whole platform is PG-primary: runtime SQL is authored with psycopg2-native
``%s`` placeholders, and the storage layer's translating StorageConnection
rewrites ``%s`` → ``?`` for the SQLite init-only fallback. A stray ``?``
placeholder in a runtime SQL string is a latent bug: it only works when the
connection happens to be raw SQLite and silently mistranslates (or logs a
warning) on the PG path.

This test tokenizes every ``.py`` module under ``tools/boundary_canvas`` and
flags any *plain* (non-raw) string literal that both looks like SQL and still
contains a ``?`` placeholder. It is deliberately pragmatic:

  * Raw strings (``r"..."``) are skipped — those are regexes (e.g. the IQE
    parser's ``(?:...)`` / ``(.+?)`` groups), never SQL.
  * A string is only considered SQL when it contains an UPPERCASE SQL keyword
    (SELECT/INSERT/UPDATE/DELETE/WHERE/VALUES/JOIN). SQL in this codebase is
    written uppercase, so lowercase prose in docstrings ("set", "from",
    "where ...") does not trip the check.

If this test fails, replace the offending ``?`` placeholder(s) with ``%s``.
"""

import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BDC_DIR = ROOT / "tools" / "boundary_canvas"

# Uppercase SQL keywords — strong signal that a string literal is SQL (not prose).
_SQL_KEYWORD_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|VALUES|JOIN)\b")


def _string_prefix(token_text: str) -> str:
    """Return the (lowercased) string-literal prefix, e.g. 'r', 'rb', 'f', ''."""
    quote = min(
        (token_text.find(q) for q in ("'", '"') if token_text.find(q) != -1),
        default=-1,
    )
    return token_text[:quote].lower() if quote > 0 else ""


def _sql_strings_with_question_mark(py_path: Path):
    """Yield (lineno, snippet) for plain SQL string literals containing '?'."""
    src = py_path.read_text(encoding="utf-8")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
    for tok in tokens:
        if tok.type != tokenize.STRING:
            continue
        prefix = _string_prefix(tok.string)
        if "r" in prefix:
            # Raw string → regex, never SQL.
            continue
        value = tok.string
        if "?" not in value:
            continue
        if _SQL_KEYWORD_RE.search(value):
            yield tok.start[0], value.strip()[:120]


def test_no_question_mark_placeholders_in_bdc_runtime_sql():
    """No runtime SQL string under tools/boundary_canvas may use a ? placeholder."""
    assert BDC_DIR.is_dir(), f"Missing directory: {BDC_DIR}"

    offenders = []
    for py_path in sorted(BDC_DIR.rglob("*.py")):
        for lineno, snippet in _sql_strings_with_question_mark(py_path):
            rel = py_path.relative_to(ROOT)
            offenders.append(f"{rel}:{lineno}: {snippet}")

    assert not offenders, (
        "Runtime SQL under tools/boundary_canvas/ must use %s placeholders "
        "(PG-native), not ?. Offending SQL string literals:\n  "
        + "\n  ".join(offenders)
    )
