# CUI // SP-CTI
"""Notification worthiness gate (oss2-triage-01).

A scored decision — **interrupt / dispatch / file** — placed *in front of* channel
routing, so an event's worthiness of attention is decided before where to send it.
Adapts the pattern from agent-chief (MIT): a "chief of staff" that filters a high
event volume down to the few worth a human's attention. ICDEV runs dozens of Genesis
reflexes on schedules plus an autonomous kanban board and an awareness engine that
promotes predictions into tasks — exactly that event-volume profile.

Scope, deliberately narrow (spike oss-02 §4.3): this is **not** a new notification
system and **not** the agent-chief package (Python 3.12+ against ICDEV's 3.9 floor).
It is one scored stage over the EXISTING `tools/notifications/` subsystem — routing,
escalation, acknowledgement and preferences already exist; only a worthiness decision
upstream of `resolve_channels` was missing. It is **off by default** (config
`enabled: false`), so nothing changes until an operator opts in.

The three actions:
* **interrupt** — worth a human's attention now; route to channels as usual.
* **dispatch** — worth an agent's action but not a human interruption; hand to an
  agent/queue instead of pinging a person.
* **file** — low worthiness; record it, do not interrupt or dispatch.

Evaluated, not asserted (agent-chief's own posture): :func:`evaluate_stream` reports
the interrupt/dispatch/file distribution over a representative event stream so the
filter rate is a measured number, not a claim.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

_BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _BASE_DIR / "args" / "notification_worthiness.yaml"

INTERRUPT = "interrupt"
DISPATCH = "dispatch"
FILE = "file"

# Fallbacks used when the YAML is absent/unparseable — resolution never raises.
_DEFAULT_SEVERITY_WEIGHTS = {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.2, "info": 0.1}
_DEFAULT_THRESHOLDS = {"interrupt": 0.75, "dispatch": 0.4}


@dataclass
class WorthinessDecision:
    action: str          # INTERRUPT | DISPATCH | FILE
    score: float
    reason: str
    enabled: bool = True  # False => gate is off; caller should keep current behavior


def load_config(path: Optional[str | pathlib.Path] = None) -> Dict[str, Any]:
    p = pathlib.Path(path) if path else _CONFIG_PATH
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def is_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    cfg = config if config is not None else load_config()
    return bool(cfg.get("enabled", False))


def score_worthiness(
    event_type: str,
    severity: str = "info",
    metadata: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> WorthinessDecision:
    """Score an event and map it to interrupt / dispatch / file.

    Score = severity weight + event-type modifier + metadata signals, clamped to
    [0, 1]. Config-driven (``args/notification_worthiness.yaml``); safe defaults when
    absent. When the gate is disabled the decision carries ``enabled=False`` and
    ``action=INTERRUPT`` so a caller that ignores the flag preserves today's
    "route everything" behavior.
    """
    cfg = config if config is not None else load_config()
    metadata = metadata or {}
    enabled = bool(cfg.get("enabled", False))

    weights = {**_DEFAULT_SEVERITY_WEIGHTS, **(cfg.get("severity_weights") or {})}
    modifiers = cfg.get("event_type_modifiers") or {}
    thresholds = {**_DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}

    base = weights.get((severity or "info").lower(), 0.1)
    modifier = float(modifiers.get(event_type, 0.0))

    # Metadata signals: an explicitly actionable event nudges up; a routine/digest/
    # heartbeat marker nudges down. These are advisory hints callers may set.
    signal = 0.0
    reasons: List[str] = [f"severity={severity}({base:+.2f})"]
    if modifier:
        reasons.append(f"type={event_type}({modifier:+.2f})")
    if metadata.get("actionable") is True:
        signal += 0.15
        reasons.append("actionable(+0.15)")
    if metadata.get("routine") is True or metadata.get("digest") is True:
        signal -= 0.25
        reasons.append("routine/digest(-0.25)")
    if metadata.get("user_facing") is False:
        signal -= 0.10
        reasons.append("not-user-facing(-0.10)")

    score = max(0.0, min(1.0, base + modifier + signal))

    if score >= thresholds["interrupt"]:
        action = INTERRUPT
    elif score >= thresholds["dispatch"]:
        action = DISPATCH
    else:
        action = FILE

    return WorthinessDecision(
        action=action,
        score=round(score, 3),
        reason=f"{action} @ {score:.3f} [{', '.join(reasons)}]",
        enabled=enabled,
    )


def evaluate_stream(events: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a representative event stream through the scorer and report the
    interrupt/dispatch/file distribution — the 'evaluated, not asserted' number.

    ``events`` are dicts with ``event_type``/``severity``/``metadata``. Returns
    counts, rates, and the interruption ratio (events per interrupt), agent-chief's
    headline metric.
    """
    counts = {INTERRUPT: 0, DISPATCH: 0, FILE: 0}
    for ev in events:
        d = score_worthiness(
            ev.get("event_type", ""), ev.get("severity", "info"), ev.get("metadata"), config=config
        )
        counts[d.action] += 1
    total = len(events)
    interrupts = counts[INTERRUPT] or 1  # avoid div/0 for the ratio
    return {
        "total": total,
        "counts": counts,
        "rates": {k: round(v / total, 3) if total else 0.0 for k, v in counts.items()},
        "events_per_interrupt": round(total / interrupts, 2),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json
    from dataclasses import asdict

    parser = argparse.ArgumentParser(description="Notification worthiness gate (oss2-triage-01)")
    parser.add_argument("--event-type", default="")
    parser.add_argument("--severity", default="info")
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args()
    print(json.dumps(asdict(score_worthiness(ns.event_type, ns.severity)), indent=2))
