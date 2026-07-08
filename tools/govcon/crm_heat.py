#!/usr/bin/env python3
# CUI // SP-CTI
"""CRM engagement heat — per-agency relationship warmth for the GovCon BD
pipeline view (prop-cap-14).

Joins pg_crm_accounts (by agency name) to each account's most recent
pg_crm_engagement_scores row, aggregating across accounts sharing an agency
(sum interaction counts, average scores) into a single heat reading per
agency: level (cold/warm/hot), numeric score, total interaction count, and
most recent interaction timestamp.

Usage:
    python tools/govcon/crm_heat.py --agencies "DoD,DHS" --json
"""
import argparse
import json

from tools.db.storage import get_connection

HOT_THRESHOLD = 60.0
WARM_THRESHOLD = 25.0


def _get_db():
    return get_connection()


def _heat_level(score):
    if score >= HOT_THRESHOLD:
        return "hot"
    if score >= WARM_THRESHOLD:
        return "warm"
    return "cold"


def get_engagement_heat_by_agency(agencies):
    """Return a per-agency CRM engagement heat reading.

    Args:
        agencies: Iterable of agency name strings (e.g. pipeline opportunity
            agencies). Duplicates and falsy values are ignored.

    Returns:
        Dict keyed by agency name -> {level, score, interaction_count,
        last_interaction_at, account_count}. Agencies with no matching CRM
        account, or if the CRM tables don't exist yet, are simply absent
        from the result (never raises).
    """
    agency_list = sorted({a for a in agencies if a})
    if not agency_list:
        return {}

    try:
        conn = _get_db()
    except Exception:
        return {}

    result = {}
    try:
        placeholders = ",".join(["%s"] * len(agency_list))
        accounts = conn.execute(
            f"SELECT id, agency FROM pg_crm_accounts WHERE agency IN ({placeholders})",
            tuple(agency_list),
        ).fetchall()

        accounts_by_agency = {}
        for row in accounts:
            d = dict(row)
            accounts_by_agency.setdefault(d["agency"], []).append(d["id"])

        for agency, account_ids in accounts_by_agency.items():
            scores = []
            interaction_total = 0
            last_interaction = None
            for account_id in account_ids:
                latest = conn.execute(
                    "SELECT score, interaction_count, last_interaction_at "
                    "FROM pg_crm_engagement_scores WHERE account_id = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (account_id,),
                ).fetchone()
                if not latest:
                    continue
                d = dict(latest)
                scores.append(float(d["score"] or 0.0))
                interaction_total += int(d["interaction_count"] or 0)
                if d["last_interaction_at"] and (
                    last_interaction is None or d["last_interaction_at"] > last_interaction
                ):
                    last_interaction = d["last_interaction_at"]

            if not scores:
                continue
            avg_score = sum(scores) / len(scores)
            result[agency] = {
                "level": _heat_level(avg_score),
                "score": round(avg_score, 1),
                "interaction_count": interaction_total,
                "last_interaction_at": last_interaction,
                "account_count": len(account_ids),
            }
    except Exception:
        return {}
    finally:
        conn.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="CRM engagement heat by agency")
    parser.add_argument("--agencies", required=True, help="Comma-separated agency names")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = get_engagement_heat_by_agency(args.agencies.split(","))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
