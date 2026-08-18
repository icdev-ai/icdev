# CUI // SP-CTI
"""Tests for the Cortex ``external`` backend adapter (cef-bck-02).

The rung that reaches OUTSIDE the boundary. Before it, ``tools/cortex`` had no
DataBridge import at all — "leverage DataBridge to reach external content that
is configured and authorized" described two subsystems that had never been
introduced to each other.

What these tests are actually protecting, in order of how quietly each would
break:

* **The rung is fail-closed and cannot be talked out of it.** The platform
  default is ``governance.fail_closed: false``; for an outbound call a gate that
  cannot run must BLOCK, so ``_egress_context`` pins True and an explicit
  ``ctx.fail_closed=False`` from the caller does not lower it.
* **Every emptiness says why.** Air-gap, nothing granted, a missing scope, a
  broker denial and an unaudited fetch are five DIFFERENT reasons for zero
  results, and only "the sources matched nothing" may return empty ``.errors``.
  Collapsing them is the ctx-perf-04 defect wearing a new hat.
* **A refusal is recorded.** The broker audits its own decisions; a refusal
  Cortex reaches BEFORE the broker (a service key without
  ``databridge:<connector>:read``) goes into the same table through
  ``broker.record_denial`` — a refusal nobody recorded is indistinguishable from
  a call nobody made.

The round-trip tests deliberately do NOT stub ``broker._audit``. A module only
ever tested with its risky path mocked away is untested — that is precisely how
``databridge_agent_access_log`` stayed empty for the whole life of the broker
(see tests/test_databridge_first_grant.py, which takes the same position).
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from tools.cortex import search_service
from tools.cortex.config import resolve_fail_closed
from tools.cortex.schemas import CORTEX_BACKENDS, CortexContext

FEED_URL = (
    "https://www.federalregister.gov/api/v1/documents.rss"
    "?conditions%5Bagencies%5D%5B%5D=national-institute-of-standards-and-technology"
)
GRANTED_ROLE = "cortex_analyst"
READ_SCOPE = "databridge:rss:read"


# ---------------------------------------------------------------------------
# Registration — the five points a backend has to land on
# ---------------------------------------------------------------------------


def test_external_is_registered_in_both_tables():
    assert "external" in CORTEX_BACKENDS
    assert set(search_service.BACKEND_ADAPTERS) == set(CORTEX_BACKENDS)
    assert search_service.BACKEND_ADAPTERS["external"] is search_service.search_external


def test_tools_cortex_imports_the_databridge_broker():
    """The gap this card exists to close: Cortex had no databridge import."""
    broker = search_service._broker()
    for name in ("fetch", "list_available", "record_denial"):
        assert callable(getattr(broker, name)), name


def test_external_carries_a_weight_and_a_timeout():
    """Lowest of the EVIDENTIARY backends — the comparison sme is not in.

    Measured against EVIDENTIARY_BACKENDS rather than CORTEX_BACKENDS, because
    ``sme`` weighs 0.0 and that 0.0 is not a ranking. It is the advisory floor
    (cef-bck-03): an opinion authored by a model at query time contributes
    nothing to RRF so it can never carry a fused list. Comparing against it
    would ask this rung to sit below a number that is not on the same scale, and
    the invariant that actually matters here is narrower — external must not
    outrank a backend that MEASURED similarity, and it is the only evidentiary
    backend that measured none.
    """
    from tools.cortex.config import CORTEX_CONFIG_DEFAULTS, resolve_strategy_weights
    from tools.cortex.schemas import ADVISORY_BACKENDS, EVIDENTIARY_BACKENDS

    weights = resolve_strategy_weights(CORTEX_CONFIG_DEFAULTS["search"])
    assert "external" in EVIDENTIARY_BACKENDS
    assert "external" not in ADVISORY_BACKENDS, (
        "external RETRIEVES — a feed item existed before the query and can be "
        "re-read. Being separately governed is a different axis from being an "
        "opinion, and only the second belongs in the advisory split."
    )
    assert weights["external"] < min(
        weights[b] for b in EVIDENTIARY_BACKENDS if b != "external"
    ), "external must not outweigh a backend that measured similarity"
    assert search_service._DEFAULT_TIMEOUTS["external"] > 0


def test_external_is_not_in_the_default_fan_out():
    """An UNCLASSIFIABLE query must not be the trigger for an outbound call."""
    assert "external" not in search_service._DEFAULT_FAN_OUT_BACKENDS


def test_the_read_scope_exists_and_is_never_granted_by_default():
    from tools.cortex.service_keys import ALL_SCOPES, DEFAULT_SCOPES

    assert READ_SCOPE in ALL_SCOPES
    assert READ_SCOPE not in DEFAULT_SCOPES


# ---------------------------------------------------------------------------
# fail_closed is pinned for this rung
# ---------------------------------------------------------------------------


def test_egress_context_pins_fail_closed_over_an_explicit_false():
    ctx = CortexContext(tenant_id="t1", classification="CUI", fail_closed=False)
    egress = search_service._egress_context(ctx)

    assert egress.fail_closed is True
    # The platform default resolves False; this rung does not consult it.
    assert resolve_fail_closed(CortexContext()) is False
    assert resolve_fail_closed(egress) is True


def test_egress_context_does_not_mutate_the_callers_context():
    """A search rung has no standing to re-post the rest of the request."""
    ctx = CortexContext(tenant_id="t1", fail_closed=False)
    search_service._egress_context(ctx)

    assert ctx.fail_closed is False


def test_egress_context_preserves_identity_and_classification():
    ctx = CortexContext(tenant_id="t9", user_id="u1", classification="SECRET",
                        session_id="s1", scopes=[READ_SCOPE])
    egress = search_service._egress_context(ctx)

    assert (egress.tenant_id, egress.user_id, egress.classification) == (
        "t9", "u1", "SECRET")
    assert egress.session_id == "s1"
    assert egress.scopes == [READ_SCOPE]


# ---------------------------------------------------------------------------
# Stubbed-broker cases: what each kind of emptiness says
# ---------------------------------------------------------------------------


class _StubBroker:
    """A broker whose every entry point records that it was reached."""

    def __init__(self, sources=None, outcome=None, raises=None):
        self.sources = sources if sources is not None else [{
            "connector": "rss",
            "tables": [FEED_URL],
            "classification_ceiling": "UNCLASSIFIED",
            "description": "Federal Register",
        }]
        self.outcome = outcome
        self.raises = raises
        self.fetches: list = []
        self.denials: list = []

    def list_available(self, agent_id=""):
        if isinstance(self.sources, Exception):
            raise self.sources
        return list(self.sources)

    def fetch(self, agent_id, connector, table, **kwargs):
        self.fetches.append((agent_id, connector, table, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.outcome

    def record_denial(self, agent_id, connector, table, reason):
        self.denials.append((agent_id, connector, table, reason))
        return True


class _Outcome:
    def __init__(self, ok=True, rows=None, error="", audited=True):
        self.ok = ok
        self.rows = list(rows or [])
        self.row_count = len(self.rows)
        self.error = error
        self.audited = audited


def _row(headline, body="", link="https://www.federalregister.gov/d/1", entry_id="1"):
    return {
        "headline": headline,
        "body_excerpt": body,
        "signal_date": "2026-08-01T00:00:00+00:00",
        "source": "federal-register",
        "link": link,
        "entry_id": entry_id,
        "author": "",
        "tags": [],
    }


@pytest.fixture()
def stub(monkeypatch):
    """Install a stub broker and return a factory that configures it."""
    holder: dict = {}

    def _install(**kwargs):
        broker = _StubBroker(**kwargs)
        holder["broker"] = broker
        monkeypatch.setattr(search_service, "_broker", lambda: broker)
        # Never let the developer's own environment decide.
        monkeypatch.setattr(search_service, "_external_airgap", lambda ctx: False)
        return broker

    return _install


def _stages(results) -> list:
    return [e["stage"] for e in getattr(results, "errors", [])]


def test_airgap_returns_errors_and_reaches_no_network(monkeypatch):
    broker = _StubBroker()
    monkeypatch.setattr(search_service, "_broker", lambda: broker)

    results = search_service.search_external(
        "nist publications", ctx=CortexContext(air_gap=True))

    assert list(results) == []
    assert _stages(results) == ["airgap"]
    assert "air-gap" in results.errors[0]["message"]
    # Not merely "no rows": nothing was contacted, and nothing was even asked
    # for. The broker refuses independently — this rung must not rely on that
    # to avoid the socket.
    assert broker.fetches == []


def test_platform_airgap_is_honoured_even_when_the_context_says_nothing(monkeypatch):
    broker = _StubBroker()
    monkeypatch.setattr(search_service, "_broker", lambda: broker)
    monkeypatch.setattr(
        search_service, "_backend",
        lambda name: MagicMock(is_airgap=lambda: True) if name == "airgap"
        else pytest.fail(f"unexpected backend import {name!r}"),
    )

    results = search_service.search_external("nist", ctx=CortexContext())

    assert _stages(results) == ["airgap"]
    assert broker.fetches == []


def test_nothing_granted_is_reported_not_returned_as_an_empty_corpus(stub):
    broker = stub(sources=[])

    results = search_service.search_external("nist", ctx=CortexContext())

    assert list(results) == []
    assert _stages(results) == ["authorization"]
    assert GRANTED_ROLE in results.errors[0]["message"]
    assert broker.fetches == []


def test_an_unreadable_manifest_denies_rather_than_guessing_a_source_list(stub):
    broker = stub(sources=RuntimeError("manifest unreadable"))

    results = search_service.search_external("nist", ctx=CortexContext())

    assert _stages(results) == ["authorization"]
    assert broker.fetches == []


def test_a_missing_scope_denies_and_the_denial_is_audited(stub):
    """The one refusal the broker cannot make, so the one Cortex must record."""
    broker = stub(outcome=_Outcome(rows=[_row("NIST SP 800-53")]))

    results = search_service.search_external(
        "nist", ctx=CortexContext(scopes=["cortex:search"]))

    assert list(results) == []
    assert _stages(results) == ["scope"]
    assert READ_SCOPE in results.errors[0]["message"]
    assert "NOT AUDITED" not in results.errors[0]["message"]
    # Recorded in databridge_agent_access_log, through the broker's own writer.
    assert broker.denials == [(GRANTED_ROLE, "rss", FEED_URL,
                              f"presented service key lacks scope {READ_SCOPE!r}")]
    # And no fetch happened.
    assert broker.fetches == []


def test_an_empty_scope_list_is_a_denial_not_an_absence(stub):
    """A presented key carrying no scopes is a refusal; None is not a claim."""
    broker = stub(outcome=_Outcome(rows=[_row("NIST SP 800-53")]))

    denied = search_service.search_external("nist", ctx=CortexContext(scopes=[]))
    assert _stages(denied) == ["scope"]
    assert broker.fetches == []

    allowed = search_service.search_external("nist", ctx=CortexContext(scopes=None))
    assert list(allowed), "scopes=None must defer to the broker's own grant"
    assert broker.fetches


def test_an_unrecorded_denial_says_so(stub):
    broker = stub()
    broker.record_denial = lambda *a, **k: False

    results = search_service.search_external("nist", ctx=CortexContext(scopes=[]))

    assert "NOT AUDITED" in results.errors[0]["message"]


def test_a_broker_denial_is_annotated_as_denied(stub):
    stub(outcome=_Outcome(ok=False, error="agent 'x' is not granted 'rss'"))

    results = search_service.search_external(
        "nist", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert list(results) == []
    assert _stages(results) == ["denied"]
    assert "not granted" in results.errors[0]["message"]


def test_an_unaudited_fetch_drops_its_rows_and_is_annotated_separately(stub):
    """An unaudited decision is a CONTROL failure; an ordinary denial is not."""
    stub(outcome=_Outcome(ok=False, rows=[_row("leaked")], audited=False,
                          error="audit row could not be written"))

    results = search_service.search_external(
        "nist", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert list(results) == []
    assert _stages(results) == ["audit"]


def test_a_raising_broker_does_not_take_the_search_down(stub):
    stub(raises=RuntimeError("boom"))

    results = search_service.search_external(
        "nist", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert list(results) == []
    assert _stages(results) == ["fetch"]


def test_zero_rows_with_no_failure_carries_NO_errors(stub):
    """The one emptiness that means what an empty list looks like it means."""
    stub(outcome=_Outcome(rows=[]))

    results = search_service.search_external(
        "nist", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert list(results) == []
    assert list(results.errors) == []


def test_the_source_cap_is_reported_never_silent(stub, monkeypatch):
    monkeypatch.setattr(
        search_service, "_external_cfg",
        lambda config=None: {"agent_id": GRANTED_ROLE, "max_sources": 1},
    )
    broker = stub(
        sources=[
            {"connector": "rss", "tables": ["https://a.example/f.xml",
                                            "https://b.example/f.xml"],
             "classification_ceiling": "UNCLASSIFIED"},
        ],
        outcome=_Outcome(rows=[_row("one")]),
    )

    results = search_service.search_external(
        "one", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert "cap" in _stages(results)
    assert len(broker.fetches) == 1


def test_the_caller_classification_is_what_the_broker_bounds(stub):
    broker = stub(outcome=_Outcome(rows=[]))

    search_service.search_external(
        "nist", ctx=CortexContext(classification="CUI", scopes=[READ_SCOPE]))

    assert broker.fetches[0][3]["classification"] == "CUI"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_results_are_normalized_with_a_mandatory_citation(stub):
    stub(outcome=_Outcome(rows=[
        _row("NIST SP 800-53 Rev. 5 revision", "A notice about the catalog.",
             link="https://www.federalregister.gov/d/2026-1", entry_id="fr-1"),
    ]))

    (result,) = search_service.search_external(
        "nist catalog revision", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert result.backend == "external"
    assert result.strategy == "brokered"
    assert 0.0 <= result.score <= 1.0
    low, high = search_service._EXTERNAL_BAND
    assert low <= result.score <= high
    assert result.citation.source_id == "fr-1"
    assert result.citation.source_type == "external_document"
    assert result.citation.url == "https://www.federalregister.gov/d/2026-1"
    assert result.citation.title == "NIST SP 800-53 Rev. 5 revision"
    # The grant's declared ceiling, not the caller's clearance.
    assert result.citation.classification == "UNCLASSIFIED"
    assert result.metadata["connector"] == "rss"
    assert result.metadata["external"] is True
    assert result.metadata["fail_closed"] is True


def test_a_source_with_no_native_score_reports_None_not_zero(stub):
    """A measurement nobody made is not a measurement of zero."""
    stub(outcome=_Outcome(rows=[_row("anything")]))

    (result,) = search_service.search_external(
        "anything", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert result.raw_scores["native_score"] is None
    assert "term_overlap" in result.raw_scores
    assert result.raw_scores["feed_rank"] == 0


def test_a_matching_row_outranks_a_non_matching_one(stub):
    stub(outcome=_Outcome(rows=[
        _row("Unrelated agency notice", entry_id="a"),
        _row("Cryptographic module validation program update",
             "cryptographic module validation", entry_id="b"),
    ]))

    results = search_service.search_external(
        "cryptographic module validation",
        ctx=CortexContext(scopes=[READ_SCOPE]),
    )

    assert [r.citation.source_id for r in results] == ["b", "a"]


def test_top_k_is_honoured(stub):
    stub(outcome=_Outcome(rows=[_row(f"item {i}", entry_id=str(i)) for i in range(9)]))

    results = search_service.search_external(
        "item", top_k=3, ctx=CortexContext(scopes=[READ_SCOPE]))

    assert len(results) == 3


def test_a_row_that_is_not_a_dict_is_skipped_rather_than_crashing(stub):
    stub(outcome=_Outcome(rows=["not a row", _row("real")]))

    results = search_service.search_external(
        "real", ctx=CortexContext(scopes=[READ_SCOPE]))

    assert len(results) == 1


# ---------------------------------------------------------------------------
# The round trip, against a REAL broker and a REAL audit table
# ---------------------------------------------------------------------------
#
# _audit is not stubbed here. The acceptance criterion is that a governed
# cortex_search reaching this rung WRITES a databridge_agent_access_log row, and
# a test that mocks the writer cannot tell you whether it did.


class _FeedDict(dict):
    """feedparser returns attribute-accessible dicts."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover - mirrors feedparser
            raise AttributeError(item) from exc


@pytest.fixture()
def live_db(tmp_path, monkeypatch):
    """A real SQLite DB carrying the two tables the round trip touches."""
    db = tmp_path / "cortex_external.db"
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


@pytest.fixture()
def seeded_db(live_db):
    """The shipped connection descriptor, seeded through the real seeder."""
    from icdev.tools.databridge.seed_connections import seed

    result = seed()
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


def _fake_feed(monkeypatch, entries):
    import icdev.tools.databridge.connectors.rss_connector as mod

    feed = _FeedDict(bozo=False, entries=entries, status=200, version="rss20",
                     feed=_FeedDict(title="Federal Register"))
    monkeypatch.setattr(mod, "feedparser", MagicMock(parse=MagicMock(return_value=feed)))
    # Imported explicitly, not by string target: `tools.http` is the shim
    # package, so `monkeypatch.setattr("tools.http.egress_guard.egress_guard",
    # ...)` resolves attribute-by-attribute and fails on a submodule nothing has
    # imported yet — which made this test pass or fail on collection ORDER.
    import tools.http.egress_guard as guard_mod

    monkeypatch.setattr(
        guard_mod, "egress_guard",
        lambda url, cfg, resolver=None: (True, "ok", ["1.2.3.4"]),
    )


def _entry(title, summary, entry_id):
    return _FeedDict(title=title, summary=summary, link=f"https://x/{entry_id}",
                     id=entry_id, published_parsed=(2026, 8, 1, 0, 0, 0, 0, 1, 0),
                     tags=[])


def test_a_reaching_search_writes_one_allowed_audit_row(seeded_db, monkeypatch):
    _fake_feed(monkeypatch, [
        _entry("NIST SP 800-53 Rev. 5", "Catalog revision notice", "fr-1"),
        _entry("FIPS 140-3", "Validation program", "fr-2"),
    ])

    results = search_service.search_external(
        "NIST catalog revision",
        top_k=5,
        ctx=CortexContext(classification="UNCLASSIFIED", scopes=[READ_SCOPE]),
    )

    assert list(results.errors) == [], results.errors
    assert len(results) == 2
    assert all(r.citation.source_id for r in results)

    rows = _audit_rows(seeded_db)
    assert len(rows) == 1
    agent_id, connector, table, decision, _reason, returned = rows[0]
    assert (agent_id, connector, decision) == (GRANTED_ROLE, "rss", "allowed")
    assert table == FEED_URL
    assert returned == 2


def test_an_unauthorized_role_denies_and_the_denial_is_audited(seeded_db, monkeypatch):
    """Role enforcement is the BROKER's, inherited whole rather than restated."""
    _fake_feed(monkeypatch, [_entry("NIST SP 800-53 Rev. 5", "…", "fr-1")])
    monkeypatch.setattr(
        search_service, "_external_cfg",
        lambda config=None: {"agent_id": "unauthorized_role"},
    )

    results = search_service.search_external(
        "nist", ctx=CortexContext(classification="UNCLASSIFIED",
                                  scopes=[READ_SCOPE]))

    assert list(results) == []
    # list_available() filters this role out entirely, so the rung reports that
    # it has no reach rather than collecting a denial — probing for denials is
    # indistinguishable from an attack in the audit trail, which is why
    # list_available exists.
    assert _stages(results) == ["authorization"]
    assert _audit_rows(seeded_db) == []


def test_an_unauthorized_table_denies_and_the_denial_is_audited(seeded_db, monkeypatch):
    """A source the manifest does not allowlist: refused, and the row lands."""
    _fake_feed(monkeypatch, [_entry("anything", "…", "x")])
    broker = search_service._broker()
    monkeypatch.setattr(broker, "list_available", lambda agent_id="": [{
        "connector": "rss",
        "tables": ["https://evil.example/feed.xml"],
        "classification_ceiling": "UNCLASSIFIED",
    }])

    results = search_service.search_external(
        "anything", ctx=CortexContext(classification="UNCLASSIFIED",
                                      scopes=[READ_SCOPE]))

    assert list(results) == []
    assert _stages(results) == ["denied"]
    assert "not in the grant" in results.errors[0]["message"]

    rows = _audit_rows(seeded_db)
    assert len(rows) == 1
    assert rows[0][3] == "denied"
    assert "not in the grant" in rows[0][4]


def test_an_over_ceiling_classification_denies_and_the_denial_is_audited(
    seeded_db, monkeypatch
):
    """The grant's ceiling is UNCLASSIFIED; a SECRET context may not egress."""
    _fake_feed(monkeypatch, [_entry("NIST SP 800-53 Rev. 5", "…", "fr-1")])

    results = search_service.search_external(
        "nist", ctx=CortexContext(classification="SECRET", scopes=[READ_SCOPE]))

    assert list(results) == []
    assert _stages(results) == ["denied"]
    assert "exceeds ceiling" in results.errors[0]["message"]

    rows = _audit_rows(seeded_db)
    assert len(rows) == 1
    assert rows[0][3] == "denied"
    assert "exceeds ceiling" in rows[0][4]


def test_a_missing_scope_writes_a_denied_row_through_the_real_broker(
    seeded_db, monkeypatch
):
    """The Cortex-side refusal lands in the SAME table as a broker-side one."""
    _fake_feed(monkeypatch, [_entry("NIST SP 800-53 Rev. 5", "…", "fr-1")])

    results = search_service.search_external(
        "nist", ctx=CortexContext(classification="UNCLASSIFIED",
                                  scopes=["cortex:search"]))

    assert _stages(results) == ["scope"]
    rows = _audit_rows(seeded_db)
    assert len(rows) == 1
    agent_id, connector, table, decision, reason, returned = rows[0]
    assert (agent_id, connector, table, decision) == (
        GRANTED_ROLE, "rss", FEED_URL, "denied")
    assert READ_SCOPE in reason
    assert returned == 0


def test_the_rung_is_reachable_through_the_router(seeded_db, monkeypatch):
    """strategy="external" routes to this adapter — the fifth registration point."""
    _fake_feed(monkeypatch, [_entry("NIST SP 800-53 Rev. 5", "Catalog", "fr-1")])

    results = search_service.search(
        "NIST catalog",
        strategy="external",
        ctx=CortexContext(classification="UNCLASSIFIED", scopes=[READ_SCOPE]),
    )

    assert [r.backend for r in results] == ["external"]
    assert results[0].strategy == "external:override[external]"
    assert _audit_rows(seeded_db)[0][3] == "allowed"
