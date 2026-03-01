# CUI // SP-CTI
# ICDEV GovCon Capability Mapper — Phase 59 (D363)
# Maps ICDEV capabilities to RFP requirement patterns via keyword overlap.

"""
Capability Mapper — match requirement patterns against ICDEV capability catalog.

Reads from:
    - context/govcon/icdev_capability_catalog.json (capability definitions)
    - rfp_requirement_patterns (clustered requirement patterns)
    - rfp_shall_statements (individual shall statements)

Writes to:
    - icdev_capability_map (append-only bridge: pattern → capability with score)

Coverage scoring:
    >= 0.80  →  L (compliant)
    0.40–0.79 → M (partial)
    < 0.40   →  N (non-compliant / gap)

Usage:
    python tools/govcon/capability_mapper.py --map-all --json
    python tools/govcon/capability_mapper.py --map-pattern --pattern-id <id> --json
    python tools/govcon/capability_mapper.py --coverage --json
    python tools/govcon/capability_mapper.py --coverage --domain devsecops --json
    python tools/govcon/capability_mapper.py --gaps --json
    python tools/govcon/capability_mapper.py --compliance-matrix --opp-id <id> --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CATALOG_PATH = _ROOT / "context" / "govcon" / "icdev_capability_catalog.json"
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── helpers (adapted from source_scanner.py) ──────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, action, details="", actor="capability_mapper"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), _now(), "govcon.capability_map", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _load_config():
    try:
        import yaml
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ── catalog loading ───────────────────────────────────────────────────

def load_capability_catalog():
    """Load ICDEV capability catalog from JSON."""
    if not _CATALOG_PATH.exists():
        return []
    with open(_CATALOG_PATH) as f:
        data = json.load(f)
    return data.get("capabilities", [])


# ── keyword matching ──────────────────────────────────────────────────

def _normalize_keywords(keywords):
    """Normalize keywords to lowercase set."""
    return {k.lower().strip() for k in keywords if k}


def compute_coverage_score(pattern_keywords, capability):
    """Compute coverage score: keyword overlap between pattern and capability.

    Uses a weighted approach:
    - Exact keyword matches get full weight
    - Partial matches (keyword appears in capability keyword) get 0.5 weight
    - Domain category match gets 0.1 bonus
    """
    if not pattern_keywords:
        return 0.0

    cap_keywords = _normalize_keywords(capability.get("keywords", []))
    pat_keywords = _normalize_keywords(pattern_keywords)

    if not cap_keywords:
        return 0.0

    exact_matches = pat_keywords & cap_keywords
    exact_score = len(exact_matches)

    # Partial matches: pattern keyword appears as substring in any capability keyword
    partial_score = 0
    remaining_pat = pat_keywords - exact_matches
    for pk in remaining_pat:
        for ck in cap_keywords:
            if pk in ck or ck in pk:
                partial_score += 0.5
                break

    # Domain category match bonus
    domain_bonus = 0
    cap_category = capability.get("category", "").lower()
    # Check if any pattern keyword hints at the capability's domain
    domain_hints = {
        "devsecops": {"devsecops", "ci/cd", "pipeline", "cicd", "devops"},
        "ato_rmf": {"ato", "rmf", "authorization", "ssp", "poam"},
        "ai_ml": {"ai", "ml", "machine learning", "artificial intelligence", "llm"},
        "cloud": {"cloud", "aws", "azure", "gcp", "migration", "modernization"},
        "security": {"security", "vulnerability", "penetration", "zero trust"},
        "compliance": {"compliance", "fedramp", "cmmc", "nist", "hipaa"},
        "agile": {"agile", "scrum", "safe", "sprint", "backlog"},
        "data": {"data", "analytics", "etl", "warehouse"},
        "management": {"management", "program", "project", "earned value"},
    }
    if cap_category in domain_hints:
        if pat_keywords & domain_hints[cap_category]:
            domain_bonus = 0.1

    total = exact_score + partial_score + domain_bonus
    max_possible = len(pat_keywords)
    score = min(total / max_possible, 1.0) if max_possible > 0 else 0.0

    return round(score, 4)


def coverage_to_grade(score):
    """Convert coverage score to L/M/N grade."""
    cfg = _load_config().get("capability_mapping", {})
    compliant_threshold = cfg.get("min_coverage_for_compliant", 0.80)
    partial_threshold = cfg.get("min_coverage_for_partial", 0.40)

    if score >= compliant_threshold:
        return "L"
    elif score >= partial_threshold:
        return "M"
    else:
        return "N"


# ── mapping ───────────────────────────────────────────────────────────

def map_pattern_to_capabilities(pattern, capabilities):
    """Map a single requirement pattern to matching capabilities.

    Returns list of (capability_id, capability_name, score, grade) tuples,
    sorted by score descending.
    """
    keywords_str = pattern.get("keyword_fingerprint", "") or pattern.get("keywords", "")
    if isinstance(keywords_str, str):
        try:
            keywords = json.loads(keywords_str)
        except (json.JSONDecodeError, TypeError):
            keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
    else:
        keywords = keywords_str

    cfg = _load_config().get("capability_mapping", {})
    min_overlap = cfg.get("min_keyword_overlap", 2)

    results = []
    for cap in capabilities:
        score = compute_coverage_score(keywords, cap)
        if score > 0:
            # Check minimum keyword overlap
            cap_kw = _normalize_keywords(cap.get("keywords", []))
            pat_kw = _normalize_keywords(keywords)
            overlap_count = len(cap_kw & pat_kw)
            if overlap_count >= min_overlap or score >= 0.40:
                grade = coverage_to_grade(score)
                results.append({
                    "capability_id": cap["id"],
                    "capability_name": cap["name"],
                    "category": cap.get("category", ""),
                    "score": score,
                    "grade": grade,
                    "matched_keywords": sorted(cap_kw & pat_kw),
                    "evidence": cap.get("evidence", ""),
                })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def map_all_patterns(store=True):
    """Map all requirement patterns to ICDEV capabilities.

    Returns mapping results and optionally stores in icdev_capability_map.
    """
    capabilities = load_capability_catalog()
    if not capabilities:
        return {"status": "error", "message": "Capability catalog not found or empty"}

    conn = _get_db()
    patterns = conn.execute(
        "SELECT * FROM rfp_requirement_patterns ORDER BY frequency DESC"
    ).fetchall()

    if not patterns:
        conn.close()
        return {"status": "ok", "patterns_mapped": 0, "message": "No requirement patterns found"}

    all_mappings = []
    stored_count = 0

    for pattern in patterns:
        p_dict = dict(pattern)
        mappings = map_pattern_to_capabilities(p_dict, capabilities)

        for m in mappings:
            mapping_record = {
                "pattern_id": p_dict["id"],
                "pattern_name": p_dict.get("pattern_name", ""),
                "domain": p_dict.get("domain_category", ""),
                **m,
            }
            all_mappings.append(mapping_record)

            if store:
                try:
                    conn.execute(
                        "INSERT INTO icdev_capability_map "
                        "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            p_dict["id"],
                            m["capability_id"],
                            m["score"],
                            m["grade"],
                            json.dumps(m["matched_keywords"]),
                            _now(),
                            json.dumps({"capability_name": m["capability_name"], "evidence": m["evidence"]}),
                        ),
                    )
                    stored_count += 1
                except sqlite3.IntegrityError:
                    pass

    if store:
        _audit(conn, "map_all", f"Mapped {len(patterns)} patterns → {stored_count} capability links")
        conn.commit()
    conn.close()

    return {
        "status": "ok",
        "patterns_mapped": len(patterns),
        "capability_links": stored_count if store else len(all_mappings),
        "mappings": all_mappings,
    }


def map_single_pattern(pattern_id, store=True):
    """Map a single requirement pattern to capabilities."""
    capabilities = load_capability_catalog()
    conn = _get_db()

    pattern = conn.execute(
        "SELECT * FROM rfp_requirement_patterns WHERE id = ?", (pattern_id,)
    ).fetchone()

    if not pattern:
        conn.close()
        return {"status": "error", "message": f"Pattern {pattern_id} not found"}

    p_dict = dict(pattern)
    mappings = map_pattern_to_capabilities(p_dict, capabilities)

    if store:
        for m in mappings:
            try:
                conn.execute(
                    "INSERT INTO icdev_capability_map "
                    "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        pattern_id,
                        m["capability_id"],
                        m["score"],
                        m["grade"],
                        json.dumps(m["matched_keywords"]),
                        _now(),
                        json.dumps({"capability_name": m["capability_name"], "evidence": m["evidence"]}),
                    ),
                )
            except sqlite3.IntegrityError:
                pass
        _audit(conn, "map_pattern", f"Pattern {pattern_id} → {len(mappings)} capabilities")
        conn.commit()
    conn.close()

    return {
        "status": "ok",
        "pattern_id": pattern_id,
        "pattern_name": p_dict.get("pattern_name", ""),
        "mappings": mappings,
    }


# ── coverage analysis ─────────────────────────────────────────────────

def get_coverage(domain=None):
    """Get coverage analysis by domain.

    Returns per-domain breakdown: total patterns, L/M/N counts, avg score.
    """
    conn = _get_db()

    query = """
        SELECT
            p.domain_category,
            m.capability_id,
            m.coverage_score,
            m.grade,
            p.pattern_name,
            p.frequency
        FROM icdev_capability_map m
        JOIN rfp_requirement_patterns p ON m.pattern_id = p.id
    """
    params = []
    if domain:
        query += " WHERE p.domain_category = ?"
        params.append(domain)
    query += " ORDER BY p.domain_category, m.coverage_score DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        return {"status": "ok", "domains": {}, "message": "No mappings found. Run --map-all first."}

    domains = {}
    for row in rows:
        d = row["domain_category"] or "uncategorized"
        if d not in domains:
            domains[d] = {"patterns": set(), "L": 0, "M": 0, "N": 0, "scores": [], "mappings": []}
        domains[d]["patterns"].add(row["pattern_name"])
        grade = row["grade"]
        if grade in ("L", "M", "N"):
            domains[d][grade] += 1
        domains[d]["scores"].append(row["coverage_score"])
        domains[d]["mappings"].append({
            "pattern_name": row["pattern_name"],
            "capability_id": row["capability_id"],
            "score": row["coverage_score"],
            "grade": grade,
            "frequency": row["frequency"],
        })

    summary = {}
    for d, data in domains.items():
        avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        summary[d] = {
            "unique_patterns": len(data["patterns"]),
            "total_mappings": len(data["scores"]),
            "L_count": data["L"],
            "M_count": data["M"],
            "N_count": data["N"],
            "avg_coverage": round(avg_score, 4),
            "grade_distribution": f"L:{data['L']} M:{data['M']} N:{data['N']}",
            "top_mappings": data["mappings"][:10],
        }

    return {"status": "ok", "domains": summary}


def get_gaps():
    """Identify requirement patterns with no or low capability coverage.

    Returns patterns where best coverage < 0.40 (grade N).
    """
    conn = _get_db()

    # Patterns with mappings but low scores
    low_coverage = conn.execute("""
        SELECT p.id, p.pattern_name, p.domain_category, p.frequency,
               p.representative_text, MAX(m.coverage_score) as best_score
        FROM rfp_requirement_patterns p
        LEFT JOIN icdev_capability_map m ON p.id = m.pattern_id
        GROUP BY p.id
        HAVING best_score IS NULL OR best_score < 0.40
        ORDER BY p.frequency DESC
    """).fetchall()

    gaps = []
    for row in low_coverage:
        gaps.append({
            "pattern_id": row["id"],
            "pattern_name": row["pattern_name"],
            "domain": row["domain_category"],
            "frequency": row["frequency"],
            "best_coverage": row["best_score"] or 0.0,
            "representative_text": (row["representative_text"] or "")[:200],
            "grade": "N",
            "recommendation": "New capability or enhancement needed",
        })

    conn.close()

    return {
        "status": "ok",
        "total_gaps": len(gaps),
        "gaps": gaps,
    }


def get_compliance_matrix(opportunity_id):
    """Generate L/M/N compliance matrix for a specific opportunity.

    Maps each shall statement from the opportunity through patterns to capabilities.
    """
    conn = _get_db()
    capabilities = load_capability_catalog()

    # Get shall statements for this opportunity
    stmts = conn.execute(
        "SELECT * FROM rfp_shall_statements WHERE sam_opportunity_id = ? ORDER BY domain_category",
        (opportunity_id,),
    ).fetchall()

    if not stmts:
        conn.close()
        return {"status": "error", "message": f"No shall statements for opportunity {opportunity_id}"}

    matrix = []
    for stmt in stmts:
        s_dict = dict(stmt)
        keywords_str = s_dict.get("keywords", "[]")
        try:
            keywords = json.loads(keywords_str)
        except (json.JSONDecodeError, TypeError):
            keywords = [k.strip() for k in str(keywords_str).split(",") if k.strip()]

        # Find best matching capability
        best_match = None
        best_score = 0.0
        for cap in capabilities:
            score = compute_coverage_score(keywords, cap)
            if score > best_score:
                best_score = score
                best_match = cap

        grade = coverage_to_grade(best_score)
        matrix.append({
            "shall_id": s_dict["id"],
            "statement": (s_dict.get("statement_text", "") or "")[:200],
            "domain": s_dict.get("domain_category", ""),
            "statement_type": s_dict.get("statement_type", ""),
            "best_capability": best_match["name"] if best_match else "None",
            "best_capability_id": best_match["id"] if best_match else None,
            "coverage_score": best_score,
            "grade": grade,
            "evidence": (best_match.get("evidence", "") if best_match else ""),
        })

    # Summary
    l_count = sum(1 for m in matrix if m["grade"] == "L")
    m_count = sum(1 for m in matrix if m["grade"] == "M")
    n_count = sum(1 for m in matrix if m["grade"] == "N")
    total = len(matrix)

    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "total_requirements": total,
        "L_compliant": l_count,
        "M_partial": m_count,
        "N_gap": n_count,
        "compliance_rate": round(l_count / total, 4) if total > 0 else 0,
        "matrix": matrix,
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovCon Capability Mapper (D363)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--map-all", action="store_true", help="Map all patterns to capabilities")
    group.add_argument("--map-pattern", action="store_true", help="Map single pattern")
    group.add_argument("--coverage", action="store_true", help="Coverage analysis by domain")
    group.add_argument("--gaps", action="store_true", help="Identify coverage gaps")
    group.add_argument("--compliance-matrix", action="store_true", help="L/M/N matrix for opportunity")
    group.add_argument("--catalog", action="store_true", help="List capability catalog")

    parser.add_argument("--pattern-id", help="Pattern ID for --map-pattern")
    parser.add_argument("--opp-id", help="Opportunity ID for --compliance-matrix")
    parser.add_argument("--domain", help="Filter by domain category")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    if args.map_all:
        result = map_all_patterns(store=True)
    elif args.map_pattern:
        if not args.pattern_id:
            print("Error: --pattern-id required", file=sys.stderr)
            sys.exit(1)
        result = map_single_pattern(args.pattern_id, store=True)
    elif args.coverage:
        result = get_coverage(domain=args.domain)
    elif args.gaps:
        result = get_gaps()
    elif args.compliance_matrix:
        if not args.opp_id:
            print("Error: --opp-id required", file=sys.stderr)
            sys.exit(1)
        result = get_compliance_matrix(args.opp_id)
    elif args.catalog:
        caps = load_capability_catalog()
        result = {
            "status": "ok",
            "total_capabilities": len(caps),
            "capabilities": [
                {"id": c["id"], "name": c["name"], "category": c["category"],
                 "keywords_count": len(c.get("keywords", [])),
                 "tools_count": len(c.get("tools", [])),
                 "controls_count": len(c.get("compliance_controls", []))}
                for c in caps
            ],
        }

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        _print_human(result, args)
    else:
        print(json.dumps(result, indent=2, default=str))


def _print_human(result, args):
    """Human-readable output."""
    status = result.get("status", "unknown")
    print(f"\n{'=' * 60}")
    print(f"  ICDEV Capability Mapper — {status.upper()}")
    print(f"{'=' * 60}")

    if "mappings" in result:
        print(f"\n  Patterns mapped: {result.get('patterns_mapped', 0)}")
        print(f"  Capability links: {result.get('capability_links', 0)}")
        for m in result["mappings"][:20]:
            grade = m.get("grade", "?")
            color = {"L": "✅", "M": "⚠️", "N": "❌"}.get(grade, "?")
            print(f"  {color} [{grade}] {m.get('pattern_name', '')[:40]:40s} → {m.get('capability_name', '')[:30]:30s} ({m.get('score', 0):.2f})")

    if "domains" in result:
        for domain, data in result["domains"].items():
            print(f"\n  [{domain}] {data['grade_distribution']}  avg={data['avg_coverage']:.2f}")

    if "gaps" in result:
        print(f"\n  Total gaps: {result['total_gaps']}")
        for g in result["gaps"][:15]:
            print(f"  ❌ [{g['domain']}] {g['pattern_name'][:50]:50s} freq={g['frequency']}")

    if "matrix" in result:
        print(f"\n  Compliance Matrix — {result['total_requirements']} requirements")
        print(f"  L={result['L_compliant']} M={result['M_partial']} N={result['N_gap']}  rate={result.get('compliance_rate', 0):.0%}")
        for m in result["matrix"]:
            grade = m["grade"]
            color = {"L": "✅", "M": "⚠️", "N": "❌"}.get(grade, "?")
            print(f"  {color} [{grade}] {m['statement'][:60]:60s} → {m['best_capability'][:25]}")

    print()


if __name__ == "__main__":
    main()
