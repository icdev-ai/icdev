# CUI // SP-CTI
"""Scheduled memory upkeep reflex — closes the 0%-embedding scheduling gap.

oss2-meas-01 measured 0% embedding coverage: the memory tier's upkeep
(maintenance_cron) was built but never scheduled, so hybrid_search's semantic half
was inert. This reflex wires the non-destructive upkeep (flush + bounded embedding
backfill) onto the daemon schedule.
"""
from __future__ import annotations

import importlib

r = importlib.import_module("tools.genesis.reflexes.memory_maintenance_reflex")


def test_registered_in_daemon_reflex_names():
    from tools.genesis.daemon import REFLEX_NAMES

    assert "memory_maintenance_reflex" in REFLEX_NAMES


def test_reflex_registry_gate_still_passes():
    """My own rri gate must accept the new reflex (module + callable run)."""
    from tools.workflow.coherence_checker import check_reflex_registry

    result = check_reflex_registry()
    assert result.status == "pass", result.message


def test_run_flushes_and_embeds(monkeypatch):
    import tools.memory.maintenance_cron as mc

    monkeypatch.setattr(mc, "flush_buffer", lambda: {"flushed": 3})
    monkeypatch.setattr(
        mc, "embed_unembedded",
        lambda limit=None: {"embedded": 5, "errors": 0, "total_unembedded": 5, "provider": "llm"},
    )
    out = r.run({}, None)
    assert out["flush"] == {"flushed": 3}
    assert out["embed"]["embedded"] == 5


def test_embed_is_bounded_per_run(monkeypatch):
    import tools.memory.maintenance_cron as mc

    captured = {}

    def fake_embed(limit=None):
        captured["limit"] = limit
        return {"embedded": 0}

    monkeypatch.setattr(mc, "flush_buffer", lambda: {})
    monkeypatch.setattr(mc, "embed_unembedded", fake_embed)
    r.run({}, None)
    assert captured["limit"] == r.EMBED_MAX_PER_RUN


def test_degrades_on_no_provider(monkeypatch):
    import tools.memory.maintenance_cron as mc

    monkeypatch.setattr(mc, "flush_buffer", lambda: {})
    monkeypatch.setattr(mc, "embed_unembedded", lambda limit=None: {"embedded": 0, "status": "no_provider"})
    out = r.run({}, None)  # must not raise
    assert out["embed"]["status"] == "no_provider"


def test_survives_a_step_failure(monkeypatch):
    import tools.memory.maintenance_cron as mc

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mc, "flush_buffer", boom)
    monkeypatch.setattr(mc, "embed_unembedded", lambda limit=None: {"embedded": 1})
    out = r.run({}, None)  # one step failing must not abort the reflex
    assert "error" in out["flush"]
    assert out["embed"]["embedded"] == 1


def test_embed_unembedded_limit_adds_a_bounded_select(monkeypatch):
    """The new limit param must produce a bounded SELECT (catch-up over cycles)."""
    import tools.memory.maintenance_cron as mc

    captured = {}

    class _Cur:
        def execute(self, sql, *a):
            captured["sql"] = sql

        def fetchall(self):
            return []  # no rows -> returns all_embedded, no provider work

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(mc, "get_connection", lambda: _Conn())
    mc.embed_unembedded(limit=200)
    assert "LIMIT 200" in captured["sql"]


# ── PG-write path: backend-aware format + systematic-failure diagnosis ────────


def _fake_conn_cursor(select_rows, on_write=None):
    calls = {"writes": 0}

    class _Cur:
        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("SELECT"):
                self._rows = list(select_rows)
            else:
                calls["writes"] += 1
                if on_write:
                    on_write(sql, params)

        def fetchall(self):
            return getattr(self, "_rows", [])

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def rollback(self):
            calls["rollbacks"] = calls.get("rollbacks", 0) + 1

        def close(self):
            pass

    return _Conn(), calls


def test_pg_backend_writes_vector_literal(monkeypatch):
    """On PG the write must use %s::vector with a bracketed literal, not a bytea blob."""
    import tools.memory.maintenance_cron as mc

    seen = {}
    conn, _ = _fake_conn_cursor(
        [(1, "some content")],
        on_write=lambda sql, params: seen.update(sql=sql, params=params),
    )
    monkeypatch.setattr(mc, "get_connection", lambda: conn)
    monkeypatch.setattr(mc, "is_pg", lambda c=None: True)

    class _Prov:
        def embed(self, t):
            return [0.1, 0.2, 0.3]

    monkeypatch.setattr("tools.llm.get_embedding_provider", lambda: _Prov())
    out = mc.embed_unembedded()
    assert "::vector" in seen["sql"]
    assert seen["params"][0].startswith("[") and seen["params"][0].endswith("]")
    assert out["embedded"] == 1
    assert out["backend"] == "postgresql"


def test_systematic_write_failure_aborts_early_with_diagnosis(monkeypatch):
    """A dimension/type mismatch fails every write — the run must abort after the
    first batch (not churn) and surface first_error, so 0% becomes DIAGNOSABLE."""
    import tools.memory.maintenance_cron as mc

    def boom(sql, params):
        raise RuntimeError("expected 1536 dimensions, not 768")

    # 50 rows, batch_size 20 -> would be 3 batches if it didn't abort early
    conn, calls = _fake_conn_cursor([(i, f"c{i}") for i in range(50)], on_write=boom)
    monkeypatch.setattr(mc, "get_connection", lambda: conn)
    monkeypatch.setattr(mc, "is_pg", lambda c=None: True)

    class _Prov:
        def embed(self, t):
            return [0.0] * 768

    monkeypatch.setattr("tools.llm.get_embedding_provider", lambda: _Prov())
    out = mc.embed_unembedded()
    assert out["embedded"] == 0
    assert out["errors"] == 1          # aborted after the first failing batch
    assert "768" in out["first_error"]
    assert calls.get("rollbacks", 0) >= 1
