#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""LCAT Mapper -- parse PWS/SOW, map tasks to Labor Categories with BLS SOC codes.

Extracts task descriptions from Performance Work Statements, maps to standardized
Labor Categories using BLS SOC codes, calculates FTE allocations, and generates
Basis of Estimate (BOE) documentation.  Deterministic (no LLM, air-gap safe).

DB tables: pg_lcat_allocations (write), pg_cost_volumes (read), audit_trail (write)

Usage:
    python tools/govcon/lcat_mapper.py --opportunity-id "opp-xxx" --parse-pws --text "..." --json
    python tools/govcon/lcat_mapper.py --opportunity-id "opp-xxx" --parse-pws --file pws.txt --json
    python tools/govcon/lcat_mapper.py --opportunity-id "opp-xxx" --map --json
    python tools/govcon/lcat_mapper.py --opportunity-id "opp-xxx" --calculate --duration-months 12 --json
    python tools/govcon/lcat_mapper.py --opportunity-id "opp-xxx" --boe --json
    python tools/govcon/lcat_mapper.py --opportunity-id "opp-xxx" --list --json
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# BLS SOC Catalog -- top 30 IT/Engineering/Management labor categories
# ---------------------------------------------------------------------------
BLS_SOC_CATALOG = {
    "15-1252": {
        "title": "Software Developers",
        "keywords": [
            "software",
            "developer",
            "programming",
            "code",
            "application",
            "api",
            "microservice",
            "backend",
            "frontend",
            "full stack",
            "devops",
        ],
    },
    "15-1212": {
        "title": "Information Security Analysts",
        "keywords": [
            "security",
            "cybersecurity",
            "stig",
            "rmf",
            "ato",
            "vulnerability",
            "penetration",
            "siem",
            "incident response",
            "isso",
        ],
    },
    "15-1244": {
        "title": "Network and Computer Systems Administrators",
        "keywords": [
            "network",
            "sysadmin",
            "system administrator",
            "infrastructure",
            "server",
            "vmware",
            "active directory",
            "dns",
            "dhcp",
        ],
    },
    "15-1211": {
        "title": "Computer Systems Analysts",
        "keywords": [
            "systems analyst",
            "requirements",
            "business analyst",
            "functional",
            "process improvement",
            "gap analysis",
        ],
    },
    "15-1241": {
        "title": "Computer Network Architects",
        "keywords": ["network architect", "network design", "wan", "lan", "sdwan", "zero trust", "network engineering"],
    },
    "15-1251": {
        "title": "Computer Programmers",
        "keywords": ["programmer", "coding", "scripting", "automation", "python", "java", "javascript", "bash"],
    },
    "15-1232": {
        "title": "Computer User Support Specialists",
        "keywords": ["help desk", "tier 1", "tier 2", "end user", "desktop support", "troubleshoot", "service desk"],
    },
    "15-1231": {
        "title": "Computer Network Support Specialists",
        "keywords": ["network support", "network troubleshoot", "network monitor", "noc"],
    },
    "15-1245": {
        "title": "Database Administrators and Architects",
        "keywords": ["database", "dba", "sql", "postgresql", "oracle", "data model", "etl", "data warehouse"],
    },
    "15-1221": {
        "title": "Computer and Information Research Scientists",
        "keywords": [
            "research",
            "machine learning",
            "ai",
            "artificial intelligence",
            "nlp",
            "data science",
            "algorithm",
        ],
    },
    "15-2031": {
        "title": "Operations Research Analysts",
        "keywords": ["operations research", "optimization", "simulation", "modeling", "analytics", "decision analysis"],
    },
    "15-2051": {
        "title": "Data Scientists and Mathematical Science Occupations",
        "keywords": [
            "data scientist",
            "data analytics",
            "statistical",
            "predictive",
            "visualization",
            "tableau",
            "power bi",
        ],
    },
    "17-2061": {
        "title": "Computer Hardware Engineers",
        "keywords": ["hardware", "embedded", "firmware", "fpga", "circuit", "pcb", "iot", "edge computing"],
    },
    "11-3021": {
        "title": "Computer and Information Systems Managers",
        "keywords": [
            "it manager",
            "program manager",
            "project manager",
            "technical lead",
            "team lead",
            "scrum master",
            "agile",
        ],
    },
    "13-1111": {
        "title": "Management Analysts",
        "keywords": [
            "management consultant",
            "process improvement",
            "organizational",
            "strategic planning",
            "change management",
        ],
    },
    "15-1299": {
        "title": "Computer Occupations, All Other",
        "keywords": ["cloud", "aws", "azure", "gcp", "kubernetes", "docker", "container", "terraform", "ansible"],
    },
    "15-1243": {
        "title": "Database Architects",
        "keywords": ["data architect", "data modeling", "schema design", "data governance", "master data"],
    },
    "17-2199": {
        "title": "Engineers, All Other",
        "keywords": ["systems engineer", "solution architect", "enterprise architect", "technical architect"],
    },
    "15-1242": {
        "title": "Computer Hardware Engineers",
        "keywords": ["hardware engineer", "component", "integration test"],
    },
    "13-1161": {
        "title": "Market Research Analysts",
        "keywords": ["market research", "competitive analysis", "capture", "business development", "bd"],
    },
    "43-9011": {
        "title": "Computer Operators",
        "keywords": ["operator", "batch processing", "job scheduling", "mainframe"],
    },
    "15-1253": {
        "title": "Software Quality Assurance Analysts and Testers",
        "keywords": ["qa", "quality assurance", "testing", "test automation", "selenium", "playwright", "regression"],
    },
    "13-1082": {
        "title": "Project Management Specialists",
        "keywords": ["pmp", "project management", "scheduling", "earned value", "evm", "milestone", "deliverable"],
    },
    "11-9199": {"title": "Managers, All Other", "keywords": ["director", "vp", "executive", "oversight", "governance"]},
    "27-3042": {
        "title": "Technical Writers",
        "keywords": ["technical writer", "documentation", "user guide", "sop", "procedure", "manual"],
    },
    "15-1254": {
        "title": "Web Developers",
        "keywords": ["web developer", "html", "css", "react", "angular", "vue", "ui", "ux", "frontend"],
    },
    "13-1199": {
        "title": "Business Operations Specialists",
        "keywords": ["operations", "logistics", "supply chain", "procurement"],
    },
    "33-1099": {
        "title": "First-Line Supervisors of Protective Service Workers",
        "keywords": ["physical security", "access control", "guard", "surveillance"],
    },
    "15-1255": {
        "title": "Web and Digital Interface Designers",
        "keywords": ["ux designer", "ui designer", "interaction design", "wireframe", "prototype"],
    },
    "29-9021": {
        "title": "Health Information Technologists",
        "keywords": ["health it", "ehr", "hipaa", "hl7", "fhir", "medical informatics"],
    },
}

# Complexity keywords for FTE estimation
COMPLEXITY_KEYWORDS = {
    "highly_complex": [
        "architect",
        "design",
        "complex",
        "enterprise",
        "large-scale",
        "migration",
        "modernization",
        "transformation",
    ],
    "complex": ["develop", "implement", "integrate", "configure", "deploy", "analyze", "engineer"],
    "moderate": ["maintain", "support", "monitor", "update", "patch", "administer", "manage"],
    "simple": ["document", "report", "track", "coordinate", "schedule", "communicate"],
}

COMPLEXITY_FTE = {"highly_complex": 2.0, "complex": 1.0, "moderate": 0.5, "simple": 0.25}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="lcat"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details, opportunity_id=None):
    conn.execute(
        "INSERT INTO audit_trail (created_at, event_type, actor, action, details, project_id, session_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            _now(),
            event_type,
            "lcat_mapper",
            action,
            json.dumps(details) if isinstance(details, dict) else str(details),
            opportunity_id,
            None,
        ),
    )


def _content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def parse_pws(text):
    """Extract task descriptions from PWS/SOW text."""
    tasks = []
    # Pattern 1: Numbered tasks (e.g., "3.1.1 Task Title")
    numbered = re.findall(r"(\d+(?:\.\d+)+)\s+(.+?)(?:\n|$)", text)
    for num, title in numbered:
        tasks.append({"task_number": num.strip(), "description": title.strip(), "source": "numbered_section"})

    # Pattern 2: Shall statements
    shall_re = re.compile(r"(?:The\s+)?(?:contractor|vendor|offeror)\s+shall\s+(.+?)(?:\.|;|$)", re.IGNORECASE)
    for m in shall_re.finditer(text):
        desc = m.group(1).strip()
        if len(desc) > 20:
            tasks.append({"task_number": None, "description": f"shall {desc}", "source": "shall_statement"})

    # Pattern 3: Bullet items with action verbs
    bullet_re = re.compile(
        r"(?:^|\n)\s*[-•*]\s*((?:Provide|Develop|Maintain|Support|Manage|Design|Implement|Configure|Monitor|Perform|Conduct|Deliver|Ensure|Create|Establish)\s+.+?)(?:\n|$)",
        re.IGNORECASE,
    )
    for m in bullet_re.finditer(text):
        desc = m.group(1).strip()
        if len(desc) > 15:
            tasks.append({"task_number": None, "description": desc, "source": "action_bullet"})

    # Deduplicate by content hash
    seen = set()
    unique = []
    for t in tasks:
        h = _content_hash(t["description"].lower())
        if h not in seen:
            seen.add(h)
            unique.append(t)

    return {"status": "ok", "tasks": unique, "count": len(unique)}


def map_to_lcat(task_description):
    """Map a task description to BLS SOC labor category via keyword matching."""
    desc_lower = task_description.lower()
    scores = {}

    for soc_code, info in BLS_SOC_CATALOG.items():
        score = 0
        for kw in info["keywords"]:
            if kw in desc_lower:
                score += 1
        if score > 0:
            scores[soc_code] = score

    if not scores:
        return {"soc_code": "15-1299", "title": "Computer Occupations, All Other", "confidence": 0.1, "match_score": 0}

    best_soc = max(scores, key=scores.get)
    best_score = scores[best_soc]
    confidence = min(1.0, best_score / 3.0)

    return {
        "soc_code": best_soc,
        "title": BLS_SOC_CATALOG[best_soc]["title"],
        "confidence": round(confidence, 2),
        "match_score": best_score,
    }


def _assess_complexity(description):
    """Determine task complexity from keywords."""
    desc_lower = description.lower()
    for level in ["highly_complex", "complex", "moderate", "simple"]:
        for kw in COMPLEXITY_KEYWORDS[level]:
            if kw in desc_lower:
                return level
    return "moderate"  # default


def calculate_ftes(tasks, duration_months=12):
    """Calculate FTE allocations per task."""
    allocations = []
    for task in tasks:
        desc = task.get("description", "")
        complexity = _assess_complexity(desc)
        fte = COMPLEXITY_FTE[complexity]
        lcat = map_to_lcat(desc)

        allocations.append(
            {
                "task_description": desc,
                "task_number": task.get("task_number"),
                "labor_category": lcat["title"],
                "bls_soc_code": lcat["soc_code"],
                "confidence": lcat["confidence"],
                "complexity": complexity,
                "fte_count": fte,
                "duration_months": duration_months,
                "total_hours": round(fte * duration_months * 173.33, 1),  # ~173.33 hrs/month
            }
        )

    total_fte = sum(a["fte_count"] for a in allocations)
    return {
        "status": "ok",
        "allocations": allocations,
        "total_fte": round(total_fte, 2),
        "duration_months": duration_months,
    }


def store_allocations(opportunity_id, allocations):
    """Store LCAT allocations in DB."""
    conn = _get_db()
    stored = []
    for alloc in allocations:
        aid = _gen_id("lcat")
        conn.execute(
            "INSERT INTO pg_lcat_allocations (id, cost_volume_id, task_description, labor_category, "
            "bls_soc_code, fte_count, hourly_rate, annual_cost, basis_of_estimate, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                aid,
                opportunity_id,
                alloc["task_description"],
                alloc["labor_category"],
                alloc["bls_soc_code"],
                alloc["fte_count"],
                None,
                None,
                json.dumps({"complexity": alloc.get("complexity"), "confidence": alloc.get("confidence")}),
                _now(),
            ),
        )
        stored.append(aid)

    _audit(conn, "lcat.store", f"Stored {len(stored)} LCAT allocations", {"count": len(stored)}, opportunity_id)
    conn.commit()
    conn.close()
    return {"status": "ok", "stored_count": len(stored), "ids": stored}


def list_allocations(opportunity_id):
    """List stored LCAT allocations."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, task_description, labor_category, bls_soc_code, fte_count, hourly_rate, annual_cost, basis_of_estimate, created_at "
        "FROM pg_lcat_allocations WHERE cost_volume_id = %s ORDER BY created_at",
        (opportunity_id,),
    ).fetchall()
    conn.close()

    allocations = []
    for r in rows:
        allocations.append(
            {
                "id": r["id"] if isinstance(r, dict) else r[0],
                "task_description": r["task_description"] if isinstance(r, dict) else r[1],
                "labor_category": r["labor_category"] if isinstance(r, dict) else r[2],
                "bls_soc_code": r["bls_soc_code"] if isinstance(r, dict) else r[3],
                "fte_count": r["fte_count"] if isinstance(r, dict) else r[4],
                "hourly_rate": r["hourly_rate"] if isinstance(r, dict) else r[5],
                "annual_cost": r["annual_cost"] if isinstance(r, dict) else r[6],
            }
        )

    total_fte = sum(a["fte_count"] or 0 for a in allocations)
    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "allocations": allocations,
        "count": len(allocations),
        "total_fte": round(total_fte, 2),
    }


def generate_boe(opportunity_id, tasks=None, allocations=None):
    """Generate Basis of Estimate markdown document."""
    if allocations is None:
        result = list_allocations(opportunity_id)
        allocations = result.get("allocations", [])

    lines = [
        "# CUI // SP-CTI",
        "",
        "# Basis of Estimate (BOE)",
        "",
        f"**Opportunity ID:** {opportunity_id}",
        f"**Generated:** {_now()}",
        f"**Total LCATs:** {len(allocations)}",
        f"**Total FTEs:** {sum(a.get('fte_count', 0) or 0 for a in allocations):.1f}",
        "",
        "## Labor Category Allocations",
        "",
        "| # | Labor Category | BLS SOC | FTE | Task Description |",
        "|---|---------------|---------|-----|-----------------|",
    ]

    for i, alloc in enumerate(allocations, 1):
        desc = (
            (alloc.get("task_description", "")[:60] + "...")
            if len(alloc.get("task_description", "")) > 60
            else alloc.get("task_description", "")
        )
        lines.append(
            f"| {i} | {alloc.get('labor_category', 'N/A')} | {alloc.get('bls_soc_code', 'N/A')} | {alloc.get('fte_count', 0):.1f} | {desc} |"
        )

    lines.extend(
        [
            "",
            "## Methodology",
            "",
            "FTE estimates derived from task complexity analysis using ICDEV™ deterministic classifier.",
            "Labor categories mapped to BLS Standard Occupational Classification (SOC) codes.",
            "All estimates are subject to prime contractor review and adjustment.",
            "",
            "# CUI // SP-CTI",
        ]
    )

    boe_text = "\n".join(lines)
    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "boe_markdown": boe_text,
        "allocation_count": len(allocations),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="LCAT Mapper — PWS task extraction and labor category mapping")
    parser.add_argument("--opportunity-id", required=True, help="Opportunity UUID")
    parser.add_argument("--parse-pws", action="store_true", help="Parse PWS text to extract tasks")
    parser.add_argument("--text", help="PWS text (inline)")
    parser.add_argument("--file", help="PWS text file path")
    parser.add_argument("--map", action="store_true", help="Map extracted tasks to LCATs and store")
    parser.add_argument("--calculate", action="store_true", help="Calculate FTE allocations")
    parser.add_argument("--duration-months", type=int, default=12, help="Period of performance in months")
    parser.add_argument("--boe", action="store_true", help="Generate Basis of Estimate document")
    parser.add_argument("--list", action="store_true", help="List stored allocations")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = {}

    if args.parse_pws:
        text = args.text or ""
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        if not text:
            result = {"status": "error", "message": "Provide --text or --file with PWS content"}
        else:
            result = parse_pws(text)
            if args.map and result.get("tasks"):
                calc = calculate_ftes(result["tasks"], args.duration_months)
                stored = store_allocations(args.opportunity_id, calc["allocations"])
                result["allocations"] = calc
                result["stored"] = stored

    elif args.calculate:
        existing = list_allocations(args.opportunity_id)
        result = {
            "status": "ok",
            "message": "Use --parse-pws --map to extract and store allocations first",
            "existing": existing,
        }

    elif args.boe:
        result = generate_boe(args.opportunity_id)

    elif args.list:
        result = list_allocations(args.opportunity_id)

    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        if "boe_markdown" in result:
            print(result["boe_markdown"])
        elif "allocations" in result and isinstance(result["allocations"], list):
            print(f"\n  LCAT Allocations for {args.opportunity_id}")
            print(f"  {'=' * 60}")
            for a in result["allocations"]:
                print(
                    f"  {a.get('labor_category', 'N/A'):35s} SOC {a.get('bls_soc_code', 'N/A'):10s} FTE {a.get('fte_count', 0):.1f}"
                )
            print(f"\n  Total FTE: {result.get('total_fte', 0):.1f}")
        else:
            print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
