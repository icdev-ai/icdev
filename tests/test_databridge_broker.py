# CUI // SP-CTI
"""The DataBridge agent broker — the only route from an agent to a SaaS system.

Before this, agents could not reach DataBridge at all: no ACE role's
`icdev_tools` referenced it, `agent_toolsets.yaml` had no bundle, and
`TOOL_REGISTRY` had no generic fetch. The only thing keeping a co-worker away
from a customer's Splunk was that nobody had wired it up — an accident, not a
control. Wiring it up without a chokepoint would have turned the accident into
a hole.

These tests pin the chokepoint: deny-all by default, per-agent authorization,
read-only, fail-closed redaction, air-gap interlock, and an audit row on every
outcome including denials.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from icdev.tools.databridge import broker


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch):
    """Audit writes need a DB; assert on them explicitly where it matters."""
    monkeypatch.setattr(broker, "_audit", lambda *a, **k: None)


@pytest.fixture
def granted(monkeypatch):
    """A manifest granting one connector, one table, one agent."""
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{
            "name": "github",
            "connection_id": "corp-github",
            "tables": ["issues"],
            "agents": ["security_analyst"],
            "classification_ceiling": "UNCLASSIFIED",
        }],
    })
    # Redaction verified separately; here it is a pass-through so authorization
    # is what is under test.
    monkeypatch.setattr(broker, "_redact_outbound", lambda t: (t, 0))
    # Likewise the connection lookup. `corp-github` has no db_connections row and
    # the broker refuses a grant whose connection cannot be read (fail-closed) —
    # correct, but it would short-circuit every test below into the same denial.
    # The real lookup, and that refusal, are covered in
    # tests/test_databridge_first_grant.py.
    monkeypatch.setattr(broker, "_connection_config", lambda _cid: {})


# ---------------------------------------------------------------------------
# Deny-all is the default
# ---------------------------------------------------------------------------


def test_no_manifest_denies_everything():
    """A missing config must not be indistinguishable from a grant."""
    result = broker.fetch("security_analyst", "github", "issues")
    assert result.ok is False
    assert "not granted" in result.error


def test_disabled_manifest_denies(monkeypatch):
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": False,
        "connectors": [{"name": "github", "tables": ["issues"]}],
    })
    assert broker.fetch("a", "github", "issues").ok is False


def test_unlisted_table_is_denied(granted):
    """`tables` is an allowlist, not a hint."""
    result = broker.fetch("security_analyst", "github", "secrets")
    assert result.ok is False
    assert "not in the grant" in result.error


def test_unlisted_agent_is_denied(granted):
    result = broker.fetch("some_generated_sme", "github", "issues")
    assert result.ok is False
    assert "not granted" in result.error


def test_empty_agent_list_grants_any_agent(monkeypatch):
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{"name": "github", "tables": ["issues"], "agents": []}],
    })
    monkeypatch.setattr(broker, "_redact_outbound", lambda t: (t, 0))
    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(read=MagicMock(return_value=MagicMock(data=[{"id": 1}])))
        assert broker.fetch("any_new_sme", "github", "issues").ok is True


# ---------------------------------------------------------------------------
# Authorization happens BEFORE anything expensive or observable
# ---------------------------------------------------------------------------


def test_denial_never_touches_the_connector(granted):
    """An unknown target must cost no credential resolution and no DNS."""
    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        broker.fetch("security_analyst", "splunk", "events")
        gi.assert_not_called()


def test_classification_above_the_ceiling_is_denied(granted):
    result = broker.fetch("security_analyst", "github", "issues", classification="CUI")
    assert result.ok is False
    assert "exceeds ceiling" in result.error


def test_unknown_classification_is_denied(granted):
    result = broker.fetch("security_analyst", "github", "issues", classification="MADE-UP")
    assert result.ok is False


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", [
    "INSERT INTO incidents VALUES (1)",
    "update tickets set status='closed'",
    "DELETE FROM records",
    "drop table users",
])
def test_write_shaped_queries_are_refused(granted, query):
    """An agent writing to a system ICDEV does not own cannot be rolled back."""
    result = broker.fetch("security_analyst", "github", "issues", query=query)
    assert result.ok is False
    assert "read-only" in result.error


def test_write_flag_is_refused(granted):
    result = broker.fetch("security_analyst", "github", "issues", filters={"_write": True})
    assert result.ok is False
    assert "read-only" in result.error


# ---------------------------------------------------------------------------
# Outbound redaction is fail-closed
# ---------------------------------------------------------------------------


def test_unavailable_redaction_denies_the_fetch(monkeypatch):
    """A filter value is the one part of a fetch carrying caller content.

    An unavailable sanitizer means we cannot say what would leave, which is
    exactly when not to send it.
    """
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{"name": "github", "tables": ["issues"], "agents": []}],
    })

    def _boom(_text):
        raise broker.BrokerDenied("outbound redaction unavailable: simulated")

    monkeypatch.setattr(broker, "_redact_outbound", _boom)

    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        result = broker.fetch("a", "github", "issues", query="Acme Corp incident")
        assert result.ok is False
        assert "redaction unavailable" in result.error
        gi.assert_not_called()


def test_redacted_text_is_what_reaches_the_connector(monkeypatch):
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{"name": "github", "tables": ["issues"], "agents": []}],
    })
    monkeypatch.setattr(broker, "_redact_outbound", lambda t: ("<REDACTED>", 1))

    captured = {}

    def _read(request):
        captured["query"] = request.query
        captured["filters"] = request.filters
        return MagicMock(data=[])

    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(read=_read)
        result = broker.fetch(
            "a", "github", "issues",
            query="Acme Corp", filters={"owner": "jane.doe@example.com"},
        )

    assert "Acme" not in captured["query"]
    assert "jane.doe" not in str(captured["filters"])
    assert result.redactions >= 1


def test_empty_values_skip_redaction(monkeypatch):
    """No content, nothing to sanitize — and no reason to fail on an outage."""
    assert broker._redact_outbound("") == ("", 0)


# ---------------------------------------------------------------------------
# Air-gap
# ---------------------------------------------------------------------------


def test_air_gap_denies_before_authorization(granted, monkeypatch):
    """An external SaaS call is off-box by definition."""
    monkeypatch.setattr(broker, "_airgap_active", lambda: True)

    result = broker.fetch("security_analyst", "github", "issues")
    assert result.ok is False
    assert "air-gap" in result.error


def test_air_gap_hides_available_sources(granted, monkeypatch):
    monkeypatch.setattr(broker, "_airgap_active", lambda: True)
    assert broker.list_available("security_analyst") == []


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_agents_can_discover_their_own_reach(granted):
    """Probing and collecting denials looks like an attack in the audit trail."""
    sources = broker.list_available("security_analyst")
    assert len(sources) == 1
    assert sources[0]["connector"] == "github"
    assert sources[0]["tables"] == ["issues"]


def test_discovery_respects_the_agent_grant(granted):
    assert broker.list_available("some_other_agent") == []


# ---------------------------------------------------------------------------
# Bounds and auditing
# ---------------------------------------------------------------------------


def test_row_limit_is_capped(granted):
    """An unbounded fetch is how a tool call becomes an exfiltration."""
    rows = [{"id": i} for i in range(5000)]
    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(read=MagicMock(return_value=MagicMock(data=rows)))
        result = broker.fetch("security_analyst", "github", "issues", limit=99999)

    assert result.row_count <= broker.HARD_MAX_ROWS


def test_denials_are_audited(monkeypatch):
    """Denials are the interesting half of the trail."""
    calls = []
    monkeypatch.setattr(broker, "_audit",
                        lambda *a, **k: calls.append((a[3] if len(a) > 3 else "", a)))

    broker.fetch("a", "nope", "table")
    assert calls and calls[0][0] == "denied"


def test_connector_errors_degrade_to_a_result(granted):
    """An agent loop should reason about failure, not crash on it."""
    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(read=MagicMock(side_effect=RuntimeError("upstream 500")))
        result = broker.fetch("security_analyst", "github", "issues")

    assert result.ok is False
    assert "connector error" in result.error


def test_egress_refusal_is_reported_not_raised(granted):
    """saas_base raises PermissionError from the egress guard."""
    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(read=MagicMock(
            side_effect=PermissionError("egress blocked (denied_ip_range)")))
        result = broker.fetch("security_analyst", "github", "issues")

    assert result.ok is False
    assert "blocked" in result.error


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_shipped_manifest_grants_no_customer_system():
    """An install must not silently authorise agents against customer systems.

    This used to assert `enabled is False` and `connectors == []`, which is not
    the invariant — it is one way of satisfying it, and the one that left 33
    connectors and 0 authorized (cef-fnd-03). The invariant is about WHAT is
    granted: a public, credential-free, world-readable source discloses nothing
    by being reachable, while anything holding a credential is a per-deployment
    decision. The `auth_method: none` half of that rule is enforced in
    tests/test_databridge_first_grant.py, which reads the connection descriptors;
    here we pin the properties visible on the grant itself.
    """
    manifest = broker.load_manifest()
    for grant in manifest["connectors"]:
        assert grant.get("agents"), (
            f"{grant.get('name')!r} is granted to EVERY agent, including "
            f"runtime-generated SMEs"
        )
        assert grant.get("tables"), f"{grant.get('name')!r} has no table allowlist"
        assert grant.get("classification_ceiling") == "UNCLASSIFIED", (
            f"{grant.get('name')!r} ships a ceiling above UNCLASSIFIED"
        )


def test_mcp_tools_are_registered():
    from tools.mcp.tool_registry import TOOL_REGISTRY

    assert "databridge_fetch" in TOOL_REGISTRY
    assert "databridge_sources" in TOOL_REGISTRY


def test_toolset_bundle_is_read_only():
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "args/agent_toolsets.yaml").read_text(
            encoding="utf-8"
        )
    )
    bundle = cfg["bundles"]["external_data"]
    assert bundle["mutating"] is False
    assert set(bundle["tools"]) == {"databridge_fetch", "databridge_sources"}


# ---------------------------------------------------------------------------
# The audit trail must actually be written
# ---------------------------------------------------------------------------
#
# The first draft wrote to db_sync_log, whose schema requires a connection_id FK
# and row counts -- an authorization decision has neither, so every insert failed
# silently into a warning and the trail was empty exactly when it mattered. The
# tests did not catch it because every one of them stubbed _audit.
#
# These do NOT stub it. A module only ever tested with its risky path mocked away
# is untested, not working.


@pytest.fixture
def real_audit_db(tmp_path, monkeypatch):
    """A real SQLite DB with the access-log table, and _audit unstubbed."""
    import sqlite3

    db = tmp_path / "audit.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE databridge_agent_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'unknown',
            connector_name TEXT NOT NULL DEFAULT '',
            table_name TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT 'denied'
                CHECK(decision IN ('allowed','denied')),
            reason TEXT NOT NULL DEFAULT '',
            rows_returned INTEGER NOT NULL DEFAULT 0,
            redactions_applied INTEGER NOT NULL DEFAULT 0,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            classification TEXT NOT NULL DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()

    # Undo the autouse stub — this fixture is about the real write path.
    monkeypatch.undo()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    return db


def _rows(db):
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT agent_id, connector_name, table_name, decision, reason "
            "FROM databridge_agent_access_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_a_denial_is_actually_persisted(real_audit_db):
    """The row must reach the table, not a swallowed exception."""
    broker.fetch("security_analyst", "github", "issues")

    rows = _rows(real_audit_db)
    assert rows, "no audit row was written — the insert failed silently"
    # Exactly one: a decision recorded twice is as wrong as one recorded never.
    assert len(rows) == 1
    agent, connector, table, decision, reason = rows[0]
    assert agent == "security_analyst"
    assert connector == "github"
    assert decision == "denied"
    assert "not granted" in reason


def test_an_allowed_fetch_is_persisted_with_counts(real_audit_db, monkeypatch):
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{"name": "github", "tables": ["issues"], "agents": []}],
    })
    monkeypatch.setattr(broker, "_redact_outbound", lambda t: (t, 2))

    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(
            read=MagicMock(return_value=MagicMock(data=[{"id": 1}, {"id": 2}]))
        )
        broker.fetch("a", "github", "issues", query="something")

    rows = _rows(real_audit_db)
    assert len(rows) == 1
    assert rows[0][3] == "allowed"


def test_audit_targets_the_access_log_not_the_sync_log():
    """db_sync_log requires a connection_id FK and counts rows.

    An authorization decision has neither. Writing there is what made the trail
    silently empty.
    """
    from pathlib import Path

    # Read the FILE, not the attribute: the autouse fixture patches _audit, so
    # inspect.getsource would return the stub's source and the assertion would
    # pass against a lambda.
    source = (
        Path(broker.__file__).read_text(encoding="utf-8")
    )
    audit_body = source.split("def _audit(")[1].split("def fetch(")[0]
    assert "databridge_agent_access_log" in audit_body
    assert "INSERT INTO db_sync_log" not in audit_body


def test_access_log_is_registered_append_only():
    """An audit table that can be rewritten is not an audit table."""
    from pathlib import Path

    hook = (Path(__file__).resolve().parents[1] / ".claude/hooks/pre_tool_use.py").read_text(
        encoding="utf-8"
    )
    assert '"databridge_agent_access_log"' in hook


# ---------------------------------------------------------------------------
# An audit write that FAILS must surface (cef-fnd-01)
# ---------------------------------------------------------------------------
#
# The table did not exist on the primary PostgreSQL backend at all: its only DDL
# lives in init_icdev_db.py and is written in SQLite syntax (AUTOINCREMENT,
# datetime('now')), so it never ran there. Every insert raised UndefinedTable,
# _audit caught it and logged a warning, and the fetch returned ok=True with
# rows. Every external fetch was unauditable and nothing anywhere went red.
#
# The tests above could not catch that: a missing TABLE and a swallowed
# EXCEPTION are two defects, and the fixture that supplies the table hides the
# second one. These pin the swallow itself.


@pytest.fixture
def audit_db_without_the_table(tmp_path, monkeypatch):
    """A real DB pointed at by storage, with NO access-log table.

    Reproduces the live PostgreSQL condition on a backend the test suite can
    actually create.
    """
    import sqlite3

    db = tmp_path / "no_table.db"
    sqlite3.connect(db).close()

    monkeypatch.undo()  # drop the autouse _audit stub — the write path is the subject
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    return db


def test_audit_raises_when_the_row_cannot_be_written(audit_db_without_the_table):
    """The failure is raised, not logged and forgotten."""
    with pytest.raises(broker.AuditWriteFailed) as exc:
        broker._audit("a", "github", "issues", "allowed")

    assert "could not be recorded" in str(exc.value)


def test_an_unauditable_allowed_fetch_is_not_a_clean_call(
    audit_db_without_the_table, monkeypatch
):
    """The rows are withheld rather than delivered unaudited.

    "Auto-fetch, and log it" is not a fetch that logs when convenient. The read
    already happened, so what is still preventable is the agent RECEIVING data
    whose access nothing recorded.
    """
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{"name": "github", "tables": ["issues"], "agents": []}],
    })
    monkeypatch.setattr(broker, "_redact_outbound", lambda t: (t, 0))

    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(
            read=MagicMock(return_value=MagicMock(data=[{"secret": "value"}]))
        )
        result = broker.fetch("a", "github", "issues")

    assert result.ok is False
    assert result.audited is False
    assert result.rows == []
    assert result.row_count == 0
    assert "audit" in result.error.lower()


def test_an_unauditable_denial_says_so(audit_db_without_the_table):
    """A denial nobody recorded is still reported as unrecorded.

    An agent repeatedly refused a connector is the signal this table carries;
    losing it silently is the same defect one severity down.
    """
    result = broker.fetch("a", "nope", "table")

    assert result.ok is False
    assert result.audited is False
    assert "NOT AUDITED" in result.error


def test_a_successful_fetch_reports_that_it_was_audited(real_audit_db, monkeypatch):
    """`audited` is a field, not a note inside the error string.

    A caller alerting on "the trail is missing rows" must not have to parse
    prose to find out.
    """
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{"name": "github", "tables": ["issues"], "agents": []}],
    })
    monkeypatch.setattr(broker, "_redact_outbound", lambda t: (t, 0))

    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(read=MagicMock(return_value=MagicMock(data=[{"id": 1}])))
        result = broker.fetch("a", "github", "issues")

    assert result.ok is True
    assert result.audited is True
    assert result.to_dict()["audited"] is True


def test_audit_failure_is_not_swallowed_in_source():
    """No `except ...: logger.warning` swallow may return to _audit.

    Read the FILE rather than the attribute: the autouse fixture patches _audit,
    so inspect.getsource would return the stub.
    """
    from pathlib import Path

    source = Path(broker.__file__).read_text(encoding="utf-8")
    audit_body = source.split("def _audit(")[1].split("def fetch(")[0]
    assert "raise AuditWriteFailed" in audit_body


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def _migration_dir():
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[1]
        / "tools/db/migrations/20260817010532_databridge_agent_access_log"
    )


def _migration_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cef_fnd_01_up_inspect", str(_migration_dir() / "up.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_creates_the_table(tmp_path, monkeypatch):
    """Applying the migration to an empty database yields a writable table."""
    import importlib.util
    import sqlite3

    db = tmp_path / "migrated.db"
    sqlite3.connect(db).close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    up_py = _migration_dir() / "up.py"
    spec = importlib.util.spec_from_file_location("cef_fnd_01_up", str(up_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up()

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM databridge_agent_access_log"
        ).fetchone()[0] == 0
        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(databridge_agent_access_log)"
        )}
    finally:
        conn.close()

    # Every column the broker's INSERT names, plus the two RLS predicate columns
    # get_connection() injects into every SELECT the table serves.
    assert {
        "agent_id", "connector_name", "table_name", "decision", "reason",
        "rows_returned", "redactions_applied", "created_at",
        "tenant_id", "classification",
    } <= columns


def test_the_migration_is_postgres_native():
    """The PG branch must not be SQLite DDL run through translate_sql.

    That is what left the table missing: the only DDL was
    `INTEGER PRIMARY KEY AUTOINCREMENT` / `datetime('now')`, which is not
    PostgreSQL, so on a PG-primary deployment nothing created it.
    """
    import inspect

    mod = _migration_module()

    assert "BIGSERIAL" in mod._DDL_PG
    assert "TIMESTAMPTZ" in mod._DDL_PG
    assert "AUTOINCREMENT" not in mod._DDL_PG
    assert "datetime('now')" not in mod._DDL_PG
    # The SQLite fallback is carried alongside rather than derived by
    # translate_sql, which CLAUDE.md forbids from being load-bearing.
    assert "AUTOINCREMENT" in mod._DDL_SQLITE

    # `_dialect` does not exist on a PostgreSQL StorageConnection, so the
    # getattr(conn, "_dialect", "sqlite") idiom copied through older migrations
    # silently selects the SQLite branch on PG. Assert on the FUNCTION body, not
    # the file, so the docstring may name the trap it avoids.
    body = inspect.getsource(mod.up)
    assert "_backend" in body
    assert "_dialect" not in body


def test_the_sqlite_init_ddl_matches_the_migration():
    """Two definitions of one table is how this table went missing.

    `tools/db/init_icdev_db.py::SCHEMA_SQL` is the SQLite init fallback and the
    migration is the PostgreSQL primary. They drifted the moment the SQLite one
    was the only one that existed — so pin them to the same column set, which is
    the drift that actually bites: a column present in one backend and absent in
    the other makes the broker's INSERT succeed on a laptop and raise in
    production.
    """
    import re

    from tools.db.init_icdev_db import SCHEMA_SQL

    def _columns(ddl: str) -> set[str]:
        body = re.search(
            r"CREATE TABLE IF NOT EXISTS databridge_agent_access_log\s*\((.*?)\n\)",
            ddl,
            re.S,
        )
        assert body, "the table's DDL is not where this test expects it"
        names = set()
        for line in body.group(1).splitlines():
            line = line.strip()
            token = line.split()[0] if line else ""
            if token and token.upper() not in {"CHECK", "PRIMARY", "UNIQUE", "CONSTRAINT"}:
                names.add(token)
        return names

    mod = _migration_module()
    assert _columns(SCHEMA_SQL) == _columns(mod._DDL_SQLITE) == _columns(mod._DDL_PG)


def test_the_classification_default_is_a_label_not_a_banner():
    """RLS matches `classification IN (<labels>)`; a banner matches nothing.

    A row defaulted to 'CUI // SP-CTI' is written, retained, and invisible to
    every caller at every clearance.
    """
    from tools.security.security_context import classifications_dominated_by

    mod = _migration_module()
    for ddl in (mod._DDL_PG, mod._DDL_SQLITE):
        assert "CUI // SP-CTI" not in ddl
        assert "classification     TEXT        NOT NULL DEFAULT 'CUI'" in ddl

    # The vocabulary the predicate is drawn from. A banner is in none of it, at
    # any clearance — which is what would have made the rows unreadable.
    assert "CUI" in (classifications_dominated_by("CUI") or set())
    assert "CUI // SP-CTI" not in (classifications_dominated_by("TOP SECRET") or set())
