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


# ---------------------------------------------------------------------------
# Draft protocol plans — the second, independent re-raise path.
#
# Check #3 used to query mc_net_protocol_plans with no join to the parent
# session, so closing a session did not close it: an archived session holding a
# leftover 'draft' plan kept minting a card every cycle forever.  Confirmed live
# on nmig-ee617c955aaf, which was archived and still owned a draft vlan plan.
#
# These run against a real SQLite file rather than a fake, so the WHERE clause
# is actually evaluated by a SQL engine — a fake that ignores the query would
# pass no matter which rows the reflex asked for.
# ---------------------------------------------------------------------------

_OLD = "2026-06-18T02:32:45.743783+00:00"  # comfortably past _STALE_PLAN_DAYS


@pytest.fixture
def canvas_db(tmp_path, monkeypatch):
    """A real sqlite canvas DB wired into the reflex, seeded per test."""
    db = tmp_path / "migration_canvas.db"
    monkeypatch.setenv("MC_DB_PATH", str(db))
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    # The promotion block opens a real icdev connection even when it has nothing
    # to promote; point it at a throwaway file rather than the repo database.
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))

    init_db = importlib.import_module("tools.migration_canvas.db.init_db")
    with init_db.get_connection() as conn:
        conn.execute(
            "CREATE TABLE mc_net_sessions (id TEXT PRIMARY KEY, src_model TEXT, "
            "tgt_model TEXT, status TEXT, updated_at TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE mc_net_protocol_plans (id TEXT PRIMARY KEY, session_id TEXT, "
            "protocol TEXT, status TEXT, created_at TEXT)"
        )
        conn.commit()

    def _seed(session_status, *, plan_status="draft", orphan=False):
        with init_db.get_connection() as conn:
            if not orphan:
                conn.execute(
                    "INSERT INTO mc_net_sessions (id, src_model, tgt_model, status, "
                    "updated_at, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                    ("nmig-ee617c955aaf", "MX204", "ASR-9901", session_status, _OLD, _OLD),
                )
            conn.execute(
                "INSERT INTO mc_net_protocol_plans (id, session_id, protocol, status, "
                "created_at) VALUES (%s,%s,%s,%s,%s)",
                ("plan-1", "nmig-ee617c955aaf", "vlan", plan_status, _OLD),
            )
            conn.commit()

    return _seed


@pytest.fixture
def plan_findings(canvas_db, monkeypatch):
    """Seed the canvas DB, stub the other two checks, return plan findings."""

    def _run(session_status, **kw):
        canvas_db(session_status, **kw)
        # Only the EOL check is faked out here.  The canvas connection is left
        # real: get_canvas_connection routes through storage.get_connection, so
        # patching that would swap the sqlite DB this test depends on for a fake.
        netmig = importlib.import_module("tools.migration_canvas.network_migration")
        monkeypatch.setattr(netmig, "_mc_conn", _cm(FakeConn()))
        monkeypatch.setattr(netmig, "_nc_conn", _cm(FakeConn({"FROM ni_devices": []})))
        # promotion_threshold above 1.0 keeps this about detection only
        result = _reflex.run({"promotion_threshold": 2.0}, None)
        plan_errors = [e for e in result["details"]["errors"] if "protocol_plan" in e]
        assert not plan_errors, plan_errors
        return result["details"]["breakdown"]["stale_protocol_plans"]

    return _run


class TestDraftPlansOnClosedSessions:
    @pytest.mark.parametrize("terminal", _reflex._TERMINAL_SESSION_STATUSES)
    def test_terminal_session_stops_its_draft_plan_re_raising(self, plan_findings, terminal):
        """Closing the session is what the wizard's new control does; honour it."""
        assert plan_findings(terminal) == 0

    def test_open_session_still_raises_its_draft_plan(self, plan_findings):
        """The filter must not silence the finding it exists to report."""
        assert plan_findings("in_progress") == 1

    def test_orphaned_plan_does_not_raise(self, plan_findings):
        """No parent session means nobody to action the card."""
        assert plan_findings("in_progress", orphan=True) == 0

    def test_approved_plan_on_open_session_does_not_raise(self, plan_findings):
        """Only 'draft' plans are stale; the status filter must still apply."""
        assert plan_findings("in_progress", plan_status="approved") == 0
