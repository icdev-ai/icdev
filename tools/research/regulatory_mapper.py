#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Regulatory Mapper — map regulations discovered during research to ICDEV™ crosswalk frameworks.

Maps regulatory signals from the research_signals table to ICDEV™ compliance
frameworks via the regulatory_registry.json registry. Produces crosswalk
coverage scores, enforcement action counts, and deadline detection for each
regulation-to-ICDEV™ mapping.

Architecture:
    D-RES-6:  Regulatory-to-crosswalk mapping via registry + keyword matching
    D26:      Declarative JSON registry (add new bodies without code changes)
    D6:       Append-only INSERT into research_regulatory_map
    D111:     Dual-hub crosswalk model (NIST 800-53 US hub)

Usage:
    # Map all regulatory signals for a session
    python tools/research/regulatory_mapper.py --map --session-id "rsess-xxx" --json

    # Show regulatory landscape for a session
    python tools/research/regulatory_mapper.py --landscape --session-id "rsess-xxx" --json

    # Show regulations for a specific challenge
    python tools/research/regulatory_mapper.py --challenge-regs --challenge-id "rchal-xxx" --json

    # Human-readable output
    python tools/research/regulatory_mapper.py --landscape --session-id "rsess-xxx" --human
"""

import argparse
import json
import os
import re
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

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "research_config.yaml"
REGISTRY_PATH = BASE_DIR / "context" / "research" / "regulatory_registry.json"

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

    def audit_log_event(**kwargs):
        return -1


# =========================================================================
# ICDEV™ FRAMEWORK DEFINITIONS
# =========================================================================
ICDEV_FRAMEWORKS = [
    "nist_800_53",
    "fedramp_moderate",
    "fedramp_high",
    "cmmc",
    "nist_800_171",
    "hipaa",
    "hitrust",
    "soc2",
    "pci_dss",
    "iso27001",
    "cjis",
    "nist_800_207",
]

# Keywords for enforcement action detection
ENFORCEMENT_KEYWORDS = [
    "enforcement",
    "penalty",
    "fine",
    "violation",
    "action",
    "sanction",
    "consent order",
    "cease and desist",
    "injunction",
    "remediation order",
    "civil money penalty",
]

# Regex for deadline/effective date detection
_RE_DEADLINE = re.compile(
    r"(?:deadline|effective\s+date|compliance\s+date|enforcement\s+date|"
    r"mandatory\s+by|required\s+by|due\s+date|sunset\s+date)"
    r"[:\s]*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\w+\s+\d{1,2},?\s+\d{4}|\d{4})",
    re.IGNORECASE,
)


# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _regmap_id():
    """Generate a regulatory map ID with rregmap- prefix."""
    return f"rregmap-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (best-effort, never raises)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="research-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="research-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load research config from YAML with fallback defaults."""
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# =========================================================================
# REGISTRY LOADER
# =========================================================================
def load_registry():
    """Load regulatory_registry.json. Returns dict of body definitions.

    Returns:
        Dict mapping body abbreviation to body definition dict.
        Returns empty dict if file not found or parse error.
    """
    if not REGISTRY_PATH.exists():
        return {}
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("regulatory_bodies", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _get_icdev_frameworks():
    """Return list of ICDEV™ compliance frameworks that exist.

    Returns:
        List of framework identifier strings.
    """
    return list(ICDEV_FRAMEWORKS)


# =========================================================================
# KEYWORD MATCHING HELPERS
# =========================================================================
def _match_body_to_signal(body_key, body_def, signal_title, signal_body):
    """Match a registry body to a signal by keyword overlap.

    Checks the body name, key, key_regulations, and verticals against
    the signal title and body text.

    Args:
        body_key: Registry key (e.g. "CFTC", "SEC").
        body_def: Dict from regulatory_registry.json.
        signal_title: Signal title text.
        signal_body: Signal body text.

    Returns:
        Float match score in [0.0, 1.0]. Higher = stronger match.
    """
    text = f"{signal_title} {signal_body}".lower()
    if not text.strip():
        return 0.0

    match_count = 0
    total_checks = 0

    # Check body key (abbreviation)
    total_checks += 1
    if body_key.lower() in text:
        match_count += 1

    # Check full name
    name = body_def.get("name", "").lower()
    if name:
        total_checks += 1
        # Check each significant word in the name (>3 chars)
        name_words = [w for w in name.split() if len(w) > 3]
        name_hits = sum(1 for w in name_words if w in text)
        if name_words and name_hits >= min(2, len(name_words)):
            match_count += 1

    # Check key regulations
    regulations = body_def.get("key_regulations", [])
    for reg in regulations:
        total_checks += 1
        if reg.lower() in text:
            match_count += 1

    # Check verticals
    verticals = body_def.get("verticals", [])
    for vert in verticals:
        total_checks += 1
        if vert.lower() in text:
            match_count += 1

    if total_checks == 0:
        return 0.0
    return match_count / total_checks


def _count_enforcement_actions(text):
    """Count enforcement action keywords in text.

    Args:
        text: Combined title + body text to scan.

    Returns:
        Integer count of enforcement keyword occurrences.
    """
    text_lower = text.lower()
    count = 0
    for kw in ENFORCEMENT_KEYWORDS:
        count += text_lower.count(kw)
    return count


def _detect_deadline(text):
    """Detect deadline/effective dates in text.

    Args:
        text: Combined title + body text to scan.

    Returns:
        First detected deadline string, or None.
    """
    match = _RE_DEADLINE.search(text)
    if match:
        return match.group(1).strip()
    return None


def _compute_crosswalk_coverage(nist_controls, icdev_frameworks):
    """Compute crosswalk coverage between NIST controls and ICDEV™ frameworks.

    Coverage is computed as:
    - full_match (1.0): framework exists in ICDEV_FRAMEWORKS
    - crosswalk_match (0.5): NIST controls have overlap with framework scope
    - no_match (0.0): no coverage

    Args:
        nist_controls: List of NIST control IDs (e.g. ["AC-2", "AU-2"]).
        icdev_frameworks: List of matched ICDEV™ framework names.

    Returns:
        Float coverage score in [0.0, 1.0].
    """
    if not nist_controls:
        return 0.0

    total_frameworks = len(ICDEV_FRAMEWORKS)
    if total_frameworks == 0:
        return 0.0

    matched_score = 0.0
    for fw in ICDEV_FRAMEWORKS:
        if fw in icdev_frameworks:
            matched_score += 1.0
        elif nist_controls:
            # Crosswalk match: if we have NIST controls, partial credit
            matched_score += 0.5
        # else: 0.0 for no match

    coverage = matched_score / total_frameworks
    return round(min(1.0, coverage), 4)


def _determine_icdev_frameworks(nist_controls):
    """Determine which ICDEV™ frameworks are relevant based on NIST control mappings.

    Uses control family prefix matching to determine framework relevance.

    Args:
        nist_controls: List of NIST control IDs.

    Returns:
        List of matching ICDEV™ framework identifiers.
    """
    if not nist_controls:
        return []

    # "all" means the body maps to all NIST controls
    if "all" in nist_controls:
        return list(ICDEV_FRAMEWORKS)

    matched = set()

    # NIST 800-53 is always relevant if we have any NIST controls
    matched.add("nist_800_53")

    # Extract control families (e.g. "AC" from "AC-2")
    families = set()
    for ctrl in nist_controls:
        parts = ctrl.split("-")
        if parts:
            families.add(parts[0].upper())

    # FedRAMP uses all NIST 800-53 controls
    if families:
        matched.add("fedramp_moderate")
        matched.add("fedramp_high")

    # NIST 800-171 focuses on AC, AT, AU, CM, IA, IR, MA, MP, PE, PS, RA, CA, SC, SI, PM
    nist_171_families = {"AC", "AT", "AU", "CM", "IA", "IR", "MA", "MP", "PE", "PS", "RA", "CA", "SC", "SI", "PM"}
    if families & nist_171_families:
        matched.add("nist_800_171")
        matched.add("cmmc")

    # HIPAA focuses on AC, AU, SC, MP, IA, CP
    hipaa_families = {"AC", "AU", "SC", "MP", "IA", "CP"}
    if families & hipaa_families:
        matched.add("hipaa")
        matched.add("hitrust")

    # SOC 2 focuses on AC, AU, CC, CM, SC, SI
    soc2_families = {"AC", "AU", "CM", "SC", "SI"}
    if families & soc2_families:
        matched.add("soc2")

    # PCI DSS focuses on AC, AU, SC, SI, IA, CM
    pci_families = {"AC", "AU", "SC", "SI", "IA", "CM"}
    if families & pci_families:
        matched.add("pci_dss")

    # ISO 27001 has broad coverage
    if families:
        matched.add("iso27001")

    # CJIS focuses on AC, AU, IA, SC, PE
    cjis_families = {"AC", "AU", "IA", "SC", "PE"}
    if families & cjis_families:
        matched.add("cjis")

    # NIST 800-207 ZTA focuses on AC, SC, SI, AU, SA
    zta_families = {"AC", "SC", "SI", "AU", "SA"}
    if families & zta_families:
        matched.add("nist_800_207")

    return sorted(matched)


# =========================================================================
# CORE FUNCTIONS
# =========================================================================
def map_regulatory_signals(session_id, db_path=None):
    """Map regulatory signals for a session to ICDEV™ crosswalk frameworks.

    Loads the regulatory registry, finds all signals with source='regulatory_body'
    for the given session, matches each to a registry body via keyword matching,
    extracts NIST control mappings, determines ICDEV™ framework coverage,
    counts enforcement actions, detects deadlines, and INSERTs results into
    research_regulatory_map (append-only).

    Args:
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        Dict with mapped count, skipped count, avg_coverage, and body summary.
    """
    registry = load_registry()
    if not registry:
        return {
            "error": "Regulatory registry not found or empty",
            "hint": f"Expected at: {REGISTRY_PATH}",
        }

    conn = _get_db(db_path)
    try:
        # Get all regulatory signals for this session
        rows = conn.execute(
            """SELECT * FROM research_signals
               WHERE session_id = %s AND source = 'regulatory_body'
               ORDER BY discovered_at ASC""",
            (session_id,),
        ).fetchall()

        if not rows:
            return {
                "session_id": session_id,
                "mapped": 0,
                "skipped": 0,
                "avg_coverage": 0.0,
                "bodies": {},
                "mapped_at": _now(),
            }

        mapped = 0
        skipped = 0
        coverage_scores = []
        body_counts = {}

        for row in rows:
            signal = dict(row)
            signal_title = signal.get("title", "")
            signal_body = signal.get("body", "") or ""
            signal_url = signal.get("url", "")
            combined_text = f"{signal_title} {signal_body}"

            # Find best matching body in registry
            best_body_key = None
            best_body_def = None
            best_score = 0.0

            for body_key, body_def in registry.items():
                score = _match_body_to_signal(body_key, body_def, signal_title, signal_body)
                if score > best_score:
                    best_score = score
                    best_body_key = body_key
                    best_body_def = body_def

            # Require minimum match score
            if best_score < 0.1 or not best_body_def:
                skipped += 1
                continue

            # Extract NIST control mappings from registry
            nist_controls = best_body_def.get("nist_control_mapping", [])

            # Determine ICDEV™ framework coverage
            icdev_frameworks = _determine_icdev_frameworks(nist_controls)

            # Compute crosswalk coverage
            crosswalk_coverage = _compute_crosswalk_coverage(nist_controls, icdev_frameworks)
            coverage_scores.append(crosswalk_coverage)

            # Count enforcement actions
            enforcement_count = _count_enforcement_actions(combined_text)

            # Detect deadlines
            deadline = _detect_deadline(combined_text)

            # Build gap analysis
            all_frameworks = set(ICDEV_FRAMEWORKS)
            covered_frameworks = set(icdev_frameworks)
            uncovered = sorted(all_frameworks - covered_frameworks)
            gap_analysis = {
                "covered": sorted(covered_frameworks),
                "uncovered": uncovered,
                "coverage_pct": round(len(covered_frameworks) / max(len(all_frameworks), 1) * 100, 1),
            }

            # Determine regulation name from registry key_regulations or signal title
            reg_name = signal_title
            key_regs = best_body_def.get("key_regulations", [])
            for kr in key_regs:
                if kr.lower() in combined_text.lower():
                    reg_name = kr
                    break

            # Build metadata
            metadata = {
                "match_score": round(best_score, 4),
                "signal_id": signal.get("id", ""),
                "source_type": signal.get("source_type", ""),
                "body_country": best_body_def.get("country", ""),
                "body_website": best_body_def.get("website", ""),
            }

            # INSERT into research_regulatory_map (append-only)
            map_id = _regmap_id()
            conn.execute(
                """INSERT INTO research_regulatory_map
                   (id, session_id, challenge_id, regulatory_body, regulation_name,
                    regulation_id, regulation_url, enforcement_actions, deadline,
                    nist_controls, icdev_frameworks, crosswalk_coverage,
                    gap_analysis, metadata, mapped_at, classification)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI')""",
                (
                    map_id,
                    session_id,
                    None,  # challenge_id linked later via map_challenge_regulations
                    best_body_key,
                    reg_name,
                    best_body_key,  # regulation_id = body abbreviation
                    signal_url or best_body_def.get("website", ""),
                    enforcement_count,
                    deadline,
                    json.dumps(nist_controls),
                    json.dumps(icdev_frameworks),
                    crosswalk_coverage,
                    json.dumps(gap_analysis),
                    json.dumps(metadata),
                    _now(),
                ),
            )
            mapped += 1

            # Track body counts
            body_counts[best_body_key] = body_counts.get(best_body_key, 0) + 1

        conn.commit()

        avg_coverage = round(sum(coverage_scores) / len(coverage_scores), 4) if coverage_scores else 0.0

        _audit(
            "research.regulatory.map",
            f"Mapped {mapped} regulatory signals for session {session_id} "
            f"({skipped} skipped, avg coverage {avg_coverage:.4f})",
            {
                "session_id": session_id,
                "mapped": mapped,
                "skipped": skipped,
                "avg_coverage": avg_coverage,
                "bodies": body_counts,
            },
        )

        return {
            "session_id": session_id,
            "mapped": mapped,
            "skipped": skipped,
            "avg_coverage": avg_coverage,
            "bodies": body_counts,
            "mapped_at": _now(),
        }

    finally:
        conn.close()


def map_challenge_regulations(challenge_id, session_id, db_path=None):
    """Map a specific challenge to its regulatory landscape.

    Gets the challenge from DB, retrieves all regulatory maps for the session,
    and matches regulatory maps to the challenge by keyword overlap between
    challenge keywords and regulation keywords. Links matched regulatory maps
    to the challenge by updating challenge_id.

    Args:
        challenge_id: Research challenge ID.
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        Dict with challenge_id, matched regulation count, and regulation names.
    """
    conn = _get_db(db_path)
    try:
        # Get challenge from DB
        challenge_row = conn.execute("SELECT * FROM research_challenges WHERE id = %s", (challenge_id,)).fetchone()
        if not challenge_row:
            return {"error": f"Challenge not found: {challenge_id}"}

        challenge = dict(challenge_row)

        # Parse challenge keywords
        try:
            challenge_keywords = json.loads(challenge.get("keywords", "[]") or "[]")
            if not isinstance(challenge_keywords, list):
                challenge_keywords = []
        except (json.JSONDecodeError, TypeError):
            challenge_keywords = []

        challenge_title = (challenge.get("title", "") or "").lower()
        challenge_desc = (challenge.get("description", "") or "").lower()
        challenge_text = f"{challenge_title} {challenge_desc} {' '.join(challenge_keywords).lower()}"

        # Get all regulatory maps for this session (unlinked to a challenge)
        reg_maps = conn.execute(
            """SELECT * FROM research_regulatory_map
               WHERE session_id = %s
               ORDER BY mapped_at ASC""",
            (session_id,),
        ).fetchall()

        linked = 0
        regulation_names = []

        for reg_row in reg_maps:
            reg = dict(reg_row)
            reg_body = (reg.get("regulatory_body", "") or "").lower()
            reg_name = (reg.get("regulation_name", "") or "").lower()

            # Parse NIST controls for keyword matching
            try:
                nist_controls = json.loads(reg.get("nist_controls", "[]") or "[]")
                if not isinstance(nist_controls, list):
                    nist_controls = []
            except (json.JSONDecodeError, TypeError):
                nist_controls = []

            # Build regulation text for matching
            reg_text = f"{reg_body} {reg_name} {' '.join(nist_controls).lower()}"

            # Count keyword overlap
            match_count = 0
            for kw in challenge_keywords:
                if len(kw) >= 3 and kw.lower() in reg_text:
                    match_count += 1
            # Also check regulation terms in challenge text
            reg_terms = reg_body.split() + reg_name.split()
            for term in reg_terms:
                if len(term) >= 4 and term in challenge_text:
                    match_count += 1

            # Require minimum overlap
            if match_count >= 1:
                conn.execute(
                    """UPDATE research_regulatory_map
                       SET challenge_id = %s
                       WHERE id = %s AND (challenge_id IS NULL OR challenge_id = '')""",
                    (challenge_id, reg["id"]),
                )
                linked += 1
                regulation_names.append(reg.get("regulation_name", ""))

        conn.commit()

        _audit(
            "research.regulatory.challenge_link",
            f"Linked {linked} regulations to challenge {challenge_id}",
            {
                "challenge_id": challenge_id,
                "session_id": session_id,
                "linked": linked,
                "regulation_names": regulation_names,
            },
        )

        return {
            "challenge_id": challenge_id,
            "session_id": session_id,
            "regulations_linked": linked,
            "regulation_names": regulation_names,
            "linked_at": _now(),
        }

    finally:
        conn.close()


def get_regulatory_landscape(session_id, db_path=None):
    """Return regulatory landscape summary for a session.

    Aggregates regulation counts per body, total enforcement actions,
    average crosswalk coverage, and sorts by body name.

    Args:
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        Dict with bodies list, total counts, and avg coverage.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM research_regulatory_map
               WHERE session_id = %s
               ORDER BY regulatory_body ASC""",
            (session_id,),
        ).fetchall()

        if not rows:
            return {
                "session_id": session_id,
                "total_regulations": 0,
                "total_enforcement_actions": 0,
                "avg_crosswalk_coverage": 0.0,
                "bodies": [],
            }

        # Aggregate per body
        body_data = {}
        total_enforcement = 0
        coverage_scores = []

        for row in rows:
            reg = dict(row)
            body = reg.get("regulatory_body", "unknown")
            enforcement = reg.get("enforcement_actions", 0) or 0
            coverage = reg.get("crosswalk_coverage", 0.0) or 0.0

            if body not in body_data:
                body_data[body] = {
                    "regulatory_body": body,
                    "regulation_count": 0,
                    "enforcement_actions": 0,
                    "avg_coverage": 0.0,
                    "coverages": [],
                    "regulations": [],
                    "deadlines": [],
                }

            body_data[body]["regulation_count"] += 1
            body_data[body]["enforcement_actions"] += enforcement
            body_data[body]["coverages"].append(coverage)
            body_data[body]["regulations"].append(reg.get("regulation_name", ""))

            deadline = reg.get("deadline")
            if deadline:
                body_data[body]["deadlines"].append(deadline)

            total_enforcement += enforcement
            coverage_scores.append(coverage)

        # Compute averages and clean up
        bodies = []
        for body_key in sorted(body_data.keys()):
            bd = body_data[body_key]
            coverages = bd.pop("coverages")
            bd["avg_coverage"] = round(sum(coverages) / max(len(coverages), 1), 4)
            bodies.append(bd)

        avg_coverage = round(sum(coverage_scores) / len(coverage_scores), 4) if coverage_scores else 0.0

        return {
            "session_id": session_id,
            "total_regulations": len(rows),
            "total_enforcement_actions": total_enforcement,
            "avg_crosswalk_coverage": avg_coverage,
            "bodies": bodies,
        }

    finally:
        conn.close()


def get_challenge_regulations(challenge_id, db_path=None):
    """Get all regulatory mappings for a challenge.

    Args:
        challenge_id: Research challenge ID.
        db_path: Optional database path override.

    Returns:
        Dict with challenge_id and list of regulation mappings.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM research_regulatory_map
               WHERE challenge_id = %s
               ORDER BY mapped_at ASC""",
            (challenge_id,),
        ).fetchall()

        regulations = []
        for row in rows:
            reg = dict(row)
            # Parse JSON fields
            try:
                nist_controls = json.loads(reg.get("nist_controls", "[]") or "[]")
            except (json.JSONDecodeError, TypeError):
                nist_controls = []
            try:
                icdev_frameworks = json.loads(reg.get("icdev_frameworks", "[]") or "[]")
            except (json.JSONDecodeError, TypeError):
                icdev_frameworks = []
            try:
                gap_analysis = json.loads(reg.get("gap_analysis", "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                gap_analysis = {}
            try:
                metadata = json.loads(reg.get("metadata", "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                metadata = {}

            regulations.append(
                {
                    "id": reg["id"],
                    "session_id": reg.get("session_id", ""),
                    "regulatory_body": reg.get("regulatory_body", ""),
                    "regulation_name": reg.get("regulation_name", ""),
                    "regulation_id": reg.get("regulation_id", ""),
                    "regulation_url": reg.get("regulation_url", ""),
                    "enforcement_actions": reg.get("enforcement_actions", 0),
                    "deadline": reg.get("deadline"),
                    "nist_controls": nist_controls,
                    "icdev_frameworks": icdev_frameworks,
                    "crosswalk_coverage": reg.get("crosswalk_coverage", 0.0),
                    "gap_analysis": gap_analysis,
                    "metadata": metadata,
                    "mapped_at": reg.get("mapped_at", ""),
                }
            )

        return {
            "challenge_id": challenge_id,
            "total_regulations": len(regulations),
            "regulations": regulations,
        }

    finally:
        conn.close()


# =========================================================================
# HUMAN-READABLE OUTPUT
# =========================================================================
def _print_human(args, result):
    """Print human-readable output for each command."""
    print("=" * 70)
    print("  REGULATORY MAPPER -- CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}")
        hint = result.get("hint")
        if hint:
            print(f"  HINT:  {hint}")
        print()
        print("=" * 70)
        return

    if args.map:
        print(f"\n  Session:      {result.get('session_id', '')}")
        print(f"  Mapped:       {result.get('mapped', 0)}")
        print(f"  Skipped:      {result.get('skipped', 0)}")
        print(f"  Avg Coverage: {result.get('avg_coverage', 0):.4f}")
        print(f"  Mapped At:    {result.get('mapped_at', '')}")
        bodies = result.get("bodies", {})
        if bodies:
            print("\n  Regulations per Body:")
            print(f"    {'Body':12s}  {'Count':>6s}")
            print(f"    {'-' * 12}  {'-' * 6}")
            for body, count in sorted(bodies.items()):
                print(f"    {body:12s}  {count:6d}")

    elif args.landscape:
        print(f"\n  Session:             {result.get('session_id', '')}")
        print(f"  Total Regulations:   {result.get('total_regulations', 0)}")
        print(f"  Enforcement Actions: {result.get('total_enforcement_actions', 0)}")
        print(f"  Avg Coverage:        {result.get('avg_crosswalk_coverage', 0):.4f}")
        bodies = result.get("bodies", [])
        if bodies:
            print()
            print(f"    {'Body':12s}  {'Regs':>5s}  {'Enforce':>8s}  {'Coverage':>9s}  Deadlines")
            print(f"    {'-' * 12}  {'-' * 5}  {'-' * 8}  {'-' * 9}  {'-' * 20}")
            for bd in bodies:
                deadlines = bd.get("deadlines", [])
                dl_str = ", ".join(deadlines[:3]) if deadlines else "None"
                print(
                    f"    {bd['regulatory_body']:12s}  "
                    f"{bd['regulation_count']:5d}  "
                    f"{bd['enforcement_actions']:8d}  "
                    f"{bd['avg_coverage']:9.4f}  "
                    f"{dl_str}"
                )

    elif args.challenge_regs:
        print(f"\n  Challenge:    {result.get('challenge_id', '')}")
        print(f"  Regulations:  {result.get('total_regulations', 0)}")
        regs = result.get("regulations", [])
        if regs:
            print()
            for i, reg in enumerate(regs, 1):
                print(f"  {i:3d}. [{reg.get('regulatory_body', '')}] {reg.get('regulation_name', '')}")
                print(
                    f"       Coverage: {reg.get('crosswalk_coverage', 0):.4f}  |  "
                    f"Enforcement: {reg.get('enforcement_actions', 0)}  |  "
                    f"Deadline: {reg.get('deadline') or 'None'}"
                )
                nist = reg.get("nist_controls", [])
                if nist:
                    print(f"       NIST: {', '.join(nist[:8])}")
                frameworks = reg.get("icdev_frameworks", [])
                if frameworks:
                    print(f"       ICDEV™: {', '.join(frameworks[:6])}")
                print()
        else:
            print("\n  No regulations linked to this challenge.")

    print("=" * 70)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="ICDEV™ Research Engine Regulatory Mapper -- CUI // SP-CTI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --map --session-id rsess-xxx --json\n"
            "  %(prog)s --landscape --session-id rsess-xxx --json\n"
            "  %(prog)s --challenge-regs --challenge-id rchal-xxx --json\n"
            "  %(prog)s --landscape --session-id rsess-xxx --human\n"
        ),
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--map",
        action="store_true",
        help="Map regulatory signals to ICDEV™ crosswalk frameworks",
    )
    group.add_argument(
        "--landscape",
        action="store_true",
        help="Show regulatory landscape summary for a session",
    )
    group.add_argument(
        "--challenge-regs",
        action="store_true",
        help="Show regulations for a specific challenge",
    )

    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Research session ID (with --map, --landscape)",
    )
    parser.add_argument(
        "--challenge-id",
        type=str,
        default=None,
        help="Research challenge ID (with --challenge-regs)",
    )

    args = parser.parse_args()

    try:
        if args.map:
            if not args.session_id:
                parser.error("--map requires --session-id")
            result = map_regulatory_signals(args.session_id, db_path=args.db_path)
        elif args.landscape:
            if not args.session_id:
                parser.error("--landscape requires --session-id")
            result = get_regulatory_landscape(args.session_id, db_path=args.db_path)
        elif args.challenge_regs:
            if not args.challenge_id:
                parser.error("--challenge-regs requires --challenge-id")
            result = get_challenge_regulations(args.challenge_id, db_path=args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.human:
            _print_human(args, result)
        elif args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Default to JSON if neither --human nor --json specified
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as e:
        error = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except ValueError as e:
        error = {"error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except Exception as e:
        error = {"error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
