# CUI // SP-CTI
"""Tests for tools/foundry/meta_scorer.py — ACF adaptive composite threshold (acf-ada-02).

Hermetic: every test uses an in-memory / tmp-path sqlite copy of foundry_config.yaml
and a stubbed ``tools.db.storage.get_connection`` that returns a SQLite cursor over
seeded ``foundry_outcomes`` rows.  No real DB or LLM is touched.

Coverage:

  * acf-ada-02 (1)  adjust_threshold() + propose_removals() exist and return
                    the documented shapes; load_config/save_config round-trip.
  * acf-ada-02 (2)  3 consecutive ``vv_fail`` outcomes raise min_composite by >= 0.05
                    (and the raise is bounded by max_composite).
  * acf-ada-02 (3)  3 consecutive ``shipped`` outcomes lower min_composite by <= 0.05
                    (and the lower is bounded by min_composite_floor).
  * acf-ada-02 (4)  write_proposals() writes to args/meta_harness_proposals.yaml
                    and the file round-trips with the expected ``acf_scorer_retirements``
                    key.
  * acf-ada-02 (+)  sliding-window false-approve rate math is correct (empty / mixed);
                    propose_removals returns [] when FA rate is below ceiling;
                    run_meta_score() end-to-end on a seeded fixture is a single
                    function call (no I/O surprises).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from tools.foundry import meta_scorer


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_config(tmp_path):
    """Return a tmp foundry_config.yaml with sensible defaults + return its Path."""
    cfg = {
        "foundry": {"cadence_hours": 24},
        "scoring": {
            "weights": {
                "novelty": 0.25, "feasibility": 0.25,
                "strategic_fit": 0.25, "market_timing": 0.25,
            },
            "min_composite": 0.6,
        },
        "adaptive": {
            "window": 5,
            "false_approve_ceiling": 0.4,
            "raise_step": 0.05,
            "lower_step": 0.05,
            "max_composite": 0.95,
            "min_composite_floor": 0.30,
            "min_window_for_action": 3,
        },
    }
    p = tmp_path / "foundry_config.yaml"
    p.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return p, cfg


@pytest.fixture
def tmp_proposals(tmp_path):
    """Return a tmp path for meta_harness_proposals.yaml (does not pre-create)."""
    return tmp_path / "meta_harness_proposals.yaml"


def _row(id_, outcome, metric=0.0, concept_id=None):
    """Build a ``foundry_outcomes`` row dict matching the schema."""
    return {
        "id": id_,
        "concept_id": concept_id,
        "outcome": outcome,
        "metric": metric,
        "detail": "{}",
        "created_at": f"2026-06-08T00:00:{id_:02d}Z",
    }


# ── acf-ada-02 (1) public API exists and shapes are correct ──────────────────

def test_load_save_config_round_trips(tmp_config):
    """load_config + save_config preserve the key sections across an I/O round trip."""
    path, original = tmp_config
    loaded = meta_scorer.load_config(path=path)
    assert loaded["scoring"]["min_composite"] == 0.6
    assert loaded["adaptive"]["window"] == 5

    # Mutate + save -> file reflects the new value on a fresh load.
    loaded["scoring"]["min_composite"] = 0.7
    assert meta_scorer.save_config(loaded, path=path) is True

    reloaded = meta_scorer.load_config(path=path)
    assert reloaded["scoring"]["min_composite"] == 0.7


def test_adjust_threshold_returns_documented_shape(tmp_config):
    """adjust_threshold returns the documented fields regardless of action."""
    path, cfg = tmp_config
    outcomes = [_row(i, "shipped") for i in range(3)]
    res = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)

    for key in (
        "action", "reason", "old_min_composite", "new_min_composite",
        "delta", "false_approve_rate", "window_observed",
        "ceiling", "bounded_by", "persisted",
    ):
        assert key in res, f"adjust_threshold() missing {key!r} in result"
    assert res["old_min_composite"] == 0.6
    assert res["false_approve_rate"] == 0.0
    assert res["action"] in ("raise", "lower", "hold")


def test_propose_removals_returns_list():
    """propose_removals always returns a list (possibly empty)."""
    res = meta_scorer.propose_removals(config={"scoring": {"min_composite": 0.6}}, outcomes=[])
    assert res == []


# ── acf-ada-02 (2) 3 consecutive vv_fail → min_composite raised by >= 0.05 ───

def test_three_vv_fail_raise_min_composite_by_at_least_0_05(tmp_config):
    """A 3-vv_fail sliding window must raise min_composite by >= 0.05 in one pass."""
    path, cfg = tmp_config
    # 3 consecutive vv_fail (most-recent first, as the SQL ORDER BY DESC yields).
    outcomes = [_row(i, "vv_fail") for i in range(3)]

    res = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)

    assert res["action"] == "raise", f"expected raise, got {res['action']} (reason={res['reason']!r})"
    assert res["old_min_composite"] == 0.6
    delta = res["new_min_composite"] - res["old_min_composite"]
    assert delta >= 0.05, f"raise delta {delta} < 0.05 (res={res})"
    # And the persisted value on disk is exactly what we returned.
    reloaded = meta_scorer.load_config(path=path)
    assert reloaded["scoring"]["min_composite"] == res["new_min_composite"]


def test_raise_respects_max_composite_bound(tmp_config):
    """Repeated raises must saturate at adaptive.max_composite (no starvation)."""
    path, cfg = tmp_config
    cfg["scoring"]["min_composite"] = 0.93  # already close to the cap
    outcomes = [_row(i, "vv_fail") for i in range(3)]

    res = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)
    assert res["action"] == "raise"
    assert res["new_min_composite"] == 0.95  # capped at max_composite=0.95
    # And a second pass is a hold (already at the cap).
    res2 = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)
    assert res2["action"] == "hold"


def test_raise_requires_min_window_for_action(tmp_config):
    """Below min_window_for_action samples → hold even if every sample is bad."""
    path, cfg = tmp_config
    # Only 2 samples; the gate requires >= 3 to act.
    outcomes = [_row(i, "vv_fail") for i in range(2)]
    res = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)
    assert res["action"] == "hold"
    assert res["new_min_composite"] == res["old_min_composite"] == 0.6


# ── acf-ada-02 (3) 3 consecutive shipped → min_composite lowered by <= 0.05 ─

def test_three_shipped_lower_min_composite_by_at_most_0_05(tmp_config):
    """3 consecutive shipped → lower min_composite by exactly lower_step (0.05)."""
    path, cfg = tmp_config
    outcomes = [_row(i, "shipped") for i in range(3)]

    res = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)

    assert res["action"] == "lower", f"expected lower, got {res['action']} (reason={res['reason']!r})"
    assert res["old_min_composite"] == 0.6
    delta = res["old_min_composite"] - res["new_min_composite"]
    assert 0.0 < delta <= 0.05, f"lower delta {delta} not in (0, 0.05] (res={res})"


def test_lower_respects_min_composite_floor(tmp_config):
    """Repeated lowers must saturate at adaptive.min_composite_floor (no floodgate)."""
    path, cfg = tmp_config
    cfg["scoring"]["min_composite"] = 0.32  # close to the floor
    outcomes = [_row(i, "shipped") for i in range(3)]

    res = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)
    assert res["action"] == "lower"
    assert res["new_min_composite"] == 0.30  # capped at min_composite_floor
    # A second pass holds at the floor.
    res2 = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)
    assert res2["action"] == "hold"


def test_mixed_window_with_low_fa_rate_holds(tmp_config):
    """A window that is not predominantly bad → no action (avoid oscillation)."""
    path, cfg = tmp_config
    # 4 shipped, 1 vv_fail → FA rate 0.2 < 0.4 ceiling → hold.
    outcomes = (
        [_row(0, "vv_fail")]
        + [_row(i, "shipped") for i in range(1, 5)]
    )
    res = meta_scorer.adjust_threshold(config=cfg, outcomes=outcomes, config_path=path)
    assert res["action"] == "hold"
    assert res["new_min_composite"] == 0.6


# ── FA rate math ─────────────────────────────────────────────────────────────

def test_compute_false_approve_rate_empty_is_zero():
    assert meta_scorer.compute_false_approve_rate([]) == 0.0


def test_compute_false_approve_rate_mixed_window():
    outcomes = (
        [_row(0, "vv_fail"), _row(1, "abandoned")]
        + [_row(i, "shipped") for i in range(2, 5)]
    )
    # 2 of 5 are FA → 0.4
    rate = meta_scorer.compute_false_approve_rate(outcomes, window=5)
    assert rate == 0.4


def test_compute_false_approve_rate_window_clips():
    outcomes = (
        [_row(0, "vv_fail"), _row(1, "vv_fail"), _row(2, "vv_fail")]
        + [_row(i, "shipped") for i in range(3, 8)]
    )
    # window=3 → all 3 are FA → 1.0
    rate = meta_scorer.compute_false_approve_rate(outcomes, window=3)
    assert rate == 1.0


# ── acf-ada-02 (4) proposals written to args/meta_harness_proposals.yaml ────

def test_write_proposals_creates_yaml_with_documented_keys(tmp_proposals):
    """write_proposals writes a YAML file with the acf_scorer_retirements key."""
    proposals = [{
        "heuristic_name": "acf-weight-novelty",
        "weight_key": "novelty",
        "score_column": "novelty_score",
        "current_weight": 0.25,
        "proposed_weight": 0.0,
        "proposal": "retire",
        "support": 0.6,
        "fail_mean": 0.30,
        "shipped_mean": 0.80,
        "fail_count": 5,
        "shipped_count": 3,
        "false_approve_rate": 0.5,
        "reason": "test rationale",
    }]
    metrics = {"window": 5, "false_approve_rate": 0.5, "ceiling": 0.4,
               "old_min_composite": 0.6, "new_min_composite": 0.65}

    written = meta_scorer.write_proposals(proposals, metrics, path=tmp_proposals)
    assert written == tmp_proposals
    assert tmp_proposals.exists()

    loaded = yaml.safe_load(tmp_proposals.read_text(encoding="utf-8"))
    assert "generated_at" in loaded
    assert "metrics_snapshot" in loaded
    assert loaded["metrics_snapshot"]["acf_meta_scorer"] == metrics
    assert loaded["acf_scorer_retirements"] == proposals


def test_write_proposals_merges_with_existing_file(tmp_proposals):
    """A pre-existing proposals file is preserved and the ACF block is appended."""
    # Pre-seed with an existing oracle_heuristic_retirements block.
    seed = {
        "generated_at": "2026-06-01T00:00:00Z",
        "oracle_heuristic_retirements": [
            {"heuristic_name": "old-heuristic", "proposal": "retire"}
        ],
    }
    tmp_proposals.write_text(yaml.dump(seed, default_flow_style=False, sort_keys=False), encoding="utf-8")

    new_proposals = [{
        "heuristic_name": "acf-weight-fit", "weight_key": "strategic_fit",
        "current_weight": 0.25, "proposed_weight": 0.0, "proposal": "retire",
    }]
    metrics = {"window": 5, "false_approve_rate": 0.6, "ceiling": 0.4,
               "old_min_composite": 0.6, "new_min_composite": 0.7}

    written = meta_scorer.write_proposals(new_proposals, metrics, path=tmp_proposals)
    assert written == tmp_proposals

    loaded = yaml.safe_load(tmp_proposals.read_text(encoding="utf-8"))
    # Existing block untouched.
    assert loaded["oracle_heuristic_retirements"][0]["heuristic_name"] == "old-heuristic"
    # ACF block appended.
    assert any(p["heuristic_name"] == "acf-weight-fit" for p in loaded["acf_scorer_retirements"])


def test_write_proposals_empty_list_is_noop(tmp_proposals):
    """Empty proposals → no file is created, return value is None."""
    written = meta_scorer.write_proposals([], {}, path=tmp_proposals)
    assert written is None
    assert not tmp_proposals.exists()


# ── propose_removals end-to-end with a stubbed DB connection ────────────────

def test_propose_removals_below_ceiling_returns_empty(tmp_config):
    """FA rate below the ceiling → no removals proposed (no spurious churn)."""
    _, cfg = tmp_config
    # 5 clean outcomes → FA rate 0.0
    outcomes = [_row(i, "shipped") for i in range(5)]
    res = meta_scorer.propose_removals(config=cfg, outcomes=outcomes)
    assert res == []


def test_propose_removals_with_db_above_ceiling(tmp_config, monkeypatch):
    """A failing-only window with distinguishable concept profiles emits proposals."""
    _, cfg = tmp_config

    # Build a tiny in-memory SQLite holding 3 failed + 2 shipped concepts, all
    # with their score columns populated.  Then point get_connection at it.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE foundry_outcomes (
            id INTEGER PRIMARY KEY,
            concept_id INTEGER,
            outcome TEXT,
            metric REAL,
            detail TEXT DEFAULT '{}',
            created_at TEXT
        );
        CREATE TABLE foundry_concepts (
            id INTEGER PRIMARY KEY,
            novelty_score REAL,
            market_score REAL,
            fit_score REAL,
            effort_estimate REAL,
            compliance_risk REAL
        );
    """)
    # Failed concepts: low novelty + market; high effort.
    failed_scores = [(0.20, 0.25, 0.30, 0.80, 0.70),
                     (0.18, 0.22, 0.28, 0.82, 0.75),
                     (0.15, 0.30, 0.32, 0.85, 0.78)]
    # Shipped baseline: healthy scores on all dimensions.
    shipped_scores = [(0.85, 0.80, 0.88, 0.40, 0.30),
                      (0.90, 0.85, 0.90, 0.35, 0.25)]
    for i, s in enumerate(failed_scores):
        cid = i + 1
        conn.execute(
            "INSERT INTO foundry_concepts "
            "(id, novelty_score, market_score, fit_score, effort_estimate, compliance_risk) "
            "VALUES (?, ?, ?, ?, ?, ?)", (cid, *s)
        )
        conn.execute(
            "INSERT INTO foundry_outcomes (concept_id, outcome, metric, created_at) "
            "VALUES (?, 'vv_fail', 0.0, ?)", (cid, f"2026-06-08T00:00:{i:02d}Z")
        )
    for j, s in enumerate(shipped_scores):
        cid = len(failed_scores) + j + 1
        conn.execute(
            "INSERT INTO foundry_concepts "
            "(id, novelty_score, market_score, fit_score, effort_estimate, compliance_risk) "
            "VALUES (?, ?, ?, ?, ?, ?)", (cid, *s)
        )
        conn.execute(
            "INSERT INTO foundry_outcomes (concept_id, outcome, metric, created_at) "
            "VALUES (?, 'shipped', 1.0, ?)", (cid, f"2026-06-07T00:00:{j:02d}Z")
        )
    conn.commit()

    # Build the same 5-row outcomes list the SQL query would return
    # (most-recent first → failed first, shipped second).
    outcomes = [_row(i, "vv_fail", concept_id=i + 1) for i in range(3)] + [
        _row(3 + j, "shipped", concept_id=3 + j + 1) for j in range(2)
    ]

    # Stub the storage module's get_connection to return our conn.  We resolve
    # ``tools.db.storage`` via importlib (not ``import tools.db.storage as ...``)
    # because the canonical shim makes those two imports resolve to different
    # module objects — only importlib.getattr-style patches hit the right one.
    import importlib
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)

    res = meta_scorer.propose_removals(config=cfg, outcomes=outcomes, conn=conn)
    # 3 of 5 are FA → FA rate 0.6 > 0.4 ceiling → proposals are expected.
    assert res, "expected at least one retirement proposal on a failing-only fixture"
    # Each proposal is a dict with the documented keys.
    for p in res:
        assert p["proposal"] == "retire"
        assert p["proposed_weight"] == 0.0
        assert "reason" in p
        assert p["false_approve_rate"] >= 0.4
    # At least one proposal should target a benefit dimension (novelty / market / fit).
    benefit_dims = {"novelty", "market_timing", "strategic_fit"}
    assert any(p["weight_key"] in benefit_dims for p in res), (
        f"expected at least one benefit-dimension proposal, got: {[p['weight_key'] for p in res]}"
    )


# ── end-to-end run_meta_score on a seeded fixture ───────────────────────────

def test_run_meta_score_raise_path_writes_proposals(tmp_path, monkeypatch):
    """The full pass raises the threshold and writes a proposals file."""
    cfg_path = tmp_path / "foundry_config.yaml"
    proposals_path = tmp_path / "meta_harness_proposals.yaml"

    # Seed a config identical to the production defaults.
    seed = {
        "scoring": {
            "weights": {"novelty": 0.25, "feasibility": 0.25,
                        "strategic_fit": 0.25, "market_timing": 0.25},
            "min_composite": 0.6,
        },
        "adaptive": {
            "window": 5, "false_approve_ceiling": 0.4,
            "raise_step": 0.05, "lower_step": 0.05,
            "max_composite": 0.95, "min_composite_floor": 0.30,
            "min_window_for_action": 3,
        },
    }
    cfg_path.write_text(yaml.dump(seed, default_flow_style=False, sort_keys=False), encoding="utf-8")

    # Point load_config/save_config at our tmp copies via the module-level
    # constants.  monkeypatch.setattr handles the "module-global" rewrite that
    # plain `patch` would miss on a shim-rewritten import (see project memory).
    monkeypatch.setattr(meta_scorer, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(meta_scorer, "PROPOSALS_FILE", proposals_path)

    # Seed a sqlite holding 3 vv_fail + 2 shipped concepts.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE foundry_outcomes (
            id INTEGER PRIMARY KEY,
            concept_id INTEGER,
            outcome TEXT,
            metric REAL,
            detail TEXT DEFAULT '{}',
            created_at TEXT
        );
        CREATE TABLE foundry_concepts (
            id INTEGER PRIMARY KEY,
            novelty_score REAL,
            market_score REAL,
            fit_score REAL,
            effort_estimate REAL,
            compliance_risk REAL
        );
    """)
    failed = [(0.20, 0.25, 0.30, 0.80, 0.70),
              (0.18, 0.22, 0.28, 0.82, 0.75),
              (0.15, 0.30, 0.32, 0.85, 0.78)]
    shipped = [(0.85, 0.80, 0.88, 0.40, 0.30),
               (0.90, 0.85, 0.90, 0.35, 0.25)]
    for i, s in enumerate(failed):
        cid = i + 1
        conn.execute(
            "INSERT INTO foundry_concepts "
            "(id, novelty_score, market_score, fit_score, effort_estimate, compliance_risk) "
            "VALUES (?, ?, ?, ?, ?, ?)", (cid, *s)
        )
        conn.execute(
            "INSERT INTO foundry_outcomes (concept_id, outcome, metric, created_at) "
            "VALUES (?, 'vv_fail', 0.0, ?)", (cid, f"2026-06-08T00:00:{i:02d}Z")
        )
    for j, s in enumerate(shipped):
        cid = len(failed) + j + 1
        conn.execute(
            "INSERT INTO foundry_concepts "
            "(id, novelty_score, market_score, fit_score, effort_estimate, compliance_risk) "
            "VALUES (?, ?, ?, ?, ?, ?)", (cid, *s)
        )
        conn.execute(
            "INSERT INTO foundry_outcomes (concept_id, outcome, metric, created_at) "
            "VALUES (?, 'shipped', 1.0, ?)", (cid, f"2026-06-07T00:00:{j:02d}Z")
        )
    conn.commit()

    import importlib
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)

    result = meta_scorer.run_meta_score(dry_run=False, conn=conn)

    assert result["ran"] is True
    assert result["dry_run"] is False
    # Adjust path: raised
    assert result["adjust"]["action"] == "raise"
    assert result["adjust"]["new_min_composite"] >= 0.65
    # Threshold actually persisted to disk
    on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["scoring"]["min_composite"] == result["adjust"]["new_min_composite"]
    # Proposals file written
    assert result["proposals_written"] is True
    assert result["proposals_path"] == str(proposals_path)
    assert proposals_path.exists()
    parsed = yaml.safe_load(proposals_path.read_text(encoding="utf-8"))
    assert "acf_scorer_retirements" in parsed
    assert parsed["acf_scorer_retirements"], "expected at least one proposal on failing-only fixture"


def test_run_meta_score_dry_run_does_not_persist(tmp_path, monkeypatch):
    """A dry_run pass does not touch disk for either the config or the proposals."""
    cfg_path = tmp_path / "foundry_config.yaml"
    proposals_path = tmp_path / "meta_harness_proposals.yaml"
    seed = {
        "scoring": {"min_composite": 0.6},
        "adaptive": {
            "window": 5, "false_approve_ceiling": 0.4,
            "raise_step": 0.05, "lower_step": 0.05,
            "max_composite": 0.95, "min_composite_floor": 0.30,
            "min_window_for_action": 3,
        },
    }
    cfg_path.write_text(yaml.dump(seed, default_flow_style=False, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(meta_scorer, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(meta_scorer, "PROPOSALS_FILE", proposals_path)

    # Minimal outcomes: 3 vv_fail (no concept profiles → no proposals, but the
    # threshold raise should still apply since FA rate is 1.0).
    outcomes = [_row(i, "vv_fail") for i in range(3)]

    # Stub _fetch_outcomes to return our fixture and avoid any DB.
    monkeypatch.setattr(meta_scorer, "_fetch_outcomes",
                        lambda window, conn: outcomes)

    result = meta_scorer.run_meta_score(dry_run=True)

    assert result["dry_run"] is True
    assert result["adjust"]["action"] == "raise"
    assert result["adjust"]["persisted"] is False
    assert result["proposals_written"] is False
    # Disk untouched
    on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["scoring"]["min_composite"] == 0.6
    assert not proposals_path.exists()
