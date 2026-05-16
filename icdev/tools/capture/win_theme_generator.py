#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Win Theme Generator for GovProposal.

Generates discriminating win themes by analyzing opportunity descriptions,
customer profiles, competitor landscape, and company capabilities from the
knowledge base.  Each theme includes supporting evidence and a discriminator
type so capture leads can map themes to proposal sections.

Usage:
    python tools/capture/win_theme_generator.py --generate --opp-id OPP-abc123 --json
    python tools/capture/win_theme_generator.py --get --opp-id OPP-abc123 --json
    python tools/capture/win_theme_generator.py --get --proposal-id PROP-001 --json
    python tools/capture/win_theme_generator.py --score --theme-id WT-abc123 --json
    python tools/capture/win_theme_generator.py --map --proposal-id PROP-001 --json
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

# Optional YAML import for future config extensions
try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None

# Discriminator types matching the CHECK constraint on win_themes.discriminator_type
DISCRIMINATOR_TYPES = (
    "technical", "management", "cost", "past_performance",
    "personnel", "innovation", "risk",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wt_id():
    """Generate a win-theme ID: WT- followed by 12 hex characters."""
    return "WT-" + secrets.token_hex(6)


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
        event_type: Category of event (e.g. 'capture.win_theme').
        action: Human-readable description of the action.
        entity_type: Type of entity affected.
        entity_id: ID of the affected entity.
        details: Optional JSON-serializable details dict.
    """
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event_type,
            "win_theme_generator",
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


def _load_customer_profile(conn, agency):
    """Load customer profile for an agency, if available."""
    row = conn.execute(
        "SELECT * FROM customer_profiles WHERE agency = ? LIMIT 1",
        (agency,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _load_capabilities(conn, naics_code=None, limit=30):
    """Load company capabilities from the knowledge base."""
    if naics_code:
        rows = conn.execute(
            "SELECT * FROM kb_entries WHERE is_active = 1 "
            "AND entry_type IN ('capability', 'solution_architecture', "
            "'methodology', 'tool_technology', 'domain_expertise') "
            "AND (naics_codes LIKE ? OR naics_codes IS NULL) "
            "ORDER BY quality_score DESC NULLS LAST, usage_count DESC "
            "LIMIT ?",
            (f"%{naics_code}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM kb_entries WHERE is_active = 1 "
            "AND entry_type IN ('capability', 'solution_architecture', "
            "'methodology', 'tool_technology', 'domain_expertise') "
            "ORDER BY quality_score DESC NULLS LAST, usage_count DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_past_performances(conn, agency=None, naics_code=None, limit=10):
    """Load relevant past-performance records."""
    clauses = ["is_active = 1"]
    params = []
    if agency:
        clauses.append("agency LIKE ?")
        params.append(f"%{agency}%")
    if naics_code:
        clauses.append("naics_code = ?")
        params.append(naics_code)
    where = " AND ".join(clauses)
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM past_performances WHERE {where} "
        "ORDER BY cpars_rating ASC, period_of_performance_end DESC "
        "LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_competitor_landscape(conn, opp_id, agency=None, naics_code=None):
    """Load competitor data relevant to this opportunity space."""
    competitors = []
    if agency or naics_code:
        clauses = ["is_active = 1"]
        params = []
        if naics_code:
            clauses.append("naics_codes LIKE ?")
            params.append(f"%{naics_code}%")
        where = " AND ".join(clauses)
        rows = conn.execute(
            f"SELECT * FROM competitors WHERE {where} LIMIT 10", params
        ).fetchall()
        competitors = [_row_to_dict(r) for r in rows]
    # Also pull any existing black-hat analyses for this opp
    bh_rows = conn.execute(
        "SELECT * FROM black_hat_analyses WHERE opportunity_id = ?",
        (opp_id,),
    ).fetchall()
    black_hats = [_row_to_dict(r) for r in bh_rows]
    return competitors, black_hats


def _infer_themes(opp, capabilities, past_perfs, customer, competitors):
    """Deterministically infer 3-5 win themes from gathered intelligence.

    Returns a list of dicts, each with: theme_text, supporting_evidence,
    discriminator_type.
    """
    themes = []
    opp_desc = (opp.get("description") or "").lower()
    opp_title = (opp.get("title") or "").lower()

    # --- 1. Technical capability theme ---
    tech_evidence = []
    for cap in capabilities:
        title_lower = (cap.get("title") or "").lower()
        content_lower = (cap.get("content") or "").lower()
        # Simple keyword overlap with opportunity description
        if any(kw in opp_desc or kw in opp_title for kw in
               title_lower.split()[:5] if len(kw) > 4):
            tech_evidence.append(f"KB:{cap['id']} - {cap['title']}")
        if len(tech_evidence) >= 3:
            break
    if tech_evidence:
        themes.append({
            "theme_text": (
                "Proven technical approach validated by direct experience "
                "delivering similar solutions for federal customers."
            ),
            "supporting_evidence": json.dumps(tech_evidence),
            "discriminator_type": "technical",
        })

    # --- 2. Past performance theme ---
    pp_evidence = []
    for pp in past_perfs:
        rating = pp.get("cpars_rating") or "satisfactory"
        pp_evidence.append(
            f"PP:{pp['id']} - {pp['contract_name']} ({rating})"
        )
        if len(pp_evidence) >= 3:
            break
    if pp_evidence:
        themes.append({
            "theme_text": (
                "Demonstrated track record of successful delivery with "
                "consistently high CPARS ratings on comparable contracts."
            ),
            "supporting_evidence": json.dumps(pp_evidence),
            "discriminator_type": "past_performance",
        })

    # --- 3. Customer-focused / mission theme ---
    if customer:
        mission = customer.get("mission_statement") or ""
        pain = customer.get("pain_points") or ""
        themes.append({
            "theme_text": (
                "Deep understanding of the customer's mission and "
                "strategic priorities, translating pain points into "
                "measurable outcomes."
            ),
            "supporting_evidence": json.dumps({
                "agency": customer.get("agency"),
                "priorities": customer.get("strategic_priorities"),
                "pain_points": pain[:200] if pain else None,
            }),
            "discriminator_type": "management",
        })

    # --- 4. Innovation / risk-reduction theme ---
    innovation_kbs = [c for c in capabilities
                      if (c.get("entry_type") == "solution_architecture"
                          or c.get("entry_type") == "methodology")]
    if innovation_kbs:
        inno_evidence = [
            f"KB:{k['id']} - {k['title']}" for k in innovation_kbs[:3]
        ]
        themes.append({
            "theme_text": (
                "Innovative yet proven solution architecture that reduces "
                "implementation risk while accelerating time-to-value."
            ),
            "supporting_evidence": json.dumps(inno_evidence),
            "discriminator_type": "innovation",
        })

    # --- 5. Competitive differentiation theme ---
    if competitors:
        weak_areas = []
        for comp in competitors:
            weaknesses = comp.get("weaknesses") or ""
            if weaknesses:
                weak_areas.append(f"{comp['company_name']}: {weaknesses[:120]}")
        if weak_areas:
            themes.append({
                "theme_text": (
                    "Unique differentiators that directly address known "
                    "competitor gaps, positioning our team as the lowest-risk, "
                    "highest-value choice."
                ),
                "supporting_evidence": json.dumps(weak_areas[:3]),
                "discriminator_type": "risk",
            })

    # Guarantee at least one generic theme if none matched
    if not themes:
        themes.append({
            "theme_text": (
                "Comprehensive, compliant response grounded in relevant "
                "experience and a customer-focused delivery model."
            ),
            "supporting_evidence": json.dumps(["general_compliance"]),
            "discriminator_type": "management",
        })

    return themes[:5]


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def generate_themes(opp_id, db_path=None):
    """Analyze opportunity and generate 3-5 discriminating win themes.

    Examines the opportunity description, customer profile, competitor
    landscape, and company capabilities (KB + past performance) to produce
    themes with supporting evidence and discriminator types.

    Args:
        opp_id: Opportunity ID to generate themes for.
        db_path: Optional database path override.

    Returns:
        list of dicts, each with id, theme_text, supporting_evidence,
        discriminator_type, and metadata.

    Raises:
        ValueError: If opportunity not found.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
        agency = opp.get("agency")
        naics = opp.get("naics_code")

        customer = _load_customer_profile(conn, agency)
        capabilities = _load_capabilities(conn, naics)
        past_perfs = _load_past_performances(conn, agency, naics)
        competitors, _ = _load_competitor_landscape(conn, opp_id, agency, naics)

        raw_themes = _infer_themes(
            opp, capabilities, past_perfs, customer, competitors
        )

        created = []
        now = _now()
        for t in raw_themes:
            theme_id = _wt_id()
            conn.execute(
                "INSERT INTO win_themes "
                "(id, opportunity_id, theme_text, supporting_evidence, "
                " discriminator_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    theme_id, opp_id, t["theme_text"],
                    t["supporting_evidence"], t["discriminator_type"], now,
                ),
            )
            created.append({
                "id": theme_id,
                "opportunity_id": opp_id,
                "theme_text": t["theme_text"],
                "supporting_evidence": _parse_json_field(
                    t["supporting_evidence"]
                ),
                "discriminator_type": t["discriminator_type"],
                "created_at": now,
            })

        _audit(conn, "capture.win_theme_generate",
               f"Generated {len(created)} win themes for {opp_id}",
               "win_themes", opp_id,
               {"count": len(created),
                "theme_ids": [c["id"] for c in created]})
        conn.commit()
        return created
    finally:
        conn.close()


def get_themes(opp_id=None, proposal_id=None, db_path=None):
    """Retrieve win themes by opportunity or proposal.

    Args:
        opp_id: Filter by opportunity ID.
        proposal_id: Filter by proposal ID.
        db_path: Optional database path override.

    Returns:
        list of theme dicts.

    Raises:
        ValueError: If neither opp_id nor proposal_id provided.
    """
    if not opp_id and not proposal_id:
        raise ValueError("Must provide --opp-id or --proposal-id")

    conn = _get_db(db_path)
    try:
        if opp_id:
            rows = conn.execute(
                "SELECT * FROM win_themes WHERE opportunity_id = ? "
                "ORDER BY strength_rating DESC NULLS LAST, created_at DESC",
                (opp_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM win_themes WHERE proposal_id = ? "
                "ORDER BY strength_rating DESC NULLS LAST, created_at DESC",
                (proposal_id,),
            ).fetchall()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            d["supporting_evidence"] = _parse_json_field(
                d.get("supporting_evidence")
            )
            d["usage_sections"] = _parse_json_field(
                d.get("usage_sections")
            )
            results.append(d)
        return results
    finally:
        conn.close()


def score_theme(theme_id, criteria=None, db_path=None):
    """Rate a win theme's strength based on evidence quality and differentiation.

    Scoring dimensions (default):
      - evidence_count   (0.25): Number of supporting KB/PP references.
      - evidence_quality (0.25): Average quality_score of referenced KB entries.
      - specificity      (0.20): Length and detail of theme text.
      - differentiation  (0.30): Whether discriminator type is present and
        supporting evidence references competitor gaps.

    Args:
        theme_id: Win-theme ID to score.
        criteria: Optional dict of custom scoring weights.
        db_path: Optional database path override.

    Returns:
        dict with theme_id, dimension_scores, overall strength_rating.

    Raises:
        ValueError: If theme not found.
    """
    default_criteria = {
        "evidence_count": 0.25,
        "evidence_quality": 0.25,
        "specificity": 0.20,
        "differentiation": 0.30,
    }
    weights = criteria if criteria else default_criteria

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM win_themes WHERE id = ?", (theme_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Win theme not found: {theme_id}")
        theme = _row_to_dict(row)

        evidence = _parse_json_field(theme.get("supporting_evidence")) or []
        if isinstance(evidence, dict):
            evidence = [json.dumps(evidence)]
        elif isinstance(evidence, str):
            evidence = [evidence]

        # --- evidence_count ---
        ev_count = len(evidence)
        ev_count_score = min(ev_count / 3.0, 1.0)

        # --- evidence_quality: look up KB quality scores ---
        quality_scores = []
        for ev in evidence:
            ev_str = str(ev)
            if ev_str.startswith("KB:"):
                kb_id = ev_str.split(" - ")[0].replace("KB:", "").strip()
                kb_row = conn.execute(
                    "SELECT quality_score FROM kb_entries WHERE id = ?",
                    (kb_id,),
                ).fetchone()
                if kb_row and kb_row["quality_score"] is not None:
                    quality_scores.append(kb_row["quality_score"])
        ev_quality_score = (
            sum(quality_scores) / len(quality_scores)
            if quality_scores else 0.5
        )

        # --- specificity ---
        text_len = len(theme.get("theme_text") or "")
        specificity_score = min(text_len / 200.0, 1.0)

        # --- differentiation ---
        disc_type = theme.get("discriminator_type")
        diff_score = 0.5
        if disc_type in ("innovation", "risk"):
            diff_score = 0.9
        elif disc_type in ("technical", "past_performance"):
            diff_score = 0.7
        elif disc_type in ("personnel", "management"):
            diff_score = 0.6
        elif disc_type == "cost":
            diff_score = 0.5
        # Boost if evidence references competitor weaknesses
        evidence_text = json.dumps(evidence).lower()
        if any(kw in evidence_text for kw in
               ("weakness", "gap", "lack", "competitor")):
            diff_score = min(diff_score + 0.15, 1.0)

        dimension_scores = {
            "evidence_count": round(ev_count_score, 3),
            "evidence_quality": round(ev_quality_score, 3),
            "specificity": round(specificity_score, 3),
            "differentiation": round(diff_score, 3),
        }

        overall = sum(
            dimension_scores[dim] * weights.get(dim, 0.0)
            for dim in dimension_scores
        )
        overall = round(overall, 3)

        # Persist the rating
        conn.execute(
            "UPDATE win_themes SET strength_rating = ? WHERE id = ?",
            (overall, theme_id),
        )
        _audit(conn, "capture.win_theme_score",
               f"Scored theme {theme_id}: {overall}",
               "win_themes", theme_id, dimension_scores)
        conn.commit()

        return {
            "theme_id": theme_id,
            "dimension_scores": dimension_scores,
            "weights": weights,
            "strength_rating": overall,
        }
    finally:
        conn.close()


def map_to_sections(proposal_id, db_path=None):
    """Suggest which proposal sections should emphasize each win theme.

    Maps themes to sections based on discriminator type:
      - technical      -> technical volume sections
      - management     -> management volume sections
      - cost           -> cost volume (if applicable)
      - past_performance -> past_performance volume
      - personnel      -> management volume (staffing sections)
      - innovation     -> technical volume + executive_summary
      - risk           -> management + executive_summary

    Args:
        proposal_id: Proposal ID whose themes and sections to map.
        db_path: Optional database path override.

    Returns:
        list of dicts with theme_id, theme_text, recommended_sections.

    Raises:
        ValueError: If proposal not found or has no themes.
    """
    conn = _get_db(db_path)
    try:
        prop = conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if prop is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        prop = _row_to_dict(prop)

        # Load themes linked to this proposal or its opportunity
        opp_id = prop.get("opportunity_id")
        themes = conn.execute(
            "SELECT * FROM win_themes WHERE proposal_id = ? "
            "OR opportunity_id = ? ORDER BY strength_rating DESC NULLS LAST",
            (proposal_id, opp_id),
        ).fetchall()
        themes = [_row_to_dict(t) for t in themes]
        if not themes:
            raise ValueError(
                f"No win themes found for proposal {proposal_id}"
            )

        # Load sections
        sections = conn.execute(
            "SELECT id, volume, section_number, section_title "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()
        sections = [_row_to_dict(s) for s in sections]

        # Build volume -> section list mapping
        volume_sections = {}
        for s in sections:
            vol = s.get("volume", "technical")
            volume_sections.setdefault(vol, []).append(s)

        DISC_TO_VOLUMES = {
            "technical": ["technical", "executive_summary"],
            "management": ["management", "executive_summary"],
            "cost": ["cost"],
            "past_performance": ["past_performance"],
            "personnel": ["management"],
            "innovation": ["technical", "executive_summary"],
            "risk": ["management", "executive_summary"],
        }

        mappings = []
        for theme in themes:
            disc = theme.get("discriminator_type") or "technical"
            target_vols = DISC_TO_VOLUMES.get(disc, ["technical"])

            recommended = []
            for vol in target_vols:
                for sec in volume_sections.get(vol, []):
                    recommended.append({
                        "section_id": sec["id"],
                        "volume": sec["volume"],
                        "section_number": sec["section_number"],
                        "section_title": sec["section_title"],
                    })

            # Persist mapping to usage_sections
            section_ids = [r["section_id"] for r in recommended]
            conn.execute(
                "UPDATE win_themes SET usage_sections = ? WHERE id = ?",
                (json.dumps(section_ids), theme["id"]),
            )

            mappings.append({
                "theme_id": theme["id"],
                "theme_text": theme["theme_text"],
                "discriminator_type": disc,
                "recommended_sections": recommended,
            })

        _audit(conn, "capture.win_theme_map",
               f"Mapped {len(mappings)} themes to sections for {proposal_id}",
               "proposal", proposal_id,
               {"theme_count": len(mappings)})
        conn.commit()

        return mappings
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build argument parser for the CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal Win Theme Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --generate --opp-id OPP-abc123 --json\n"
            "  %(prog)s --get --opp-id OPP-abc123 --json\n"
            "  %(prog)s --score --theme-id WT-abc123 --json\n"
            "  %(prog)s --map --proposal-id PROP-001 --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--generate", action="store_true",
                        help="Generate win themes for an opportunity")
    action.add_argument("--get", action="store_true",
                        help="Retrieve win themes")
    action.add_argument("--score", action="store_true",
                        help="Score a win theme's strength")
    action.add_argument("--map", action="store_true",
                        help="Map themes to proposal sections")

    parser.add_argument("--opp-id", help="Opportunity ID")
    parser.add_argument("--proposal-id", help="Proposal ID")
    parser.add_argument("--theme-id", help="Win theme ID for scoring")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.generate:
            if not args.opp_id:
                parser.error("--generate requires --opp-id")
            result = generate_themes(args.opp_id, db_path=db)

        elif args.get:
            result = get_themes(
                opp_id=args.opp_id,
                proposal_id=args.proposal_id,
                db_path=db,
            )

        elif args.score:
            if not args.theme_id:
                parser.error("--score requires --theme-id")
            result = score_theme(args.theme_id, db_path=db)

        elif args.map:
            if not args.proposal_id:
                parser.error("--map requires --proposal-id")
            result = map_to_sections(args.proposal_id, db_path=db)

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                print(f"Found {len(result)} theme(s):")
                for item in result:
                    tid = item.get("id") or item.get("theme_id", "?")
                    disc = item.get("discriminator_type", "")
                    text = (item.get("theme_text") or "")[:80]
                    print(f"  [{tid}] ({disc}) {text}")
            elif isinstance(result, dict):
                for key, value in result.items():
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
