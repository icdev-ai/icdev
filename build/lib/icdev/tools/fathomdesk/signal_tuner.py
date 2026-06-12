# CUI // SP-CTI
"""FathomDesk signal tuner — threshold optimisation with dry-run support.

dry_run() previews the tuning pipeline without writing to the database.
"""
from __future__ import annotations

from typing import Any

from tools.fathomdesk import signal_generator


def dry_run(signals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Preview the tuning steps without making permanent changes.

    Returns a dict with keys:
      dry_run       — always True
      tuner         — module identifier string
      steps_count   — number of steps that would execute
      thresholds    — currently loaded threshold values
      steps         — ordered list of step descriptor dicts
      input_signals — count of signals provided (0 if none given)
    """
    thresholds = signal_generator.load_thresholds()
    steps = [
        {"step_id": "load_thresholds", "action": "read args/signal_thresholds.yaml"},
        {"step_id": "filter_signals", "action": "apply min_confidence / min_score gates"},
        {"step_id": "score_backtest", "action": "compute precision/recall/f1 via backtester.score()"},
        {"step_id": "emit_report", "action": "return metrics without writing to DB"},
    ]
    return {
        "dry_run": True,
        "tuner": "signal_tuner",
        "steps_count": len(steps),
        "thresholds": thresholds,
        "steps": steps,
        "input_signals": len(signals) if signals else 0,
    }
