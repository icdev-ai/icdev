#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Black Hat Review — Simulated Competitor Analysis for GovProposal.

For each known competitor in the opportunity's agency/NAICS space, generates
an analysis covering strengths, weaknesses, likely approach, win themes,
teaming strategy, price estimate, and counter-strategies.  Results are
stored in the black_hat_analyses table so capture teams can refine their
proposals against the competitive landscape.

Usage:
    python tools/capture/black_hat_review.py --analyze --opp-id OPP-abc123 --json
    python tools/capture/black_hat_review.py --get --opp-id OPP-abc123 --json
    python tools/capture/black_hat_review.py --get --opp-id OPP-abc123 --competitor-name "Acme" --json
    python tools/capture/black_hat_review.py --counter-strategy --opp-id OPP-abc123 --json
"""

import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# Optional YAML import
try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bh_id():
    """Generate a black-hat analysis ID: BH- followed by 12 hex characters."""
    return "BH-" + secrets.token_hex(6)


def _now():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    """Open a database connection with WAL mode and foreign keys enabled.

    Args:
        db_path: Optional path override.  Falls back to DB_PATH.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, event_type, action, entity_type=None, entity_id=None,
           details=None):
    """Write an append-only audit trail record.

    Args:
        conn: Active database connection.
        event_type: Category of event.
        action: Human-readable description.
        entity_type: Type of entity affected.
        entity_id: ID of the affected entity.
        details: Optional JSON-serializable details dict.
    """
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event_type,
            "black_hat_review",
            action,
            entity_type,
            entity_id,
            json.dumps(details) if details else None,
            _now(),
        ),
    )


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


def _parse_json_field(value):
    """Safely parse a JSON string field."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


# ---------------------------------------------------------------------------
# Internal analysis helpers
# ---------------------------------------------------------------------------

def _load_opportunity(conn, opp_id):
    """Load opportunity record and raise if not found."""
    row = conn.execute(
        "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Opportunity not found: {opp_id}")
    return _row_to_dict(row)


def _find_competitors_for_opp(conn, opp):
    """Find competitors relevant to this opportunity's agency/NAICS.

    Uses agency match from competitor_wins and NAICS overlap from
    the competitors table.  Returns a de-duplicated list of competitor
    dicts.

    Args:
        conn: Active database connection.
        opp: Opportunity dict.

    Returns:
        list of competitor dicts (from competitors table).
    """
    agency = opp.get("agency") or ""
    naics = opp.get("naics_code") or ""

    # Strategy 1: competitors with wins at the same agency
    win_rows = conn.execute(
        "SELECT DISTINCT competitor_id FROM competitor_wins "
        "WHERE agency LIKE ? AND competitor_id IS NOT NULL LIMIT 20",
        (f"%{agency}%",),
    ).fetchall()
    comp_ids_from_wins = {r["competitor_id"] for r in win_rows}

    # Strategy 2: competitors whose NAICS overlap
    naics_rows = conn.execute(
        "SELECT id FROM competitors WHERE is_active = 1 "
        "AND naics_codes LIKE ? LIMIT 20",
        (f"%{naics}%",),
    ).fetchall()
    comp_ids_from_naics = {r["id"] for r in naics_rows}

    all_ids = comp_ids_from_wins | comp_ids_from_naics
    if not all_ids:
        # Fall back: just return all active competitors
        rows = conn.execute(
            "SELECT * FROM competitors WHERE is_active = 1 LIMIT 10"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    placeholders = ",".join("?" for _ in all_ids)
    rows = conn.execute(
        f"SELECT * FROM competitors WHERE id IN ({placeholders}) "
        "AND is_active = 1",
        list(all_ids),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_competitor_wins(conn, competitor_id, agency=None, limit=5):
    """Load recent wins for a specific competitor.

    Args:
        conn: Active database connection.
        competitor_id: Competitor ID.
        agency: Optional agency filter.
        limit: Max results.

    Returns:
        list of competitor_wins dicts.
    """
    if agency:
        rows = conn.execute(
            "SELECT * FROM competitor_wins "
            "WHERE competitor_id = ? AND agency LIKE ? "
            "ORDER BY award_date DESC LIMIT ?",
            (competitor_id, f"%{agency}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM competitor_wins "
            "WHERE competitor_id = ? "
            "ORDER BY award_date DESC LIMIT ?",
            (competitor_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_pricing_benchmarks(conn, naics_code):
    """Load pricing benchmarks for the NAICS code.

    Args:
        conn: Active database connection.
        naics_code: NAICS code string.

    Returns:
        dict of pricing stats, or None.
    """
    if not naics_code:
        return None
    row = conn.execute(
        "SELECT * FROM pricing_benchmarks WHERE naics_code = ? "
        "ORDER BY data_period DESC LIMIT 1",
        (naics_code,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _build_analysis(competitor, wins, opp, pricing):
    """Build a single competitor analysis record.

    Args:
        competitor: dict from competitors table.
        wins: list of dicts from competitor_wins.
        opp: Opportunity dict.
        pricing: Pricing benchmark dict or None.

    Returns:
        dict with analysis fields ready for insertion.
    """
    name = competitor.get("company_name", "Unknown")

    # Strengths
    strengths = []
    raw_strengths = competitor.get("strengths") or ""
    if raw_strengths:
        strengths.append(raw_strengths)
    if wins:
        strengths.append(
            f"Has {len(wins)} recent win(s) in this agency/NAICS space."
        )
    if competitor.get("clearance_level"):
        strengths.append(
            f"Holds {competitor['clearance_level']} clearance."
        )
    vehicles = _parse_json_field(competitor.get("contract_vehicles")) or []
    if vehicles:
        strengths.append(
            f"Has access to contract vehicles: {', '.join(vehicles[:3])}."
        )

    # Weaknesses
    weaknesses = []
    raw_weak = competitor.get("weaknesses") or ""
    if raw_weak:
        weaknesses.append(raw_weak)
    if not wins:
        weaknesses.append("No recent wins found in this specific space.")
    emp_count = competitor.get("employee_count")
    if emp_count and emp_count < 50:
        weaknesses.append(
            f"Smaller firm ({emp_count} employees) — capacity risk."
        )

    # Likely approach
    caps = competitor.get("capabilities") or ""
    approach = (
        f"Likely to emphasize {caps[:150]}. "
        f"Expected to leverage incumbent relationships "
        f"and past performance in {opp.get('agency', 'the agency')} space."
    )

    # Likely win themes
    likely_themes = []
    if wins:
        likely_themes.append("Proven track record with this customer")
    if raw_strengths:
        likely_themes.append(f"Technical depth: {raw_strengths[:100]}")
    likely_themes.append("Cost-competitive pricing based on market knowledge")

    # Likely teaming
    likely_teaming = (
        "May team with niche providers to fill capability gaps in "
        "areas outside their core competencies."
    )

    # Price estimate
    price_est = "Unable to estimate — no pricing benchmark data."
    if pricing:
        median = pricing.get("median_rate")
        avg = pricing.get("average_rate")
        if median and avg:
            # Apply competitive factor based on company size
            factor = 0.95 if (emp_count and emp_count > 200) else 1.02
            est = round((median + avg) / 2 * factor, 2)
            price_est = (
                f"Estimated blended rate ~${est}/hr "
                f"(market median ${median}, avg ${avg})."
            )

    # Counter-strategies
    counters = []
    if wins:
        counters.append(
            f"Differentiate on innovation — {name} may rely on "
            f"incumbent approach. Highlight fresh methodology."
        )
    if weaknesses:
        counters.append(
            f"Exploit identified weakness: {weaknesses[0][:120]}"
        )
    counters.append(
        "Emphasize risk reduction through proven past performance "
        "and customer-specific understanding."
    )
    counters.append(
        "Demonstrate lower total cost of ownership rather than "
        "competing on hourly rate alone."
    )

    return {
        "competitor_name": name,
        "competitor_strengths": json.dumps(strengths),
        "competitor_weaknesses": json.dumps(weaknesses),
        "likely_approach": approach,
        "likely_win_themes": json.dumps(likely_themes),
        "likely_teaming": likely_teaming,
        "price_estimate": price_est,
        "counter_strategies": json.dumps(counters),
        "risk_to_us": (
            "HIGH" if len(wins) >= 3
            else "MEDIUM" if len(wins) >= 1
            else "LOW"
        ),
    }


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def analyze_competitors(opp_id, db_path=None):
    """Run Black Hat analysis for all relevant competitors on an opportunity.

    For each known competitor in the opportunity's agency/NAICS space,
    generates a comprehensive analysis covering strengths, weaknesses,
    likely approach, win themes, teaming, price estimate, and counter-
    strategies.  Results are stored in the black_hat_analyses table.

    Args:
        opp_id: Opportunity ID to analyze.
        db_path: Optional database path override.

    Returns:
        list of analysis dicts, one per competitor.

    Raises:
        ValueError: If opportunity not found.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
        agency = opp.get("agency")
        naics = opp.get("naics_code")

        competitors = _find_competitors_for_opp(conn, opp)
        pricing = _load_pricing_benchmarks(conn, naics)

        results = []
        now = _now()

        for comp in competitors:
            wins = _load_competitor_wins(conn, comp["id"], agency)
            analysis = _build_analysis(comp, wins, opp, pricing)

            bh_id = _bh_id()
            conn.execute(
                "INSERT INTO black_hat_analyses "
                "(id, opportunity_id, competitor_name, "
                " competitor_strengths, competitor_weaknesses, "
                " likely_approach, likely_win_themes, likely_teaming, "
                " price_estimate, counter_strategies, risk_to_us, "
                " created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bh_id, opp_id, analysis["competitor_name"],
                    analysis["competitor_strengths"],
                    analysis["competitor_weaknesses"],
                    analysis["likely_approach"],
                    analysis["likely_win_themes"],
                    analysis["likely_teaming"],
                    analysis["price_estimate"],
                    analysis["counter_strategies"],
                    analysis["risk_to_us"],
                    now,
                ),
            )

            result_entry = {
                "id": bh_id,
                "opportunity_id": opp_id,
                "competitor_name": analysis["competitor_name"],
                "competitor_strengths": _parse_json_field(
                    analysis["competitor_strengths"]
                ),
                "competitor_weaknesses": _parse_json_field(
                    analysis["competitor_weaknesses"]
                ),
                "likely_approach": analysis["likely_approach"],
                "likely_win_themes": _parse_json_field(
                    analysis["likely_win_themes"]
                ),
                "likely_teaming": analysis["likely_teaming"],
                "price_estimate": analysis["price_estimate"],
                "counter_strategies": _parse_json_field(
                    analysis["counter_strategies"]
                ),
                "risk_to_us": analysis["risk_to_us"],
                "created_at": now,
            }
            results.append(result_entry)

        _audit(conn, "capture.black_hat_analyze",
               f"Analyzed {len(results)} competitors for {opp_id}",
               "black_hat_analyses", opp_id,
               {"count": len(results),
                "competitors": [r["competitor_name"] for r in results]})
        conn.commit()
        return results
    finally:
        conn.close()


def get_analysis(opp_id, competitor_name=None, db_path=None):
    """Retrieve black hat analyses for an opportunity.

    Args:
        opp_id: Opportunity ID.
        competitor_name: Optional filter by competitor name.
        db_path: Optional database path override.

    Returns:
        list of analysis dicts.

    Raises:
        ValueError: If opp_id is not provided.
    """
    if not opp_id:
        raise ValueError("Must provide --opp-id")

    conn = _get_db(db_path)
    try:
        if competitor_name:
            rows = conn.execute(
                "SELECT * FROM black_hat_analyses "
                "WHERE opportunity_id = ? AND competitor_name LIKE ? "
                "ORDER BY created_at DESC",
                (opp_id, f"%{competitor_name}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM black_hat_analyses "
                "WHERE opportunity_id = ? ORDER BY created_at DESC",
                (opp_id,),
            ).fetchall()

        results = []
        for r in rows:
            d = _row_to_dict(r)
            for field in ("competitor_strengths", "competitor_weaknesses",
                          "likely_win_themes", "counter_strategies"):
                d[field] = _parse_json_field(d.get(field))
            results.append(d)
        return results
    finally:
        conn.close()


def generate_counter_strategy(opp_id, db_path=None):
    """Generate consolidated counter-strategy recommendations.

    Aggregates all black hat analyses for the opportunity and produces
    a prioritized list of counter-strategy actions that address the
    most dangerous competitors first.

    Args:
        opp_id: Opportunity ID.
        db_path: Optional database path override.

    Returns:
        dict with competitor_count, risk_summary, and
        prioritized_recommendations.

    Raises:
        ValueError: If no analyses found for the opportunity.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM black_hat_analyses WHERE opportunity_id = ? "
            "ORDER BY risk_to_us DESC, created_at DESC",
            (opp_id,),
        ).fetchall()
        if not rows:
            raise ValueError(
                f"No black hat analyses found for {opp_id}. "
                "Run --analyze first."
            )

        analyses = [_row_to_dict(r) for r in rows]

        # Risk summary
        risk_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for a in analyses:
            risk = a.get("risk_to_us", "LOW")
            risk_counts[risk] = risk_counts.get(risk, 0) + 1

        # Collect all counter-strategies, prioritized by risk
        risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        sorted_analyses = sorted(
            analyses,
            key=lambda x: risk_order.get(x.get("risk_to_us", "LOW"), 2),
        )

        recommendations = []
        seen_strategies = set()

        for a in sorted_analyses:
            comp = a.get("competitor_name", "Unknown")
            risk = a.get("risk_to_us", "LOW")
            counters = _parse_json_field(a.get("counter_strategies")) or []
            if isinstance(counters, str):
                counters = [counters]

            for strategy in counters:
                # De-duplicate similar strategies (simple normalization)
                norm = strategy.lower().strip()[:80]
                if norm not in seen_strategies:
                    seen_strategies.add(norm)
                    recommendations.append({
                        "target_competitor": comp,
                        "risk_level": risk,
                        "strategy": strategy,
                        "priority": (
                            "CRITICAL" if risk == "HIGH"
                            else "IMPORTANT" if risk == "MEDIUM"
                            else "CONSIDER"
                        ),
                    })

        # Aggregate theme-level guidance
        overall_guidance = []
        if risk_counts.get("HIGH", 0) > 0:
            overall_guidance.append(
                "HIGH-RISK competitors identified. Focus proposal on "
                "innovation and risk-reduction themes that neutralize "
                "incumbent advantages."
            )
        if risk_counts.get("MEDIUM", 0) > 0:
            overall_guidance.append(
                "MEDIUM-RISK competitors present. Ensure discriminators "
                "are clearly articulated in every evaluation section."
            )
        overall_guidance.append(
            "Validate all claims with concrete past performance evidence "
            "and quantifiable metrics."
        )

        result = {
            "opportunity_id": opp_id,
            "competitor_count": len(analyses),
            "risk_summary": risk_counts,
            "overall_guidance": overall_guidance,
            "prioritized_recommendations": recommendations[:20],
        }

        _audit(conn, "capture.black_hat_counter",
               f"Generated counter-strategy for {opp_id} "
               f"({len(analyses)} competitors)",
               "black_hat_analyses", opp_id,
               {"recommendation_count": len(recommendations)})
        conn.commit()
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build argument parser for the CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal Black Hat Review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --analyze --opp-id OPP-abc123 --json\n"
            "  %(prog)s --get --opp-id OPP-abc123 --json\n"
            "  %(prog)s --get --opp-id OPP-abc123 "
            "--competitor-name 'Acme' --json\n"
            "  %(prog)s --counter-strategy --opp-id OPP-abc123 --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--analyze", action="store_true",
                        help="Run black hat analysis for an opportunity")
    action.add_argument("--get", action="store_true",
                        help="Retrieve existing analyses")
    action.add_argument("--counter-strategy", action="store_true",
                        help="Generate consolidated counter-strategies")

    parser.add_argument("--opp-id", help="Opportunity ID")
    parser.add_argument("--competitor-name",
                        help="Filter by competitor name (for --get)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.analyze:
            if not args.opp_id:
                parser.error("--analyze requires --opp-id")
            result = analyze_competitors(args.opp_id, db_path=db)

        elif args.get:
            if not args.opp_id:
                parser.error("--get requires --opp-id")
            result = get_analysis(
                opp_id=args.opp_id,
                competitor_name=args.competitor_name,
                db_path=db,
            )

        elif args.counter_strategy:
            if not args.opp_id:
                parser.error("--counter-strategy requires --opp-id")
            result = generate_counter_strategy(args.opp_id, db_path=db)

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                print(f"Found {len(result)} analysis record(s):")
                for item in result:
                    comp = item.get("competitor_name", "?")
                    risk = item.get("risk_to_us", "?")
                    print(f"  [{item.get('id', '?')}] {comp} (Risk: {risk})")
            elif isinstance(result, dict):
                for key, value in result.items():
                    if isinstance(value, (list, dict)):
                        print(f"  {key}: {json.dumps(value, default=str)}")
                    else:
                        print(f"  {key}: {value}")

    except ValueError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        if args.json:
            print(json.dumps({"error": f"Database error: {exc}"}, indent=2))
        else:
            print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
