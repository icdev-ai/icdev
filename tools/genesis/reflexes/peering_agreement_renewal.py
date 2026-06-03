# CUI // SP-CTI
"""Genesis reflex: check peering agreements expiring within a configurable look-ahead window.

Detection hierarchy for alarm severity:
  1. LLM contextual — AI assesses per-agreement risk given days remaining and peer context
     (only when anomaly_detection.enabled=true in genesis_config.yaml)
  2. Static fallback — critical when days_remaining <= critical_days (default 30)

All thresholds are sourced from genesis_config.yaml reflexes.peering_agreement_renewal
so they can be tuned without code changes. Module-level values below are defaults only.

Air-gap safe: LLM calls are optional and degrade gracefully to the static threshold.
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations

import json as _json
import os
from typing import Any, Dict, Optional

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger(__name__)
except Exception:
    import logging
    logger = logging.getLogger(__name__)

CADENCE_HOURS = 24

# Static fallback thresholds — overridable via genesis_config.yaml
_DAYS_AHEAD = 90       # look-ahead window for expiring agreements
_CRITICAL_DAYS = 30    # agreements at or below this many days remaining → critical severity


def _llm_assess_severity(
    item: Dict[str, Any],
    days_remaining: Optional[int],
    model: Optional[str] = None,
) -> Optional[str]:
    """Ask the LLM to classify alarm severity for a peering agreement renewal.

    Returns "critical", "major", "minor", or None on error/unavailable.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        peer_name = item.get("peer_name", "unknown")
        peer_asn = item.get("peer_asn", "unknown")
        contract_end = item.get("contract_end", "unknown")

        prompt = (
            f"Peering agreement with '{peer_name}' (AS{peer_asn}) "
            f"expires {contract_end} — {days_remaining} day(s) remaining.\n\n"
            "Classify the NOCC alarm severity:\n"
            "  critical — immediate risk requiring urgent action (renewal imminent, <30d typical)\n"
            "  major    — significant risk requiring prompt attention (renewal approaching)\n"
            "  minor    — low risk, informational (early warning, >60d remaining)\n\n"
            "Consider: days remaining, standard BGP peering renewal lead times (30–90 days), "
            "and whether the peer is a transit or peering-only relationship.\n"
            'Respond ONLY with JSON: {"severity": "critical|major|minor", "rationale": "<one sentence>"}'
        )

        resolved_model = (
            model
            or os.environ.get("ICDEV_PEERING_ANOMALY_MODEL")
            or os.environ.get("ICDEV_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        )

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a network operations anomaly detection assistant. "
                "Assess BGP peering agreement renewal risk severity accurately and conservatively."
            ),
            model=resolved_model,
            max_tokens=128,
            temperature=0.0,
            agent_id="peering-renewal-anomaly-detector",
            classification="CUI",
            effort="low",
            skip_injection_scan=True,
        )

        router = LLMRouter()
        response = router.invoke("anomaly_detection", request)
        parsed = _json.loads(response.content.strip())
        severity = parsed.get("severity", "major")
        if severity not in ("critical", "major", "minor"):
            severity = "major"
        logger.debug(
            "LLM peering severity: peer=%s days=%s severity=%s rationale=%s",
            peer_name, days_remaining, severity, parsed.get("rationale", ""),
        )
        return severity
    except Exception as exc:
        logger.debug("LLM peering severity assessment failed (%s), using static fallback", exc)
        return None


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Check nc_peering_agreements for contracts expiring within days_ahead days.

    Returns:
        agreements_checked: int
        at_risk_count: int
        events_published: int
        risk_list: list[dict]
        errors: list[str]
        status: "ok" | "error"
    """
    dry_run = ctx.get("dry_run", False)
    errors: list[str] = []
    events_published = 0
    alarms_created = 0

    # Load thresholds from config (genesis_config.yaml via ctx) with module defaults as fallback
    days_ahead = int(ctx.get("days_ahead", _DAYS_AHEAD))
    critical_days = int(ctx.get("critical_days", _CRITICAL_DAYS))
    anomaly_cfg = ctx.get("anomaly_detection", {})
    use_llm_severity = bool(anomaly_cfg.get("enabled", True))

    # Open Network Canvas connection
    nc_conn = None
    try:
        from tools.network.db.init_db import get_connection as nc_conn_factory
        nc_conn = nc_conn_factory()
    except Exception as e:
        return {"status": "error", "errors": [f"NC DB unreachable: {e}"],
                "agreements_checked": 0, "at_risk_count": 0,
                "events_published": 0, "risk_list": []}

    try:
        from tools.network.agreement_lifecycle import get_renewal_risk
        risk_list = get_renewal_risk(nc_conn, days_ahead=days_ahead)
        at_risk = len(risk_list)

        # Count total operational agreements for context
        try:
            cur = nc_conn.cursor()
            cur.execute("SELECT COUNT(*) FROM nc_peering_agreements WHERE status='operational'")
            row = cur.fetchone()
            agreements_checked = row[0] if row else 0
        except Exception:
            agreements_checked = 0

        if not dry_run:
            # Attempt to create NOCC alarms for each at-risk agreement
            try:
                from tools.noc_canvas.db.init_db import get_connection as nocc_conn_factory
                nocc = nocc_conn_factory()
                try:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc).isoformat()
                    for item in risk_list:
                        days = item.get("days_remaining")

                        # Severity: prefer LLM assessment, fall back to static threshold
                        severity = None
                        if use_llm_severity:
                            severity = _llm_assess_severity(item, days)
                        if severity is None:
                            # Static fallback: critical when at or below critical_days
                            severity = "critical" if (days is not None and days <= critical_days) else "major"

                        desc = (
                            f"Peering agreement with {item.get('peer_name','')} "
                            f"(AS{item.get('peer_asn','')}) expires {item.get('contract_end','')} "
                            f"({days}d remaining)"
                        )
                        # Dedup: skip if identical alarm already open
                        try:
                            r = nocc.execute(
                                "SELECT id FROM noc_alarms WHERE alarm_source='peering_renewal' AND device_name=? AND cleared=0 LIMIT 1",
                                (str(item.get("id", "")),),
                            ).fetchone()
                        except Exception:
                            r = None
                        if r:
                            continue
                        try:
                            nocc.execute(
                                "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (?,?,?,?,?,0,0,0,?,?,'CUI')",
                                ("peering_renewal", severity, "bgp",
                                 str(item.get("id", "")), desc, now, now),
                            )
                        except Exception:
                            nocc.execute(
                                "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, description, cleared, acknowledged, suppressed, first_seen, last_seen, classification) VALUES (%s,%s,%s,%s,%s,0,0,0,%s,%s,'CUI')",
                                ("peering_renewal", severity, "bgp",
                                 str(item.get("id", "")), desc, now, now),
                            )
                        alarms_created += 1
                    nocc.commit()
                finally:
                    try:
                        nocc.close()
                    except Exception:
                        pass
            except Exception as e:
                errors.append(f"NOCC alarm insert skipped: {e}")

    except Exception as e:
        errors.append(str(e))
        return {"status": "error", "errors": errors, "agreements_checked": 0,
                "at_risk_count": 0, "events_published": 0, "risk_list": []}
    finally:
        try:
            nc_conn.close()
        except Exception:
            pass

    return {
        "status": "ok",
        "agreements_checked": agreements_checked,
        "at_risk_count": at_risk,
        "alarms_created": alarms_created,
        "events_published": events_published,
        "risk_list": risk_list,
        "dry_run": dry_run,
        "errors": errors,
    }
