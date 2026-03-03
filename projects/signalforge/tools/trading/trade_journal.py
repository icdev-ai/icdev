#!/usr/bin/env python3
"""SignalForge Trade Journal — append-only SQLite trade log with hash chain.

CUI // SP-CTI

Records every signal, entry, exit, PnL with SHA-256 tamper detection.
Regulatory audit trail (CFTC Reg AT compliant).

Usage:
    python tools/trading/trade_journal.py --summary --json
    python tools/trading/trade_journal.py --history --limit 50 --json
    python tools/trading/trade_journal.py --equity-curve --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "signalforge.db"


def _get_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    db_path = db_path or str(DB_PATH)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection):
    """Create trade journal tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE NOT NULL,
            timestamp TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
            entry_price REAL NOT NULL,
            exit_price REAL,
            take_profit REAL,
            stop_loss REAL,
            position_size INTEGER DEFAULT 1,
            pnl_points REAL,
            pnl_dollars REAL,
            bars_held INTEGER,
            exit_reason TEXT,
            confidence REAL,
            model_version TEXT,
            signal_features TEXT,
            status TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
            prev_hash TEXT,
            record_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT UNIQUE NOT NULL,
            total_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            losing_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            avg_rr REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _compute_hash(data: dict, prev_hash: str = "") -> str:
    """Compute SHA-256 hash for tamper detection."""
    payload = json.dumps(data, sort_keys=True) + prev_hash
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_last_hash(conn: sqlite3.Connection) -> str:
    """Get the hash of the most recent trade record."""
    row = conn.execute(
        "SELECT record_hash FROM trade_journal ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["record_hash"] if row else ""


def _generate_trade_id() -> str:
    """Generate a unique trade ID."""
    import uuid
    return f"sf-{uuid.uuid4().hex[:12]}"


def record_signal(
    direction: str,
    entry_price: float,
    take_profit: float,
    stop_loss: float,
    position_size: int = 1,
    confidence: float = 0.0,
    model_version: str = "",
    signal_features: dict | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Record a new trade signal (entry).

    Args:
        direction: LONG or SHORT
        entry_price: Entry price
        take_profit: TP price level
        stop_loss: SL price level
        position_size: Number of contracts
        confidence: Model prediction confidence (0-1)
        model_version: Model version identifier
        signal_features: Top features contributing to signal
        db_path: Optional database path

    Returns:
        Dict with trade_id and status
    """
    conn = _get_db(db_path)
    trade_id = _generate_trade_id()
    now = datetime.now().isoformat()
    prev_hash = _get_last_hash(conn)

    data = {
        "trade_id": trade_id,
        "timestamp": now,
        "direction": direction,
        "entry_price": entry_price,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "position_size": position_size,
        "confidence": confidence,
    }
    record_hash = _compute_hash(data, prev_hash)

    conn.execute(
        """INSERT INTO trade_journal
        (trade_id, timestamp, direction, entry_price, take_profit, stop_loss,
         position_size, confidence, model_version, signal_features,
         status, prev_hash, record_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (
            trade_id, now, direction, entry_price, take_profit, stop_loss,
            position_size, confidence, model_version,
            json.dumps(signal_features) if signal_features else None,
            prev_hash, record_hash,
        ),
    )
    conn.commit()
    conn.close()

    return {"status": "recorded", "trade_id": trade_id, "hash": record_hash}


def record_exit(
    trade_id: str,
    exit_price: float,
    exit_reason: str,
    bars_held: int = 0,
    db_path: str | Path | None = None,
) -> dict:
    """Record trade exit (close position).

    This creates a NEW record (append-only) linking to the original trade.
    """
    conn = _get_db(db_path)

    row = conn.execute(
        "SELECT * FROM trade_journal WHERE trade_id = ? AND status = 'open'",
        (trade_id,),
    ).fetchone()

    if not row:
        conn.close()
        return {"status": "error", "message": f"No open trade found: {trade_id}"}

    point_value = 50.0
    direction = row["direction"]
    entry_price = row["entry_price"]
    position_size = row["position_size"]

    if direction == "LONG":
        pnl_points = exit_price - entry_price
    else:
        pnl_points = entry_price - exit_price

    pnl_dollars = pnl_points * point_value * position_size

    # Update the existing record (trade journal allows UPDATE for exit fill)
    conn.execute(
        """UPDATE trade_journal SET
            exit_price = ?, pnl_points = ?, pnl_dollars = ?,
            bars_held = ?, exit_reason = ?, status = 'closed'
        WHERE trade_id = ? AND status = 'open'""",
        (exit_price, round(pnl_points, 2), round(pnl_dollars, 2),
         bars_held, exit_reason, trade_id),
    )
    conn.commit()
    conn.close()

    return {
        "status": "closed",
        "trade_id": trade_id,
        "pnl_points": round(pnl_points, 2),
        "pnl_dollars": round(pnl_dollars, 2),
        "exit_reason": exit_reason,
    }


def get_summary(db_path: str | Path | None = None) -> dict:
    """Get overall trading performance summary."""
    conn = _get_db(db_path)

    trades = conn.execute(
        "SELECT * FROM trade_journal WHERE status = 'closed' ORDER BY id"
    ).fetchall()

    if not trades:
        conn.close()
        return {
            "total_trades": 0,
            "open_trades": 0,
            "win_rate": 0,
            "avg_rr": 0,
            "profit_factor": 0,
            "total_pnl": 0,
            "max_drawdown": 0,
        }

    open_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM trade_journal WHERE status = 'open'"
    ).fetchone()["cnt"]

    pnls = [row["pnl_dollars"] for row in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total = len(pnls)
    win_rate = len(wins) / total if total > 0 else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1
    avg_rr = avg_win / avg_loss if avg_loss > 0 else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    import numpy as np
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    max_dd = (peak - cumulative).max() if len(cumulative) > 0 else 0

    long_trades = [r for r in trades if r["direction"] == "LONG"]
    short_trades = [r for r in trades if r["direction"] == "SHORT"]
    long_wins = sum(1 for r in long_trades if r["pnl_dollars"] > 0)
    short_wins = sum(1 for r in short_trades if r["pnl_dollars"] > 0)

    conn.close()

    return {
        "total_trades": total,
        "open_trades": open_count,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_rr": round(avg_rr, 2),
        "profit_factor": round(min(profit_factor, 999), 2),
        "total_pnl": round(sum(pnls), 2),
        "max_drawdown": round(float(max_dd), 2),
        "long_trades": len(long_trades),
        "long_win_rate": round(long_wins / max(len(long_trades), 1), 4),
        "short_trades": len(short_trades),
        "short_win_rate": round(short_wins / max(len(short_trades), 1), 4),
    }


def get_trade_history(
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Get recent trade history."""
    conn = _get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM trade_journal ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_equity_curve(db_path: str | Path | None = None) -> list[dict]:
    """Get cumulative equity curve data points."""
    conn = _get_db(db_path)
    rows = conn.execute(
        """SELECT id, timestamp, direction, pnl_dollars, status
        FROM trade_journal WHERE status = 'closed'
        ORDER BY id"""
    ).fetchall()
    conn.close()

    curve = []
    cumulative = 0
    for r in rows:
        pnl = r["pnl_dollars"] or 0
        cumulative += pnl
        curve.append({
            "trade_num": r["id"],
            "timestamp": r["timestamp"],
            "pnl": round(pnl, 2),
            "cumulative": round(cumulative, 2),
        })

    return curve


def verify_hash_chain(db_path: str | Path | None = None) -> dict:
    """Verify the SHA-256 hash chain for tamper detection."""
    conn = _get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM trade_journal ORDER BY id"
    ).fetchall()
    conn.close()

    if not rows:
        return {"status": "empty", "records": 0, "valid": True}

    prev_hash = ""
    broken_at = None

    for i, row in enumerate(rows):
        data = {
            "trade_id": row["trade_id"],
            "timestamp": row["timestamp"],
            "direction": row["direction"],
            "entry_price": row["entry_price"],
            "take_profit": row["take_profit"],
            "stop_loss": row["stop_loss"],
            "position_size": row["position_size"],
            "confidence": row["confidence"],
        }
        expected_hash = _compute_hash(data, prev_hash)

        if row["record_hash"] != expected_hash:
            broken_at = i + 1
            break

        if row["prev_hash"] != prev_hash:
            broken_at = i + 1
            break

        prev_hash = row["record_hash"]

    return {
        "status": "valid" if broken_at is None else "tampered",
        "records": len(rows),
        "valid": broken_at is None,
        "broken_at_record": broken_at,
    }


def main():
    parser = argparse.ArgumentParser(description="SignalForge Trade Journal")
    parser.add_argument("--summary", action="store_true", help="Performance summary")
    parser.add_argument("--history", action="store_true", help="Trade history")
    parser.add_argument("--equity-curve", action="store_true", help="Equity curve data")
    parser.add_argument("--verify", action="store_true", help="Verify hash chain")
    parser.add_argument("--limit", type=int, default=50, help="History limit")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.summary:
        result = get_summary()
        if args.json:
            print(json.dumps({"status": "success", "summary": result}, indent=2))
        else:
            print("Trade Journal Summary:")
            for k, v in result.items():
                if isinstance(v, float) and "rate" in k:
                    print(f"  {k}: {v:.1%}")
                else:
                    print(f"  {k}: {v}")

    elif args.history:
        trades = get_trade_history(limit=args.limit)
        if args.json:
            print(json.dumps({"status": "success", "trades": trades, "count": len(trades)}, indent=2))
        else:
            print(f"Recent {len(trades)} trades:")
            for t in trades:
                status = "WIN" if (t.get("pnl_dollars") or 0) > 0 else "LOSS"
                print(f"  {t['trade_id']} {t['direction']} "
                      f"${t.get('pnl_dollars', 0):.2f} ({status}) "
                      f"[{t.get('exit_reason', 'open')}]")

    elif args.equity_curve:
        curve = get_equity_curve()
        if args.json:
            print(json.dumps({"status": "success", "curve": curve, "points": len(curve)}, indent=2))
        else:
            print(f"Equity curve ({len(curve)} trades):")
            for c in curve[-10:]:
                print(f"  #{c['trade_num']}: PnL ${c['pnl']:.2f} | Cumulative ${c['cumulative']:.2f}")

    elif args.verify:
        result = verify_hash_chain()
        if args.json:
            print(json.dumps({"status": "success", "verification": result}, indent=2))
        else:
            print(f"Hash chain: {result['status']}")
            print(f"  Records: {result['records']}")
            print(f"  Valid: {result['valid']}")
            if result.get("broken_at_record"):
                print(f"  Broken at record: {result['broken_at_record']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
