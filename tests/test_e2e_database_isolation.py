# CUI // SP-CTI
"""The documented E2E database recipe actually isolates (qa-fail-6a87916931be3793).

THE DEFECT THIS GUARDS
----------------------
``playwright.config.ts`` documented a throwaway-database recipe and stated the
reason plainly -- the suite writes fixtures, and running it against the
canonical ``icdev`` leaves them there::

    python tools/db/bootstrap_pg.py
    ICDEV_PG_DATABASE=icdev_e2e ICDEV_PG_DB=icdev_e2e npx playwright test

THAT COMMAND REDIRECTED NOTHING. ``.env`` sets ``ICDEV_DATABASE_URL``, and every
connection site in ``tools/db/storage.py`` reads the DSN FIRST -- the discrete
``ICDEV_PG_DATABASE`` is consulted only when no DSN is present. Measured on the
live deployment 2026-09-05 with exactly those variables exported,
``SELECT current_database()`` answered ``icdev``. So an operator following the
documented recipe held a false belief in isolation and ran ~840 tests' worth of
fixture writes into the canonical board -- a live mechanism for the E2E residue
already seen there.

WHY THE DOCUMENTATION ALONE IS NOT THE FIX
------------------------------------------
A comment is what was wrong in the first place, and the disagreement between it
and the code was SILENT. The plumbing is repaired (``webServerDatabaseEnv``) AND
the result is now MEASURED: ``globalSetup`` asks the server which database it is
on via ``current_database()`` and refuses the run when that is not the database
the run asked for. Measuring the server rather than re-reading our own
environment is the whole point -- it is also the only thing that catches
``reuseExistingServer``, where a dashboard already up on the port means
Playwright starts no server and every variable the run exported is inert.

THE PRE-EXISTING DEFECT FOUND ON THE WAY
----------------------------------------
The isolation assert reads ``/api/health``, which reported ``degraded`` on every
request -- on the canonical dashboard too, measured on :5050. Inside a request a
security context is attached and ``row_security.inject_row_predicate`` rewrote
the liveness probe ``SELECT 1`` into
``SELECT 1 WHERE (classification IS NULL OR ...)``, which raises
``UndefinedColumn`` because a FROM-less SELECT has no such column. The route
swallowed it and answered ``db: false``. That is the same failure mode the
module's own ``_is_system_table`` guard already names one function up.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CONFIG = REPO_ROOT / "playwright.config.ts"
GLOBAL_SETUP = REPO_ROOT / "globalSetup.ts"
RESOLVER = REPO_ROOT / "tests" / "e2e" / "fixtures" / "e2e_database.ts"
DOMAIN = REPO_ROOT / "icdev_domain.yaml"


# ---------------------------------------------------------------------------
# The measurement: which database is this process ACTUALLY on?
# ---------------------------------------------------------------------------

def test_active_database_measures_sqlite_file(tmp_path):
    """It reports the file the connection is really open on."""
    from tools.db.storage import active_database

    db = tmp_path / "probe.db"
    conn = sqlite3.connect(str(db))
    try:
        from tools.db.storage import StorageConnection

        wrapped = StorageConnection(conn, "sqlite")
        result = active_database(wrapped)
    finally:
        conn.close()

    assert result["backend"] == "sqlite"
    assert result["measured"] is True
    assert Path(result["database"]).resolve() == db.resolve()


def test_active_database_never_echoes_the_environment(monkeypatch, tmp_path):
    """The requested name must not leak into the measurement.

    This is the defect in miniature: ``ICDEV_PG_DATABASE`` is a REQUEST, and a
    health report that echoed it would have agreed with the operator and been
    wrong. Nothing may make the measurement agree with the env var.
    """
    from tools.db.storage import StorageConnection, active_database

    monkeypatch.setenv("ICDEV_PG_DATABASE", "a_database_nothing_is_open_on")
    db = tmp_path / "probe.db"
    conn = sqlite3.connect(str(db))
    try:
        result = active_database(StorageConnection(conn, "sqlite"))
    finally:
        conn.close()

    assert result["database"] != "a_database_nothing_is_open_on"


def test_active_database_reports_unmeasured_rather_than_guessing():
    """A connection that cannot answer yields None, never a fabricated name."""
    from tools.db.storage import active_database

    class Broken:
        _backend = "postgresql"

        def execute(self, *_a, **_kw):
            raise RuntimeError("connection is gone")

    result = active_database(Broken())
    assert result["measured"] is False
    assert result["database"] is None


# ---------------------------------------------------------------------------
# The pre-existing defect: RLS injected into a statement with no relation
# ---------------------------------------------------------------------------

TABLELESS = [
    "SELECT 1",
    "SELECT current_database() AS db",
    "SELECT now()",
]

RELATIONAL = [
    "SELECT * FROM kanban_tasks",
    "SELECT 1 FROM kanban_tasks",
    # A scalar subquery still reads a relation, so it must STILL be filtered --
    # the exemption is decided on the raw statement for exactly this reason.
    "SELECT (SELECT id FROM kanban_tasks LIMIT 1)",
]


@pytest.mark.parametrize("sql", TABLELESS)
def test_tableless_statements_are_not_rewritten(sql):
    from tools.security.row_security import inject_row_predicate

    out, extra, _ = inject_row_predicate(
        sql, tenant_id="t1", classifications={"public"}, placeholder="?"
    )
    assert out == sql, f"a statement reading no relation was rewritten: {out}"
    assert extra == ()


@pytest.mark.parametrize("sql", RELATIONAL)
def test_statements_that_read_a_relation_are_still_filtered(sql):
    """The exemption must never widen into a way past row security."""
    from tools.security.row_security import inject_row_predicate

    out, _extra, _ = inject_row_predicate(
        sql, tenant_id="t1", classifications={"public"}, placeholder="?"
    )
    assert out != sql, f"row security did not filter a relational read: {sql}"


def test_liveness_probe_survives_an_attached_security_context(tmp_path):
    """``SELECT 1`` under a security context must execute, not raise.

    This is the exact call ``/api/health`` makes. Before the fix it raised
    ``UndefinedColumn`` on PostgreSQL and the route reported the database down.
    """
    from tools.db.storage import get_connection
    from tools.security.security_context import SecurityContext

    conn = get_connection(db_path=str(tmp_path / "probe.db"))
    try:
        conn.set_security_context(SecurityContext(user_id="u", tenant_id="t1"))
        assert conn.execute("SELECT 1").fetchone() is not None
    finally:
        conn.close()


def test_health_route_reports_the_measured_database_not_the_env():
    """The route must publish a MEASURED database, and say when it could not."""
    source = (REPO_ROOT / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    route = source.split('@app.route("/api/health")', 1)[1].split("@app.route", 1)[0]

    assert "active_database" in route, "/api/health must MEASURE the database"
    assert "database_measured" in route, (
        "/api/health must distinguish 'not measured' from a measured answer"
    )
    # CODE only. A comment naming the variable is useful and is not a finding --
    # the same narrowing tests/test_e2e_base_url_single_source.py records.
    code = "\n".join(
        line for line in route.splitlines() if not line.strip().startswith("#")
    )
    for env_var in ("ICDEV_PG_DATABASE", "ICDEV_DATABASE_URL"):
        assert env_var not in code, (
            f"/api/health must never echo {env_var} -- that is a request, not a measurement"
        )


# ---------------------------------------------------------------------------
# The recipe: one resolver, and the per-run knob outranks the ambient DSN
# ---------------------------------------------------------------------------

def test_the_shared_resolver_exists():
    assert RESOLVER.is_file(), f"missing shared E2E database resolver: {RESOLVER}"


def test_the_per_run_knob_outranks_the_ambient_dsn():
    """``ICDEV_PG_DATABASE`` must be consulted BEFORE ``ICDEV_DATABASE_URL``.

    Measured 2026-09-05: an ordinary shell on this deployment already exports
    ``ICDEV_DATABASE_URL`` naming the canonical board. Ranking the DSN first
    lets the ambient configuration silently outrank the variable the operator
    typed to escape it -- which is the card's own defect, reproduced inside its
    fix. It shipped that way for one run and was caught end to end.
    """
    body = RESOLVER.read_text(encoding="utf-8")
    fn = body.split("export function requestedDatabase", 1)[1]
    fn = fn.split("export function", 1)[0]

    order = [m.group(1) for m in re.finditer(r"'(ICDEV_[A-Z_]+)'", fn)]
    seen = [v for v in order if v in {"ICDEV_PG_DATABASE", "ICDEV_DATABASE_URL"}]
    assert seen, "requestedDatabase names neither variable"
    assert seen[0] == "ICDEV_PG_DATABASE", (
        f"the ambient DSN outranks the per-run knob: {seen}"
    )


def test_the_name_wins_by_clearing_the_dsn_not_by_hoping():
    """A named database is made to win, since `storage.py` prefers the DSN.

    The empty string is load-bearing: present-but-falsy means
    ``load_dotenv(override=False)`` will not restore ``.env``'s DSN, while
    ``storage.py``'s ``if db_url:`` is false so the discrete name is used.
    """
    body = RESOLVER.read_text(encoding="utf-8")
    fn = body.split("export function webServerDatabaseEnv", 1)[1]
    assert "ICDEV_DATABASE_URL: ''" in fn, (
        "the DSN must be cleared for the server, or the named database is outranked"
    )
    assert "return {};" in fn, (
        "a run that requested nothing must be left exactly as it was"
    )


def test_the_config_uses_the_shared_resolver_and_does_not_re_derive_it():
    body = CONFIG.read_text(encoding="utf-8")
    assert "webServerDatabaseEnv" in body, (
        "playwright.config.ts must build webServer.env from the shared resolver"
    )
    # A second copy of the precedence is how this defect happened.
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("//")
    )
    assert "process.env.ICDEV_PG_DATABASE" not in code, (
        "the config must not re-derive the database precedence"
    )


def test_the_documented_recipe_is_the_one_that_works():
    """The comment and the code must agree -- that disagreement WAS the defect."""
    body = CONFIG.read_text(encoding="utf-8")
    assert "qa-fail-6a87916931be3793" in body, (
        "the config must record why the old recipe did not redirect"
    )
    assert "ICDEV_DATABASE_URL" in body, (
        "the config must name the variable that actually wins"
    )


# ---------------------------------------------------------------------------
# The assert: measured, and unmeasured is not a pass
# ---------------------------------------------------------------------------

def test_global_setup_asserts_isolation_after_reachability():
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    assert "assertDatabaseIsolated" in body
    default_export = body.split("export default async function globalSetup", 1)[1]
    assert "assertBaseUrlReachable" in default_export
    assert "assertDatabaseIsolated" in default_export
    assert default_export.index("assertBaseUrlReachable") < default_export.index(
        "assertDatabaseIsolated"
    ), "an unreachable dashboard must be reported as such, not as unmeasured isolation"


def test_an_unconfirmed_isolation_is_refused_not_waved_through():
    """`unmeasured` must throw when a database was requested.

    Degrading it to a warning restores the exact false belief the card is about:
    "I asked for isolation and nothing complained".
    """
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    fn = body.split("export async function assertDatabaseIsolated", 1)[1]
    fn = fn.split("\n}", 1)[0]
    assert "throw new Error" in fn, "a mismatch or unmeasured verdict must fail the run"
    # Only the two harmless verdicts may return early.
    assert "'confirmed'" in fn and "'not_requested'" in fn


def test_the_success_verdict_does_not_overclaim_isolation():
    """A run on the canonical board must not print a green "isolated".

    On this deployment the ambient environment always names ``icdev``, so a
    verdict called `isolated` would tick beside the canonical board on every
    ordinary run -- the same false comfort the broken recipe gave.
    """
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    assert "'confirmed'" in body
    assert "verdict: 'isolated'" not in body
    assert "requested.explicit" in body, (
        "a database taken from the ambient configuration must be reported as such"
    )


def test_the_check_has_an_auditable_kill_switch_and_no_neutraliser():
    body = GLOBAL_SETUP.read_text(encoding="utf-8")
    assert "ICDEV_E2E_DB_CHECK" in body


# ---------------------------------------------------------------------------
# The declaration: the guard need not be stood down for a routine local run
# ---------------------------------------------------------------------------

def test_the_e2e_database_is_declared_by_this_domain():
    """Otherwise the documented recipe cannot start a server at all.

    An operator who has to run ``ICDEV_IDENTITY_GUARD=0`` to use the isolated
    database has switched the guard off for the cross-parent case it actually
    exists to catch.
    """
    import yaml

    declared = yaml.safe_load(DOMAIN.read_text(encoding="utf-8"))["db"]["databases"]
    assert "icdev" in declared
    assert "icdev_e2e" in declared, (
        "the repo's own documented E2E database must not require standing the "
        "identity guard down"
    )


def test_the_declaration_still_refuses_the_other_parent():
    """Widening it by one name must not widen it to ICDEV[FT]."""
    import yaml

    declared = yaml.safe_load(DOMAIN.read_text(encoding="utf-8"))["db"]["databases"]
    assert "icdev_ft" not in declared
# CUI // SP-CTI
