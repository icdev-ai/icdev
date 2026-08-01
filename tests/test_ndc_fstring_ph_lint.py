# CUI // SP-CTI
"""Regression lint: no non-f-string SQL literals containing ``{_ph}``.

``tools/network/blueprint.py`` (and siblings) build SQL by interpolating a
per-backend placeholder token via ``_ph = sql_placeholder(conn)`` inside
f-strings, e.g. ``f"... WHERE id={_ph}"``.

If the ``f`` prefix is omitted, the literal text ``{_ph}`` reaches the database
driver instead of ``?`` / ``%s``. For the ``fields.append("updated_at={_ph}")``
family this also desynchronizes the placeholder/value counts and breaks the
route (e.g. the topology-metadata UPDATE). See task ndc-sql-01.

This test walks ``tools/network/**/*.py`` with :mod:`tokenize` and fails,
listing ``file:line``, for any ``STRING`` token whose text contains ``{_ph}``
but whose prefix lacks ``f``. Continuation lines *inside* a multi-line f-string
are tokenized as ``FSTRING_*`` tokens (Python 3.12+), so they are correctly
ignored.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NETWORK_DIR = REPO_ROOT / "tools" / "network"


def _string_prefix(token_text: str) -> str:
    """Return the (lowercased) string prefix that precedes the opening quote."""
    idx = 0
    while idx < len(token_text) and token_text[idx] not in ("'", '"'):
        idx += 1
    return token_text[:idx].lower()


def _find_offenders(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, snippet)`` for every non-f STRING literal with ``{_ph}``."""
    src = path.read_text(encoding="utf-8")
    offenders: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in tokens:
            if tok.type != tokenize.STRING:
                continue
            if "{_ph}" not in tok.string:
                continue
            if "f" in _string_prefix(tok.string):
                continue
            snippet = tok.string.replace("\n", "\\n")[:80]
            offenders.append((tok.start[0], snippet))
    except tokenize.TokenError as exc:  # pragma: no cover - defensive
        raise AssertionError(f"tokenize failed for {path}: {exc}") from exc
    return offenders


def test_no_non_fstring_ph_literals() -> None:
    assert NETWORK_DIR.is_dir(), f"missing network dir: {NETWORK_DIR}"

    problems: list[str] = []
    for py in sorted(NETWORK_DIR.rglob("*.py")):
        for lineno, snippet in _find_offenders(py):
            rel = py.relative_to(REPO_ROOT).as_posix()
            problems.append(f"{rel}:{lineno}: {snippet}")

    assert not problems, (
        "Found non-f-string SQL literal(s) containing '{_ph}' — the literal "
        "text will reach the DB driver instead of a placeholder. Add the 'f' "
        "prefix:\n" + "\n".join(problems)
    )
