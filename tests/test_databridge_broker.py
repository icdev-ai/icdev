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


def test_shipped_manifest_grants_nothing():
    """An install must not silently authorise agents against customer systems."""
    manifest = broker.load_manifest()
    assert manifest["enabled"] is False
    assert manifest["connectors"] == []


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
            rows_returned INTEGER DEFAULT 0,
            redactions_applied INTEGER DEFAULT 0,
            classification TEXT DEFAULT 'CUI // SP-CTI',
            created_at TEXT DEFAULT (datetime('now'))
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
    assert any(r[3] == "allowed" for r in rows)


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

    hook = (Path(__file__).resolve().parents[1] / "tools/hooks/shared_checks.py").read_text(
        encoding="utf-8"
    )
    assert '"databridge_agent_access_log"' in hook
