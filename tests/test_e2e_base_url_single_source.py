# CUI // SP-CTI
"""One base URL expression for the whole E2E suite (qa-fail-0d954757a83824da).

THE DEFECT THIS PINS
--------------------
``playwright.config.ts`` resolves ``use.baseURL`` as::

    ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>

and ``tests/e2e/fixtures/auth.ts`` and ``tests/e2e/fixtures/govcon_cpmp.ts``
each carried their own copy that read ``ICDEV_DASHBOARD_URL`` alone. A run with
BOTH variables set -- which is what the QA sweep does
(``ICDEV_E2E_BASE_URL=http://127.0.0.1:5050``,
``ICDEV_DASHBOARD_URL=http://localhost:5050``) -- therefore pointed the CSRF
bootstrap at ``127.0.0.1`` and the specs' own requests at ``localhost``.

One server, two host spellings, and a cookie jar is keyed by HOST. So the
bootstrap established a session and an ``icdev_csrf`` token on ``127.0.0.1``
while every spec request arrived on ``localhost`` with a different session and
a different token, the pinned ``X-CSRF-Token`` header still carrying the first
one. Measured against the live dashboard::

    GET  localhost:5050/api/cpmp/contracts                  -> 200
    PUT  localhost:5050/api/cpmp/contracts/<id>/status
         + X-CSRF-Token bootstrapped on 127.0.0.1           -> 403 CSRF_FAILED
    PUT  same, token bootstrapped on the SAME host          -> 400 (the real answer)

Every GET passes and only mutating calls fail, so it reads as a defect in
whichever endpoint happened to be exercised rather than as an environment
mismatch. It cost ``gcpl-cset-11`` on run ``qa-1787705278``.

WHY A STRUCTURAL TEST AND NOT JUST THE FIX
------------------------------------------
This is the SECOND time. ``qa-fail-e2e-baseurl-01`` was the same "one variable
answering two questions" defect; that fix corrected ``playwright.config.ts`` and
``ai_ify.spec.ts`` and left the shared fixtures reading ``ICDEV_DASHBOARD_URL``
alone. ``auth.ts`` even documented its copy as "the same resolution
``playwright.config.ts`` and ``fixtures/govcon_cpmp.ts`` use, so a spec and its
auth bootstrap can never end up pointed at different servers" -- which was the
precise thing that was not true.

A COMMENT CLAIMING TWO EXPRESSIONS AGREE CANNOT ENFORCE IT. This test can: the
expression may appear in exactly one file, and every other consumer imports it.

Scope note: ``globalSetup.ts`` is deliberately OUT of scope. It reads these
variables to NAME which one supplied the in-force baseURL (``_baseUrlSource``)
and to report "baseURL is unset" -- a different question from "what IS the base
URL", and one that legitimately inspects the variables themselves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = REPO_ROOT / "tests" / "e2e"

# The ONE module allowed to build a base URL out of the environment.
CANONICAL = E2E_DIR / "fixtures" / "base_url.ts"

# Files outside tests/e2e/ that consume the suite's base URL.
EXTRA_SCANNED = (REPO_ROOT / "playwright.config.ts",)

# Reading any of these is what makes a site a second copy of the resolution.
BASE_URL_ENV_VARS = (
    "ICDEV_E2E_BASE_URL",
    "ICDEV_DASHBOARD_URL",
    "ICDEV_DASHBOARD_PORT",
)


def strip_comments(source: str) -> str:
    """Blank out ``//`` and ``/* */`` comments, preserving newlines and columns.

    Hand-written rather than two regexes, because the naive pair gets this file
    family WRONG in both directions:

    * ``//[^\\n]*`` also matches the ``//`` inside ``http://localhost`` -- a
      substring of the very expression being scanned for, so a regex stripper
      erases the finding it exists to make. That is not hypothetical: the first
      draft of this test reported the canonical module itself as not honouring
      ``ICDEV_DASHBOARD_PORT``, because the line declaring the port had been
      blanked from its own ``http://``.
    * prose ABOUT the variables is what every fix in this family leaves behind,
      so a scanner over raw text reports the explanation of the defect as the
      defect.

    So string and template literals are tracked, and a ``//`` inside one is not
    a comment.
    """
    out: list[str] = []
    i, n = 0, len(source)
    quote: str | None = None
    in_block = False
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if in_block:
            if ch == "*" and nxt == "/":
                out.append("  ")
                i += 2
                in_block = False
                continue
            out.append(ch if ch == "\n" else " ")
            i += 1
            continue
        if quote is not None:
            out.append(ch)
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            out.append("  ")
            i += 2
            continue
        if ch == "/" and nxt == "/":
            end = source.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def scanned_files() -> list[Path]:
    files = [p for p in sorted(E2E_DIR.rglob("*.ts")) if p.resolve() != CANONICAL.resolve()]
    files.extend(p for p in EXTRA_SCANNED if p.exists())
    return files


def find_restatements(path: Path) -> list[tuple[int, str]]:
    code = strip_comments(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(code.splitlines(), start=1):
        if any(f"process.env.{var}" in line for var in BASE_URL_ENV_VARS):
            hits.append((lineno, line.strip()))
    return hits


def test_strip_comments_keeps_a_url_inside_a_literal():
    """The stripper must not read ``http://`` as the start of a comment.

    Without this the whole scan silently under-reports: every restatement this
    test hunts for ends in a ``http://localhost:...`` fallback, so blanking from
    that ``//`` deletes the evidence and the suite reports clean.
    """
    src = "const a = `http://localhost:${process.env.ICDEV_DASHBOARD_PORT}`; // note\n"
    stripped = strip_comments(src)
    assert "process.env.ICDEV_DASHBOARD_PORT" in stripped
    assert "note" not in stripped
    assert len(stripped.splitlines()) == len(src.splitlines())


def test_strip_comments_drops_prose_about_the_variables():
    """Explaining the defect is not committing it."""
    src = "// reads process.env.ICDEV_DASHBOARD_URL\n/* and process.env.ICDEV_E2E_BASE_URL */\n"
    assert "process.env" not in strip_comments(src)


def test_canonical_module_exists():
    assert CANONICAL.is_file(), (
        f"{CANONICAL.relative_to(REPO_ROOT)} is the single source of the E2E base URL. "
        "Deleting it does not remove the rule below -- it removes the thing every "
        "other file imports."
    )


def test_canonical_module_resolves_the_config_precedence():
    """The one expression must be the one ``playwright.config.ts`` documents."""
    code = strip_comments(CANONICAL.read_text(encoding="utf-8"))
    positions = [
        code.index(f"process.env.{var}")
        for var in ("ICDEV_E2E_BASE_URL", "ICDEV_DASHBOARD_URL")
    ]
    assert positions == sorted(positions), (
        "ICDEV_E2E_BASE_URL must be read BEFORE ICDEV_DASHBOARD_URL. "
        "ICDEV_DASHBOARD_URL answers 'how does a process reach the dashboard' and is "
        "legitimately a container gateway; ICDEV_E2E_BASE_URL answers 'what does the "
        "runner on this host navigate to'. Reversing them re-opens qa-fail-e2e-baseurl-01."
    )
    assert "process.env.ICDEV_DASHBOARD_PORT" in code, (
        "The localhost fallback must honour ICDEV_DASHBOARD_PORT, or an isolated run "
        "(ICDEV_DASHBOARD_PORT=5090) falls through to whatever dashboard owns 5050."
    )


@pytest.mark.parametrize("path", scanned_files(), ids=lambda p: p.name)
def test_no_second_base_url_resolution(path: Path):
    """No file may rebuild the base URL out of the environment itself."""
    hits = find_restatements(path)
    assert not hits, (
        f"{path.relative_to(REPO_ROOT)} resolves the E2E base URL from the environment "
        "itself instead of importing it:\n"
        + "\n".join(f"  line {n}: {text}" for n, text in hits)
        + "\n\nUse the one resolution:\n"
        "    import { BASE_URL } from './fixtures/base_url';   // './base_url' inside fixtures/\n"
        "    const BASE = BASE_URL;\n\n"
        "A second copy drifts from playwright.config.ts's `use.baseURL`, and when it does "
        "the CSRF bootstrap and the spec's own requests land on two host spellings of one "
        "server -- two cookie jars, two tokens, every GET 200 and every mutating request "
        "403 CSRF_FAILED (qa-fail-0d954757a83824da)."
    )
# CUI // SP-CTI
