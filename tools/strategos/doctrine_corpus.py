# CUI // SP-CTI
"""Doctrine Corpus — Clausewitz CoG passages, doctrine citations, and historical precedents.

Provides two capabilities:
  1. Built-in corpus: key Clausewitz "On War" passages + historical case studies,
     available offline without seeded sg_corpus_documents data.
  2. Seeder: inserts the built-in corpus into sg_corpus_documents so it is
     discoverable by the full RAG pipeline (corrective_rag, war_retriever).

Usage:
    from tools.strategos.doctrine_corpus import (
        get_cog_passage,
        get_doctrine_citations,
        get_historical_precedents,
        seed_corpus,
    )

CLI:
    python tools/strategos/doctrine_corpus.py --list            # list all entries
    python tools/strategos/doctrine_corpus.py --seed --json     # seed sg_corpus_documents
    python tools/strategos/doctrine_corpus.py --query "taiwan" --json
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.doctrine_corpus")

# ── Built-in doctrine corpus ───────────────────────────────────────────────────

# Each entry: id, title, doctrine_type, content, theater_tags, keywords
_CORPUS: list[dict[str, Any]] = [
    # ── Clausewitz — Center of Gravity ─────────────────────────────────────
    {
        "id": "clausewitz-cog-001",
        "title": "On War — Center of Gravity (Book 8, Ch. 4)",
        "doctrine_type": "doctrine",
        "theater_tags": ["all"],
        "keywords": ["center of gravity", "cog", "clausewitz", "hub", "power"],
        "content": (
            "One must keep the dominant characteristics of both belligerents in mind. "
            "Out of these characteristics a certain center of gravity develops, the hub "
            "of all power and movement, on which everything depends. It is against this "
            "center of gravity that the concentrated blow must be directed."
            " — Carl von Clausewitz, On War, Book 8, Chapter 4"
        ),
    },
    {
        "id": "clausewitz-cog-002",
        "title": "On War — CoG: Alliance, Capital, Army (Book 8, Ch. 4)",
        "doctrine_type": "doctrine",
        "theater_tags": ["all"],
        "keywords": ["center of gravity", "cog", "alliance", "capital", "army", "clausewitz"],
        "content": (
            "The center of gravity is always found where the mass is greatest. "
            "It will also be the place where the blow will be most effective. "
            "In small countries which rely on foreign support, it is usually the army "
            "of their allies. In countries torn by internal dissension, it is generally "
            "the capital. In small countries dependent on large ones, it is usually "
            "the army of the larger power."
            " — Carl von Clausewitz, On War, Book 8, Chapter 4"
        ),
    },
    {
        "id": "clausewitz-friction-001",
        "title": "On War — Friction in War (Book 1, Ch. 7)",
        "doctrine_type": "doctrine",
        "theater_tags": ["all"],
        "keywords": ["friction", "fog of war", "uncertainty", "clausewitz", "plan"],
        "content": (
            "Everything in war is very simple, but the simplest thing is difficult. "
            "The difficulties accumulate and end by producing a kind of friction that "
            "is inconceivable unless one has experienced war. Friction is the only "
            "concept that more or less corresponds to the factors that distinguish "
            "real war from war on paper."
            " — Carl von Clausewitz, On War, Book 1, Chapter 7"
        ),
    },
    {
        "id": "clausewitz-culmination-001",
        "title": "On War — Culminating Point of Victory (Book 7, Ch. 22)",
        "doctrine_type": "doctrine",
        "theater_tags": ["all"],
        "keywords": ["culmination", "overextension", "logistics", "sustainment", "clausewitz"],
        "content": (
            "There is a culminating point of victory. Since the superiority which leads "
            "to peace must be a goal of military action, following up a victory to the "
            "point where it leads to peace is the most important thing in strategy. "
            "Beyond that point the balance tips the other way, and the reaction that "
            "follows is usually much stronger than the original blow."
            " — Carl von Clausewitz, On War, Book 7, Chapter 22"
        ),
    },
    {
        "id": "clausewitz-trinity-001",
        "title": "On War — Remarkable Trinity (Book 1, Ch. 1)",
        "doctrine_type": "doctrine",
        "theater_tags": ["all"],
        "keywords": ["trinity", "government", "army", "people", "passion", "reason", "clausewitz"],
        "content": (
            "War is more than a true chameleon that slightly adapts its characteristics "
            "to the given case. As a total phenomenon its dominant tendencies always make "
            "war a remarkable trinity — composed of primordial violence, hatred, and "
            "enmity; of the play of chance and probability within which the creative "
            "spirit is free to roam; and of its element of subordination, as an instrument "
            "of policy, which makes it subject to reason alone."
            " — Carl von Clausewitz, On War, Book 1, Chapter 1"
        ),
    },
    # ── FM 3-0 Operations — Unified Land Operations ────────────────────────
    {
        "id": "fm3-0-deep-close-rear",
        "title": "FM 3-0 — Deep, Close, and Rear Operations",
        "doctrine_type": "field_manual",
        "theater_tags": ["all", "land"],
        "keywords": ["deep operations", "close operations", "rear area", "fm 3-0", "combined arms"],
        "content": (
            "Simultaneous, deep, close, and rear operations compress the enemy's "
            "decision cycle, exploit enemy vulnerabilities before they can be corrected, "
            "and prevent enemy recovery. Deep operations attack enemy follow-on forces, "
            "disrupt C2 nodes, and interdict logistics. Close operations destroy enemy "
            "forces in contact. Rear operations protect friendly forces and sustainment."
            " — FM 3-0, Operations (2022)"
        ),
    },
    {
        "id": "fm5-0-mdmp-001",
        "title": "FM 5-0 — Military Decision Making Process (MDMP)",
        "doctrine_type": "field_manual",
        "theater_tags": ["all"],
        "keywords": ["mdmp", "course of action", "coa", "wargaming", "decision", "fm 5-0"],
        "content": (
            "The MDMP is a single, established, and proven analytical process. "
            "Commanders use the MDMP to understand situations, develop options, "
            "select the best option, and produce orders. A properly executed MDMP "
            "produces three viable COAs: one friendly-tempo COA (COA Alpha), one "
            "economy-of-force COA (COA Bravo), and one low-risk consolidation COA "
            "(COA Charlie). COAs are evaluated against suitability, feasibility, "
            "acceptability, distinguishability, and completeness."
            " — FM 5-0, The Operations Process (2022)"
        ),
    },
    # ── Joint Doctrine ──────────────────────────────────────────────────────
    {
        "id": "jp3-0-joint-ops-001",
        "title": "JP 3-0 — Joint Operations: Targeting the CoG",
        "doctrine_type": "field_manual",
        "theater_tags": ["all", "joint"],
        "keywords": ["joint operations", "cog", "critical vulnerability", "jp 3-0", "jcs"],
        "content": (
            "The commander must identify the adversary's center of gravity and "
            "determine what capabilities and sources of strength support it. "
            "Critical capabilities are the primary abilities that allow a CoG to "
            "function as a CoG. Critical requirements are the essential conditions, "
            "resources, and means for a critical capability to be fully operational. "
            "Critical vulnerabilities are critical requirements that are deficient or "
            "can be made deficient."
            " — JP 3-0, Joint Operations (2022)"
        ),
    },
    # ── Historical Precedents ───────────────────────────────────────────────
    {
        "id": "hist-gulf-war-1991-cog",
        "title": "Historical Case — Gulf War 1991: Destroying the Iraqi CoG",
        "doctrine_type": "historical_case",
        "theater_tags": ["middle east", "iraq", "gulf"],
        "keywords": ["gulf war", "desert storm", "iraq", "republican guard", "cog", "1991", "coalition"],
        "content": (
            "Coalition planners in Desert Storm identified the Iraqi Republican Guard "
            "and Saddam Hussein's command-and-control network as the enemy's center "
            "of gravity. The air campaign's 43-day phase systematically attacked "
            "Iraqi C2 nodes, air defense, and logistics before the 100-hour ground "
            "campaign enveloped the Republican Guard. The result — 100,000+ Iraqi "
            "casualties versus 148 U.S. killed in action — validated the principle of "
            "striking the CoG after shaping operations degrade its critical "
            "requirements. Key lesson: deep air interdiction that destroys logistics "
            "and C2 before the ground assault compresses enemy decision-making and "
            "achieves culmination at a fraction of the expected cost."
            " — Historical Case: Operation Desert Storm, 1991"
        ),
    },
    {
        "id": "hist-crimea-2014-hybrid",
        "title": "Historical Case — Crimea 2014: Hybrid Warfare and Information CoG",
        "doctrine_type": "historical_case",
        "theater_tags": ["ukraine", "crimea", "russia", "black sea"],
        "keywords": ["crimea", "ukraine", "russia", "hybrid warfare", "information", "2014", "little green men"],
        "content": (
            "Russia's 2014 Crimea operation demonstrated that the adversary's center "
            "of gravity in a hybrid warfare scenario can be the information environment "
            "and local political will rather than massed forces. Russian Spetsnaz "
            "('little green men') operated without insignia, exploiting the fog of war "
            "to delay NATO and Ukrainian responses. Information operations suppressed "
            "Ukrainian unit cohesion and exploited pro-Russian sentiment in Crimea. "
            "Lesson: against a hybrid adversary, targeting the information CoG — "
            "narrative control, C2 of proxy forces, and local legitimacy — is as "
            "decisive as kinetic action. Culmination was achieved in 16 days with "
            "minimal conventional combat."
            " — Historical Case: Russian Annexation of Crimea, February–March 2014"
        ),
    },
    {
        "id": "hist-ukraine-2022-resilience",
        "title": "Historical Case — Ukraine 2022: Defending the Moral CoG",
        "doctrine_type": "historical_case",
        "theater_tags": ["ukraine", "russia", "donbas", "kyiv"],
        "keywords": ["ukraine", "russia", "kyiv", "2022", "national will", "zelensky", "nato", "resilience"],
        "content": (
            "Russia's February 2022 invasion failed to achieve its initial 72-hour "
            "decapitation of Ukrainian leadership in Kyiv. Ukraine's CoG proved to be "
            "national will, institutionalized in President Zelensky's refusal to flee "
            "and rapid mobilization of reserve forces. Russia's CoG — its logistics "
            "tail and operational C2 — was exposed by over-extension on multiple axes "
            "and Ukrainian anti-armor/MANPADS effectiveness. By Day 40 Russia withdrew "
            "from northern Ukraine, illustrating Clausewitz's culminating point principle. "
            "Key lesson: a defender who preserves national will and international "
            "legitimacy can frustrate a numerically superior attacker who violates the "
            "principle of mass and overextends."
            " — Historical Case: Russian Invasion of Ukraine, February 2022"
        ),
    },
    {
        "id": "hist-taiwan-strait-gray-zone",
        "title": "Historical Case — Taiwan Strait: Gray Zone and A2/AD CoG",
        "doctrine_type": "historical_case",
        "theater_tags": ["taiwan", "pla", "indo-pacific", "south china sea"],
        "keywords": ["taiwan", "pla", "adiz", "a2ad", "anti-access", "gray zone", "maritime", "indo-pacific"],
        "content": (
            "PLA gray-zone operations against Taiwan (2021–present) target the "
            "island's psychological resilience and alliance cohesion as its CoG. "
            "Frequent ADIZ incursions, undersea cable mapping, and cyber campaigns "
            "serve as combined-arms shaping prior to any kinetic phase. JICMARS "
            "analysis identifies the A2/AD bubble (DF-21D/DF-26 plus J-20 CAP) "
            "as the PLA's own CoG — if the missile-launch infrastructure is degraded, "
            "PLA amphibious lift becomes critically vulnerable. Lesson: in a contested "
            "strait-crossing scenario, the defender should mass counter-C2/ISR "
            "effects on A2/AD nodes before the attacker achieves sea/air dominance; "
            "the attacker must neutralize shore-based ASM batteries early."
            " — Historical Case: Taiwan Strait Gray-Zone Operations, 2021–present"
        ),
    },
    {
        "id": "hist-falklands-1982-maritime",
        "title": "Historical Case — Falklands 1982: Maritime CoG and Logistics",
        "doctrine_type": "historical_case",
        "theater_tags": ["falklands", "south atlantic", "maritime"],
        "keywords": ["falklands", "maritime", "logistics", "air power", "exocet", "1982", "uk", "argentina"],
        "content": (
            "The 1982 Falklands War illustrated that in expeditionary maritime operations, "
            "the logistics/amphibious task force becomes the critical CoG. Argentina's "
            "Exocet missiles and Skyhawk attacks targeted this precisely — the sinking "
            "of HMS Sheffield and Atlantic Conveyor threatened the entire operation. "
            "British success hinged on air defense, rapid amphibious seizure of San Carlos "
            "Bay, and speed to decisive action before Argentine air could attrite "
            "the task force to the culminating point. Lesson: an expeditionary force's "
            "sustainment node is its most vulnerable critical requirement; "
            "speed and air superiority protect it."
            " — Historical Case: Falklands War, April–June 1982"
        ),
    },
]

# Index for fast lookup
_CORPUS_BY_ID: dict[str, dict] = {e["id"]: e for e in _CORPUS}
_THEATER_INDEX: dict[str, list[str]] = {}  # theater_tag → [ids]
_KEYWORD_INDEX: dict[str, list[str]] = {}  # keyword → [ids]

for _entry in _CORPUS:
    for _tag in _entry.get("theater_tags", []):
        _THEATER_INDEX.setdefault(_tag, []).append(_entry["id"])
    for _kw in _entry.get("keywords", []):
        _KEYWORD_INDEX.setdefault(_kw.lower(), []).append(_entry["id"])


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass
class DoctrineEntry:
    """A retrieved doctrine or historical precedent entry."""
    id: str
    title: str
    doctrine_type: str  # doctrine | field_manual | historical_case
    content: str
    relevance_score: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    def citation_line(self) -> str:
        """Short citation suitable for inline reference in a brief."""
        source_label = {
            "doctrine": "Clausewitz",
            "field_manual": "FM/JP Doctrine",
            "historical_case": "Historical Precedent",
        }.get(self.doctrine_type, "Reference")
        return f"*{source_label}: {self.title}*"


def get_cog_passage(theater: str = "all") -> DoctrineEntry:
    """Return the most relevant Clausewitz CoG passage for a theater."""
    # Always return the primary CoG definition for all theaters
    primary = _CORPUS_BY_ID["clausewitz-cog-001"]
    return DoctrineEntry(
        id=primary["id"],
        title=primary["title"],
        doctrine_type=primary["doctrine_type"],
        content=primary["content"],
        relevance_score=1.0,
    )


def get_doctrine_citations(
    scenario: str,
    theater: str = "all",
    top_k: int = 3,
) -> list[DoctrineEntry]:
    """Return relevant doctrine entries (non-historical) for a scenario.

    Scores by: theater tag match (0.5 boost) + keyword overlap with scenario text.
    Returns top_k entries sorted by relevance_score desc.
    """
    scenario_lower = scenario.lower()
    theater_lower = theater.lower()

    scored: list[tuple[float, dict]] = []
    seen: set[str] = set()

    for entry in _CORPUS:
        if entry["doctrine_type"] == "historical_case":
            continue
        if entry["id"] in seen:
            continue

        score = 0.0
        # Theater match
        if "all" in entry.get("theater_tags", []) or theater_lower in entry.get("theater_tags", []):
            score += 0.4

        # Keyword overlap with scenario
        kw_hits = sum(1 for kw in entry.get("keywords", []) if kw in scenario_lower)
        score += min(0.6, kw_hits * 0.15)

        if score > 0:
            scored.append((score, entry))
            seen.add(entry["id"])

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        DoctrineEntry(
            id=e["id"],
            title=e["title"],
            doctrine_type=e["doctrine_type"],
            content=e["content"],
            relevance_score=round(s, 3),
        )
        for s, e in scored[:top_k]
    ]


def get_historical_precedents(
    scenario: str,
    theater: str = "all",
    top_k: int = 2,
) -> list[DoctrineEntry]:
    """Return relevant historical precedent entries for a scenario.

    Scores by theater tag match + keyword overlap. Returns top_k entries.
    """
    scenario_lower = scenario.lower()
    theater_lower = theater.lower()

    scored: list[tuple[float, dict]] = []
    seen: set[str] = set()

    for entry in _CORPUS:
        if entry["doctrine_type"] != "historical_case":
            continue
        if entry["id"] in seen:
            continue

        score = 0.0
        tags = [t.lower() for t in entry.get("theater_tags", [])]
        if theater_lower in tags or any(theater_lower in t for t in tags):
            score += 0.5
        elif "all" in tags:
            score += 0.1

        kw_hits = sum(1 for kw in entry.get("keywords", []) if kw in scenario_lower)
        score += min(0.5, kw_hits * 0.12)

        if score > 0:
            scored.append((score, entry))
            seen.add(entry["id"])

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        DoctrineEntry(
            id=e["id"],
            title=e["title"],
            doctrine_type=e["doctrine_type"],
            content=e["content"],
            relevance_score=round(s, 3),
        )
        for s, e in scored[:top_k]
    ]


def seed_corpus(dry_run: bool = False) -> dict[str, Any]:
    """Upsert built-in corpus entries into sg_corpus_documents.

    Uses INSERT OR IGNORE — safe to call multiple times. Returns summary dict.
    """
    if dry_run:
        return {
            "status": "dry_run",
            "entries": len(_CORPUS),
            "ids": [e["id"] for e in _CORPUS],
        }

    try:
        from tools.db.storage import get_connection
        conn = get_connection("icdev")
        if conn is None:
            return {"status": "error", "error": "Could not get DB connection"}

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        skipped = 0

        for entry in _CORPUS:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO sg_corpus_documents
                       (id, title, content, source_type, theater_tags, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        entry["id"],
                        entry["title"],
                        entry["content"],
                        entry["doctrine_type"],
                        json.dumps(entry.get("theater_tags", [])),
                        now,
                    ),
                )
                if conn.execute(
                    "SELECT changes()"
                ).fetchone()[0]:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.debug("Skipping %s: %s", entry["id"], exc)
                skipped += 1

        conn.commit()
        return {
            "status": "ok",
            "inserted": inserted,
            "skipped": skipped,
            "total": len(_CORPUS),
        }

    except Exception as exc:
        logger.warning("seed_corpus failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def query_corpus(
    query: str,
    top_k: int = 5,
    doctrine_types: list[str] | None = None,
) -> list[DoctrineEntry]:
    """Search the built-in corpus by keyword matching.

    doctrine_types: optional filter, e.g. ['doctrine', 'field_manual'] or ['historical_case'].
    """
    query_lower = query.lower()
    target_types = set(doctrine_types) if doctrine_types else None

    scored: list[tuple[float, dict]] = []
    for entry in _CORPUS:
        if target_types and entry["doctrine_type"] not in target_types:
            continue
        content_lower = entry["content"].lower()
        title_lower = entry["title"].lower()
        hits = sum(1 for word in query_lower.split() if len(word) > 3 and word in content_lower)
        title_hits = sum(2 for word in query_lower.split() if len(word) > 3 and word in title_lower)
        score = min(1.0, (hits + title_hits) * 0.1)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        DoctrineEntry(
            id=e["id"],
            title=e["title"],
            doctrine_type=e["doctrine_type"],
            content=e["content"],
            relevance_score=round(s, 3),
        )
        for s, e in scored[:top_k]
    ]


# ── CLI ────────────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(description="Doctrine Corpus — Clausewitz & historical precedents")
    parser.add_argument("--list", action="store_true", help="List all corpus entries")
    parser.add_argument("--seed", action="store_true", help="Seed sg_corpus_documents table")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (with --seed)")
    parser.add_argument("--query", help="Keyword search the corpus")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    if args.seed:
        result = seed_corpus(dry_run=args.dry_run)
        if args.json_out:
            print(json.dumps(result, indent=2))
        else:
            print(f"Seed result: {result}")
        return

    if args.query:
        entries = query_corpus(args.query)
        if args.json_out:
            print(json.dumps([e.to_dict() for e in entries], indent=2))
        else:
            for e in entries:
                print(f"[{e.doctrine_type}] {e.title} (score={e.relevance_score})")
                print(f"  {e.content[:120]}...")
        return

    if args.list:
        if args.json_out:
            print(json.dumps(_CORPUS, indent=2))
        else:
            for e in _CORPUS:
                print(f"[{e['doctrine_type']}] {e['id']}: {e['title']}")
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
