#!/usr/bin/env python3
# CUI // SP-CTI
"""cch-obs-02 — caching that stops working goes red instead of quiet.

cch-tel-01 made the per-call cache counts EXIST. It did not make anything notice
when they change. A provider that was serving cached tokens and stops renders
identically to one that was never enabled — both are zero — which is exactly how
Azure discarded its cached-token count for its entire life with nothing going red.

A detector needs BOTH directions to be a detector, so these tests are organised
that way:

  FIRES        a provider goes from caching to not caching; a hit share collapses;
               a surface configured to bill cached tokens has never once reported
               one.
  DOES NOT     ordinary variation, a swing on traffic too thin to judge, a
               provider whose caching is local and has no billed read to miss,
               and the zeros that predate the recorder itself.

The last of those is the one that decides whether this ships or gets muted. On
the live board every one of 13,073 ai_telemetry rows predates the instrumentation
and holds a BACKFILLED zero; a detector that counted them would file a finding
against every provider on its first run.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

regression = importlib.import_module("tools.cache_savings.regression")
reflex = importlib.import_module("tools.genesis.reflexes.cache_regression_reflex")

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
FLOOR = "2026-01-01T00:00:00+00:00"

TELEMETRY_DDL = """
CREATE TABLE ai_telemetry (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    logged_at TEXT
)
"""

#: ai_telemetry as it was BEFORE cch-tel-01. Held literally so a deployment that
#: never ran the migration is a case these tests actually cover.
LEGACY_DDL = """
CREATE TABLE ai_telemetry (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    logged_at TEXT
)
"""


def _config(**overrides):
    cfg = regression.load_config()
    cfg["instrumented_since"] = FLOOR
    cfg.update(overrides)
    return cfg


def _seed(db_path, provider, *, count, when, input_tokens=1000, cache_read=0,
          cache_creation=0):
    """Insert ``count`` rows for ``provider``, all logged at ``when``."""
    conn = sqlite3.connect(str(db_path))
    rows = [
        (
            f"{provider}-{when.isoformat()}-{i}",
            "model-x",
            provider,
            f"hash{i}",
            input_tokens,
            10,
            cache_creation,
            cache_read,
            "CUI",
            when.isoformat(),
        )
        for i in range(count)
    ]
    conn.executemany(
        "INSERT INTO ai_telemetry (id, model_id, provider, prompt_hash, input_tokens,"
        " output_tokens, cache_creation_input_tokens, cache_read_input_tokens,"
        " classification, logged_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An isolated ai_telemetry with the post-cch-tel-01 shape."""
    path = tmp_path / "telemetry.db"
    conn = sqlite3.connect(str(path))
    conn.execute(TELEMETRY_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    return path


def _open(db_path):
    from tools.db.storage import get_connection

    return get_connection(db_path=str(db_path))


def _detect(db_path, cfg=None, now=NOW):
    conn = _open(db_path)
    try:
        return regression.detect(conn=conn, config=cfg or _config(), window_end=now)
    finally:
        conn.close()


def _verdict(report, provider):
    for p in report["providers"]:
        if p["provider"] == provider:
            return p["verdict"]
    return None


# Window anchors relative to NOW: recent = [NOW-7d, NOW), baseline = [NOW-28d, NOW-7d).
RECENT = NOW - timedelta(days=3)
BASELINE = NOW - timedelta(days=14)


# ===========================================================================
# DIRECTION 1 — the signal FIRES
# ===========================================================================
def test_provider_that_stops_caching_fires_the_stopped_rung(db):
    """Caching in the baseline, exactly zero in the recent window, traffic in both.

    This is the failure the whole card exists to prevent, and the one that is
    invisible in any aggregate: the recent window on its own is indistinguishable
    from a provider that was never asked.
    """
    _seed(db, "anthropic", count=50, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "anthropic", count=50, when=RECENT, input_tokens=1000, cache_read=0)

    report = _detect(db)

    assert _verdict(report, "anthropic") == "stopped"
    assert [f["provider"] for f in report["findings"]] == ["anthropic"]


def test_collapsing_hit_share_fires_the_collapsed_rung(db):
    """Degradation short of a full stop still gets a human."""
    _seed(db, "anthropic", count=50, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "anthropic", count=50, when=RECENT, input_tokens=1960, cache_read=40)

    report = _detect(db)
    finding = report["findings"][0]

    assert finding["verdict"] == "collapsed"
    assert finding["drop_ratio"] >= 0.7
    # Non-zero recent reads: this is NOT the stopped rung wearing another name.
    assert finding["recent_cache_read"] > 0


def test_configured_for_caching_but_never_once_fires_never_cached(db):
    """A billing mechanism, a real sample, and not one cache read in its life."""
    _seed(db, "openai", count=60, when=RECENT, input_tokens=4000, cache_read=0)

    report = _detect(db)

    assert _verdict(report, "openai") == "never_cached"
    assert report["findings"][0]["instrumented_calls"] == 60


def test_a_stop_is_reported_per_provider_not_as_one_aggregate(db):
    """One provider stopping must not be washed out by another still caching."""
    _seed(db, "anthropic", count=50, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "anthropic", count=50, when=RECENT, input_tokens=1000, cache_read=0)
    _seed(db, "bedrock", count=50, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "bedrock", count=50, when=RECENT, input_tokens=600, cache_read=400)

    report = _detect(db)

    assert _verdict(report, "anthropic") == "stopped"
    assert _verdict(report, "bedrock") == regression.VERDICT_HEALTHY
    assert len(report["findings"]) == 1


# ===========================================================================
# DIRECTION 2 — normal variation does NOT fire
# ===========================================================================
def test_normal_variation_does_not_fire(db):
    """A 25% relative dip is ordinary movement, not a regression.

    The threshold is 0.7 because a bounded per-provider token share out of this
    same ledger swings >=30% relative on 29% of historical window pairs and >=50%
    on 9% of them. Firing at either would mean a card most weeks, and a signal
    that fires most weeks is muted within one.
    """
    _seed(db, "anthropic", count=50, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "anthropic", count=50, when=RECENT, input_tokens=700, cache_read=300)

    report = _detect(db)

    assert _verdict(report, "anthropic") == regression.VERDICT_HEALTHY
    assert report["findings"] == []


def test_the_threshold_boundary_is_pinned_on_both_sides(db):
    """Just under the armed drop is silent; just over it fires.

    Pinned in both directions on purpose: a test that only proves the fire case
    passes just as happily against a detector that fires on everything.
    """
    # baseline share 0.40 -> recent share 0.124: a 69% relative drop.
    _seed(db, "anthropic", count=50, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "anthropic", count=50, when=RECENT, input_tokens=876, cache_read=124)
    quiet = _detect(db)
    assert _verdict(quiet, "anthropic") == regression.VERDICT_HEALTHY

    # Same baseline, recent share 0.11: a 72.5% relative drop.
    _seed(db, "bedrock", count=50, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "bedrock", count=50, when=RECENT, input_tokens=890, cache_read=110)
    loud = _detect(db)
    assert _verdict(loud, "bedrock") == "collapsed"


def test_a_swing_on_thin_traffic_does_not_fire(db):
    """Below min_calls a ratio is a rounding artefact, and it is named as one."""
    _seed(db, "anthropic", count=5, when=BASELINE, input_tokens=600, cache_read=400)
    _seed(db, "anthropic", count=5, when=RECENT, input_tokens=1000, cache_read=0)

    report = _detect(db)

    assert report["findings"] == []
    # It HAS cached; the windows are simply too thin to judge. Named as that,
    # not as "never cached" -- those send you to two different investigations.
    assert _verdict(report, "anthropic") == regression.VERDICT_INSUFFICIENT


def test_a_local_mechanism_reporting_zero_forever_is_not_a_finding(db):
    """Ollama reuses its KV cache server-side and bills nothing back.

    A permanent zero there is CORRECT. Reporting it would be the $0.00-as-failure
    blur, and it would fire on the highest-traffic provider on this board.
    """
    _seed(db, "ollama", count=500, when=BASELINE, input_tokens=1000, cache_read=0)
    _seed(db, "ollama", count=500, when=RECENT, input_tokens=1000, cache_read=0)

    report = _detect(db)

    assert _verdict(report, "ollama") == regression.VERDICT_NO_BILLING
    assert report["findings"] == []


def test_a_provider_with_no_mechanism_declared_is_never_a_finding(db):
    """Absent from the map is `unknown`. Guessing is how a detector earns muting."""
    _seed(db, "some-new-vendor", count=200, when=RECENT, input_tokens=1000, cache_read=0)

    report = _detect(db)

    assert _verdict(report, "some-new-vendor") == regression.VERDICT_UNKNOWN_MECH
    assert report["findings"] == []


def test_a_baseline_that_was_barely_caching_is_not_a_collapse(db):
    """Falling from a 1% share is not "caching that worked" stopping."""
    _seed(db, "anthropic", count=50, when=BASELINE, input_tokens=990, cache_read=10)
    _seed(db, "anthropic", count=50, when=RECENT, input_tokens=1000, cache_read=0)

    report = _detect(db)

    assert _verdict(report, "anthropic") != "stopped"
    assert report["findings"] == []


# ===========================================================================
# The zeros that are NOT observations
# ===========================================================================
def test_backfilled_pre_instrumentation_zeros_produce_no_findings(db):
    """The live-board case, and the one that decides whether this ships.

    13,073 rows written before the cache columns existed all hold 0 because the
    migration backfilled them. Counted, they would indict every provider on the
    first run. `never_cached` therefore only sees rows at or after the floor.
    """
    old = NOW - timedelta(days=2)
    _seed(db, "openai", count=500, when=old, input_tokens=4000, cache_read=0)
    cfg = _config()
    cfg["instrumented_since"] = (NOW - timedelta(days=1)).isoformat()

    report = _detect(db, cfg)

    assert report["findings"] == []
    assert _verdict(report, "openai") == regression.VERDICT_TOO_YOUNG


def test_never_cached_is_mute_when_the_floor_cannot_be_established(db):
    """No floor means "zero" has no meaning yet, and it says so by name."""
    _seed(db, "openai", count=500, when=RECENT, input_tokens=4000, cache_read=0)
    cfg = _config()
    cfg["instrumented_since"] = None
    cfg["instrumented_migration"] = None

    report = _detect(db, cfg)

    assert report["findings"] == []
    assert _verdict(report, "openai") == regression.VERDICT_PRE_INSTR


def test_an_empty_ledger_is_unmeasurable_not_a_clean_bill(db):
    """A fresh worktree or ephemeral CI database must not read as healthy."""
    report = _detect(db)

    assert report["status"] == regression.STATUS_UNMEASURABLE
    assert report["reason"] == "no_operating_history"
    assert report["findings"] == []


def test_a_missing_telemetry_table_is_unmeasurable(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    path = tmp_path / "bare.db"
    sqlite3.connect(str(path)).close()

    report = _detect(path)

    assert report["status"] == regression.STATUS_UNMEASURABLE
    assert report["reason"] == "telemetry_table_absent"


def test_a_ledger_without_the_cache_columns_is_unmeasurable(tmp_path, monkeypatch):
    """A deployment that never ran the cch-tel-01 migration records no counts.

    Reporting "no regressions" there would be a false all-clear about a substrate
    that cannot answer the question at all.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.execute(LEGACY_DDL)
    conn.commit()
    conn.close()

    report = _detect(path)

    assert report["status"] == regression.STATUS_UNMEASURABLE
    assert report["reason"] == "cache_columns_absent"


def test_share_of_nothing_is_none_not_zero(db):
    """`None` and `0.0` are the two claims this whole card keeps apart."""
    assert regression.cache_read_share({"cache_read": 0, "input_tokens": 0}) is None
    assert regression.cache_read_share({"cache_read": 0, "input_tokens": 10}) == 0.0
    assert regression.cache_read_share(None) is None


# ===========================================================================
# The reflex — the finding has to reach a human
# ===========================================================================
def _report(findings, status="ok"):
    return {
        "status": status,
        "reason": None if status == "ok" else "no_operating_history",
        "windows": {"baseline_start": "b", "recent_start": "r", "end": "e"},
        "thresholds": regression.load_config()["thresholds"],
        "instrumented_since": FLOOR,
        "instrumented_since_source": "config",
        "findings": findings,
        "providers": findings,
    }


def _finding(provider="anthropic", verdict="stopped"):
    return {
        "provider": provider,
        "verdict": verdict,
        "mechanism": "explicit",
        "recent_calls": 50,
        "baseline_calls": 50,
        "recent_cache_read": 0,
        "baseline_cache_read": 20000,
        "recent_share": 0.0,
        "baseline_share": 0.4,
        "drop_ratio": 1.0,
        "instrumented_calls": 100,
    }


@pytest.fixture
def captured(monkeypatch):
    """Capture what the reflex would seed instead of writing to a board."""
    seen = []

    def _create_tasks(specs):
        seen.append(specs)
        return [s["id"] for s in specs]

    tf = importlib.import_module("tools.kanban.task_factory")
    monkeypatch.setattr(tf, "create_tasks", _create_tasks)
    return seen


def test_reflex_files_one_card_per_finding(monkeypatch, captured):
    monkeypatch.setattr(
        regression, "detect",
        lambda **kw: _report([_finding("anthropic"), _finding("openai", "never_cached")]),
    )

    result = reflex.run({})

    assert result["findings"] == 2
    assert result["cards_filed"] == 2
    titles = [s["title"] for s in captured[0]]
    assert "anthropic stopped reporting cached tokens" in titles
    assert "openai is configured for caching and has never cached" in titles


def test_reflex_card_id_is_deterministic_in_rung_and_provider():
    """A uuid would refile the same finding every cycle; a title match would
    collapse two distinct findings into one. Only a deterministic id does both."""
    a = reflex._card_id("cache-regr-", "stopped", "anthropic")
    assert a == reflex._card_id("cache-regr-", "stopped", "anthropic")
    assert a != reflex._card_id("cache-regr-", "collapsed", "anthropic")
    assert a != reflex._card_id("cache-regr-", "stopped", "bedrock")
    assert a.startswith("cache-regr-")


def test_reflex_card_id_is_not_gate_shaped():
    """`<card>-gate-<n>` makes promote_backlog_to_scheduled drop the row forever."""
    from tools.kanban.gates import is_manual_gate

    for rung in ("stopped", "collapsed", "never_cached"):
        title = reflex._RUNG_TITLE[rung].format(provider="anthropic")
        assert not is_manual_gate(reflex._card_id("cache-regr-", rung, "anthropic"), title)


def test_reflex_files_nothing_when_the_ledger_is_unmeasurable(monkeypatch, captured):
    """Unmeasurable is neither a finding nor an all-clear, and it files neither."""
    monkeypatch.setattr(regression, "detect", lambda **kw: _report([], status="unmeasurable"))

    result = reflex.run({})

    assert result["status"] == "unmeasurable"
    assert result["cards_filed"] == 0
    assert captured == []


def test_reflex_files_nothing_when_there_are_no_findings(monkeypatch, captured):
    monkeypatch.setattr(regression, "detect", lambda **kw: _report([]))

    result = reflex.run({})

    assert result["status"] == "ok"
    assert result["findings"] == 0
    assert captured == []


def test_reflex_dry_run_detects_but_files_nothing(monkeypatch, captured):
    monkeypatch.setattr(regression, "detect", lambda **kw: _report([_finding()]))

    result = reflex.run({"dry_run": True})

    assert result["findings"] == 1
    assert result["cards_filed"] == 0
    assert captured == []


def test_reflex_always_returns_a_success_key(monkeypatch):
    """A reflex result with no `success` key is scored a FAILURE forever, and
    three of those trip the circuit breaker and silence it permanently."""
    monkeypatch.setattr(regression, "detect", lambda **kw: _report([]))
    assert reflex.run({})["success"] is True

    def _boom(**kw):
        raise RuntimeError("telemetry backend down")

    monkeypatch.setattr(regression, "detect", _boom)
    broken = reflex.run({})
    assert broken["success"] is False
    assert broken["status"] == "error"


def test_a_card_write_failure_never_breaks_the_daemon(monkeypatch):
    monkeypatch.setattr(regression, "detect", lambda **kw: _report([_finding()]))
    tf = importlib.import_module("tools.kanban.task_factory")
    monkeypatch.setattr(tf, "create_tasks", lambda specs: (_ for _ in ()).throw(RuntimeError("board down")))

    result = reflex.run({})

    assert result["success"] is True
    assert result["cards_filed"] == 0


def test_the_card_names_the_evidence_a_reader_needs(monkeypatch, captured):
    monkeypatch.setattr(regression, "detect", lambda **kw: _report([_finding()]))

    reflex.run({})
    body = captured[0][0]["description"]

    assert "anthropic" in body
    assert "tools.cache_savings.regression" in body
    assert "args/cache_regression.yaml" in body
    # The card must not invite the reader to silence it.
    assert "Do not close this by widening a threshold" in body


# ===========================================================================
# Declared and CONSUMED — the platform's signature defect
# ===========================================================================
def test_the_reflex_is_actually_dispatched_not_merely_written():
    """A reflex nobody dispatches is ICDEV's signature bug wearing new clothes:
    enabled, importable, catalogued, and never run, with nothing going red."""
    # The runtime list, not a source substring: a name in a comment would satisfy
    # a grep and still never be dispatched.
    daemon = importlib.import_module("tools.genesis.daemon")
    assert "cache_regression_reflex" in daemon.REFLEX_NAMES, (
        "cache_regression_reflex is not in tools.genesis.daemon.REFLEX_NAMES, so "
        "the daemon will never run it"
    )

    import yaml

    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8")
    )
    entry = (cfg.get("reflexes") or {}).get("cache_regression_reflex")
    assert entry, "cache_regression_reflex missing from args/genesis_config.yaml"
    assert entry.get("enabled") is True
    assert entry.get("schedule")


def test_the_declared_success_metric_is_actually_populated(monkeypatch):
    """`daemon._run_reflex_impl_inner` reads `result["metric_value"]` and defaults
    it to 0.0. A reflex that declares a success_metric in genesis_config.yaml and
    never sets that key records 0.0 forever while looking like it reported — the
    declared-but-inert shape one layer down, and it is what this reflex did on its
    first live dispatch (3 providers evaluated, metric recorded 0.0)."""
    import yaml

    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8")
    )
    declared = cfg["reflexes"]["cache_regression_reflex"]["success_metric"]["name"]

    monkeypatch.setattr(regression, "detect", lambda **kw: _report([_finding()]))
    monkeypatch.setattr(
        importlib.import_module("tools.kanban.task_factory"),
        "create_tasks", lambda specs: [s["id"] for s in specs],
    )
    result = reflex.run({})

    assert declared in result
    assert result["metric_value"] == float(result[declared])
    assert isinstance(result["details"], dict)

    # And on every early return, not only the happy path.
    monkeypatch.setattr(regression, "detect", lambda **kw: _report([], status="unmeasurable"))
    assert "metric_value" in reflex.run({})


def test_the_armed_threshold_is_never_loosened_below_what_was_measured():
    """0.7 is the smallest swept drop with a 0.00% false-fire rate on this
    ledger's own history. Lowering it re-introduces the ~9%/week firing that
    gets a signal muted; raising it is allowed, silencing by config is not."""
    cfg = regression.load_config()
    assert cfg["thresholds"]["collapse_drop_ratio"] >= 0.7
    assert cfg["thresholds"]["min_calls_per_window"] >= 20
    assert cfg["thresholds"]["never_cached_min_calls"] >= 50
    assert cfg["enabled"] is True


def test_the_mechanism_map_covers_every_provider_that_reports_cache_tokens():
    """The four adapters that populate the cache fields must each be classified,
    or `never_cached` silently skips the providers it exists to watch."""
    cfg = regression.load_config()
    for provider in ("anthropic", "bedrock", "openai", "azure_openai"):
        assert cfg["mechanisms"].get(provider) in ("explicit", "automatic"), provider
