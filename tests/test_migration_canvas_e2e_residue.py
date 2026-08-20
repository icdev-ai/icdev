# CUI // SP-CTI
"""An E2E run must not leave a network migration session open on the real board.

`mc_net_sessions` rows created by a test are indistinguishable from an
engineer's stalled cutover, and the NMCE genesis reflex
(`tools/genesis/reflexes/migration_canvas.py`) flags any non-terminal session
untouched for 7 days, raising a kanban card that burns an agent session.

The product half of this was already fixed — a status vocabulary, PATCH
validation and a wizard close control, covered by
`test_migration_canvas_session_close.py` — and 36 sessions were archived by hand
on 2026-08-08. Four more appeared between 2026-08-09 and 2026-08-14 with
byte-identical downstream footprints (1 ai_session, 2 topology_neighbors, 6
coa_questions, 5 config_map, 4 config_questions each), because the *writers*
were never fixed: the Playwright spec and the agent-driven E2E command both
create a session and neither closed it.

These cover both writers, plus the seam that makes the close actually land: the
status the cleanup sends has to be one the PATCH route accepts and every
active-session query treats as closed. A cleanup that PATCHes an unaccepted
status is a no-op that looks like a fix.
"""

import re
from pathlib import Path

from tools.migration_canvas.constants import (
    NET_SESSION_STATUSES,
    NET_SESSION_TERMINAL_STATUSES,
)

_REPO = Path(__file__).resolve().parent.parent
_FIXTURE = _REPO / "tests" / "e2e" / "fixtures" / "migration_cleanup.ts"
_E2E_DIR = _REPO / "tests" / "e2e"
_COMMANDS = (
    _REPO / ".claude" / "commands" / "e2e" / "migration_canvas.md",
    _REPO / "icdev" / "data" / "claude_bootstrap" / "claude" / "commands" / "e2e" / "migration_canvas.md",
)

# A spec that drives the wizard's create control, or POSTs the create route,
# has made a real row and owes a close.
_CREATES_SESSION = ("Create Session & Continue", "'/migration-canvas/api/network-migration'")


def _session_creating_specs():
    hits = []
    for spec in sorted(_E2E_DIR.glob("*.spec.ts")):
        src = spec.read_text(encoding="utf-8")
        if any(marker in src for marker in _CREATES_SESSION):
            hits.append((spec, src))
    return hits


# ── The cleanup fixture ─────────────────────────────────────────────────────

def test_cleanup_fixture_exists():
    assert _FIXTURE.exists(), (
        "tests/e2e/fixtures/migration_cleanup.ts is missing — a spec that creates "
        "a network migration session has nothing to close it with"
    )


def test_cleanup_sends_a_status_the_patch_route_accepts():
    """The archive status must be in the vocabulary the PATCH route validates.

    `mc_net_api_update` returns 400 for a status outside NET_SESSION_STATUSES,
    so a fixture sending anything else archives nothing while reporting a tidy
    warning nobody reads.
    """
    src = _FIXTURE.read_text(encoding="utf-8")
    match = re.search(r"export const ARCHIVED = '([^']+)'", src)
    assert match, "migration_cleanup.ts does not declare an ARCHIVED status"
    status = match.group(1)
    assert status in NET_SESSION_STATUSES, (
        f"cleanup PATCHes status {status!r}, which mc_net_api_update rejects with "
        f"400; allowed: {sorted(NET_SESSION_STATUSES)}"
    )
    assert status in NET_SESSION_TERMINAL_STATUSES, (
        f"cleanup PATCHes {status!r}, which is not terminal — the session stays "
        f"visible to every active-session query and the NMCE reflex still flags it; "
        f"terminal: {sorted(NET_SESSION_TERMINAL_STATUSES)}"
    )


def test_cleanup_patches_the_session_route():
    src = _FIXTURE.read_text(encoding="utf-8")
    assert ".patch(" in src, "cleanup does not issue a PATCH"
    assert "/migration-canvas/api/network-migration/" in src, (
        "cleanup does not target the network migration session route"
    )


def test_cleanup_carries_credentials_and_csrf():
    """/migration-canvas/api/* is behind mdc_login_required and CSRF.

    An afterAll context built with neither gets 401 (or 403) and the session is
    never closed — which is exactly the silent-failure shape this test exists
    to prevent.
    """
    src = _FIXTURE.read_text(encoding="utf-8")
    assert "storageState" in src, (
        "cleanup does not pass storageState — an anonymous context is 401'd by "
        "mdc_login_required unless ICDEV_AUTH_BYPASS happens to be set"
    )
    assert "X-CSRF-Token" in src, (
        "cleanup does not echo the CSRF token — a cookie-authenticated PATCH is "
        "rejected by tools/security/csrf.py"
    )


def test_cleanup_reports_failure_rather_than_swallowing_it():
    src = _FIXTURE.read_text(encoding="utf-8")
    assert "console.warn" in src, (
        "cleanup swallows failures — a cleanup that quietly stopped working is "
        "how the residue accumulated in the first place"
    )


# ── Writer 1: the Playwright spec ───────────────────────────────────────────

def test_a_spec_that_creates_a_session_also_closes_it():
    specs = _session_creating_specs()
    assert specs, (
        "no e2e spec appears to create a network migration session — did the "
        "wizard's create control get renamed? This test is then blind."
    )
    for spec, src in specs:
        assert "archiveNetSessions" in src, (
            f"{spec.name} creates a network migration session but never calls "
            f"archiveNetSessions — every run leaves an in_progress row the NMCE "
            f"reflex flags as a stalled cutover"
        )
        assert "afterAll" in src, (
            f"{spec.name} cleans up only on the success path; a test that fails "
            f"half way through is exactly when residue is left behind"
        )


# ── Writer 2: the agent-driven E2E command ──────────────────────────────────

def test_e2e_command_closes_the_session_it_opens():
    for path in _COMMANDS:
        assert path.exists(), f"{path} is missing"
        text = path.read_text(encoding="utf-8")
        if "network-migration" not in text:
            continue
        assert "PATCH /migration-canvas/api/network-migration/" in text, (
            f"{path} opens a network migration session but documents no PATCH to "
            f"close it — the agent following it leaves a row on the real board"
        )
        assert any(s in text for s in NET_SESSION_TERMINAL_STATUSES), (
            f"{path} documents a close step that names no terminal status; "
            f"terminal: {sorted(NET_SESSION_TERMINAL_STATUSES)}"
        )
