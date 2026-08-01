# CUI // SP-CTI
"""End-to-end judge -> publish integration for the Pulse blog (nav-intel-09-d5).

``tests/test_nav_intel_09_pulse_judge_gate.py`` already covers the publish gate
with the verdict *pre-seeded* on the post row. This file closes the remaining
gap: driving ``POST /api/pulse/posts/<id>/judge`` first, so the verdict arrives
the way it does in production, and only then attempting to publish.

That ordering is what makes the judge-error scenario testable. The judge route
runs ``evaluate_and_store`` on a daemon thread that swallows every exception
(``tools/dashboard/api/pulse.py``), so a judge failure is indistinguishable from
a judge that never ran: ``judge_color`` is simply left NULL. The publish gate
must then refuse with the "run the judge before publishing" reason rather than
treating the empty verdict as an implicit pass.

Covered here:
  * judge errors -> no verdict written -> publish blocked 409, reason tells the
    editor to run the judge first;
  * judge returns RED -> publish blocked 409;
  * judge returns GREEN / YELLOW -> publish 200;
  * admin force override over a judge-errored (absent-verdict) post publishes
    and writes one immutable ``pulse_publish_audit`` row recording verdict
    ``absent``;
  * an unrecognized color (e.g. "amber") clears the gate -- see the docstring on
    ``test_unrecognized_color_clears_the_gate`` for why that is worth pinning.

Harness mirrors tools/dashboard/auth.py::_auth_before_request (session user_id
-> g.current_user) over a Flask test client, with get_connection() patched to an
isolated SQLite DB in every module that imported it by name -- including
``tools.dashboard.api.pulse`` itself, which the judge route reaches through
``_get_db()``.
"""
from __future__ import annotations

import importlib
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

# --- isolated pulse schema (subset of tools/pulse/db.py PULSE_SCHEMA) ---------
_SCHEMA = """
CREATE TABLE pulse_posts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    body_markdown TEXT,
    readability_score REAL,
    wp_post_id TEXT,
    judge_color TEXT,
    judge_composite REAL,
    judge_combined REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);
CREATE TABLE pulse_publish_audit (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    action TEXT NOT NULL,
    verdict TEXT,
    reviewer TEXT,
    reason TEXT NOT NULL,
    tenant_id TEXT,
    created_at TEXT NOT NULL
);
"""

_USERS = {
    "editor": {"id": "editor", "role": "reviewer", "tenant_id": "t1"},
    "admin": {"id": "admin", "role": "admin", "tenant_id": "t1"},
}

# Modules that did `from ... import get_connection` at load time. The tools/ ->
# icdev.tools/ shim gives each a distinct module object, so both need patching.
_CONN_MODULES = (
    "tools.db.storage",
    "icdev.tools.db.storage",
    "tools.pulse.db",
    "icdev.tools.pulse.db",
    "tools.dashboard.api.pulse",
    "icdev.tools.dashboard.api.pulse",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_post(db_path, post_id, *, judge_color=None, status="approved"):
    """Insert an approved post awaiting judgement."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO pulse_posts (id, title, slug, status, body_markdown, "
        "readability_score, judge_color, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            post_id,
            f"Post {post_id}",
            f"slug-{post_id}",
            status,
            "# Body\n\nSome prose for the judge to score.",
            72.5,
            judge_color,
            _now(),
            _now(),
        ),
    )
    conn.commit()
    conn.close()


def _judge_color_of(db_path, post_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT judge_color FROM pulse_posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def _status_of(db_path, post_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT status FROM pulse_posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()
    return row[0] if row else None


@pytest.fixture
def handed_out_conns():
    """Every raw sqlite connection the patched get_connection() handed to a route.

    Tracked so a test can assert the route closed what it opened (see
    ``test_judge_write_failure_does_not_leak_a_connection``) and so the fixture
    teardown can reclaim anything a route leaked -- an unclosed SQLite handle
    keeps a write lock on the tmp_path DB, which turns the *next* statement into
    a 5s busy-timeout stall rather than an obvious failure.
    """
    return []


@pytest.fixture
def db_path(tmp_path, monkeypatch, handed_out_conns):
    """Isolated SQLite pulse DB, patched into get_connection() across shims."""
    path = tmp_path / "pulse_judge_publish.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    def _get_connection(*_a, **_kw):
        from tools.db.storage import StorageConnection

        raw = sqlite3.connect(str(path), check_same_thread=False)
        raw.row_factory = sqlite3.Row
        handed_out_conns.append(raw)
        return StorageConnection(raw, "sqlite")

    for mod_name in _CONN_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "get_connection", _get_connection, raising=False)

    yield path

    # Explicit teardown: close anything still open so no DB lock (or, on
    # Windows, no open file handle blocking tmp_path cleanup) survives the test.
    for raw in handed_out_conns:
        try:
            raw.close()
        except Exception:
            pass


@pytest.fixture
def sync_threads(monkeypatch):
    """Run the judge route's daemon thread inline so the test is deterministic.

    The route does `import threading` inside the function body, so it resolves
    ``threading.Thread`` at call time from the real module -- patching the
    attribute there is what the route actually sees. That makes the patch
    process-global for the duration of the test, so the stand-in accepts the
    full Thread signature and answers the usual introspection rather than
    breaking any unrelated caller that happens to spawn a thread. (``Timer`` and
    other ``Thread`` subclasses bound their base at class-creation time and are
    unaffected -- notably pytest-timeout's watchdog, which must keep working or
    a hang here would never be reported.)
    """

    class _InlineThread:
        def __init__(
            self, group=None, target=None, name=None, args=(), kwargs=None, *, daemon=None
        ):
            self._target, self._args, self._kwargs = target, args, kwargs or {}
            self.name = name or "inline-thread"
            self.daemon = bool(daemon)
            self._started = False

        def start(self):
            self._started = True
            if self._target:
                self._target(*self._args, **self._kwargs)

        def run(self):
            if self._target:
                self._target(*self._args, **self._kwargs)

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False  # target already ran to completion inside start()

    monkeypatch.setattr(threading, "Thread", _InlineThread)


@pytest.fixture
def client(db_path, monkeypatch):
    """Flask test client with the pulse_api blueprint + simulated auth."""
    from flask import Flask, g, session

    from tools.dashboard.api import pulse as pulse_mod

    # Neutralize the export side effect the publish route triggers post-gate.
    monkeypatch.setattr(
        "tools.pulse.engine.exporter.export_both",
        lambda pid: {"mdx": "x.mdx", "html": "x.html"},
        raising=False,
    )

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(pulse_mod.pulse_api)

    @app.before_request
    def _fake_auth():  # mirrors _auth_before_request
        g.current_user = _USERS.get(session.get("user_id"))

    return app.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _judge(client, post_id):
    return client.post(f"/api/pulse/posts/{post_id}/judge", json={})


def _publish(client, post_id, body=None):
    return client.post(
        f"/api/pulse/posts/{post_id}/publish",
        json=(body if body is not None else {"auto_push": False}),
    )


def _stub_judge(monkeypatch, *, color=None, raises=None):
    """Patch the LLM judge the route imports inside its worker thread.

    The import is `from tools.writing.llm_judge import evaluate_and_store,
    init_judge_db` executed at call time, so patching the attributes on the
    module object is sufficient.
    """
    mod = importlib.import_module("tools.writing.llm_judge")

    def _evaluate_and_store(**_kw):
        if raises is not None:
            raise raises
        return {
            "status": "evaluated",
            "color_rating": {"color": color},
            "composite_score": 81.0,
            "combined_score": 79.0,
        }

    monkeypatch.setattr(mod, "init_judge_db", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(mod, "evaluate_and_store", _evaluate_and_store, raising=False)


# --- scenario 1: judge errors -> no verdict -> publish blocked ----------------

def test_judge_error_leaves_no_verdict_and_publish_says_run_judge_first(
    client, db_path, monkeypatch, sync_threads
):
    """A judge that raises must not leave the post publishable.

    The route swallows the exception on the worker thread, so the only trace is
    an unset ``judge_color``. The gate has to fail closed on that and tell the
    editor to run the judge -- not fall through to a silent publish.
    """
    _stub_judge(monkeypatch, raises=RuntimeError("judge backend unavailable"))
    _seed_post(db_path, "p-jerr")
    _login(client, "editor")

    # The judge route itself still returns 202-style "judging" -- the failure is
    # asynchronous and invisible to the caller. That is exactly why the publish
    # gate, not the judge response, is the enforcement point.
    resp = _judge(client, "p-jerr")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "judging"
    assert _judge_color_of(db_path, "p-jerr") is None

    resp = _publish(client, "p-jerr")
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["blocked"] is True
    assert data["verdict"] is None
    assert "run the" in data["reason"].lower() and "judge" in data["reason"].lower()
    assert _status_of(db_path, "p-jerr") == "approved"  # never flipped


def test_judge_non_evaluated_result_leaves_no_verdict(client, db_path, monkeypatch, sync_threads):
    """A judge returning a non-``evaluated`` status writes nothing -> still blocked."""
    mod = importlib.import_module("tools.writing.llm_judge")
    monkeypatch.setattr(mod, "init_judge_db", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(
        mod, "evaluate_and_store", lambda **_kw: {"status": "skipped"}, raising=False
    )
    _seed_post(db_path, "p-skip")
    _login(client, "editor")

    _judge(client, "p-skip")
    assert _judge_color_of(db_path, "p-skip") is None

    resp = _publish(client, "p-skip")
    assert resp.status_code == 409
    assert resp.get_json()["blocked"] is True


# --- scenario 2: judge -> RED -> blocked --------------------------------------

def test_judge_red_then_publish_blocked(client, db_path, monkeypatch, sync_threads):
    _stub_judge(monkeypatch, color="red")
    _seed_post(db_path, "p-red")
    _login(client, "editor")

    _judge(client, "p-red")
    assert _judge_color_of(db_path, "p-red") == "red"

    resp = _publish(client, "p-red")
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["blocked"] is True and data["verdict"] == "red"
    assert _status_of(db_path, "p-red") == "approved"


# --- scenario 3/4: judge -> GREEN / YELLOW -> publishes -----------------------

@pytest.mark.parametrize("color", ["green", "yellow"])
def test_judge_clearing_color_then_publish_succeeds(
    client, db_path, monkeypatch, sync_threads, color
):
    _stub_judge(monkeypatch, color=color)
    post_id = f"p-{color}"
    _seed_post(db_path, post_id)
    _login(client, "editor")

    _judge(client, post_id)
    assert _judge_color_of(db_path, post_id) == color

    resp = _publish(client, post_id)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "published"
    assert data["forced"] is False
    assert data["verdict"] == color
    assert _status_of(db_path, post_id) == "published"


def test_unrecognized_color_clears_the_gate(client, db_path, monkeypatch, sync_threads):
    """Pins current behavior: a color outside JUDGE_COLORS publishes.

    The gate is fail-closed on an *empty* verdict but fail-open on an
    *unrecognized* one -- only the literal string "red" blocks. So a judge that
    emits e.g. "amber" (not in the FAR 15.305 vocabulary the rest of the module
    documents) clears. This test documents the behavior rather than endorsing
    it; if the gate is ever tightened to allowlist JUDGE_COLORS, this is the
    test that should flip.
    """
    _stub_judge(monkeypatch, color="amber")
    _seed_post(db_path, "p-amber")
    _login(client, "editor")

    _judge(client, "p-amber")
    resp = _publish(client, "p-amber")
    assert resp.status_code == 200
    assert resp.get_json()["verdict"] == "amber"


# --- scenario 5: audited force override over a judge-errored post -------------

def test_force_publish_over_judge_error_is_audited(client, db_path, monkeypatch, sync_threads):
    """Admin override on an absent verdict publishes and records ``absent``."""
    _stub_judge(monkeypatch, raises=RuntimeError("judge backend unavailable"))
    _seed_post(db_path, "p-force")
    _login(client, "admin")

    _judge(client, "p-force")
    assert _judge_color_of(db_path, "p-force") is None

    resp = _publish(
        client,
        "p-force",
        {"auto_push": False, "force_publish": True, "force_reason": "time-sensitive advisory"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "published" and data["forced"] is True

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT post_id, action, verdict, reviewer, reason, tenant_id FROM pulse_publish_audit"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    post_id, action, verdict, reviewer, reason, tenant_id = rows[0]
    assert post_id == "p-force"
    assert action == "force_publish"
    # No verdict on record -> the audit row says so explicitly rather than NULL.
    assert verdict == "absent"
    assert reviewer == "admin"
    assert reason == "time-sensitive advisory"
    assert tenant_id == "t1"
    assert _status_of(db_path, "p-force") == "published"


# --- scenario 6: the judge worker must not leak its DB connection -------------

def _is_closed(raw_conn) -> bool:
    try:
        raw_conn.execute("SELECT 1")
        return False
    except sqlite3.ProgrammingError:
        return True


def test_judge_write_failure_does_not_leak_a_connection(
    client, db_path, monkeypatch, sync_threads, handed_out_conns
):
    """The judge worker opens a second connection to write the verdict. If the
    write raises, the bare ``except Exception: pass`` swallows it -- so without a
    ``finally`` the connection is never closed and nothing reports it.

    Under SQLite that orphan holds a write lock and the next statement stalls on
    the busy timeout; under PostgreSQL it is an idle-in-transaction backend that
    is never reclaimed, so a repeatedly-failing judge drains the pool until every
    pulse route times out. This asserts the worker closes what it opened even on
    the failure path.

    The stubbed judge reports ``evaluated`` (clearing the status check) but omits
    ``composite_score``, so the UPDATE argument tuple raises KeyError *after* the
    connection is open and before the close -- exactly the window that leaked.
    """
    mod = importlib.import_module("tools.writing.llm_judge")
    monkeypatch.setattr(mod, "init_judge_db", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(
        mod,
        "evaluate_and_store",
        lambda **_kw: {"status": "evaluated", "color_rating": {"color": "green"}},
        raising=False,
    )
    _seed_post(db_path, "p-leak")
    _login(client, "editor")

    resp = _judge(client, "p-leak")
    assert resp.status_code == 200  # failure stays invisible to the caller

    assert handed_out_conns, "expected the judge route to open at least one connection"
    open_conns = [c for c in handed_out_conns if not _is_closed(c)]
    assert not open_conns, f"judge worker leaked {len(open_conns)} connection(s) on the write-failure path"

    # The verdict never landed, so the publish gate must still fail closed.
    assert _judge_color_of(db_path, "p-leak") is None
    assert _publish(client, "p-leak").status_code == 409


def test_cleared_publish_writes_no_override_audit_row(client, db_path, monkeypatch, sync_threads):
    """The audit table records overrides only -- a normal publish stays out of it."""
    _stub_judge(monkeypatch, color="green")
    _seed_post(db_path, "p-clean")
    _login(client, "editor")

    _judge(client, "p-clean")
    assert _publish(client, "p-clean").status_code == 200

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM pulse_publish_audit").fetchone()[0]
    conn.close()
    assert count == 0
