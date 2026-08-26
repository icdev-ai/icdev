# CUI // SP-CTI
"""The E2E suite resolves its base URL in ONE place (qa-fail-a5dbf266dfb0ce4a).

THE DEFECT THIS GUARDS
----------------------
``playwright.config.ts`` resolved the URL under test as
``ICDEV_E2E_BASE_URL || ICDEV_DASHBOARD_URL || http://localhost:<PORT>`` while
nine spec-local ``BASE`` constants resolved it as
``ICDEV_DASHBOARD_URL || http://localhost:5050``.

The two agree whenever exactly ONE of those variables is set, which is why the
split survived for months. QA sweep ``qa-1787705278`` (2026-08-26) set both:
``ICDEV_E2E_BASE_URL=http://127.0.0.1:5050`` beside
``ICDEV_DASHBOARD_URL=http://localhost:5050``. ``fixtures/auth.ts`` then
performed the CSRF double-submit handshake against the CONFIG origin
(``127.0.0.1``) while each spec issued its requests at its own absolute
``BASE`` (``localhost``). A cookie jar is keyed by host, so neither the session
cookie nor ``icdev_csrf`` rode along; ``ICDEV_DASHBOARD_DEV_AUTOLOGIN`` minted a
fresh session with a fresh ``_csrf_token`` for the cookieless request, and
``csrf_protect`` compared it against the header pinned from the other origin.
Every POST/PUT in the suite answered ``403 {"code":"CSRF_FAILED"}`` — not 401,
because the auth ``before_request`` hook logs the caller in before
``csrf_protect`` reads the session — so the whole class read as a product
defect. That run filed 39 QA cards.

WHY A STRUCTURAL TEST AND NOT A BEHAVIOURAL ONE
-----------------------------------------------
This is the SECOND time the split shipped. ``qa-fail-e2e-baseurl-01`` fixed the
identical precedence in ``ai_ify.spec.ts`` — at that ONE call site — and the
other eight kept it. A Playwright test could only prove the specs that exist
today agree; what has to hold is that the NEXT spec cannot re-derive the value,
and that is a property of the source, not of a run.

The check is deliberately narrow: it matches ``process.env.<VAR>`` in CODE, so a
comment naming the variable (there are several, and they are useful) is not a
finding. The fix at any flagged site is one line —
``import { resolveBaseUrl } from './fixtures/base_url'``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The one module allowed to read the environment variables directly.
RESOLVER_RELPATH = "tests/e2e/fixtures/base_url.ts"

#: Variables whose precedence is the resolver's business and nobody else's.
GUARDED_VARS = ("ICDEV_E2E_BASE_URL", "ICDEV_DASHBOARD_URL")

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(source: str) -> str:
    """Remove TS comments so a doc mention of a variable is not a finding.

    Block comments first: a ``//`` inside a ``/* ... */`` doc block (every URL in
    this codebase's docs has one) must not truncate the block at that point.
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


def _scanned_files() -> list[Path]:
    files = [REPO_ROOT / "playwright.config.ts", REPO_ROOT / "globalSetup.ts"]
    files.extend(sorted((REPO_ROOT / "tests" / "e2e").rglob("*.ts")))
    return [f for f in files if f.is_file()]


def _relpath(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_resolver_module_exists():
    """A missing resolver would make every other assertion here vacuous."""
    resolver = REPO_ROOT / RESOLVER_RELPATH
    assert resolver.is_file(), f"{RESOLVER_RELPATH} is the single source of the E2E base URL"


def test_resolver_prefers_the_runner_variable():
    """``ICDEV_E2E_BASE_URL`` wins, because the two variables answer different questions.

    ``ICDEV_DASHBOARD_URL`` is "how does a process reach the dashboard" — ``.env``
    legitimately sets it to a container gateway for agents running inside a
    container — and ``ICDEV_E2E_BASE_URL`` is "what does the host's test runner
    navigate to". Flipping the order sends the suite at the gateway again, which
    is qa-fail-e2e-baseurl-01 (838 navigation timeouts).
    """
    body = _strip_comments((REPO_ROOT / RESOLVER_RELPATH).read_text(encoding="utf-8"))
    first = body.index("process.env.ICDEV_E2E_BASE_URL")
    second = body.index("process.env.ICDEV_DASHBOARD_URL")
    assert first < second, "resolveBaseUrl() must prefer ICDEV_E2E_BASE_URL"


def test_scan_covers_the_specs():
    """A glob that matched nothing would report a clean suite it never read."""
    scanned = {_relpath(p) for p in _scanned_files()}
    assert RESOLVER_RELPATH in scanned
    assert "tests/e2e/fixtures/auth.ts" in scanned
    assert "tests/e2e/fixtures/govcon_cpmp.ts" in scanned
    assert len([p for p in scanned if p.endswith(".spec.ts")]) > 50, (
        f"expected the whole E2E suite, scanned {len(scanned)} files"
    )


def test_no_spec_re_derives_the_base_url():
    """Only the resolver may read the base-URL environment variables."""
    offenders: list[str] = []
    for path in _scanned_files():
        rel = _relpath(path)
        if rel == RESOLVER_RELPATH:
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for var in GUARDED_VARS:
            if f"process.env.{var}" in code:
                offenders.append(f"{rel} reads process.env.{var}")

    assert not offenders, (
        "The E2E base URL must be resolved ONLY by "
        f"{RESOLVER_RELPATH}::resolveBaseUrl(). A second copy of the precedence "
        "points the CSRF/session bootstrap at one origin and the spec's requests "
        "at another, and every mutating request answers 403 CSRF_FAILED "
        "(qa-fail-a5dbf266dfb0ce4a).\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "relpath",
    ["tests/e2e/fixtures/auth.ts", "tests/e2e/fixtures/govcon_cpmp.ts", "playwright.config.ts"],
)
def test_the_three_origins_that_must_agree_call_the_resolver(relpath: str):
    """auth.ts mints the cookies, govcon_cpmp.ts addresses them, the config probes both.

    These three are asserted by NAME rather than left to the scan above, because
    the scan passes for a file that resolves the base URL by some third means —
    a hard-coded literal, say — which reintroduces the split without naming a
    variable.
    """
    code = _strip_comments((REPO_ROOT / relpath).read_text(encoding="utf-8"))
    assert "resolveBaseUrl(" in code, f"{relpath} must resolve its base URL through resolveBaseUrl()"
# CUI // SP-CTI
