#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""RFP Question Generator — auto-generate strategic questions from RFP analysis.

Analyzes RFP text for ambiguities, gaps, and strategic opportunities using
deterministic regex/keyword extraction (D-QTG-1, D362 pattern).  No LLM needed.

Analysis categories:
    1. Vague/ambiguous language ("as needed", "TBD", "appropriate", "adequate")
    2. Missing evaluation criteria weights
    3. Unclear period of performance / timeline
    4. Missing data rights / IP provisions
    5. L vs M section misalignment (instructions vs evaluation)
    6. Small business / set-aside ambiguity
    7. Unclear security / compliance requirements
    8. Missing or unusual contract terms
    9. Strategic advantage questions (narrowing scope, clarifying evaluation)

Priority scoring: deterministic weighted formula (D21 pattern)
    priority_score = category_weight * ambiguity_level * strategic_value
    High: score >= 7.0, Medium: 4.0-6.9, Low: < 4.0

Usage:
    python tools/govcon/question_generator.py --generate --opp-id <id> --json
    python tools/govcon/question_generator.py --list --opp-id <id> --json
    python tools/govcon/question_generator.py --stats --opp-id <id> --json
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "govcon_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

# =========================================================================
# CONSTANTS
# =========================================================================

# Category weights for priority scoring (scope > eval_criteria > technical > ...)
CATEGORY_WEIGHTS = {
    "scope": 3.0,
    "evaluation_criteria": 2.8,
    "technical_requirements": 2.5,
    "contract_terms": 2.0,
    "compliance_security": 1.8,
    "small_business": 1.5,
}

# Priority thresholds
PRIORITY_HIGH = 7.0
PRIORITY_MEDIUM = 4.0

# Vague phrase patterns → (regex, category, ambiguity_level, question_template)
VAGUE_PATTERNS = [
    (r"\bas\s+needed\b", "scope", 3,
     "Please clarify the frequency and scope of \"{context}\". What defines 'as needed' and how will this be measured?"),
    (r"\bto\s+be\s+determined\b", "scope", 3,
     "Section {ref} states \"{context}\" is to be determined. When will this be finalized, and what assumptions should offerors use?"),
    (r"\bTBD\b", "scope", 3,
     "Section {ref} references TBD for \"{context}\". When will this be finalized, and what assumptions should offerors use in proposals?"),
    (r"\bappropriate\b", "scope", 2,
     "What criteria define 'appropriate' in the context of \"{context}\" (Section {ref})?"),
    (r"\badequate\b", "scope", 2,
     "What quantitative measures define 'adequate' for \"{context}\" (Section {ref})?"),
    (r"\bsufficient\b", "scope", 2,
     "What thresholds constitute 'sufficient' for \"{context}\" (Section {ref})?"),
    (r"\breasonable\b", "contract_terms", 2,
     "How will 'reasonable' be measured or adjudicated in the context of \"{context}\" (Section {ref})?"),
    (r"\bmay\s+require\b", "scope", 2,
     "Under what conditions will the requirement for \"{context}\" be triggered (Section {ref})?"),
    (r"\bperiodically\b", "scope", 2,
     "What frequency constitutes 'periodically' for \"{context}\" (Section {ref})?"),
    (r"\bminimal\b", "scope", 2,
     "What threshold defines 'minimal' for \"{context}\" (Section {ref})?"),
    (r"\bapplicable\s+(standards?|regulations?|requirements?)\b", "compliance_security", 2,
     "Which specific standards or regulations are 'applicable' to \"{context}\" (Section {ref})?"),
    (r"\bin\s+accordance\s+with\s+(?:all\s+)?applicable\b", "compliance_security", 2,
     "Please enumerate the specific standards, regulations, or directives that are 'applicable' for \"{context}\" (Section {ref})."),
    (r"\bor\s+equivalent\b", "technical_requirements", 2,
     "What criteria determine an 'equivalent' alternative for \"{context}\" (Section {ref})? Who adjudicates equivalence?"),
    (r"\bat\s+(?:the\s+)?(?:government|government's)\s+discretion\b", "contract_terms", 2,
     "What factors will guide the Government's discretion regarding \"{context}\" (Section {ref})?"),
    (r"\bsignificant\b", "scope", 2,
     "What constitutes 'significant' in the context of \"{context}\" (Section {ref})?"),
    (r"\btimely\b", "scope", 2,
     "What timeframe constitutes 'timely' for \"{context}\" (Section {ref})?"),
    (r"\bsubstantial\b", "scope", 2,
     "What quantitative or qualitative measure defines 'substantial' for \"{context}\" (Section {ref})?"),
]

# Missing section detectors: (category, must_have_regex, details_regex_or_none, question)
MISSING_SECTION_CHECKS = [
    ("evaluation_criteria", r"\bevaluation\s+(criteria|factor)", r"\bweight|percent|point",
     "The solicitation does not appear to specify evaluation criteria weights or relative importance. Will point values or adjectival ratings be provided?"),
    ("contract_terms", r"\bCDRL|contract\s+data\s+requirements?\s+list|deliverable\s+list", None,
     "No CDRL list or deliverable schedule was identified in the solicitation. Will a Contract Data Requirements List (DD Form 1423) be provided?"),
    ("contract_terms", r"\bperiod\s+of\s+performance|POP\b", r"\b\d+\s*(month|year|day)",
     "The period of performance details appear incomplete. Please confirm the base period duration, option periods, and any ordering period limitations."),
    ("technical_requirements", r"\bdata\s+rights|intellectual\s+property\b", None,
     "No data rights or intellectual property provisions were identified. Will DFARS 252.227-7013/7014 or FAR 52.227-14 apply? Should offerors submit a data rights assertion list?"),
    ("small_business", r"\bsubcontracting\s+plan\b", r"\bgoal|percent",
     "Is an individual subcontracting plan required per FAR 52.219-9? What are the small business subcontracting goals by category?"),
    ("compliance_security", r"\bclearance|security\s+clearance|TS/SCI|SECRET|Top\s+Secret\b", r"\blevel|type",
     "The solicitation references security clearance requirements but does not specify the clearance level. What level of personnel security clearance is required?"),
    ("compliance_security", r"\bFedRAMP|ATO|authority\s+to\s+operate\b", r"\bbaseline|level|impact",
     "The solicitation references FedRAMP or ATO requirements. What FedRAMP baseline (Low/Moderate/High) or Impact Level (IL2-IL6) applies?"),
    ("contract_terms", r"\btransition\s*(plan|period|in|out)\b", r"\bday|month|week",
     "The solicitation references a transition period but does not specify its duration. What is the expected transition-in/transition-out period?"),
    ("evaluation_criteria", r"\bbest\s+value|lowest\s+price\s+technically\s+acceptable|LPTA|tradeoff\b", None,
     "The solicitation does not clearly state the source selection methodology. Is this a best-value tradeoff or LPTA procurement?"),
    ("contract_terms", r"\bplace\s+of\s+performance|work\s+location\b", r"\bremote|on-?site|hybrid",
     "The place of performance is not clearly specified. Is on-site presence required? What percentage of work can be performed remotely?"),
]

# L vs M misalignment patterns: (l_keyword, m_missing_keyword, question)
LM_MISALIGNMENT_PATTERNS = [
    (r"\bpast\s+performance\b", r"\brelevancy|recency\b",
     "Section L requests past performance information, but Section M does not specify relevancy or recency criteria. What past performance timeframe and contract value thresholds apply?"),
    (r"\bstaffing|personnel|key\s+personnel\b", r"\bresume|qualification|experience\b",
     "Section L requires key personnel but Section M does not specify qualification evaluation criteria. How will proposed key personnel be evaluated?"),
    (r"\btechnical\s+approach\b", r"\binnovation|methodology\b",
     "Section L requests a technical approach but Section M does not specify how innovation or methodology will be evaluated. What technical approach factors will be scored?"),
    (r"\bcost\s+(?:proposal|volume)\b", r"\brealism|reasonableness\b",
     "Section L requests a cost proposal but Section M does not specify whether cost realism or cost reasonableness analysis will be performed. Which analysis applies?"),
    (r"\bmanagement\s+(?:approach|plan|volume)\b", r"\borganization|staffing\s+plan|quality\b",
     "Section L requests a management approach but Section M does not specify management evaluation subfactors. What management elements will be evaluated?"),
]


# =========================================================================
# HELPERS
# =========================================================================

def _get_db(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="question_generator"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "govcon.question_generator", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _content_hash(text):
    """SHA-256 hash of normalized question text for dedup."""
    return hashlib.sha256(text.lower().strip().encode("utf-8")).hexdigest()


def _score_priority(category, ambiguity_level, strategic_value=1.0):
    """Compute priority (high/medium/low) from weighted formula."""
    weight = CATEGORY_WEIGHTS.get(category, 1.5)
    score = weight * ambiguity_level * strategic_value
    if score >= PRIORITY_HIGH:
        return "high", score
    elif score >= PRIORITY_MEDIUM:
        return "medium", score
    return "low", score


def _load_config():
    """Load govcon_config.yaml questions_to_government section."""
    try:
        if _HAS_YAML:
            with open(CONFIG_PATH) as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("questions_to_government", {})
    except Exception:
        pass
    return {}


def _extract_context(text, match, max_chars=120):
    """Extract surrounding context for a regex match."""
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 80)
    ctx = text[start:end].strip()
    ctx = re.sub(r"\s+", " ", ctx)
    if len(ctx) > max_chars:
        ctx = ctx[:max_chars] + "..."
    return ctx


def _guess_section_ref(text, match_pos):
    """Try to find a section reference (e.g. L.3.2.1) near the match position."""
    search_window = text[max(0, match_pos - 200):match_pos + 50]
    ref_match = re.search(
        r"(?:Section|SECTION|section)\s+([A-Z]\.\d[\d.]*)", search_window
    )
    if ref_match:
        return ref_match.group(1)
    ref_match = re.search(r"\b([LMNCJHKB]\.\d[\d.]*)\b", search_window)
    if ref_match:
        return ref_match.group(1)
    return ""


# =========================================================================
# CORE ANALYSIS FUNCTIONS
# =========================================================================

def _detect_vague_language(text, section_ref=""):
    """Scan text for vague phrases and return question candidates."""
    candidates = []
    for pattern, category, ambiguity, template in VAGUE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            context = _extract_context(text, match)
            ref = section_ref or _guess_section_ref(text, match.start())
            question_text = template.format(context=context, ref=ref or "N/A")
            priority, score = _score_priority(category, ambiguity)
            candidates.append({
                "question_text": question_text,
                "category": category,
                "priority": priority,
                "priority_score": round(score, 2),
                "ambiguity_trigger": match.group(0),
                "rfp_section_ref": ref,
            })
    return candidates


def _detect_missing_sections(text):
    """Check for missing standard RFP sections."""
    candidates = []
    for category, must_have_re, details_re, question_text in MISSING_SECTION_CHECKS:
        # Check if the topic is mentioned at all
        has_topic = bool(re.search(must_have_re, text, re.IGNORECASE))
        # If topic is mentioned but details missing, or topic not mentioned at all
        if has_topic and details_re:
            has_details = bool(re.search(details_re, text, re.IGNORECASE))
            if has_details:
                continue  # Topic present with details — no question needed
        elif has_topic:
            continue  # Topic mentioned, no detail check needed
        # Generate question
        priority, score = _score_priority(category, 2.5, strategic_value=1.2)
        candidates.append({
            "question_text": question_text,
            "category": category,
            "priority": priority,
            "priority_score": round(score, 2),
            "ambiguity_trigger": "missing_section",
            "rfp_section_ref": "",
        })
    return candidates


def _detect_lm_misalignment(text):
    """Cross-reference L vs M sections for misalignment.

    Splits text at Section L / Section M boundaries if identifiable,
    otherwise scans the full text.
    """
    candidates = []
    # Try to split L and M sections
    l_text = text
    m_text = text
    l_match = re.search(r"(?:SECTION|Section)\s+L\b", text, re.IGNORECASE)
    m_match = re.search(r"(?:SECTION|Section)\s+M\b", text, re.IGNORECASE)
    if l_match and m_match:
        l_start = l_match.start()
        m_start = m_match.start()
        if l_start < m_start:
            l_text = text[l_start:m_start]
            m_text = text[m_start:]
        else:
            m_text = text[m_start:l_start]
            l_text = text[l_start:]

    for l_kw, m_missing_kw, question_text in LM_MISALIGNMENT_PATTERNS:
        has_in_l = bool(re.search(l_kw, l_text, re.IGNORECASE))
        missing_in_m = not bool(re.search(m_missing_kw, m_text, re.IGNORECASE))
        if has_in_l and missing_in_m:
            priority, score = _score_priority("evaluation_criteria", 2.5, strategic_value=1.3)
            candidates.append({
                "question_text": question_text,
                "category": "evaluation_criteria",
                "priority": priority,
                "priority_score": round(score, 2),
                "ambiguity_trigger": "lm_misalignment",
                "rfp_section_ref": "",
            })
    return candidates


# =========================================================================
# PUBLIC API
# =========================================================================

def generate_questions(opp_id, rfp_text=None, db_path=None):
    """Generate questions from RFP analysis.

    Args:
        opp_id: Opportunity ID (used to fetch rfp_text from shall statements if not provided).
        rfp_text: Optional raw RFP text. If None, assembles from rfp_shall_statements + description.
        db_path: Optional database path override.

    Returns:
        dict: {status, questions: [...], stats: {total, by_category, by_priority}}
    """
    conn = _get_db(db_path)
    try:
        # Get RFP text — try opportunity description first, then assemble from shall statements
        if not rfp_text:
            # Check sam_gov_opportunities for description
            opp = conn.execute(
                "SELECT * FROM proposal_opportunities WHERE id = ?", (opp_id,)
            ).fetchone()
            if not opp:
                return {"status": "error", "error": f"Opportunity {opp_id} not found"}

            parts = []
            # Get SAM.gov description if linked
            if opp["sam_gov_opportunity_id"]:
                sam = conn.execute(
                    "SELECT description FROM sam_gov_opportunities WHERE id = ?",
                    (opp["sam_gov_opportunity_id"],)
                ).fetchone()
                if sam and sam["description"]:
                    parts.append(sam["description"])

            # Get shall statements
            stmts = conn.execute(
                "SELECT statement_text FROM rfp_shall_statements WHERE proposal_opportunity_id = ?",
                (opp_id,)
            ).fetchall()
            for s in stmts:
                parts.append(s["statement_text"])

            rfp_text = "\n".join(parts) if parts else ""

        if not rfp_text.strip():
            return {"status": "error", "error": "No RFP text available. Extract requirements first or provide RFP text."}

        # Run all detectors
        candidates = []
        candidates.extend(_detect_vague_language(rfp_text))
        candidates.extend(_detect_missing_sections(rfp_text))
        candidates.extend(_detect_lm_misalignment(rfp_text))

        # Dedup by content hash
        seen_hashes = set()
        deduped = []
        for c in candidates:
            h = _content_hash(c["question_text"])
            if h not in seen_hashes:
                seen_hashes.add(h)
                c["content_hash"] = h
                deduped.append(c)

        # Sort by priority score descending
        deduped.sort(key=lambda x: x.get("priority_score", 0), reverse=True)

        # Apply max limit
        config = _load_config()
        max_questions = config.get("max_auto_questions", 50)
        deduped = deduped[:max_questions]

        # Stats
        stats = {"total": len(deduped), "by_category": {}, "by_priority": {}}
        for q in deduped:
            stats["by_category"][q["category"]] = stats["by_category"].get(q["category"], 0) + 1
            stats["by_priority"][q["priority"]] = stats["by_priority"].get(q["priority"], 0) + 1

        return {"status": "ok", "questions": deduped, "stats": stats}
    finally:
        conn.close()


def generate_and_store(opp_id, rfp_text=None, db_path=None, created_by="question_generator"):
    """Generate questions and store in proposal_questions table.

    Args:
        opp_id: Opportunity ID
        rfp_text: Optional RFP text (if None, reads from DB)
        db_path: Optional DB path override
        created_by: Creator identifier for audit trail

    Returns:
        dict: {status, generated, duplicates_skipped, by_category, by_priority}
    """
    result = generate_questions(opp_id, rfp_text, db_path)
    if result["status"] != "ok":
        return result

    conn = _get_db(db_path)
    try:
        # Get existing hashes for dedup against already-stored questions
        existing = conn.execute(
            "SELECT content_hash FROM proposal_questions WHERE opportunity_id = ?",
            (opp_id,)
        ).fetchall()
        existing_hashes = {r["content_hash"] for r in existing if r["content_hash"]}

        # Get current max question number
        max_num_row = conn.execute(
            "SELECT MAX(question_number) as m FROM proposal_questions WHERE opportunity_id = ?",
            (opp_id,)
        ).fetchone()
        next_num = (max_num_row["m"] or 0) + 1

        generated = 0
        skipped = 0
        for q in result["questions"]:
            if q.get("content_hash") in existing_hashes:
                skipped += 1
                continue

            q_id = _uuid()
            conn.execute(
                """INSERT INTO proposal_questions
                   (id, opportunity_id, question_number, question_text, category, priority,
                    source, rfp_section_ref, status, ambiguity_trigger, content_hash,
                    created_by, classification, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'auto', ?, 'draft', ?, ?, ?, 'CUI', ?, ?)""",
                (q_id, opp_id, next_num, q["question_text"], q["category"], q["priority"],
                 q.get("rfp_section_ref"), q.get("ambiguity_trigger"), q.get("content_hash"),
                 created_by, _now(), _now()),
            )
            next_num += 1
            generated += 1

        # Update question count on opportunity
        conn.execute(
            "UPDATE proposal_opportunities SET question_count = (SELECT COUNT(*) FROM proposal_questions WHERE opportunity_id = ?), updated_at = ? WHERE id = ?",
            (opp_id, _now(), opp_id),
        )

        _audit(conn, "generate_questions",
               f"Generated {generated} questions for opp {opp_id} (skipped {skipped} duplicates)")
        conn.commit()

        return {
            "status": "ok",
            "generated": generated,
            "duplicates_skipped": skipped,
            "by_category": result["stats"]["by_category"],
            "by_priority": result["stats"]["by_priority"],
        }
    finally:
        conn.close()


def list_questions(opp_id, db_path=None):
    """List existing questions for an opportunity."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_questions WHERE opportunity_id = ? ORDER BY question_number",
            (opp_id,)
        ).fetchall()
        questions = [dict(r) for r in rows]

        stats = {"total": len(questions), "by_category": {}, "by_status": {}, "by_priority": {}}
        for q in questions:
            stats["by_category"][q["category"]] = stats["by_category"].get(q["category"], 0) + 1
            stats["by_status"][q["status"]] = stats["by_status"].get(q["status"], 0) + 1
            stats["by_priority"][q["priority"]] = stats["by_priority"].get(q["priority"], 0) + 1

        return {"status": "ok", "questions": questions, "stats": stats}
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================

def main():
    parser = argparse.ArgumentParser(description="RFP Question Generator (D-QTG-1)")
    parser.add_argument("--generate", action="store_true", help="Generate questions for opportunity")
    parser.add_argument("--list", action="store_true", help="List existing questions")
    parser.add_argument("--stats", action="store_true", help="Question statistics")
    parser.add_argument("--opp-id", required=True, help="Opportunity ID")
    parser.add_argument("--rfp-text", help="Optional raw RFP text (otherwise assembled from DB)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.generate:
        result = generate_and_store(args.opp_id, rfp_text=args.rfp_text)
    elif args.list:
        result = list_questions(args.opp_id)
    elif args.stats:
        result = list_questions(args.opp_id)
        if result["status"] == "ok":
            result = {"status": "ok", "stats": result["stats"]}
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("status") == "error":
            print(f"ERROR: {result.get('error')}")
        elif args.generate:
            print(f"Generated: {result.get('generated', 0)} questions")
            print(f"Skipped:   {result.get('duplicates_skipped', 0)} duplicates")
            for cat, cnt in (result.get("by_category") or {}).items():
                print(f"  {cat}: {cnt}")
        elif args.list:
            for q in result.get("questions", []):
                pri = q["priority"].upper()
                print(f"  [{pri:6s}] Q{q['question_number']}: {q['question_text'][:100]}")
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
