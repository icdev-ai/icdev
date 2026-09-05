# CUI // SP-CTI
"""The first authorized DataBridge connector, end to end (cef-fnd-03).

DataBridge shipped 33 implemented connectors and ZERO authorized ones:
``args/databridge_agent_access.yaml`` carried ``enabled: false`` with
``connectors: []``, so ``list_available()`` answered ``[]`` for every agent and
every ``fetch()`` denied before reaching a connector. ``db_connections`` — the
table a grant's ``connection_id`` points at — held 0 rows. The external rung was
declared and never climbed.

These tests pin the first grant AND the three things that had to be fixed before
a grant could work at all, each of which would otherwise re-break silently:

* **Connectors were never imported.** ``@register_connector`` runs on import and
  nothing imported the connector modules, so every brokered fetch died at
  "connector 'rss' is not registered". Worse, the mirror split made the fix
  non-obvious: ``tools.databridge.registry`` and ``icdev.tools.databridge.registry``
  are two module objects with two ``_REGISTRY`` dicts, the connectors registered
  into the first and the broker reads the second.
* **``connection_id`` was decorative.** The broker passed it through and never
  read the row, so a per-connection ``egress_allowlist`` was declared and never
  enforced.
* **The RSS connector fetched bare.** It does not extend ``saas_base``, so it
  inherited none of that class's egress guard — while being the one connector an
  agent can reach through the broker, whose docstring advertises that guard.

What is deliberately NOT loosened: deny-all default, connector+table allowlist,
per-agent grants, classification ceiling, read-only, fail-closed redaction,
air-gap interlock, row caps, and an audit row on every outcome.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from icdev.tools.databridge import broker

REPO_ROOT = Path(__file__).resolve().parents[1]
GRANT_PATH = REPO_ROOT / "args" / "databridge_agent_access.yaml"
CONNECTIONS_PATH = REPO_ROOT / "args" / "databridge_connections.yaml"


def _grants() -> list[dict]:
    raw = yaml.safe_load(GRANT_PATH.read_text(encoding="utf-8")) or {}
    return [g for g in (raw.get("connectors") or []) if isinstance(g, dict)]


def _grant_named(name: str) -> dict:
    """The one grant called *name*.

    Was `(grant,) = _grants()`, which read "the grant" while meaning "the only
    grant" — an assumption that expired the moment a second one shipped. The
    subject of the tests below is the cef-fnd-03 rss grant specifically.
    """
    matches = [g for g in _grants() if str(g.get("name")) == name]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one grant named {name!r}, got {len(matches)}")
    return matches[0]


def _descriptors() -> list[dict]:
    raw = yaml.safe_load(CONNECTIONS_PATH.read_text(encoding="utf-8")) or {}
    return [c for c in (raw.get("connections") or []) if isinstance(c, dict)]


# ---------------------------------------------------------------------------
# The shipped manifest
# ---------------------------------------------------------------------------


# `test_the_manifest_is_enabled_with_exactly_one_grant` lived here and counted
# grants. flx-bridge-02 shipped a second one (the loopback floci emulator) and a
# COUNT cannot tell the two kinds apart, so its successor is
# tests/databridge/test_floci_grant.py::test_exactly_one_shipped_grant_reaches_
# off_box, which derives "off-box" from each connection's own egress_allowlist
# rather than from a number, and asserts the properties the number was a proxy
# for. Strictly stronger, and correctly named for what it counts.


def test_the_grant_is_fully_specified():
    grant = _grant_named("rss")
    assert grant["name"] == "rss"
    assert grant["description"].strip()
    assert grant["connection_id"] == "federal-register-nist"
    # An allowlist, not a hint — and for this connector the "table" is the feed
    # URL, so it is an exact-URL allowlist.
    assert grant["tables"] == [
        "https://www.federalregister.gov/api/v1/documents.rss"
        "?conditions%5Bagencies%5D%5B%5D=national-institute-of-standards-and-technology"
    ]
    # Scoped. An empty list grants EVERY agent including runtime-generated SMEs.
    assert grant["agents"], "an empty agents list grants every agent, including generated SMEs"
    assert grant["classification_ceiling"] == "UNCLASSIFIED"


def test_no_shipped_grant_reaches_a_credentialed_system():
    """The successor to `test_shipped_manifest_grants_nothing`.

    That test asserted `enabled is False`, which is not the invariant it was
    protecting — the invariant is that an INSTALL must not silently authorise
    agents against systems the operator never reviewed. A public,
    credential-free, world-readable government feed is not such a system: its
    content is already disclosed to everyone and its blast radius is the reading
    of a public web page. Anything holding a credential still needs an operator
    decision, and this is what makes that a rule rather than a comment.
    """
    by_id = {str(d.get("id")): d for d in _descriptors()}
    for grant in _grants():
        descriptor = by_id.get(str(grant.get("connection_id")))
        assert descriptor is not None, (
            f"grant {grant['name']!r} names connection {grant.get('connection_id')!r}, "
            f"which has no descriptor — the broker will refuse it"
        )
        assert descriptor.get("auth_method", "none") == "none", (
            f"shipped grant {grant['name']!r} reaches a CREDENTIALED system. "
            f"That is a per-deployment decision, not a shipped default."
        )
        assert not descriptor.get("auth_secret_ref")


def test_no_secret_literal_appears_in_either_yaml():
    """A ref is a location; a value is a secret. Only the first may be committed."""
    from icdev.tools.databridge.seed_connections import SECRET_REF_PREFIXES

    for descriptor in _descriptors():
        ref = str(descriptor.get("auth_secret_ref") or "")
        if ref:
            assert ref.startswith(SECRET_REF_PREFIXES), (
                f"{descriptor.get('id')!r}: auth_secret_ref is a literal, not a reference"
            )
    # The grant manifest is an authorization boundary and carries no credential
    # field at all — assert that stays true rather than trusting it.
    for grant in _grants():
        assert not set(grant) & {"auth_secret_ref", "password", "token", "api_key", "secret"}


def test_the_seeder_refuses_a_literal_secret():
    """Refused, not warned. A warning still lands the secret in git."""
    from icdev.tools.databridge.seed_connections import DescriptorError, validate

    with pytest.raises(DescriptorError, match="LITERAL"):
        validate({
            "id": "x", "name": "X", "connector_name": "rss",
            "connector_type": "saas_api", "auth_method": "api_key",
            "auth_secret_ref": "hunter2",
        })


def test_the_seeder_refuses_a_banner_as_a_classification():
    """`'CUI // SP-CTI'` matches no RLS label at any clearance — invisible rows."""
    from icdev.tools.databridge.seed_connections import DescriptorError, validate

    with pytest.raises(DescriptorError, match="classification"):
        validate({
            "id": "x", "name": "X", "connector_name": "rss",
            "connector_type": "saas_api", "auth_method": "none",
            "classification": "CUI // SP-CTI",
        })


def test_a_credentialed_descriptor_must_declare_a_ref():
    from icdev.tools.databridge.seed_connections import DescriptorError, validate

    with pytest.raises(DescriptorError, match="requires an auth_secret_ref"):
        validate({
            "id": "x", "name": "X", "connector_name": "splunk",
            "connector_type": "saas_api", "auth_method": "api_key",
        })


# ---------------------------------------------------------------------------
# Discovery answers differently for a granted and an ungranted role
# ---------------------------------------------------------------------------


def test_an_authorized_role_sees_the_source():
    sources = broker.list_available("doc_reviewer")
    assert len(sources) == 1
    assert sources[0]["connector"] == "rss"
    assert sources[0]["classification_ceiling"] == "UNCLASSIFIED"


def test_an_unauthorized_role_sees_nothing():
    """Not an error, not a partial list — []."""
    assert broker.list_available("security_analyst") == []
    assert broker.list_available("some_runtime_generated_sme") == []


# ---------------------------------------------------------------------------
# The registry actually resolves the granted connector
# ---------------------------------------------------------------------------


def test_the_granted_connector_resolves_from_the_brokers_own_registry():
    """The broker reads the icdev registry. This is the copy that must answer."""
    from icdev.tools.databridge.registry import get_connector_instance

    assert get_connector_instance("rss") is not None, (
        "the broker cannot dispatch: connectors register on import and nothing "
        "imports them"
    )


def test_the_granted_connector_also_resolves_from_the_root_namespace():
    """Two module objects, two _REGISTRY dicts. Both must answer for 'rss'."""
    from tools.databridge.registry import get_connector_instance

    assert get_connector_instance("rss") is not None


def test_autoload_refuses_a_name_that_is_not_a_module_name():
    """The value becomes a module path; a traversal must not be attempted."""
    from icdev.tools.databridge.registry import autoload_connector

    for name in ("../../etc/passwd", "os.path", "Rss", "", "rss;import os"):
        assert autoload_connector(name) is False


def test_an_unknown_connector_is_reported_not_raised():
    from icdev.tools.databridge.registry import get_connector_instance

    assert get_connector_instance("no_such_connector") is None


# ---------------------------------------------------------------------------
# The RSS connector guards egress
# ---------------------------------------------------------------------------
#
# It does not extend saas_base, so it inherited none of that class's guard —
# while being the one connector reachable through the broker. Every case below
# is decided before DNS, so these need no network.


@pytest.fixture
def rss():
    from icdev.tools.databridge.registry import get_connector_instance

    instance = get_connector_instance("rss")
    assert instance is not None
    return instance


def _request(url: str):
    from icdev.tools.databridge.connector import ConnectorRequest

    return ConnectorRequest(table_name=url, limit=5)


def test_egress_refuses_plaintext_http(rss):
    rss.connect({})
    with pytest.raises(PermissionError, match="scheme_not_https"):
        rss.read(_request("http://www.federalregister.gov/feed.xml"))


def test_egress_refuses_a_host_outside_the_connections_allowlist(rss):
    rss.connect({"egress_allowlist": ["www.federalregister.gov"]})
    with pytest.raises(PermissionError, match="not_allowlisted"):
        rss.read(_request("https://evil.example.com/feed.xml"))


def test_egress_refuses_the_instance_metadata_address(rss):
    """A literal IP skips DNS and is still range-checked."""
    rss.connect({})
    with pytest.raises(PermissionError, match="denied_ip_range"):
        rss.read(_request("https://169.254.169.254/latest/meta-data/"))


def test_egress_is_stopped_outright_in_air_gap(rss, monkeypatch):
    monkeypatch.setattr("tools.airgap.is_airgap", lambda *a, **k: True)
    rss.connect({})
    with pytest.raises(PermissionError, match="air-gap"):
        rss.read(_request("https://www.federalregister.gov/feed.xml"))


# ---------------------------------------------------------------------------
# An HTTP error is not an empty feed
# ---------------------------------------------------------------------------


class _FeedDict(dict):
    """Stands in for feedparser's FeedParserDict: attribute access, and an
    absent key raises AttributeError rather than returning a default."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _fake_feedparser(monkeypatch, feed: _FeedDict):
    import icdev.tools.databridge.connectors.rss_connector as mod

    monkeypatch.setattr(mod, "feedparser", MagicMock(parse=MagicMock(return_value=feed)))
    monkeypatch.setattr(
        "tools.http.egress_guard.egress_guard", lambda url, cfg, resolver=None: (True, "ok", ["1.2.3.4"])
    )


def test_a_404_is_an_error_not_zero_rows(rss, monkeypatch):
    """The shape feedparser returns for a dead URL: bozo False, entries [], no
    `version` key at all. The old code reached `feed.version` and raised
    AttributeError, which the broker reported as "connector error: object has no
    attribute 'version'" — and had the key existed, a retired endpoint would have
    read as a successful fetch of nothing."""
    _fake_feedparser(monkeypatch, _FeedDict(bozo=False, entries=[], status=404, feed=_FeedDict()))
    rss.connect({})
    response = rss.read(_request("https://www.federalregister.gov/gone.xml"))

    assert response.status == "error"
    assert "404" in response.errors[0]


def test_a_200_with_no_entries_is_an_empty_feed_not_an_error(rss, monkeypatch):
    """The other side of the same distinction: the source published nothing."""
    _fake_feedparser(
        monkeypatch,
        _FeedDict(bozo=False, entries=[], status=200, version="rss20", feed=_FeedDict()),
    )
    rss.connect({})
    response = rss.read(_request("https://www.federalregister.gov/feed.xml"))

    assert response.status == "ok"
    assert response.row_count == 0


# ---------------------------------------------------------------------------
# The round trip, against a real audit table
# ---------------------------------------------------------------------------
#
# These do NOT stub _audit. A module only ever tested with its risky path mocked
# away is untested, not working — that is how this table stayed empty for the
# whole life of the broker.

FEED_URL = (
    "https://www.federalregister.gov/api/v1/documents.rss"
    "?conditions%5Bagencies%5D%5B%5D=national-institute-of-standards-and-technology"
)


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    """A real SQLite DB carrying both tables the round trip touches."""
    db = tmp_path / "databridge.db"
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
    """The descriptor file seeded into that DB, through the real seeder."""
    from icdev.tools.databridge.seed_connections import seed

    result = seed()
    # flx-bridge-02 added a second descriptor. This fixture's subject is the rss
    # round trip, so NARROW THE SET THE ASSERTION SCANS rather than restating the
    # whole descriptor file, which every future connection would have to be added
    # to. The assertion below is unchanged and still fails on a seed() that
    # created nothing — narrowing the subject must never drop the check. The
    # floci row is pinned in its own gated suite.
    result = dict(result, created=[i for i in result["created"] if i == "federal-register-nist"])
    assert result["created"] == ["federal-register-nist"]
    return live_db


def _audit_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT agent_id, connector_name, table_name, decision, reason, "
            "rows_returned FROM databridge_agent_access_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_an_allowed_fetch_returns_rows_and_writes_one_audit_row(seeded_db, monkeypatch):
    entries = [
        _FeedDict(title="NIST SP 800-53 Rev. 5", summary="…", link="https://x/1",
                  id="1", published_parsed=(2026, 8, 1, 0, 0, 0, 0, 1, 0), tags=[]),
        _FeedDict(title="FIPS 140-3", summary="…", link="https://x/2",
                  id="2", published_parsed=(2026, 8, 2, 0, 0, 0, 0, 1, 0), tags=[]),
    ]
    _fake_feedparser(
        monkeypatch,
        _FeedDict(bozo=False, entries=entries, status=200, version="rss20",
                  feed=_FeedDict(title="Federal Register")),
    )

    outcome = broker.fetch("doc_reviewer", "rss", FEED_URL, limit=5)

    assert outcome.ok is True, outcome.error
    assert outcome.row_count == 2
    assert outcome.audited is True
    assert outcome.rows[0]["headline"] == "NIST SP 800-53 Rev. 5"

    rows = _audit_rows(seeded_db)
    assert len(rows) == 1, "a decision recorded twice is as wrong as one recorded never"
    agent, connector, table, decision, _reason, returned = rows[0]
    assert (agent, connector, decision, returned) == ("doc_reviewer", "rss", "allowed", 2)
    assert table == FEED_URL


@pytest.mark.parametrize("agent,table,classification,expected", [
    ("doc_reviewer", "https://evil.example.com/feed.xml", "UNCLASSIFIED", "not in the grant"),
    ("doc_reviewer", FEED_URL, "SECRET", "exceeds ceiling"),
    ("security_analyst", FEED_URL, "UNCLASSIFIED", "is not granted"),
])
def test_a_denied_fetch_is_audited_with_its_reason(seeded_db, agent, table,
                                                   classification, expected):
    outcome = broker.fetch(agent, "rss", table, classification=classification)

    assert outcome.ok is False
    assert expected in outcome.error
    assert outcome.audited is True

    (row,) = _audit_rows(seeded_db)
    assert row[0] == agent
    assert row[3] == "denied"
    assert expected in row[4], "the reason is the whole value of a denial row"
    assert row[5] == 0


def test_air_gap_denies_and_audits_without_reaching_the_connector(seeded_db, monkeypatch):
    monkeypatch.setattr(broker, "_airgap_active", lambda: True)

    with patch("icdev.tools.databridge.registry.get_connector_instance") as instance:
        outcome = broker.fetch("doc_reviewer", "rss", FEED_URL)
        instance.assert_not_called()

    assert outcome.ok is False
    assert "air-gap" in outcome.error

    (row,) = _audit_rows(seeded_db)
    assert row[3] == "denied"
    assert "air-gap" in row[4]


# ---------------------------------------------------------------------------
# connection_id is no longer decorative
# ---------------------------------------------------------------------------


def test_the_connections_config_reaches_the_connectors_egress_guard(seeded_db, monkeypatch):
    """The declared egress_allowlist has to arrive somewhere or it is a comment."""
    seen: dict = {}

    def _guard(url, cfg, resolver=None):
        seen["url"] = url
        seen["cfg"] = cfg
        return (True, "ok", ["1.2.3.4"])

    import icdev.tools.databridge.connectors.rss_connector as mod

    monkeypatch.setattr(mod, "feedparser", MagicMock(parse=MagicMock(
        return_value=_FeedDict(bozo=False, entries=[], status=200,
                               version="rss20", feed=_FeedDict()))))
    monkeypatch.setattr("tools.http.egress_guard.egress_guard", _guard)

    assert broker.fetch("doc_reviewer", "rss", FEED_URL).ok is True
    assert seen["url"] == FEED_URL
    assert seen["cfg"]["allowlist"] == ["www.federalregister.gov"]


def test_a_grant_naming_an_unseeded_connection_is_refused(live_db, monkeypatch):
    """Fail closed. Running the connector on `{}` is what silently disarmed the
    per-connection egress allowlist in the first place — an unreadable
    connection is exactly the case where we cannot say what would be contacted."""
    with patch("icdev.tools.databridge.registry.get_connector_instance") as instance:
        outcome = broker.fetch("doc_reviewer", "rss", FEED_URL)
        instance.return_value.read.assert_not_called()

    assert outcome.ok is False
    assert "could not be read from db_connections" in outcome.error

    (row,) = _audit_rows(live_db)
    assert row[3] == "denied"


def test_a_grant_with_no_connection_id_still_runs_on_an_empty_config(live_db, monkeypatch):
    """The old shape stays legal: a connector needing neither endpoint nor
    credential is legitimate, and refusing it would be a new denial with no
    security story behind it."""
    monkeypatch.setattr(broker, "load_manifest", lambda: {
        "enabled": True,
        "connectors": [{"name": "rss", "tables": ["issues"], "agents": []}],
    })
    monkeypatch.setattr(broker, "_redact_outbound", lambda t: (t, 0))

    with patch("icdev.tools.databridge.registry.get_connector_instance") as instance:
        instance.return_value = MagicMock(
            connect=MagicMock(return_value=True),
            read=MagicMock(return_value=MagicMock(data=[{"id": 1}])),
        )
        outcome = broker.fetch("any_agent", "rss", "issues")

    assert outcome.ok is True
    instance.return_value.connect.assert_called_once_with({})


def test_an_unresolvable_credential_refuses_the_fetch(live_db, monkeypatch):
    """And the deny reason names the REFERENCE, never the value."""
    conn = sqlite3.connect(live_db)
    conn.execute(
        "INSERT INTO db_connections (id, name, connector_type, connector_name, "
        "config_yaml, auth_method, auth_secret_ref) VALUES (?,?,?,?,?,?,?)",
        ("federal-register-nist", "x", "saas_api", "rss", "{}", "api_key",
         "env:ICDEV_TEST_SECRET_THAT_IS_NOT_SET"),
    )
    conn.commit()
    conn.close()

    with patch("icdev.tools.databridge.registry.get_connector_instance") as instance:
        outcome = broker.fetch("doc_reviewer", "rss", FEED_URL)
        instance.return_value.read.assert_not_called()

    assert outcome.ok is False
    assert "could not be resolved" in outcome.error
    assert "ICDEV_TEST_SECRET_THAT_IS_NOT_SET" in outcome.error


# ---------------------------------------------------------------------------
# Row caps survive the grant
# ---------------------------------------------------------------------------


def test_the_row_cap_still_binds_on_the_granted_connector(seeded_db, monkeypatch):
    """An unbounded fetch is how a tool call becomes an exfiltration."""
    entries = [_FeedDict(title=f"e{i}", summary="", link="", id=str(i), tags=[])
               for i in range(5000)]
    _fake_feedparser(
        monkeypatch,
        _FeedDict(bozo=False, entries=entries, status=200, version="rss20",
                  feed=_FeedDict()),
    )

    outcome = broker.fetch("doc_reviewer", "rss", FEED_URL, limit=99999)
    assert outcome.ok is True
    assert outcome.row_count == broker.HARD_MAX_ROWS
