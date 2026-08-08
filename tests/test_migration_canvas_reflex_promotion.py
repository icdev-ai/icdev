# CUI // SP-CTI
"""The NMCE reflex was a runaway card generator.

`tools/genesis/reflexes/migration_canvas.py` minted a fresh `uuid4` per finding
per run, so its `INSERT OR IGNORE` (translated to `ON CONFLICT DO NOTHING` on
the primary key) never matched anything and every 24h cycle re-inserted the
entire finding set. Compounding it, nothing in the canvas ever moves a session
out of `in_progress` — there is no close control in the wizard and the PATCH
endpoint is never called with a status — so a stale session stays stale
forever. On the live board that produced 289 cards from 60 distinct findings,
169 of them already `scheduled`, i.e. queued to burn one agent session apiece.

These cover the two properties that stop it: findings are batched one card per
type, and a type with an already-open card does not stack another.
"""

import importlib
from contextlib import contextmanager

import pytest

_reflex = importlib.import_module("tools.genesis.reflexes.migration_canvas")


class FakeConn:
    """Minimal stand-in for a storage connection.

    `execute` returns self so both `.fetchall()` and `.fetchone()` chain off it,
    matching how the reflex actually calls it.
    """

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.inserts = []
        self.queries = []
        self._last = []

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.queries.append(norm)
        if norm.upper().startswith("INSERT"):
            self.inserts.append(params)
            self._last = []
            return self
        for key, rows in self.responses.items():
            if key in norm:
                self._last = rows
                return self
        self._last = []
        return self

    def fetchall(self):
        return self._last

    def fetchone(self):
        return self._last[0] if self._last else None

    def commit(self):
        pass


def _cm(conn):
    @contextmanager
    def _factory():
        yield conn

    return _factory


def _stale_sessions(n):
    """n sessions last touched long enough ago to clear the 7-day threshold."""
    return [
        {
            "id": f"nmig-{i:012x}",
            "src_model": "MX204",
            "tgt_model": "ASR-9901",
            "status": "in_progress",
            "updated_at": "2026-06-18T02:32:45.743783+00:00",
        }
        for i in range(n)
    ]


@pytest.fixture
def wired(monkeypatch):
    """Point the reflex at fake canvas/network DBs and capture kanban inserts."""

    def _wire(sessions, existing_open=None):
        mc = FakeConn({
            "FROM mc_net_sessions": sessions,
            "FROM mc_net_protocol_plans": [],
        })
        nc = FakeConn({"FROM ni_devices": []})
        icdev = FakeConn(
            {"FROM kanban_tasks": [{"id": existing_open}] if existing_open else []}
        )

        init_db = importlib.import_module("tools.migration_canvas.db.init_db")
        netmig = importlib.import_module("tools.migration_canvas.network_migration")
        storage = importlib.import_module("tools.db.storage")

        monkeypatch.setattr(init_db, "get_connection", _cm(mc))
        monkeypatch.setattr(netmig, "_mc_conn", _cm(mc))
        monkeypatch.setattr(netmig, "_nc_conn", _cm(nc))
        monkeypatch.setattr(storage, "get_connection", _cm(icdev))
        return icdev

    return _wire


class TestBatching:
    def test_fifty_four_stale_sessions_produce_one_card(self, wired):
        """The bug: 54 sessions meant 54 cards, every single day."""
        icdev = wired(_stale_sessions(54))
        result = _reflex.run({}, None)

        assert result["details"]["promoted_to_kanban"] == 1, (
            f"expected one batched card, got {result['details']['promoted_to_kanban']}"
        )
        assert len(icdev.inserts) == 1

    def test_batched_card_reports_the_real_count(self, wired):
        """Collapsing to one card must not hide how many sessions are stale."""
        icdev = wired(_stale_sessions(54))
        _reflex.run({}, None)

        title, description = icdev.inserts[0][1], icdev.inserts[0][2]
        assert "54" in title
        assert "54" in description

    def test_findings_metric_still_counts_every_session(self, wired):
        """Batching is a promotion concern; the health metric stays per-session."""
        wired(_stale_sessions(54))
        result = _reflex.run({}, None)

        assert result["metric_value"] == 54.0
        assert result["details"]["breakdown"]["stale_sessions"] == 54


class TestOpenCardGuard:
    def test_no_second_card_while_one_is_open(self, wired):
        """The 24h re-fire that turned 60 findings into 289 rows."""
        icdev = wired(_stale_sessions(54), existing_open="mc-reflex-b3760af6")
        result = _reflex.run({}, None)

        assert result["details"]["promoted_to_kanban"] == 0
        assert icdev.inserts == []

    def test_card_reraises_once_the_open_one_is_closed(self, wired):
        """Suppression must not be permanent — a still-true finding comes back."""
        icdev = wired(_stale_sessions(54), existing_open=None)
        result = _reflex.run({}, None)

        assert result["details"]["promoted_to_kanban"] == 1
        assert icdev.inserts

    def test_guard_query_ignores_terminal_cards(self, wired):
        """A 'done' card must not suppress forever, so it is excluded by status.

        Asserts on the SQL the reflex actually issued, not on a literal restated
        here — a test that quotes the query back to itself passes no matter what
        the reflex does.
        """
        icdev = wired(_stale_sessions(1))
        _reflex.run({}, None)

        guards = [q for q in icdev.queries if q.upper().startswith("SELECT")]
        assert guards, "reflex must check for an open card before inserting"
        guard = guards[0]
        for terminal in ("done", "failed", "cancelled"):
            assert terminal in guard, f"guard must exclude {terminal!r} cards"
        assert "NOT IN" in guard.upper()
        assert icdev.inserts, "a fresh finding with no open card should promote"


class TestPromotionThreshold:
    def test_low_confidence_findings_are_not_promoted(self, wired):
        """Threshold filtering has to survive the regrouping."""
        icdev = wired(_stale_sessions(54))
        result = _reflex.run({"promotion_threshold": 0.99}, None)

        assert result["details"]["promoted_to_kanban"] == 0
        assert icdev.inserts == []
