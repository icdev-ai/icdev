# CUI // SP-CTI
"""Insider-Risk UBA (lite) — deterministic user-behavior anomaly detection.

Card ``crx-sec-01``. A lightweight User-Behavior-Analytics baseline built over
telemetry ICDEV **already** collects — the unified activity feed
(``audit_trail`` + ``hook_events``, Phase 30) and usage tracking
(``usage_events``). It is strictly **READ-ONLY** over that telemetry: it never
UPDATEs or DELETEs audit rows.

Phase 1 is **deterministic rules only — NO ML**:

* ``off_hours_bulk_export``     — bulk export/download activity during off-hours
* ``privilege_change_burst``    — many privilege / config-change events in a short window
* ``dormant_account_activity``  — a long-dormant account that suddenly becomes active

Per-user baselines (typical active hours, endpoint mix, export volume) are
derived for context and stored alongside scores. Baselines and scores are
**derived data** (recomputable) — they are NOT audit records — and live in
``insider_risk_baselines`` / ``insider_risk_scores`` WITH ``tenant_id`` +
``classification`` columns for row-level security.

**Config-gated, DEFAULT OFF** (privacy: this monitors platform users). See
``args/insider_risk_config.yaml`` and ``docs/security/insider-risk-uba.md``.

NIST 800-53: AU-6, AU-6(1), AU-6(3), AU-7, SI-4, AC-2(12).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "args" / "insider_risk_config.yaml"

# ---------------------------------------------------------------------------
# Event classification (deterministic maps over existing telemetry)
# ---------------------------------------------------------------------------

# audit_trail.event_type values that represent an export / bulk-download action.
EXPORT_EVENT_TYPES = frozenset({
    "reqif_exported",
    "sbom_generated",
    "xacta_export",
    "emass_push",
    "document_uploaded",  # bulk document movement
})

# audit_trail.event_type values that represent a privilege / config change.
PRIVILEGE_EVENT_TYPES = frozenset({
    "config_changed",
    "secret_rotated",
    "classification_changed",
    "approval_granted",
    "approval_denied",
    "remote_binding_created",
    "remote_binding_provisioned",
    "remote_binding_revoked",
    "ato_system_registered",
})

# Substrings in a usage_events route / feature_tag that mean "export / download".
EXPORT_ROUTE_HINTS = ("export", "download", "/dl/", "bulk", "report")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,  # DEFAULT OFF — privacy: this monitors platform users
    "lookback_days": 30,
    "tenant_id": "platform",
    "classification": "CUI",
    "off_hours": {"start_hour": 22, "end_hour": 6},
    "rules": {
        "off_hours_bulk_export": {"enabled": True, "weight": 0.40, "export_threshold": 5},
        "privilege_change_burst": {"enabled": True, "weight": 0.35, "burst_threshold": 4, "window_hours": 1},
        "dormant_account_activity": {"enabled": True, "weight": 0.25, "dormant_days": 30},
    },
    "bands": {"high": 0.6, "elevated": 0.3},
    "alert": {"enabled": False, "min_score": 0.6},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load config, layered over defaults. Env ICDEV_INSIDER_RISK_ENABLED wins."""
    cfg = dict(_DEFAULT_CONFIG)
    cfg_path = Path(path) if path else _CONFIG_PATH
    try:
        import yaml

        if cfg_path.exists():
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            cfg = _deep_merge(_DEFAULT_CONFIG, loaded)
    except Exception:
        cfg = dict(_DEFAULT_CONFIG)

    env = os.environ.get("ICDEV_INSIDER_RISK_ENABLED")
    if env is not None:
        cfg["enabled"] = env.strip().lower() in ("1", "true", "yes", "on")
    return cfg


def is_enabled(config: Optional[dict] = None) -> bool:
    return bool((config or load_config()).get("enabled"))


# ---------------------------------------------------------------------------
# Schema (derived data — recomputable, NOT append-only)
# ---------------------------------------------------------------------------

def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS insider_risk_baselines (
            account_id      TEXT PRIMARY KEY,
            typical_hours   TEXT DEFAULT '[]',
            event_count     INTEGER DEFAULT 0,
            distinct_events INTEGER DEFAULT 0,
            export_count    INTEGER DEFAULT 0,
            first_seen      TEXT,
            last_seen       TEXT,
            tenant_id       TEXT,
            classification  TEXT DEFAULT 'CUI',
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS insider_risk_scores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      TEXT NOT NULL,
            risk_score      REAL NOT NULL,
            risk_band       TEXT NOT NULL,
            rules_fired     TEXT DEFAULT '[]',
            details_json    TEXT DEFAULT '{}',
            tenant_id       TEXT,
            classification  TEXT DEFAULT 'CUI',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_insider_scores_acct "
            "ON insider_risk_scores(account_id, created_at)"
        )
    except Exception:
        pass
    conn.commit()


# ---------------------------------------------------------------------------
# Telemetry collection (READ-ONLY)
# ---------------------------------------------------------------------------

def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO / 'YYYY-MM-DD HH:MM:SS' timestamp to aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _safe_query(conn, sql: str, params=()) -> list:
    """Run a read query; tolerate a missing table (fresh checkout / no migration)."""
    try:
        return list(conn.execute(sql, params).fetchall())
    except Exception:
        return []


def _row_get(row, key, idx):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        try:
            return row[idx]
        except (IndexError, KeyError, TypeError):
            return None


def prior_last_seen(conn, cutoff_iso: str) -> dict[str, datetime]:
    """Most recent activity timestamp *before* the window, per actor (unbounded).

    Used by the dormancy rule to detect reactivation after a long silence, even
    when the prior activity predates the scan lookback window. Read-only.
    """
    out: dict[str, datetime] = {}

    def _absorb(rows):
        for r in rows:
            actor = _row_get(r, "actor", 0)
            ts = _parse_ts(_row_get(r, "last_ts", 1))
            if not actor or ts is None:
                continue
            cur = out.get(str(actor))
            if cur is None or ts > cur:
                out[str(actor)] = ts

    _absorb(_safe_query(
        conn,
        "SELECT actor, MAX(created_at) AS last_ts FROM audit_trail "
        "WHERE created_at < %s GROUP BY actor",
        (cutoff_iso,),
    ))
    _absorb(_safe_query(
        conn,
        "SELECT user_session AS actor, MAX(occurred_at) AS last_ts FROM usage_events "
        "WHERE occurred_at < %s GROUP BY user_session",
        (cutoff_iso,),
    ))
    _absorb(_safe_query(
        conn,
        "SELECT session_id AS actor, MAX(created_at) AS last_ts FROM hook_events "
        "WHERE created_at < %s GROUP BY session_id",
        (cutoff_iso,),
    ))
    return out


def collect_activity(conn, lookback_days: int) -> list[dict[str, Any]]:
    """Collect a unified, per-actor activity stream over the lookback window.

    Reads audit_trail, usage_events, and hook_events — never writes. Each event
    is normalised to ``{actor, ts, kind, label}`` where ``kind`` is one of
    ``export`` / ``privilege`` / ``activity``.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    events: list[dict[str, Any]] = []

    # audit_trail — actor is the platform user; event_type drives the kind.
    for r in _safe_query(
        conn,
        "SELECT actor, event_type, created_at FROM audit_trail WHERE created_at >= %s",
        (cutoff,),
    ):
        actor = _row_get(r, "actor", 0)
        etype = _row_get(r, "event_type", 1)
        ts = _parse_ts(_row_get(r, "created_at", 2))
        if not actor or ts is None:
            continue
        if etype in EXPORT_EVENT_TYPES:
            kind = "export"
        elif etype in PRIVILEGE_EVENT_TYPES:
            kind = "privilege"
        else:
            kind = "activity"
        events.append({"actor": str(actor), "ts": ts, "kind": kind, "label": etype or ""})

    # usage_events — user_session is the actor; export routes count as exports.
    for r in _safe_query(
        conn,
        "SELECT user_session, route, feature_tag, occurred_at FROM usage_events WHERE occurred_at >= %s",
        (cutoff,),
    ):
        actor = _row_get(r, "user_session", 0)
        route = (_row_get(r, "route", 1) or "")
        feature = (_row_get(r, "feature_tag", 2) or "")
        ts = _parse_ts(_row_get(r, "occurred_at", 3))
        if not actor or ts is None:
            continue
        hay = (str(route) + " " + str(feature)).lower()
        kind = "export" if any(h in hay for h in EXPORT_ROUTE_HINTS) else "activity"
        events.append({"actor": str(actor), "ts": ts, "kind": kind, "label": str(route)})

    # hook_events — session_id is the actor; always plain activity (recency signal).
    for r in _safe_query(
        conn,
        "SELECT session_id, hook_type, created_at FROM hook_events WHERE created_at >= %s",
        (cutoff,),
    ):
        actor = _row_get(r, "session_id", 0)
        htype = _row_get(r, "hook_type", 1)
        ts = _parse_ts(_row_get(r, "created_at", 2))
        if not actor or ts is None:
            continue
        events.append({"actor": str(actor), "ts": ts, "kind": "activity", "label": htype or "hook"})

    return events


# ---------------------------------------------------------------------------
# Deterministic rules
# ---------------------------------------------------------------------------

def _is_off_hours(dt: datetime, start_hour: int, end_hour: int) -> bool:
    """True if the hour falls in the off-hours window (wraps past midnight)."""
    h = dt.hour
    if start_hour <= end_hour:
        return start_hour <= h < end_hour
    return h >= start_hour or h < end_hour  # e.g. 22..24 or 0..6


def _rule_off_hours_bulk_export(events, rc, off) -> Optional[dict]:
    count = sum(
        1 for e in events
        if e["kind"] == "export" and _is_off_hours(e["ts"], off["start_hour"], off["end_hour"])
    )
    if count >= rc["export_threshold"]:
        return {"rule": "off_hours_bulk_export", "weight": rc["weight"],
                "detail": f"{count} export/download actions during off-hours "
                          f"({off['start_hour']:02d}:00-{off['end_hour']:02d}:00)",
                "count": count}
    return None


def _rule_privilege_change_burst(events, rc) -> Optional[dict]:
    priv = sorted((e for e in events if e["kind"] == "privilege"), key=lambda e: e["ts"])
    if len(priv) < rc["burst_threshold"]:
        return None
    window = timedelta(hours=rc["window_hours"])
    best = 0
    j = 0
    for i in range(len(priv)):
        while priv[i]["ts"] - priv[j]["ts"] > window:
            j += 1
        best = max(best, i - j + 1)
    if best >= rc["burst_threshold"]:
        return {"rule": "privilege_change_burst", "weight": rc["weight"],
                "detail": f"{best} privilege/config changes within {rc['window_hours']}h",
                "count": best}
    return None


def _rule_dormant_account_activity(events, rc, prior_last_seen: Optional[datetime] = None) -> Optional[dict]:
    ts = sorted(e["ts"] for e in events)
    if not ts:
        return None
    dormant = timedelta(days=rc["dormant_days"])
    # Production signal: activity in the window following a long silence relative
    # to the account's most recent activity *before* the window (queried unbounded).
    if prior_last_seen is not None:
        gap = ts[0] - prior_last_seen
        if gap >= dormant:
            return {"rule": "dormant_account_activity", "weight": rc["weight"],
                    "detail": f"reactivated after {gap.days}d dormancy "
                              f"(>= {rc['dormant_days']}d)",
                    "count": gap.days}
    # Fallback: a long gap observed entirely within the window.
    for i in range(1, len(ts)):
        gap = ts[i] - ts[i - 1]
        if gap >= dormant:
            return {"rule": "dormant_account_activity", "weight": rc["weight"],
                    "detail": f"reactivated after {gap.days}d dormancy "
                              f"(>= {rc['dormant_days']}d)",
                    "count": gap.days}
    return None


def evaluate_actor(actor: str, events: list[dict], config: dict,
                   prior_last_seen: Optional[datetime] = None) -> dict[str, Any]:
    """Apply all enabled deterministic rules to one actor's event stream."""
    rules = config["rules"]
    off = config["off_hours"]
    fired: list[dict] = []

    if rules["off_hours_bulk_export"].get("enabled", True):
        f = _rule_off_hours_bulk_export(events, rules["off_hours_bulk_export"], off)
        if f:
            fired.append(f)
    if rules["privilege_change_burst"].get("enabled", True):
        f = _rule_privilege_change_burst(events, rules["privilege_change_burst"])
        if f:
            fired.append(f)
    if rules["dormant_account_activity"].get("enabled", True):
        f = _rule_dormant_account_activity(events, rules["dormant_account_activity"], prior_last_seen)
        if f:
            fired.append(f)

    score = round(min(1.0, sum(f["weight"] for f in fired)), 4)
    bands = config["bands"]
    band = ("high" if score >= bands["high"]
            else "elevated" if score >= bands["elevated"]
            else "normal")
    return {"account_id": actor, "risk_score": score, "risk_band": band,
            "rules_fired": [f["rule"] for f in fired], "findings": fired}


def _baseline_for(actor: str, events: list[dict], classification: str, tenant_id) -> dict:
    hours: dict[int, int] = {}
    labels: set = set()
    exports = 0
    for e in events:
        hours[e["ts"].hour] = hours.get(e["ts"].hour, 0) + 1
        labels.add(e["label"])
        if e["kind"] == "export":
            exports += 1
    typical = [h for h, _ in sorted(hours.items(), key=lambda kv: kv[1], reverse=True)[:5]]
    ts_sorted = sorted(e["ts"] for e in events)
    return {
        "account_id": actor,
        "typical_hours": sorted(typical),
        "event_count": len(events),
        "distinct_events": len(labels),
        "export_count": exports,
        "first_seen": ts_sorted[0].isoformat() if ts_sorted else None,
        "last_seen": ts_sorted[-1].isoformat() if ts_sorted else None,
        "tenant_id": tenant_id,
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# Alert soft-couple (crx-not-01) — clean hook, no hard dependency
# ---------------------------------------------------------------------------

def _maybe_alert(finding: dict, config: dict) -> bool:
    """Best-effort alert dispatch. Soft-coupled to the notification service
    (crx-not-01) which may not be merged yet — never import a hard dependency."""
    alert = config.get("alert", {})
    if not alert.get("enabled") or finding["risk_score"] < alert.get("min_score", 0.6):
        return False
    try:  # pragma: no cover - depends on optional crx-not-01 surface
        import importlib

        mod = importlib.import_module("tools.notification_service.handler_service")
        for fn_name in ("dispatch_alert", "send_alert", "notify"):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                fn({
                    "source": "insider_risk_uba",
                    "account_id": finding["account_id"],
                    "risk_score": finding["risk_score"],
                    "risk_band": finding["risk_band"],
                    "rules_fired": finding["rules_fired"],
                })
                return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# Scan entry point
# ---------------------------------------------------------------------------

def run_scan(config: Optional[dict] = None, conn=None,
             persist: bool = True) -> dict[str, Any]:
    """Compute baselines + deterministic anomaly scores over recent telemetry.

    Read-only over audit/usage/hook telemetry; writes only to the derived
    ``insider_risk_baselines`` / ``insider_risk_scores`` tables.

    Pass ``conn`` to reuse a caller-owned connection (tests); otherwise a
    main-DB connection is opened and closed. Returns a summary dict.
    """
    config = config or load_config()
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        _ensure_tables(conn)
        lookback = int(config.get("lookback_days", 30))
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=lookback)).isoformat()
        events = collect_activity(conn, lookback)
        prior = prior_last_seen(conn, cutoff_iso)

        by_actor: dict[str, list[dict]] = {}
        for e in events:
            by_actor.setdefault(e["actor"], []).append(e)

        classification = config.get("classification", "CUI")
        tenant_id = config.get("tenant_id")
        findings: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()
        alerts = 0

        for actor, actor_events in by_actor.items():
            baseline = _baseline_for(actor, actor_events, classification, tenant_id)
            result = evaluate_actor(actor, actor_events, config, prior.get(actor))
            if persist:
                conn.execute(
                    """INSERT INTO insider_risk_baselines
                       (account_id, typical_hours, event_count, distinct_events,
                        export_count, first_seen, last_seen, tenant_id, classification, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(account_id) DO UPDATE SET
                         typical_hours=excluded.typical_hours,
                         event_count=excluded.event_count,
                         distinct_events=excluded.distinct_events,
                         export_count=excluded.export_count,
                         first_seen=excluded.first_seen,
                         last_seen=excluded.last_seen,
                         tenant_id=excluded.tenant_id,
                         classification=excluded.classification,
                         updated_at=excluded.updated_at""",
                    (baseline["account_id"], json.dumps(baseline["typical_hours"]),
                     baseline["event_count"], baseline["distinct_events"],
                     baseline["export_count"], baseline["first_seen"], baseline["last_seen"],
                     tenant_id, classification, now),
                )
            if result["rules_fired"]:
                if persist:
                    conn.execute(
                        """INSERT INTO insider_risk_scores
                           (account_id, risk_score, risk_band, rules_fired, details_json,
                            tenant_id, classification, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (result["account_id"], result["risk_score"], result["risk_band"],
                         json.dumps(result["rules_fired"]), json.dumps(result["findings"]),
                         tenant_id, classification, now),
                    )
                if _maybe_alert(result, config):
                    alerts += 1
                findings.append(result)

        if persist:
            conn.commit()

        findings.sort(key=lambda r: r["risk_score"], reverse=True)
        return {
            "enabled": bool(config.get("enabled")),
            "actors_evaluated": len(by_actor),
            "events_scanned": len(events),
            "findings": findings,
            "finding_count": len(findings),
            "alerts_dispatched": alerts,
            "lookback_days": lookback,
        }
    finally:
        if own_conn:
            conn.close()


def get_summary(conn=None, limit: int = 20) -> dict[str, Any]:
    """Latest anomaly findings per account — for the security-canvas panel."""
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        _ensure_tables(conn)
        rows = _safe_query(
            conn,
            "SELECT account_id, risk_score, risk_band, rules_fired, created_at "
            "FROM insider_risk_scores s1 "
            "WHERE created_at = (SELECT MAX(created_at) FROM insider_risk_scores s2 "
            "WHERE s2.account_id = s1.account_id) "
            "ORDER BY risk_score DESC LIMIT %s",
            (limit,),
        )
        findings = []
        bands = {"high": 0, "elevated": 0, "normal": 0}
        for r in rows:
            band = _row_get(r, "risk_band", 2) or "normal"
            bands[band] = bands.get(band, 0) + 1
            rf = _row_get(r, "rules_fired", 3)
            try:
                rf = json.loads(rf) if isinstance(rf, str) else (rf or [])
            except Exception:
                rf = []
            findings.append({
                "account_id": _row_get(r, "account_id", 0),
                "risk_score": _row_get(r, "risk_score", 1),
                "risk_band": band,
                "rules_fired": rf,
                "created_at": _row_get(r, "created_at", 4),
            })
        return {"findings": findings, "bands": bands, "count": len(findings)}
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Insider-Risk UBA (lite) — deterministic anomaly scan")
    ap.add_argument("--scan", action="store_true", help="Run a scan over recent telemetry")
    ap.add_argument("--summary", action="store_true", help="Show latest findings")
    ap.add_argument("--force", action="store_true", help="Run even if disabled in config")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    config = load_config()
    if args.summary:
        out = get_summary()
    elif args.scan:
        if not config.get("enabled") and not args.force:
            out = {"enabled": False, "skipped": "insider-risk UBA is disabled "
                   "(set enabled: true in args/insider_risk_config.yaml or "
                   "ICDEV_INSIDER_RISK_ENABLED=1, or pass --force)"}
        else:
            out = run_scan(config)
    else:
        out = {"error": "specify --scan or --summary"}

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
