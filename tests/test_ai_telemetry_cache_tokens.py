#!/usr/bin/env python3
# CUI // SP-CTI
"""cch-tel-01 — prompt-cache tokens are recorded durably, per call.

``LLMResponse`` has carried ``cache_creation_input_tokens`` /
``cache_read_input_tokens`` since D-CACHE-10 and four provider adapters
populate them, but no durable table recorded them per call, so every claim
about prompt caching on this platform was unfalsifiable.

The fix extends ``ai_telemetry`` — the per-call ledger the router ALREADY
writes through ``LLMRouter._log_telemetry``, which every routing path funnels
into. These tests hold that seam:

* the shipped DDL and the INSERT agree (a column named in the INSERT but absent
  from the live schema raises inside a broad ``except`` and the ledger silently
  stops filling — the module_budget_usage failure);
* a value the provider reported reaches the row;
* **a call that returned ZERO cached tokens is recorded as 0, not skipped** —
  absent and zero must not be the same, or a provider that stopped caching
  looks identical to one that was never asked;
* the migration adds the columns to a table that already exists, and is
  idempotent.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MIGRATION_DIR = (
    REPO_ROOT / "tools" / "db" / "migrations" / "20260816135136_ai_telemetry_cache_tokens"
)

CACHE_COLUMNS = ("cache_creation_input_tokens", "cache_read_input_tokens")

# The shape ai_telemetry had BEFORE this change, used to prove the migration
# alters a pre-existing table. Held literally on purpose: reading it from
# today's DDL would make the "before" move with the "after".
_LEGACY_DDL = """
CREATE TABLE ai_telemetry (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    response_hash TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    thinking_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    cost_usd REAL,
    agent_id TEXT,
    user_id TEXT,
    project_id TEXT,
    function TEXT,
    api_key_source TEXT,
    injection_scan_result TEXT,
    classification TEXT DEFAULT 'CUI',
    logged_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _shipped_ai_telemetry_ddl() -> str:
    """The real CREATE TABLE for ai_telemetry, read out of the shipped DDL.

    Read rather than copied so that deleting the columns from
    tools/db/init_icdev_db.py fails these tests instead of leaving a private
    copy agreeing with itself.
    """
    source = (REPO_ROOT / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS ai_telemetry \(.*?\n\);",
        source,
        re.DOTALL,
    )
    assert match, "ai_telemetry DDL not found in tools/db/init_icdev_db.py"
    return match.group(0)


def _fresh_db(tmp_path: Path) -> Path:
    """A database whose ai_telemetry came from the shipped DDL."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_shipped_ai_telemetry_ddl())
    conn.commit()
    conn.close()
    return db_path


def _columns(db_path: Path, table: str = "ai_telemetry") -> list:
    conn = sqlite3.connect(str(db_path))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _load_migration(name: str):
    """Load up.py / down.py exactly as MigrationRunner does — standalone, no package."""
    path = MIGRATION_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"migration_cchtel01_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    """A fresh telemetry DB with every module alias of the logger pointed at it.

    ``tools.security.ai_telemetry_logger`` and
    ``icdev.tools.security.ai_telemetry_logger`` can resolve to DISTINCT module
    objects through the compat shim, and the router imports by the ``tools.``
    name. Patching one alias only would leave the other reading the live board.
    """
    db_path = _fresh_db(tmp_path)
    patched = 0
    for dotted in (
        "tools.security.ai_telemetry_logger",
        "icdev.tools.security.ai_telemetry_logger",
    ):
        try:
            mod = importlib.import_module(dotted)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "DB_PATH", db_path, raising=False)
        patched += 1
    assert patched, "could not import the telemetry logger under any alias"
    return db_path


def _rows(db_path: Path) -> list:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ai_telemetry")]
    finally:
        conn.close()


class _FakeResponse:
    """Minimum of LLMResponse that _log_telemetry reads."""

    def __init__(self, cache_creation=0, cache_read=0, **extra):
        self.content = "ok"
        self.model_id = "test-model"
        self.input_tokens = 100
        self.output_tokens = 20
        self.thinking_tokens = 0
        self.cost_usd = 0.0
        self.cache_creation_input_tokens = cache_creation
        self.cache_read_input_tokens = cache_read
        for key, value in extra.items():
            setattr(self, key, value)


class _FakeRequest:
    messages = [{"role": "user", "content": "hello"}]
    project_id = None
    api_key_source = "system"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_shipped_ddl_declares_the_cache_columns(tmp_path):
    """A database created from scratch has the columns without any migration."""
    columns = _columns(_fresh_db(tmp_path))
    for column in CACHE_COLUMNS:
        assert column in columns, f"{column} missing from the shipped ai_telemetry DDL"


def test_migration_adds_the_columns_to_a_pre_existing_table(tmp_path):
    """CREATE TABLE IF NOT EXISTS never alters an existing table — the migration must."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_LEGACY_DDL)
    conn.commit()

    assert not set(CACHE_COLUMNS) & set(_columns(db_path)), "fixture is not the legacy shape"

    result = _load_migration("up").up(conn)
    conn.close()

    assert result["status"] == "applied"
    assert sorted(result["added"]) == sorted(CACHE_COLUMNS)
    assert set(CACHE_COLUMNS).issubset(set(_columns(db_path)))


def test_migration_is_idempotent(tmp_path):
    """A re-run adds nothing and does not raise (duplicate-column would)."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_LEGACY_DDL)
    conn.commit()

    up = _load_migration("up").up
    up(conn)
    second = up(conn)
    conn.close()

    assert second["added"] == []
    assert sorted(second["skipped"]) == sorted(CACHE_COLUMNS)


def test_migration_skips_a_database_without_the_table(tmp_path):
    """A DB that never created ai_telemetry is not an error — the DDL covers it."""
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    result = _load_migration("up").up(conn)
    conn.close()
    assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


def test_logger_records_reported_cache_tokens(telemetry_db):
    """The counts the provider reported land in the row."""
    from tools.security.ai_telemetry_logger import AITelemetryLogger

    entry_id = AITelemetryLogger(db_path=telemetry_db).log_ai_interaction(
        model_id="test-model",
        provider="anthropic",
        prompt_hash="abc",
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=1234,
        cache_read_input_tokens=5678,
        function="code_generation",
    )

    assert entry_id, "INSERT failed — a column named in it is not in the live schema"
    rows = _rows(telemetry_db)
    assert len(rows) == 1
    assert rows[0]["cache_creation_input_tokens"] == 1234
    assert rows[0]["cache_read_input_tokens"] == 5678


def test_zero_cached_tokens_is_recorded_as_zero_not_skipped(telemetry_db):
    """ACCEPTANCE — absent and zero must not be the same value.

    A provider that stopped serving cached tokens has to be distinguishable
    from one that was never asked. So a call reporting 0 writes a row, and the
    columns hold integer 0 — never NULL, never a missing row.
    """
    from tools.security.ai_telemetry_logger import AITelemetryLogger

    entry_id = AITelemetryLogger(db_path=telemetry_db).log_ai_interaction(
        model_id="test-model",
        provider="ollama",
        prompt_hash="abc",
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        function="code_generation",
    )

    assert entry_id, "a zero-cache call must still be recorded"
    rows = _rows(telemetry_db)
    assert len(rows) == 1, "the row was skipped — zero collapsed into absent"
    for column in CACHE_COLUMNS:
        assert rows[0][column] is not None, f"{column} is NULL — that reads as 'nobody looked'"
        assert rows[0][column] == 0


def test_caller_that_omits_the_arguments_still_records_zero(telemetry_db):
    """A pre-existing call site that never passes the kwargs records 0, not NULL."""
    from tools.security.ai_telemetry_logger import AITelemetryLogger

    AITelemetryLogger(db_path=telemetry_db).log_ai_interaction(
        model_id="test-model", provider="ollama", prompt_hash="abc",
    )

    row = _rows(telemetry_db)[0]
    for column in CACHE_COLUMNS:
        assert row[column] == 0


# ---------------------------------------------------------------------------
# The router seam — the single place that knows a call happened
# ---------------------------------------------------------------------------


def test_router_telemetry_seam_forwards_cache_tokens(telemetry_db):
    """_log_telemetry carries the response's cache counts into the ledger.

    Called unbound: the method reads nothing off ``self``, and constructing a
    real LLMRouter would load provider config this test does not need.
    """
    from tools.llm.router import LLMRouter

    LLMRouter._log_telemetry(
        None,
        function="code_generation",
        request=_FakeRequest(),
        response=_FakeResponse(cache_creation=4096, cache_read=8192),
        model_id="test-model",
        provider_name="anthropic",
        latency_ms=42,
    )

    rows = _rows(telemetry_db)
    assert len(rows) == 1, "the router seam wrote nothing"
    assert rows[0]["cache_read_input_tokens"] == 8192
    assert rows[0]["cache_creation_input_tokens"] == 4096
    assert rows[0]["input_tokens"] == 100


def test_router_seam_records_zero_for_a_non_caching_provider(telemetry_db):
    """ACCEPTANCE, through the router — a 0-cache call is a row holding 0."""
    from tools.llm.router import LLMRouter

    LLMRouter._log_telemetry(
        None,
        function="code_generation",
        request=_FakeRequest(),
        response=_FakeResponse(cache_creation=0, cache_read=0),
        model_id="test-model",
        provider_name="ollama",
        latency_ms=42,
    )

    rows = _rows(telemetry_db)
    assert len(rows) == 1, "a non-caching provider's call was not recorded at all"
    assert rows[0]["cache_creation_input_tokens"] == 0
    assert rows[0]["cache_read_input_tokens"] == 0


def test_router_seam_records_zero_when_the_response_lacks_the_fields(telemetry_db):
    """An aggregate response (chain orchestrator) has no cache attrs — still 0, still a row."""
    from tools.llm.router import LLMRouter

    class _NoCacheFields:
        content = "ok"
        model_id = "chain"
        input_tokens = 10
        output_tokens = 5

    LLMRouter._log_telemetry(
        None,
        function="code_generation",
        request=_FakeRequest(),
        response=_NoCacheFields(),
        model_id="chain",
        provider_name="chain_orchestrator",
        latency_ms=7,
    )

    rows = _rows(telemetry_db)
    assert len(rows) == 1
    assert rows[0]["cache_creation_input_tokens"] == 0
    assert rows[0]["cache_read_input_tokens"] == 0


def test_provider_reported_none_is_recorded_as_zero(telemetry_db):
    """A provider that sets the field to None records 0 — never NULL."""
    from tools.llm.router import LLMRouter

    LLMRouter._log_telemetry(
        None,
        function="code_generation",
        request=_FakeRequest(),
        response=_FakeResponse(cache_creation=None, cache_read=None),
        model_id="test-model",
        provider_name="openai",
        latency_ms=11,
    )

    row = _rows(telemetry_db)[0]
    for column in CACHE_COLUMNS:
        assert row[column] == 0


# ---------------------------------------------------------------------------
# The two-tier path — a real provider call that recorded NOTHING
# ---------------------------------------------------------------------------


def _stub_direct_router(response):
    """An LLMRouter with just enough wired to exercise _invoke_model_direct.

    ``__new__`` skips ``__init__`` deliberately: this exercises one method's
    recording behaviour and must not depend on provider config or the network.
    """
    from tools.llm.router import LLMRouter

    router = LLMRouter.__new__(LLMRouter)
    router._get_model_config = lambda name: {"provider": "anthropic", "model_id": "test-model"}
    router._get_provider = lambda name: object()
    router._provider_invoke = lambda *a, **kw: response
    return router


def test_invoke_model_direct_records_the_call(telemetry_db):
    """Two-tier's real provider calls reach the ledger.

    Measured 2026-08-16 before this change: two_tier.enabled is true and
    code_generation is a worker function, so router.invoke() returned from
    _maybe_invoke_two_tier without ever reaching _log_telemetry and wrote ZERO
    rows for a call that really hit a provider.
    """
    router = _stub_direct_router(_FakeResponse(cache_creation=64, cache_read=128))

    out = router._invoke_model_direct(
        "claude-sonnet", _FakeRequest(), telemetry_function="code_generation"
    )

    assert out is not None
    rows = _rows(telemetry_db)
    assert len(rows) == 1, "a real two-tier provider call recorded nothing"
    assert rows[0]["function"] == "code_generation"
    assert rows[0]["cache_creation_input_tokens"] == 64
    assert rows[0]["cache_read_input_tokens"] == 128


def test_invoke_model_direct_records_zero_cache_for_a_local_model(telemetry_db):
    """The draft half of two-tier is local (qwen3) and caches nothing — still a row of 0s."""
    router = _stub_direct_router(_FakeResponse(cache_creation=0, cache_read=0))

    router._invoke_model_direct(
        "qwen3-local", _FakeRequest(), telemetry_function="code_generation"
    )

    rows = _rows(telemetry_db)
    assert len(rows) == 1
    assert rows[0]["cache_creation_input_tokens"] == 0
    assert rows[0]["cache_read_input_tokens"] == 0


def test_telemetry_function_label_does_not_arm_the_routing_policy(telemetry_db):
    """`telemetry_function` labels the row; only `function` reaches _provider_invoke.

    Relabelling a ledger row must never change which provider a request is
    allowed to reach — force_local is a routing decision, not a telemetry one.
    """
    router = _stub_direct_router(_FakeResponse())
    seen = {}
    router._provider_invoke = lambda *a, **kw: (
        seen.update(kw) or _FakeResponse()
    )

    router._invoke_model_direct(
        "claude-sonnet", _FakeRequest(), telemetry_function="code_generation"
    )

    assert seen.get("function") == "", "telemetry label leaked into the routing policy argument"
    assert _rows(telemetry_db)[0]["function"] == "code_generation"


def test_telemetry_failure_never_fails_the_call(telemetry_db, monkeypatch):
    """A broken ledger must not take down a served LLM response."""
    import tools.security.ai_telemetry_logger as tl

    def _boom(*a, **kw):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(tl.AITelemetryLogger, "log_ai_interaction", _boom)
    router = _stub_direct_router(_FakeResponse())

    out = router._invoke_model_direct(
        "claude-sonnet", _FakeRequest(), telemetry_function="code_generation"
    )

    assert out is not None, "a telemetry failure swallowed the response"


def test_every_recorded_call_is_distinguishable_by_cache_behaviour(telemetry_db):
    """The point of the whole change: caching claims become falsifiable.

    Two calls, one served from cache and one not, are separable in SQL. Before
    this change both rows were byte-identical on the cache dimension because
    the dimension did not exist.
    """
    from tools.llm.router import LLMRouter

    for cached in (0, 9000):
        LLMRouter._log_telemetry(
            None,
            function="code_generation",
            request=_FakeRequest(),
            response=_FakeResponse(cache_read=cached),
            model_id=f"m-{uuid.uuid4().hex[:6]}",
            provider_name="anthropic",
            latency_ms=1,
        )

    conn = sqlite3.connect(str(telemetry_db))
    try:
        served, total = conn.execute(
            "SELECT SUM(cache_read_input_tokens > 0), COUNT(*) FROM ai_telemetry"
        ).fetchone()
    finally:
        conn.close()

    assert total == 2
    assert served == 1
