# CUI // SP-CTI
"""ONE governed door to the emulator: a connection row, a grant, a bounded host
(flx-bridge-02).

flx-bridge-01 shipped a working ``floci`` connector and it was UNREACHABLE
through the broker: ``args/databridge_connections.yaml`` had no row for it and
``args/databridge_agent_access.yaml`` had no grant, so every brokered fetch died
at "connector 'floci' is not granted to agents". A connector nothing may call is
the declared-but-unconsumed defect one layer out from the one that card fixed --
and the pressure it creates is worse than inertness, because flx-twin-01's only
alternative is to import the connector directly, which is the ungoverned side
channel cef-fnd-03 exists to close.

WHAT IS PINNED HERE, AND WHY EACH ONE WOULD OTHERWISE RE-BREAK SILENTLY.

1. **The two files keep their separation.** ``databridge_connections.yaml`` is
   the ENDPOINT (where, and as whom); ``databridge_agent_access.yaml`` is the
   AUTHORIZATION (who may read what). Different reviewers, different cadence.
   A credential field appearing in the grant file, or a grant field appearing in
   the connection file, collapses that and is asserted against.

2. **``auth_method: none`` is the MEASURED answer, not a shortcut.**
   ``emulator.credentials()`` is hard-wired to the dummy pair and deliberately
   does not read the ambient AWS environment. There is no credential, so there
   is no reference -- which is a stronger form of "no secret literal in YAML"
   than any reference could be, and it keeps the standing rule that no shipped
   grant reaches a credentialed system.

3. **The egress allowlist is ENFORCED, not declared.** cef-fnd-03's own
   docstring names this exact key as the platform's signature defect ("a
   per-connection ``egress_allowlist`` was declared and never enforced"). The
   tests below do not check that the key EXISTS -- they drive the connector with
   a hostile endpoint and require a refusal.

4. **Every call writes exactly one audit row**, allowed and denied, against a
   REAL table. A module only ever tested with its risky path mocked away is
   untested; that is how ``databridge_agent_access_log`` stayed empty for the
   whole life of the broker.

5. **The grant covers the connector's whole declared surface**, because
   flx-twin-01's ``warn`` verdict is reachable ONLY through a container-backed
   table. A grant that dropped ``lambda_functions`` would make one of four
   verdicts structurally unreachable -- a check with no probe behind it.

NO NETWORK. Every case below is decided before a socket opens: the refusals are
configuration verdicts, and the audited round trip drives a stub connector.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from icdev.tools.databridge import broker

REPO_ROOT = Path(__file__).resolve().parents[2]
GRANT_PATH = REPO_ROOT / "args" / "databridge_agent_access.yaml"
CONNECTIONS_PATH = REPO_ROOT / "args" / "databridge_connections.yaml"

CONNECTION_ID = "floci-emulator-local"
GRANTED_ROLE = "twin_observatory_analyst"

#: Spellings of "this machine". Used to DERIVE which grants reach off-box, so
#: the off-box count below is a property of the shipped data rather than a
#: hand-maintained list that goes stale the next time a grant lands.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _grants() -> list[dict]:
    raw = yaml.safe_load(GRANT_PATH.read_text(encoding="utf-8")) or {}
    return [g for g in (raw.get("connectors") or []) if isinstance(g, dict)]


def _descriptors() -> list[dict]:
    raw = yaml.safe_load(CONNECTIONS_PATH.read_text(encoding="utf-8")) or {}
    return [c for c in (raw.get("connections") or []) if isinstance(c, dict)]


def _descriptor(conn_id: str) -> dict:
    for entry in _descriptors():
        if str(entry.get("id")) == conn_id:
            return entry
    raise LookupError(f"no connection descriptor with id {conn_id!r}")


def _grant(name: str) -> dict:
    for entry in _grants():
        if str(entry.get("name")) == name:
            return entry
    raise LookupError(f"no grant named {name!r}")


def _loopback_only_connection_ids() -> set:
    """Connections whose declared egress names nothing off this machine.

    Derived from the descriptor's own allowlist. A connection with NO allowlist
    is deliberately NOT counted as loopback: an empty allowlist means "no
    restriction", which is the opposite of bounded.
    """
    ids = set()
    for entry in _descriptors():
        hosts = list((entry.get("config") or {}).get("egress_allowlist") or [])
        if hosts and all(str(h).lower() in LOOPBACK_HOSTS for h in hosts):
            ids.add(str(entry.get("id")))
    return ids


# ---------------------------------------------------------------------------
# 1. The door exists, and both halves of it agree
# ---------------------------------------------------------------------------


def test_the_grant_and_the_connection_row_both_exist_and_link():
    """A grant naming a connection with no descriptor is refused by the broker.

    The link between the two files IS ``grant.connection_id == descriptor.id``,
    and nothing but a test checks it: a typo there produces a connector that is
    granted, registered, importable, and refused on every call.
    """
    grant = _grant("floci")
    assert grant["connection_id"] == CONNECTION_ID
    assert _descriptor(CONNECTION_ID)["connector_name"] == "floci"


def test_the_granted_role_sees_the_emulator_and_nobody_else_does():
    """``agents`` is scoped. An empty list grants every runtime-generated SME."""
    granted = {e["connector"] for e in broker.list_available(GRANTED_ROLE)}
    assert "floci" in granted

    for role in ("doc_reviewer", "security_analyst", "some_runtime_generated_sme"):
        assert "floci" not in {e["connector"] for e in broker.list_available(role)}, (
            f"{role!r} can see the emulator; the grant is not scoped"
        )


def test_the_grant_covers_the_connectors_whole_declared_surface():
    """flx-twin-01's four verdicts need all seven tables, ``warn`` needs a
    container-backed one, and a table the connector does not serve is a phantom
    grant nobody can exercise. Pinned against the connector's own ``TABLES`` so
    a table added later cannot silently inherit an authorization decision."""
    from tools.databridge.connectors.floci_connector import TABLES, table_is_docker_backed

    granted = list(_grant("floci")["tables"])
    assert sorted(granted) == sorted(TABLES), (
        "the grant and the connector disagree about which tables exist; a new "
        "table is a NEW authorization decision and must be made by hand here"
    )
    assert [t for t in granted if table_is_docker_backed(t)], (
        "no container-backed table is granted, so flx-twin-01's `warn` verdict "
        "is structurally unreachable"
    )


# ---------------------------------------------------------------------------
# 2. Separation of concerns, and no credential anywhere
# ---------------------------------------------------------------------------


def test_the_two_files_do_not_leak_into_each_other():
    """ENDPOINT and AUTHORIZATION have different reviewers and cadences."""
    grant = _grant("floci")
    assert not set(grant) & {
        "auth_method", "auth_secret_ref", "config", "egress_allowlist",
        "password", "token", "api_key", "secret",
    }
    assert not set(_descriptor(CONNECTION_ID)) & {
        "agents", "tables", "classification_ceiling",
    }


def test_the_emulator_connection_declares_no_credential_at_all():
    """The measured answer: ``emulator.credentials()`` is hard-wired to the
    dummy pair and deliberately ignores the ambient AWS environment, so there is
    nothing to reference. A ref here would be refused by the seeder under
    ``auth_method: none``; under any other auth_method it would be resolved and
    injected under a config key this connector never reads; and
    ``resolve_secret`` raises on an unset variable, so a shipped grant would
    refuse EVERY call on any deployment that had not exported it."""
    from tools.cloud import emulator

    descriptor = _descriptor(CONNECTION_ID)
    assert descriptor["auth_method"] == "none"
    assert not descriptor.get("auth_secret_ref")
    assert emulator.credentials() == (emulator.DEFAULT_ACCESS_KEY, emulator.DEFAULT_SECRET_KEY)


def test_the_classification_is_a_label_not_a_banner():
    """``db_connections.classification`` feeds the RLS predicate, which is built
    from the LABEL vocabulary. A banner matches no member of it at any
    clearance: the row would be written, retained and invisible."""
    from icdev.tools.databridge.seed_connections import CLASSIFICATION_LABELS

    assert _descriptor(CONNECTION_ID)["classification"] in CLASSIFICATION_LABELS


def test_exactly_one_shipped_grant_reaches_off_box():
    """The successor to ``test_the_manifest_is_enabled_with_exactly_one_grant``.

    That test counted grants, and a count cannot tell a public internet feed
    from a loopback emulator. What the count was a proxy for is that an install
    must not silently authorise agents against systems the operator never
    reviewed -- so the population is DERIVED from each connection's own declared
    egress, and every shipped grant is required to carry the properties that
    made the first one acceptable.
    """
    manifest = broker.load_manifest()
    assert manifest["enabled"] is True

    local = _loopback_only_connection_ids()
    assert CONNECTION_ID in local, "the emulator connection is not bounded to this machine"

    off_box = [g for g in manifest["connectors"]
               if str(g.get("connection_id")) not in local]
    assert len(off_box) == 1, (
        f"{len(off_box)} shipped grants reach off-box; each one is a "
        f"per-deployment decision, not a default"
    )

    by_id = {str(d.get("id")): d for d in _descriptors()}
    for grant in manifest["connectors"]:
        descriptor = by_id.get(str(grant.get("connection_id")))
        assert descriptor is not None, f"grant {grant.get('name')!r} names no descriptor"
        assert descriptor.get("auth_method", "none") == "none"
        assert grant.get("agents"), f"{grant.get('name')!r} is granted to EVERY agent"
        assert grant.get("tables"), f"{grant.get('name')!r} has no table allowlist"
        assert grant.get("classification_ceiling") == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# 3. The egress allowlist is ENFORCED, not decorative
# ---------------------------------------------------------------------------
#
# cef-fnd-03 named this key as the platform's signature defect. So these drive
# the connector rather than reading the YAML: the question is not "is a host
# listed" but "does a host that is NOT listed get refused".


@pytest.fixture
def emulator_on(monkeypatch):
    """Emulator enabled, every other seam switch cleared.

    Enabling it is what makes the endpoint decision happen at all -- disabled,
    the connector returns ``disabled`` before reading a config key, which is
    correct and would make these tests vacuous.
    """
    for key in ("FLOCI_ENDPOINT", "FLOCI_REGION", "FLOCI_DOCKER_SOCKET",
                "LOCALSTACK_ENABLED", "LOCALSTACK_ENDPOINT", "DOCKER_HOST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FLOCI_ENABLED", "true")
    return monkeypatch


def _connector():
    from tools.databridge.connectors.floci_connector import FlociConnector

    return FlociConnector()


def _shipped_egress_config() -> dict:
    return dict(_descriptor(CONNECTION_ID)["config"])


def test_the_shipped_allowlist_admits_the_emulators_own_default_endpoint(emulator_on):
    """A ceiling that refuses the only legitimate destination is not a control,
    it is an outage. The default endpoint must pass the shipped list."""
    from tools.cloud import emulator

    instance = _connector()
    instance._config = _shipped_egress_config()
    instance._ensure_configured()
    assert instance._endpoint == emulator.DEFAULT_ENDPOINT


@pytest.mark.parametrize("endpoint", [
    "http://169.254.169.254",          # cloud instance metadata
    "http://10.0.0.5:4566",            # something else on the estate
    "https://evil.example.com:4566",   # off-box entirely
])
def test_an_endpoint_outside_the_allowlist_is_refused_before_any_socket(emulator_on, endpoint):
    """A seam mis-set -- or set hostile -- must be refused, not dialled.

    Checked where the destination is DECIDED rather than per URL, because five
    of the seven tables go through boto3 and never touch the urllib helper a
    per-URL guard would sit on.
    """
    from tools.databridge.connector import ConnectorRequest

    instance = _connector()
    instance._config = {**_shipped_egress_config(), "endpoint": endpoint}
    with pytest.raises(PermissionError, match="egress_allowlist"):
        instance.read(ConnectorRequest(table_name="s3_buckets", limit=1))


def test_a_connection_declaring_no_allowlist_is_unrestricted(emulator_on):
    """Default-off, matching ``egress_guard``'s own semantics: a direct caller
    doing ``connect({})`` is unaffected, and only a connection row that declares
    a list binds. Turning this into deny-by-default would refuse every existing
    caller of the connector, which is a different card with its own survey."""
    instance = _connector()
    instance._config = {"endpoint": "http://192.0.2.7:4566"}
    instance._ensure_configured()
    assert instance._endpoint == "http://192.0.2.7:4566"


def test_the_allowlist_rule_is_egress_guards_own_rule_not_a_second_copy():
    """Two copies of a host-matching rule drift, and the drift is silent."""
    import inspect

    from tools.databridge.connectors import floci_connector

    source = inspect.getsource(floci_connector.FlociConnector._assert_endpoint_allowed)
    assert "host_allowed" in source, (
        "the connector re-implements host matching instead of calling "
        "egress_guard.host_allowed"
    )


def test_egress_guard_itself_would_refuse_this_endpoint():
    """MEASURED, and the reason ``_guard_egress`` is deliberately not used here.

    ``egress_guard`` is an INTERNET SSRF gate: https-only, and every resolved
    address range-checked. A loopback emulator over plain http is precisely the
    destination it exists to refuse -- so calling it would refuse every
    legitimate read, while declaring an allowlist and calling nothing is the
    other failure. Asserted so that a future edit "fixing" this by wiring
    ``_guard_egress`` in has to argue with a measurement.
    """
    from tools.cloud import emulator
    from tools.http.egress_guard import egress_guard

    cfg = {"allowlist": ["localhost"], "denylist": []}
    plain = f"{emulator.DEFAULT_ENDPOINT}{emulator.HEALTH_PATH}"
    assert egress_guard(plain, cfg)[1] == "scheme_not_https"
    assert egress_guard(plain.replace("http://", "https://"), cfg)[1] == "denied_ip_range"


# ---------------------------------------------------------------------------
# 4. Every call writes exactly one audit row -- against a REAL table
# ---------------------------------------------------------------------------


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    """A real SQLite DB carrying both tables the round trip touches."""
    db = tmp_path / "floci_bridge.db"
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
        CREATE TABLE db_connections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            connector_type TEXT NOT NULL,
            connector_name TEXT NOT NULL,
            config_yaml TEXT NOT NULL,
            auth_method TEXT NOT NULL DEFAULT 'none',
            auth_secret_ref TEXT,
            sync_direction TEXT DEFAULT 'read',
            status TEXT DEFAULT 'configured',
            health_status TEXT DEFAULT 'unknown',
            last_health_check TEXT,
            last_sync TEXT,
            sync_cadence_minutes INTEGER DEFAULT 60,
            classification TEXT DEFAULT 'UNCLASSIFIED',
            impact_level TEXT DEFAULT 'IL4',
            tenant_id TEXT NOT NULL DEFAULT 'default',
            project_id TEXT,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    return db


@pytest.fixture
def seeded_db(live_db):
    """The descriptor file seeded into that DB, through the REAL seeder.

    Not a hand-written INSERT: the round trip below is what proves the seeder's
    output is the shape the broker reads back, which a fixture row would assume.
    """
    from icdev.tools.databridge.seed_connections import seed

    result = seed()
    assert CONNECTION_ID in result["created"]
    return live_db


def _audit_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT agent_id, connector_name, table_name, decision, reason, rows_returned "
            "FROM databridge_agent_access_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_the_seeded_row_is_readable_back_through_the_rls_injected_path(seeded_db):
    """A row the connection manager cannot SEE is a row the broker cannot see.

    That is the failure a raw SELECT hides, and the reason ``classification``
    must be a label: the read path injects ``classification IN (...)`` drawn
    from the label vocabulary.
    """
    from icdev.tools.databridge.connection_manager import get_connection

    row = get_connection(CONNECTION_ID)
    assert row is not None, "the seeded row is invisible to the path the broker reads"
    config = yaml.safe_load(row["config_yaml"]) or {}
    assert config.get("egress_allowlist"), (
        "the egress allowlist did not survive the round trip, so the connector "
        "would run unbounded"
    )


def test_an_allowed_fetch_writes_exactly_one_audit_row(seeded_db):
    """Exactly one: a decision recorded twice is as wrong as one recorded never.

    The connector is stubbed so nothing dials the emulator, but the manifest,
    the connection row, the redaction pass and the audit write are all real.
    """
    from unittest.mock import MagicMock, patch

    with patch("icdev.tools.databridge.registry.get_connector_instance") as gi:
        gi.return_value = MagicMock(
            connect=MagicMock(return_value=True),
            read=MagicMock(return_value=MagicMock(data=[{"Name": "bucket-a"}])),
        )
        outcome = broker.fetch(GRANTED_ROLE, "floci", "s3_buckets")

    assert outcome.ok is True
    assert outcome.audited is True
    assert outcome.row_count == 1

    rows = _audit_rows(seeded_db)
    assert len(rows) == 1
    agent, connector, table, decision, _reason, returned = rows[0]
    assert (agent, connector, table, decision, returned) == (
        GRANTED_ROLE, "floci", "s3_buckets", "allowed", 1,
    )


@pytest.mark.parametrize("agent,table,expected", [
    (GRANTED_ROLE, "iam_users", "not in the grant"),
    ("doc_reviewer", "s3_buckets", "is not granted"),
    ("some_runtime_generated_sme", "health", "is not granted"),
])
def test_a_denied_fetch_writes_exactly_one_audit_row_carrying_its_reason(
    seeded_db, agent, table, expected
):
    """Denials are the interesting half: a connector an agent keeps being
    refused is either a misconfiguration or someone probing."""
    outcome = broker.fetch(agent, "floci", table)

    assert outcome.ok is False
    assert outcome.audited is True

    rows = _audit_rows(seeded_db)
    assert len(rows) == 1
    logged_agent, connector, logged_table, decision, reason, returned = rows[0]
    assert (logged_agent, connector, logged_table, decision, returned) == (
        agent, "floci", table, "denied", 0,
    )
    assert expected in reason


def test_a_write_through_the_broker_is_denied_and_audited(seeded_db):
    """The connector HAS a ``write()`` path; the broker has none, and that is
    the control. Read-only is stated rather than implied by absence."""
    outcome = broker.fetch(GRANTED_ROLE, "floci", "s3_buckets", filters={"_write": True})

    assert outcome.ok is False
    assert "read-only" in outcome.error

    rows = _audit_rows(seeded_db)
    assert len(rows) == 1
    assert rows[0][3] == "denied"


def test_re_seeding_is_idempotent(seeded_db):
    """A config table, not an append-only one: re-seeding updates in place."""
    from icdev.tools.databridge.seed_connections import seed

    result = seed()
    assert CONNECTION_ID in result["updated"]
    assert CONNECTION_ID not in result["created"]


def test_verify_reports_the_grant_as_wired(seeded_db):
    """``--verify`` is what an operator runs; an orphan grant must show up
    there, and this one must not be one."""
    from icdev.tools.databridge.seed_connections import verify

    report = verify()
    assert report["orphan_grants"] == []
    check = next(c for c in report["checks"] if c["connection_id"] == CONNECTION_ID)
    assert check["present"] is True
    assert check["granted_to_agents"] is True
    # None, not False: there is no credential to resolve, which is a different
    # fact from a credential that failed to resolve.
    assert check["secret_resolves"] is None
