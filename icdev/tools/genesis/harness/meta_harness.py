# CUI // SP-CTI
"""Meta-Harness — daily outer loop that proposes structural amendments when metrics degrade.

Two concerns:
1. Oracle heuristic retirement: when precision < PRECISION_HARD_FLOOR, identify
   heuristics that appear frequently in error cases and propose retiring them.
2. Heal constitution tightening: when false_heal_rate > FALSE_HEAL_CEILING, propose
   raising min_confidence floors for the offending resolution types.

Runs once per day (UTC); the harness reflex calls should_run_today() before
invoking run_meta_review(). State is persisted in .tmp/meta_harness_last_run.txt.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

LOG = get_logger(__name__)

# Last-resort fallback thresholds — active only when both _AnomalyDetector AND
# genesis_config.yaml harness.gates are unavailable.  Values are kept aligned with
# harness.gates in genesis_config.yaml so the fallback never silently diverges.
# Adaptive thresholds are computed at runtime by _get_adaptive_thresholds();
# config-driven values are loaded by _load_meta_config_gates().
PRECISION_HARD_FLOOR = 0.80   # aligned with harness.gates.precision_min
FALSE_HEAL_CEILING = 0.20     # aligned with harness.gates.false_heal_max

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
CONSTITUTION_PATH = BASE_DIR / "args" / "heal_constitution.yaml"
META_PROPOSALS_PATH = BASE_DIR / "args" / "meta_harness_proposals.yaml"
META_STATE_PATH = BASE_DIR / ".tmp" / "meta_harness_last_run.txt"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_str() -> str:
    return _utcnow().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# State management — once-per-day gate
# ---------------------------------------------------------------------------

def should_run_today() -> bool:
    """Return True if meta-harness has not run today (UTC date)."""
    today = _utcnow().date().isoformat()
    try:
        if META_STATE_PATH.exists():
            last = META_STATE_PATH.read_text(encoding="utf-8").strip()
            if last == today:
                return False
    except OSError:
        pass
    return True


def _mark_ran_today() -> None:
    try:
        META_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        META_STATE_PATH.write_text(_utcnow().date().isoformat(), encoding="utf-8", newline="")
    except OSError as exc:
        LOG.warning("[meta_harness] Could not write state file: %s", exc)


# ---------------------------------------------------------------------------
# Config-gate loading — reads harness.gates from genesis_config.yaml
# ---------------------------------------------------------------------------

def _find_meta_config_path() -> "Path | None":
    """Walk up from this file's location to find args/genesis_config.yaml."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "args" / "genesis_config.yaml"
        if candidate.exists():
            return candidate
    return None


def _load_meta_config_gates() -> dict[str, float]:
    """Return harness.gates thresholds from genesis_config.yaml.

    Falls back to PRECISION_HARD_FLOOR / FALSE_HEAL_CEILING when config is
    absent or unreadable, so the caller always receives numeric values.
    Used as the second-tier fallback in _get_adaptive_thresholds() — after
    _AnomalyDetector, before the module-level constants.
    """
    config_path = _find_meta_config_path()
    if config_path is None:
        return {"precision_min": PRECISION_HARD_FLOOR, "false_heal_max": FALSE_HEAL_CEILING}
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        gates = cfg.get("harness", {}).get("gates", {})
        return {
            "precision_min": float(gates.get("precision_min", PRECISION_HARD_FLOOR)),
            "false_heal_max": float(gates.get("false_heal_max", FALSE_HEAL_CEILING)),
        }
    except Exception as exc:
        LOG.debug("[meta_harness] config gates load failed: %s", exc)
        return {"precision_min": PRECISION_HARD_FLOOR, "false_heal_max": FALSE_HEAL_CEILING}


# ---------------------------------------------------------------------------
# Adaptive threshold resolution
# ---------------------------------------------------------------------------

def _get_adaptive_thresholds() -> tuple[float, float]:
    """Return (precision_floor, false_heal_ceiling) via anomaly detection.

    Delegates to _AnomalyDetector from eval_harness when sufficient historical
    data exists; falls back to module-level static constants otherwise.
    """
    try:
        from tools.genesis.harness.eval_harness import _AnomalyDetector
        detector = _AnomalyDetector()
        oracle_thresh = detector.get_thresholds("oracle_triage")
        heal_thresh = detector.get_thresholds("heal")
        precision_floor = oracle_thresh.get("precision_min", PRECISION_HARD_FLOOR)
        false_heal_ceiling = heal_thresh.get("false_heal_max", FALSE_HEAL_CEILING)
        LOG.debug(
            "[meta_harness] adaptive thresholds: precision_floor=%.3f false_heal_ceiling=%.3f",
            precision_floor, false_heal_ceiling,
        )
        return precision_floor, false_heal_ceiling
    except Exception as exc:
        LOG.debug("[meta_harness] anomaly detector unavailable, falling back to config gates: %s", exc)
        gates = _load_meta_config_gates()
        return gates["precision_min"], gates["false_heal_max"]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_constitution() -> dict:
    try:
        import yaml
        text = CONSTITUTION_PATH.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}
    except Exception as exc:
        LOG.warning("[meta_harness] Could not load heal constitution: %s", exc)
        return {}


def _load_oracle_heuristics() -> list[dict]:
    heuristics_path = BASE_DIR / "args" / "oracle_heuristics.yaml"
    try:
        import yaml
        text = heuristics_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        return data.get("heuristics", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Oracle heuristic analysis
# ---------------------------------------------------------------------------

def _get_error_case_heuristic_hits(reflex: str = "oracle_triage") -> dict[str, int]:
    """Count how many harness error cases each heuristic name was likely responsible for.

    We approximate by matching the heuristic's `reason` text against the
    harness_eval `metadata_json` field (which stores the triage reason).
    Returns {heuristic_name: error_count}.

    The column is ``metadata_json``; this read asked for ``metadata``, which has
    never existed on ``harness_eval`` (see migration 302 and pg_consolidated.sql).
    Every execution therefore raised, was caught below, and returned ``{}`` — so
    ``_propose_heuristic_retirements`` has always been handed an empty hit map
    and the meta-harness has never proposed retiring a heuristic, whatever
    precision did. Fixed alongside hgx-eval-01 because it is the same read path.
    """
    from tools.db.storage import get_connection

    cutoff = (_utcnow() - timedelta(days=30)).isoformat(timespec="seconds")
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT metadata_json
            FROM harness_eval
            WHERE reflex = %s
              AND created_at >= %s
              AND actual_outcome IS NOT NULL
              AND (
                (decision = 'promote' AND actual_outcome IN ('false_positive', 'unresolved'))
                OR
                (decision = 'dismiss' AND actual_outcome = 'resolved')
              )
            """,
            (reflex, cutoff),
        ).fetchall()
    except Exception as exc:
        LOG.debug("[meta_harness] harness_eval query failed: %s", exc)
        return {}

    heuristics = _load_oracle_heuristics()
    hits: dict[str, int] = {h["name"]: 0 for h in heuristics}

    import json
    for row in rows:
        meta_raw = row[0] if isinstance(row, (list, tuple)) else row["metadata_json"]
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except (json.JSONDecodeError, TypeError):
            continue
        reason = str(meta.get("reason", "")).lower()
        for h in heuristics:
            if h["name"] in hits and h.get("reason", "").lower()[:30] in reason:
                hits[h["name"]] += 1

    return hits


def _propose_heuristic_retirements(
    precision: float,
    heuristic_hits: dict[str, int],
    precision_floor: float | None = None,
) -> list[dict]:
    """Propose retiring heuristics with >= 2 error-case hits when precision is low."""
    if precision_floor is None:
        precision_floor, _ = _get_adaptive_thresholds()
    if precision >= precision_floor:
        return []

    proposals = []
    for name, hit_count in heuristic_hits.items():
        if hit_count >= 2:
            proposals.append({
                "heuristic_name": name,
                "error_case_hits": hit_count,
                "proposal": "retire",
                "reason": (
                    f"Appeared in {hit_count} error cases while oracle precision "
                    f"({precision:.3f}) is below floor {precision_floor:.3f}. "
                    "Consider retiring or inverting this heuristic."
                ),
            })

    return sorted(proposals, key=lambda x: -x["error_case_hits"])


# ---------------------------------------------------------------------------
# Heal constitution analysis
# ---------------------------------------------------------------------------

def _get_heal_error_types() -> dict[str, int]:
    """Count false heals by resolution_type from self_healing_events."""
    from tools.db.storage import get_connection

    cutoff = (_utcnow() - timedelta(days=30)).isoformat(timespec="seconds")
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT action_taken, COUNT(*) AS cnt
            FROM self_healing_events
            WHERE outcome = 'failed'
              AND created_at >= %s
            GROUP BY action_taken
            """,
            (cutoff,),
        ).fetchall()
        return {
            (row[0] if isinstance(row, (list, tuple)) else row["action_taken"]): (
                row[1] if isinstance(row, (list, tuple)) else row["cnt"]
            )
            for row in rows
        }
    except Exception:
        return {}


def _propose_constitution_tightening(
    false_heal_rate: float,
    constitution: dict,
    error_types: dict[str, int],
    false_heal_ceiling: float | None = None,
) -> list[dict]:
    """Propose raising min_confidence for resolution types with high failure counts."""
    if false_heal_ceiling is None:
        _, false_heal_ceiling = _get_adaptive_thresholds()
    if false_heal_rate < false_heal_ceiling:
        return []

    proposals = []

    for res_type, fail_count in sorted(error_types.items(), key=lambda x: -x[1]):
        if fail_count < 2:
            continue

        # Find current floor for this type
        current_floor = 0.70
        for rule in constitution.get("rules", []):
            if res_type in rule.get("applies_to_types", []):
                current_floor = rule.get("min_confidence", current_floor)
                break

        proposed_floor = min(0.95, round(current_floor + 0.05, 2))
        if proposed_floor <= current_floor:
            continue

        proposals.append({
            "resolution_type": res_type,
            "current_min_confidence": current_floor,
            "proposed_min_confidence": proposed_floor,
            "fail_count_30d": fail_count,
            "reason": (
                f"Resolution type '{res_type}' had {fail_count} failures in last 30 days "
                f"while false_heal_rate ({false_heal_rate:.3f}) exceeds ceiling "
                f"{false_heal_ceiling:.3f}. Propose raising min_confidence from "
                f"{current_floor} to {proposed_floor}."
            ),
        })

    return proposals


# ---------------------------------------------------------------------------
# Proposal writer
# ---------------------------------------------------------------------------

def _write_meta_proposals(
    oracle_proposals: list[dict],
    heal_proposals: list[dict],
    metrics: dict,
) -> Path:
    """Write all proposals to args/meta_harness_proposals.yaml."""
    import yaml

    content = {
        "generated_at": _utcnow_str(),
        "metrics_snapshot": metrics,
        "oracle_heuristic_retirements": oracle_proposals,
        "heal_constitution_tightening": heal_proposals,
    }

    text = (
        "# Meta-Harness Proposals — generated automatically\n"
        "# Review these proposals and apply manually:\n"
        "#   Oracle retirements: edit args/oracle_heuristics.yaml\n"
        "#   Heal tightening: edit args/heal_constitution.yaml\n"
        "# Delete this file after review.\n\n"
        + yaml.dump(content, default_flow_style=False, sort_keys=False)
    )

    META_PROPOSALS_PATH.write_text(text, encoding="utf-8", newline="")
    return META_PROPOSALS_PATH


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_meta_review(dry_run: bool = False) -> dict[str, Any]:
    """Run the meta-harness review. Returns a summary dict."""
    from tools.genesis.harness.eval_harness import compute_metrics

    metrics_oracle = compute_metrics("oracle_triage", window_days=30)
    metrics_heal = compute_metrics("heal", window_days=30)

    combined_metrics = {"oracle_triage": metrics_oracle, "heal": metrics_heal}

    oracle_proposals: list[dict] = []
    heal_proposals: list[dict] = []

    precision = metrics_oracle.get("precision", 1.0)
    false_heal_rate = metrics_heal.get("false_heal_rate", 0.0)

    precision_floor, false_heal_ceiling = _get_adaptive_thresholds()

    # Oracle heuristic retirement analysis
    if precision < precision_floor:
        hits = _get_error_case_heuristic_hits("oracle_triage")
        oracle_proposals = _propose_heuristic_retirements(precision, hits, precision_floor)
        if oracle_proposals:
            LOG.info(
                "[meta_harness] oracle precision=%.3f below floor %.3f — %d retirement proposals",
                precision, precision_floor, len(oracle_proposals),
            )

    # Heal constitution tightening analysis
    if false_heal_rate > false_heal_ceiling:
        constitution = _load_constitution()
        error_types = _get_heal_error_types()
        heal_proposals = _propose_constitution_tightening(
            false_heal_rate, constitution, error_types, false_heal_ceiling
        )
        if heal_proposals:
            LOG.info(
                "[meta_harness] false_heal_rate=%.3f above ceiling %.3f — %d tightening proposals",
                false_heal_rate, false_heal_ceiling, len(heal_proposals),
            )

    proposals_written = bool(oracle_proposals or heal_proposals)

    if proposals_written and not dry_run:
        _write_meta_proposals(oracle_proposals, heal_proposals, combined_metrics)

    if not dry_run:
        _mark_ran_today()

    return {
        "ran": True,
        "dry_run": dry_run,
        "precision": precision,
        "false_heal_rate": false_heal_rate,
        "precision_floor": precision_floor,
        "false_heal_ceiling": false_heal_ceiling,
        "oracle_proposals": oracle_proposals,
        "heal_proposals": heal_proposals,
        "proposals_written": proposals_written and not dry_run,
        "proposals_path": str(META_PROPOSALS_PATH) if proposals_written and not dry_run else None,
    }
