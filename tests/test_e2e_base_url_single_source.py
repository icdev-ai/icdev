# CUI // SP-CTI
"""The e2e suite must resolve its base URL in ONE place (qa-fail-b2537204d4a9b6dd).

THE DEFECT THIS PINS
--------------------
`playwright.config.ts` resolves the dashboard URL as
``ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<port>``. Seven
spec-local ``BASE`` constants re-derived it as ``ICDEV_DASHBOARD_URL ||
'http://localhost:5050'`` and one as ``ICDEV_DASHBOARD_URL ||
'http://127.0.0.1:5050'`` -- so a run that set ``ICDEV_E2E_BASE_URL`` (the
documented escape hatch for a container gateway), or that set nothing at all
while a spec defaulted to the other loopback spelling, sent the config and the
specs at two DIFFERENT SPELLINGS OF ONE SERVER.

That is not cosmetic, because ``fixtures/auth.ts`` bootstraps the CSRF token
against the CONFIGURED ``baseURL`` and pins it as a context-wide
``X-CSRF-Token`` header, while the cookie jar keys the Flask session by HOST.
A spec addressing the other spelling therefore:

  1. sends its first request with no session cookie -- ``csrf_protect`` returns
     early (nothing to forge) and it passes, which is why the split hides;
  2. gets a SECOND session from dev auto-login, with its OWN ``_csrf_token``;
  3. sends every later mutating request with that session and the FIRST
     session's token -- ``403 {"code":"CSRF_FAILED"}``.

Measured on run qa-1787705278: 14 of 39 failures were that 403, across 8 spec
files, and every one of them reads as an endpoint defect on the card it filed.
Reproduced locally on ``cpmp_performance.spec.ts`` -- 21/21 pass with the hosts
aligned, 6 fail with ``ICDEV_E2E_BASE_URL=http://127.0.0.1:5050`` and the spec
constant left on ``localhost``.

WHAT IS ASSERTED
----------------
No spec may re-derive the base URL from ``ICDEV_DASHBOARD_URL`` on its own. It
either imports the shared resolver from ``fixtures/auth`` or spells the full
precedence, ``ICDEV_E2E_BASE_URL`` first -- the same precedence
``playwright.config.ts`` uses. A structural check, deliberately: the two
resolutions agreeing is a property of the SOURCE, and a runtime check would only
observe whichever pair of values today's environment happens to produce.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
E2E_DIR = REPO_ROOT / "tests" / "e2e"
AUTH_FIXTURE = E2E_DIR / "fixtures" / "auth.ts"

#: The variable a spec must not read on its own.
_LEGACY_VAR = "ICDEV_DASHBOARD_URL"
#: The variable `playwright.config.ts` consults FIRST.
_CONFIG_VAR = "ICDEV_E2E_BASE_URL"

_QUOTES = ("'", '"', "`")


def _strip_comments(source: str) -> str:
    """Drop comments so prose ABOUT the variable is never read as a use of it.

    String-aware, and that is load-bearing rather than fastidious: a regex that
    cuts at the first ``//`` eats the rest of ``'http://localhost:5050'``
    INCLUDING its closing quote and the ``;`` after it, which welds the next
    statement onto this one. An offender sitting beside a compliant neighbour
    would then read as compliant -- the scan would report clean on the very
    shape it exists to find.
    """
    out: list[str] = []
    i, n = 0, len(source)
    quote = ""
    while i < n:
        ch = source[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
            out.append(ch)
            i += 1
            continue
        if source.startswith("//", i):
            while i < n and source[i] != "\n":
                i += 1
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _statement_around(code: str, index: int) -> str:
    """The ``;``-delimited statement containing ``index``."""
    start = code.rfind(";", 0, index) + 1
    end = code.find(";", index)
    return code[start : end if end != -1 else len(code)]


def _ts_sources() -> list[Path]:
    return sorted(p for p in E2E_DIR.rglob("*.ts") if p.is_file())


def _offending_statements(path: Path) -> list[str]:
    code = _strip_comments(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for match in re.finditer(rf"process\.env\.{_LEGACY_VAR}\b", code):
        statement = _statement_around(code, match.start())
        if _CONFIG_VAR not in statement:
            out.append(" ".join(statement.split())[:200])
    return out


def test_e2e_sources_exist() -> None:
    """A vacuous sweep must never read as a clean one."""
    sources = _ts_sources()
    assert sources, f"no .ts sources under {E2E_DIR} -- the scan below would pass vacuously"
    assert AUTH_FIXTURE.exists(), f"{AUTH_FIXTURE} is the shared resolver and is missing"


def test_comment_stripper_keeps_url_literals_whole() -> None:
    """The stripper must not swallow a ``;`` that lives inside a URL string."""
    sample = "const A = 'http://localhost:5050'; // trailing\nconst B = 2;"
    stripped = _strip_comments(sample)
    assert "'http://localhost:5050'" in stripped
    assert "trailing" not in stripped
    assert stripped.count(";") == 2


def test_auth_fixture_resolver_matches_config_precedence() -> None:
    """`fixtures/auth.ts` is the shared resolver, so it must lead with the config's variable."""
    code = _strip_comments(AUTH_FIXTURE.read_text(encoding="utf-8"))
    match = re.search(r"export const DEFAULT_BASE_URL\s*=", code)
    assert match, "fixtures/auth.ts no longer exports DEFAULT_BASE_URL"
    statement = _statement_around(code, match.start())
    legacy = statement.find(f"process.env.{_LEGACY_VAR}")
    config = statement.find(f"process.env.{_CONFIG_VAR}")
    assert config != -1, (
        "DEFAULT_BASE_URL must consult ICDEV_E2E_BASE_URL first, the same precedence "
        "playwright.config.ts uses -- otherwise the CSRF bootstrap probes a different "
        f"host from the one the specs call. Got: {' '.join(statement.split())}"
    )
    assert legacy == -1 or config < legacy, (
        "ICDEV_E2E_BASE_URL must be consulted BEFORE ICDEV_DASHBOARD_URL"
    )


@pytest.mark.parametrize("path", _ts_sources(), ids=lambda p: p.name)
def test_no_spec_re_derives_the_base_url(path: Path) -> None:
    """A spec reading ICDEV_DASHBOARD_URL without ICDEV_E2E_BASE_URL splits the suite's host."""
    offenders = _offending_statements(path)
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} re-derives the base URL without "
        f"{_CONFIG_VAR}, so it can address a different spelling of the server than "
        "the CSRF bootstrap did (403 CSRF_FAILED on every mutating request after "
        "the first). Import DEFAULT_BASE_URL from ./fixtures/auth instead.\n  "
        + "\n  ".join(offenders)
    )
