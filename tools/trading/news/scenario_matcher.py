# fmt: off
# =============================================================================
# SCENARIO_TEMPLATES REFERENCE TABLE (auto-generated from scenario_engine.py)
# Source: tools/trading/market_intel/scenario_engine.py — SCENARIO_TEMPLATES dict
# Used by D2 keyword-map task to wire trigger phrases → scenario keys.
#
# | # | Key                           | Name                                              | Category        | Typical Trigger Phrase                                      |
# |---|-------------------------------|---------------------------------------------------|-----------------|-------------------------------------------------------------|
# |  1| rate_hike_25bps               | Interest Rate Hike (+25bps)                       | monetary_policy | "rate hike", "raised rates", "25 basis points"              |
# |  2| rate_hike_50bps               | Aggressive Rate Hike (+50bps)                     | monetary_policy | "+50bps", "aggressive hike", "jumbo hike"                   |
# |  3| rate_cut_25bps                | Interest Rate Cut (-25bps)                        | monetary_policy | "rate cut", "cuts rates", "easing"                          |
# |  4| cbdc_adoption                 | Federal Reserve CBDC Launch                       | monetary_policy | "digital dollar", "cbdc", "central bank digital"            |
# |  5| war_middle_east               | Major Middle East Conflict (e.g., Iran)           | geopolitical    | "middle east conflict", "iran strike", "red sea"            |
# |  6| war_asia_pacific              | Asia-Pacific Conflict (e.g., Taiwan Strait)       | geopolitical    | "taiwan strait", "china invasion", "taiwan blockade"        |
# |  7| major_lawsuit                 | Major Regulatory Lawsuit (e.g., antitrust)        | legal           | "antitrust", "doj lawsuit", "ftc lawsuit"                   |
# |  8| terrorist_attack              | Major Terrorist Attack                            | security        | "terrorist attack", "terrorism", "bomb attack"              |
# |  9| natural_disaster              | Major Natural Disaster                            | natural         | "hurricane", "earthquake", "wildfire"                       |
# | 10| pandemic_outbreak             | Pandemic / Health Crisis                          | health          | "pandemic", "outbreak", "lockdown"                          |
# | 11| historical_2008_gfc           | 2008 Global Financial Crisis (replay)             | historical      | "lehman collapse", "credit freeze", "financial crisis"      |
# | 12| historical_2001_dotcom        | 2001 Dot-Com Crash (replay)                       | historical      | "dot-com crash", "tech bubble", "nasdaq crash"              |
# | 13| historical_covid_2020         | COVID-19 Pandemic Crash (replay)                  | historical      | "covid crash", "2020 pandemic", "fastest drop"              |
# | 14| historical_gulf_war_1990      | Gulf War 1990 (replay)                            | historical      | "gulf war", "kuwait invasion", "iraq kuwait"                |
# | 15| historical_iraq_2003          | Iraq War 2003 (replay)                            | historical      | "iraq war 2003", "us invasion iraq"                         |
# | 16| historical_ukraine_2022       | Russia-Ukraine War 2022 (replay)                  | historical      | "russia ukraine", "ukraine invasion", "russia invades"      |
# | 17| historical_fed_hike_cycle_2022| 2022-23 Fed Rate Hike Cycle (replay)              | historical      | "fed hike cycle", "svb collapse", "zero to five percent"    |
# | 18| historical_nixon_shock_1971   | Nixon Shock 1971 — End of Gold Standard (replay)  | historical      | "gold standard", "nixon shock", "dollar gold"               |
# | 19| historical_black_monday_1987  | Black Monday 1987 (replay)                        | historical      | "black monday", "program trading", "dow fell 22"            |
# | 20| nfp_hot_surprise              | Non-Farm Payrolls — Hot Surprise (+150K)          | economic_data   | "payrolls beat", "nfp surprise", "hot jobs report"          |
# | 21| cpi_hot_surprise              | CPI — Hot Inflation Surprise (+0.3%)              | economic_data   | "cpi surprise", "cpi above", "hot cpi"                     |
# | 22| ppi_hot_surprise              | PPI — Producer Inflation Surprise                 | economic_data   | "ppi surprise", "producer prices surge"                     |
# | 23| gdp_miss                      | GDP Growth Miss — Recession Signal                | economic_data   | "gdp miss", "gdp contraction", "negative gdp"              |
# | 24| unemployment_spike            | Unemployment Rate Spike (+0.3%)                   | economic_data   | "unemployment spike", "layoffs surge", "jobless claims"     |
# | 25| retail_sales_miss             | Retail Sales Miss — Consumer Spending Weakness    | economic_data   | "retail sales miss", "consumer spending decline"            |
# | 26| ism_contraction               | ISM Manufacturing — Contraction (<50)             | economic_data   | "ism below 50", "manufacturing contraction", "pmi below 50" |
# | 27| housing_starts_drop           | Housing Starts Plunge — Housing Recession         | economic_data   | "housing starts drop", "housing recession"                  |
# | 28| consumer_confidence_drop      | Consumer Confidence Collapse                      | economic_data   | "consumer confidence drop", "confidence plunge"             |
# | 29| midterm_volatility            | Midterm Election Year Volatility                  | political       | "midterm election", "midterm volatility"                    |
# | 30| sweet_spot_entry              | Election Cycle Sweet Spot (Midterm Q4 → Pre-Elec) | political       | "election cycle sweet spot", "pre-election rally"           |
# | 31| election_uncertainty_q3       | Presidential Election Q3 Uncertainty              | political       | "election uncertainty", "presidential race"                 |
# | 32| party_change_sector_rotation  | Party Change — Sector Rotation                    | political       | "party change", "new administration", "sector rotation"     |
# | 33| trade_war_escalation          | Trade War Escalation (Tariffs)                    | trade           | "trade war", "tariff", "trade escalation"                   |
# =============================================================================
# fmt: on
# CUI // SP-CTI
"""FathomDesk News — scenario matcher.

Maps news items to scenario_engine SCENARIO_TEMPLATES via deterministic
keyword matching. Optionally wires to scenario_engine.run_scenario() for
on-demand execution.

Scenario keys (33 total from scenario_engine.py):
  monetary_policy: rate_hike_25bps, rate_hike_50bps, rate_cut_25bps, cbdc_adoption
  geopolitical: war_middle_east, war_asia_pacific
  legal: major_lawsuit
  security: terrorist_attack
  natural: natural_disaster
  health: pandemic_outbreak
  historical: historical_2008_gfc, historical_2001_dotcom, historical_covid_2020,
              historical_gulf_war_1990, historical_iraq_2003, historical_ukraine_2022,
              historical_fed_hike_cycle_2022, historical_nixon_shock_1971,
              historical_black_monday_1987
  economic_data: nfp_hot_surprise, cpi_hot_surprise, ppi_hot_surprise, gdp_miss,
                 unemployment_spike, retail_sales_miss, ism_contraction,
                 housing_starts_drop, consumer_confidence_drop
  political: midterm_volatility, sweet_spot_entry, election_uncertainty_q3,
             party_change_sector_rotation
  trade: trade_war_escalation

CLI:
    python -m tools.trading.news.scenario_matcher --batch --json
    python -m tools.trading.news.scenario_matcher --id <news_id> --json
"""

from __future__ import annotations

import json
import sys

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from tools.trading.news.db import insert_scenario_link

# ---------------------------------------------------------------------------
# Keyword -> scenario_key mapping
# ---------------------------------------------------------------------------
SCENARIO_KEYWORDS: dict[str, list[str]] = {
    "rate_hike_25bps": [
        "rate hike", "hikes rates", "+25bps", "raised rates", "25 basis points",
        "quarter-point hike", "tightening",
    ],
    "rate_hike_50bps": [
        "+50bps", "50 basis points", "aggressive hike", "double hike",
        "half-point hike", "jumbo hike",
    ],
    "rate_cut_25bps": [
        "rate cut", "cuts rates", "-25bps", "lowered rates", "25 basis point cut",
        "quarter-point cut", "easing",
    ],
    "cbdc_adoption": [
        "cbdc", "digital dollar", "digital currency", "central bank digital",
    ],
    "war_middle_east": [
        "middle east conflict", "iran strike", "iran attack", "israel war",
        "gaza", "hezbollah", "houthi", "red sea", "strait of hormuz",
    ],
    "war_asia_pacific": [
        "taiwan strait", "taiwan conflict", "china invasion", "south china sea",
        "asia pacific conflict", "taiwan blockade",
    ],
    "major_lawsuit": [
        "antitrust", "doj lawsuit", "sec charges", "regulatory action",
        "ftc lawsuit", "monopoly ruling",
    ],
    "terrorist_attack": [
        "terrorist attack", "terrorism", "bomb attack", "mass shooting",
        "homeland security threat",
    ],
    "natural_disaster": [
        "hurricane", "earthquake", "tsunami", "wildfire", "flood",
        "climate disaster", "category 5",
    ],
    "pandemic_outbreak": [
        "pandemic", "outbreak", "epidemic", "virus spread", "who emergency",
        "lockdown", "quarantine",
    ],
    "nfp_hot_surprise": [
        "nfp surprise", "payrolls beat", "jobs surge", "hot jobs report",
        "payrolls above", "nonfarm payrolls beat",
    ],
    "cpi_hot_surprise": [
        "cpi surprise", "cpi above", "inflation surprise", "hot cpi",
        "cpi beat", "consumer prices surge",
    ],
    "ppi_hot_surprise": [
        "ppi surprise", "ppi above", "producer prices surge", "hot ppi",
    ],
    "gdp_miss": [
        "gdp miss", "gdp below", "gdp contraction", "recession signal",
        "negative gdp", "economic contraction",
    ],
    "unemployment_spike": [
        "unemployment spike", "unemployment surge", "jobless claims surge",
        "unemployment above", "layoffs surge",
    ],
    "retail_sales_miss": [
        "retail sales miss", "retail sales below", "consumer spending decline",
        "retail weakness",
    ],
    "ism_contraction": [
        "ism contraction", "ism below 50", "manufacturing contraction",
        "pmi below 50", "manufacturing decline",
    ],
    "housing_starts_drop": [
        "housing starts drop", "housing starts plunge", "housing recession",
        "new home construction falls",
    ],
    "consumer_confidence_drop": [
        "consumer confidence drop", "consumer sentiment drop",
        "confidence plunge", "consumer confidence falls",
    ],
    "trade_war_escalation": [
        "trade war", "tariff", "tariffs", "trade escalation", "retaliatory tariff",
        "trade sanctions", "import duties", "export ban",
    ],
    "midterm_volatility": [
        "midterm election", "midterm volatility",
    ],
    "sweet_spot_entry": [
        "election cycle sweet spot", "pre-election rally",
    ],
    "election_uncertainty_q3": [
        "election uncertainty", "presidential race",
    ],
    "party_change_sector_rotation": [
        "party change", "new administration", "sector rotation election",
    ],
}

# Weight for multi-word vs single-word matches
_KEYWORD_WEIGHT = {"multi_word": 1.5, "single_word": 1.0}


def match(news_id: str) -> tuple[str, float, str] | None:
    """Match a news item to a scenario key.

    Returns (scenario_key, confidence, reason) or None if no match >= 0.3.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT title, summary FROM ad_news_items WHERE id = %s", (news_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
    finally:
        conn.close()

    title = (row[0] or "").lower()
    summary = (row[1] or "").lower()
    text = f"{title} {summary}"

    best_key = None
    best_conf = 0.0
    best_reason = ""

    for scenario_key, keywords in SCENARIO_KEYWORDS.items():
        matched = []
        for kw in keywords:
            if kw.lower() in text:
                matched.append(kw)

        if not matched:
            continue

        # Confidence: fraction of keywords matched, weighted
        total_weight = sum(
            _KEYWORD_WEIGHT["multi_word"] if " " in kw else _KEYWORD_WEIGHT["single_word"]
            for kw in keywords
        )
        matched_weight = sum(
            _KEYWORD_WEIGHT["multi_word"] if " " in kw else _KEYWORD_WEIGHT["single_word"]
            for kw in matched
        )
        confidence = matched_weight / total_weight if total_weight > 0 else 0.0

        if confidence > best_conf:
            best_conf = confidence
            best_key = scenario_key
            best_reason = f"Matched keywords: {', '.join(matched)}"

    if best_key and best_conf >= 0.3:
        return (best_key, round(best_conf, 3), best_reason)
    return None


def match_and_run(news_id: str) -> dict | None:
    """Match news to scenario, run it, and record the link.

    Returns dict with run_id, scenario_key, confidence, reason, or None.
    """
    result = match(news_id)
    if not result:
        return None

    scenario_key, confidence, reason = result

    # Run the scenario
    try:
        from tools.trading.market_intel.scenario_engine import run_scenario
        run_result = run_scenario(scenario_key)
        run_id = run_result.get("run_id") if isinstance(run_result, dict) else None
    except Exception:
        run_id = None

    # Record the link
    link_id = insert_scenario_link(
        news_id,
        run_id,
        scenario_key,
        confidence,
        reason,
    )

    return {
        "news_id": news_id,
        "scenario_key": scenario_key,
        "match_confidence": confidence,
        "match_reason": reason,
        "scenario_run_id": run_id,
        "link_id": link_id,
    }


def batch_match() -> dict:
    """Match all news items that have impact_level='high' or 'medium'."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, impact_level FROM ad_news_items "
            "WHERE impact_level IN ('high', 'medium') "
            "ORDER BY published_at DESC"
        )
        items = cur.fetchall()
    finally:
        conn.close()

    stats = {
        "total_items": len(items),
        "matched": 0,
        "unmatched": 0,
        "high_matched": 0,
        "high_total": 0,
        "by_scenario": {},
        "misses": [],
    }

    for item_id, impact in items:
        if impact == "high":
            stats["high_total"] += 1

        result = match(item_id)
        if result:
            scenario_key, confidence, reason = result
            stats["matched"] += 1
            if impact == "high":
                stats["high_matched"] += 1
            stats["by_scenario"][scenario_key] = stats["by_scenario"].get(scenario_key, 0) + 1

            # Record the link (without running scenario)
            insert_scenario_link(
                item_id,
                None,  # scenario_run_id
                scenario_key,
                confidence,
                reason,
            )
        else:
            stats["unmatched"] += 1
            stats["misses"].append(item_id)

    # Compute high-impact match rate
    if stats["high_total"] > 0:
        stats["high_match_rate"] = round(stats["high_matched"] / stats["high_total"], 3)
    else:
        stats["high_match_rate"] = 0.0

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="FathomDesk News Scenario Matcher")
    parser.add_argument("--batch", action="store_true", help="Match all high/medium items")
    parser.add_argument("--id", help="Match a single news item")
    parser.add_argument("--run", action="store_true", help="Also run matched scenario")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.batch:
        result = batch_match()
    elif args.id:
        if args.run:
            result = match_and_run(args.id)
        else:
            m = match(args.id)
            result = {"scenario_key": m[0], "confidence": m[1], "reason": m[2]} if m else None
    else:
        parser.print_help()
        sys.exit(0)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)
    sys.exit(0)


if __name__ == "__main__":
    main()
