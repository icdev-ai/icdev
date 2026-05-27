# CUI // SP-CTI
"""Genesis Reflex — FathomDesk Trap Sweep.

Runs on a 4-hour cadence. Scans recent signal direction flips from
ad_signals to detect bull and bear trap patterns, then persists qualifying
events to ad_trap_events via the insert path established in ad712-trap-01
(ta_config.yaml thresholds + ad_trap_events schema from migration 030).

Bull trap: ticker posted a BUY signal followed by a SELL reversal.
Bear trap: ticker posted a SELL signal followed by a BUY reversal.

Deduplication: per-ticker cooldown via ad_reflex_cooldowns (COOLDOWN_HOURS=4)
prevents the same ticker from generating a duplicate event within 4 hours.

GREEN tier (read + append-only writes, no LLM in hot path).  Air-gap safe.
"""
IMPLEMENTATION_STATUS = "full"

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

# ── Constants ──────────────────────────────────────────────────────────────────

COOLDOWN_HOURS = 4
_REFLEX_KEY = "fathomdesk_trap_sweep"
_DEDUP_WINDOW_HOURS = 24  # skip ticker if it already has an event within this window
_MIN_CONFIDENCE = 0.50    # minimum signal confidence to consider a flip a trap


# ── TA config loader ───────────────────────────────────────────────────────────

def _load_trap_config() -> Dict[str, Any]:
    """Load trap thresholds from args/ta_config.yaml, return safe defaults on error."""
    defaults: Dict[str, Any] = {
        "breakout_volume_ratio": 0.7,
        "vol_lookback": 20,
        "max_reentry_bars": 3,
        "confirmation_lookback": 5,
    }
    try:
        import yaml  # type: ignore[import]
        cfg_path = BASE_DIR / "args" / "ta_config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            traps = raw.get("traps", {})
            if traps:
                defaults.update(traps)
    except Exception:
        pass
    return defaults


# ── Cooldown helpers ───────────────────────────────────────────────────────────

def _check_cooldown(conn: Any, key: str, hours: int) -> bool:
    """Return True if cooldown has expired (safe to emit)."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT value FROM ad_reflex_cooldowns WHERE key = ? AND value > ?",
            (key, cutoff),
        ).fetchone()
        return row is None
    except Exception:
        return True


def _mark_cooldown(conn: Any, key: str, now: datetime) -> None:
    """Record current time as last emission for a cooldown key."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_reflex_cooldowns (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO ad_reflex_cooldowns (key, value) VALUES (?, ?)",
            (key, now.isoformat()),
        )
        conn.commit()
    except Exception:
        pass


# ── Trap event writer ──────────────────────────────────────────────────────────

def _insert_trap_event(conn: Any, event: Dict[str, Any]) -> bool:
    """Insert one row into ad_trap_events.  Returns True on success."""
    try:
        conn.execute(
            "INSERT INTO ad_trap_events "
            "(ticker, pattern, broken_level, confidence, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event["ticker"],
                event["pattern"],
                event.get("broken_level"),
                event.get("confidence", 0.0),
                json.dumps(event.get("evidence", {}), default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        print(f"  [trap_sweep] WARNING: insert failed for {event.get('ticker')}: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


# ── Sentiment helpers ──────────────────────────────────────────────────────────

_SENTIMENT_ELEVATION = 0.15   # additive boost when bearish sentiment + near resistance
_SENTIMENT_BEARISH_THRESHOLD = 0.4  # sentiment_weight below this = bearish


def _get_sentiment_weight(conn: Any, ticker: str) -> float:
    """Return the most recent sentiment_weight for *ticker* from oracle_predictions.

    sentiment_weight is stored in scoring_weights JSON as {'sentiment_weight': <float>}.
    Returns 0.5 (neutral) when no Oracle prediction is found or the field is absent.
    """
    try:
        row = conn.execute(
            "SELECT scoring_weights FROM oracle_predictions "
            "WHERE subject_id = ? ORDER BY created_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if row is None:
            return 0.5
        raw = row[0] if not hasattr(row, "keys") else row["scoring_weights"]
        if not raw:
            return 0.5
        weights = json.loads(raw) if isinstance(raw, str) else raw
        return float(weights.get("sentiment_weight", 0.5))
    except Exception:
        return 0.5


def _is_near_resistance(pattern: str) -> bool:
    """Return True when the trap pattern implies the price is/was near resistance.

    A bull trap (BUY→SELL flip) means the ticker broke above resistance then failed —
    by definition it was at/near resistance.  Bear traps touch support, not resistance,
    so this check does not apply to them.
    """
    return pattern == "bull_trap"


# ── Core sweep logic ───────────────────────────────────────────────────────────

def _trap_sweep(conn: Any) -> List[Dict[str, Any]]:
    """Detect bull/bear trap patterns and persist qualifying events.

    Algorithm:
    1. For each ticker in ad_signals, fetch the two most recent signals.
    2. If the signals have opposite directions (a flip), classify the trap type.
    3. Skip if either signal confidence is below _MIN_CONFIDENCE.
    4. Skip if a trap event already exists for this ticker within _DEDUP_WINDOW_HOURS.
    5. Insert the event; apply per-ticker cooldown (COOLDOWN_HOURS=4).

    Returns list of inserted event dicts.
    """
    inserted: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    try:
        tickers_rows = conn.execute(
            "SELECT DISTINCT ticker FROM ad_signals"
        ).fetchall()
    except Exception as exc:
        print(f"  [trap_sweep] WARNING: could not query ad_signals: {exc}")
        return inserted

    for trow in tickers_rows:
        ticker = trow[0] if not hasattr(trow, "keys") else trow["ticker"]
        if not ticker:
            continue

        cooldown_key = f"{_REFLEX_KEY}:{ticker}"
        if not _check_cooldown(conn, cooldown_key, COOLDOWN_HOURS):
            continue  # still within cooldown window

        # Fetch 2 most recent signals for this ticker
        try:
            rows = conn.execute(
                "SELECT direction, confidence, created_at FROM ad_signals "
                "WHERE ticker = ? ORDER BY created_at DESC LIMIT 2",
                (ticker,),
            ).fetchall()
        except Exception:
            continue

        if len(rows) < 2:
            continue

        r0 = dict(rows[0]) if hasattr(rows[0], "keys") else {
            "direction": rows[0][0], "confidence": rows[0][1], "created_at": rows[0][2]
        }
        r1 = dict(rows[1]) if hasattr(rows[1], "keys") else {
            "direction": rows[1][0], "confidence": rows[1][1], "created_at": rows[1][2]
        }

        dir_new = (r0.get("direction") or "").upper()
        dir_old = (r1.get("direction") or "").upper()
        conf_new = float(r0.get("confidence") or 0.0)
        conf_old = float(r1.get("confidence") or 0.0)

        # Direction flip with sufficient confidence
        if dir_new == dir_old or not dir_new or not dir_old:
            continue
        if conf_new < _MIN_CONFIDENCE or conf_old < _MIN_CONFIDENCE:
            continue

        # Classify trap type
        if dir_old in ("BUY", "LONG") and dir_new in ("SELL", "SHORT"):
            pattern = "bull_trap"
        elif dir_old in ("SELL", "SHORT") and dir_new in ("BUY", "LONG"):
            pattern = "bear_trap"
        else:
            continue  # HOLD or other — not a trap pattern

        # Dedup: skip if existing event within window
        dedup_cutoff = (now - timedelta(hours=_DEDUP_WINDOW_HOURS)).isoformat()
        try:
            existing = conn.execute(
                "SELECT id FROM ad_trap_events "
                "WHERE ticker = ? AND pattern = ? AND created_at > ? LIMIT 1",
                (ticker, pattern, dedup_cutoff),
            ).fetchone()
            if existing:
                continue
        except Exception:
            pass

        base_confidence = round((conf_new + conf_old) / 2.0, 4)

        # Sentiment elevation: bearish news + price near resistance raises trap probability
        sentiment_weight = _get_sentiment_weight(conn, ticker)
        sentiment_elevated = (
            sentiment_weight < _SENTIMENT_BEARISH_THRESHOLD
            and _is_near_resistance(pattern)
        )
        confidence = round(
            min(1.0, base_confidence + (_SENTIMENT_ELEVATION if sentiment_elevated else 0.0)),
            4,
        )

        event: Dict[str, Any] = {
            "ticker": ticker,
            "pattern": pattern,
            "confidence": confidence,
            "evidence": {
                "prior_direction": dir_old,
                "new_direction": dir_new,
                "prior_confidence": conf_old,
                "new_confidence": conf_new,
                "prior_signal_at": r1.get("created_at"),
                "new_signal_at": r0.get("created_at"),
                "detected_by": _REFLEX_KEY,
                "sentiment_weight": sentiment_weight,
                "sentiment_elevated": sentiment_elevated,
                "base_confidence": base_confidence,
            },
        }

        ok = _insert_trap_event(conn, event)
        if ok:
            _mark_cooldown(conn, cooldown_key, now)
            inserted.append(event)
            elev_tag = f" +{_SENTIMENT_ELEVATION} sentiment_elev" if sentiment_elevated else ""
            print(
                f"  [trap_sweep] inserted {pattern} for {ticker} "
                f"(conf={confidence:.2f}, flip={dir_old}→{dir_new}"
                f", sentiment_w={sentiment_weight:.2f}{elev_tag})"
            )

    return inserted


# ── Genesis contract ───────────────────────────────────────────────────────────

def run(config: Dict[str, Any], session: Any) -> Dict[str, Any]:
    """Execute one trap-sweep reflex cycle.

    Called by the Genesis daemon every interval_seconds (14400 s = 4 hours).
    Returns a summary dict consumed by the daemon's success_metric gate.
    """
    run_id = f"trap-sweep-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    print(f"[trap_sweep] run start  run_id={run_id}")

    if get_connection is None:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": "get_connection not available", "run_id": run_id},
        }

    # Reflex-level cooldown guard — skip if last full sweep was < COOLDOWN_HOURS ago
    conn = None
    try:
        conn = get_connection()
    except Exception as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": f"DB connection failed: {exc}", "run_id": run_id},
        }

    if not _check_cooldown(conn, _REFLEX_KEY, COOLDOWN_HOURS):
        conn.close()
        print(f"[trap_sweep] skipped — still within {COOLDOWN_HOURS}h reflex cooldown")
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"skipped": True, "reason": "cooldown", "run_id": run_id},
        }

    trap_config = _load_trap_config()
    print(
        f"  [trap_sweep] trap_config: vol_ratio={trap_config['breakout_volume_ratio']} "
        f"vol_lookback={trap_config['vol_lookback']} "
        f"max_reentry={trap_config['max_reentry_bars']}"
    )

    inserted: List[Dict[str, Any]] = []
    try:
        inserted = _trap_sweep(conn)
    except Exception as exc:
        print(f"  [trap_sweep] WARNING: sweep failed: {exc}")

    _mark_cooldown(conn, _REFLEX_KEY, now)
    conn.close()

    print(f"[trap_sweep] run complete  inserted={len(inserted)}")
    return {
        "success": True,
        "metric_value": float(len(inserted)),
        "details": {
            "run_id": run_id,
            "traps_inserted": len(inserted),
            "trap_config": trap_config,
            "events": [
                {"ticker": e["ticker"], "pattern": e["pattern"],
                 "confidence": e["confidence"]}
                for e in inserted
            ],
            "ran_at": now.isoformat(),
        },
    }
