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
