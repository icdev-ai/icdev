# CUI // SP-CTI
"""Intent Assessment Lens — PMESII-PT historical precedent matcher.

Compares the current operational environment's PMESII-PT 7-dimensional vector
against a seeded library of 15 historical conflict cases using cosine similarity.
Returns the top-3 closest precedents, an intent_assessment_score (0–10), and
a structured narrative.

Dimensions (index order):
    0 = Political    (P)
    1 = Military     (M)
    2 = Economic     (E)
    3 = Social       (S)
    4 = Information  (I)
    5 = Infrastructure (I2)
    6 = Physical Terrain (PT)

Feeds into SIOEngine via sg_sio_assessments with lens_source='intent_assessment'.
"""

from __future__ import annotations

import json
import math
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.intelligence.oracle.intent_assessment")

# ── PMESII-PT dimension labels ──────────────────────────────────────────────
PMESII_DIMS = [
    "political",
    "military",
    "economic",
    "social",
    "information",
    "infrastructure",
    "physical_terrain",
]

# ── NATO Admiral/Source Reliability ─────────────────────────────────────────
# Maps confidence 0-1 → NATO STANAG 2511 reliability code
_NATO_RELIABILITY_MAP = [
    (0.80, "A1"),  # Completely reliable; confirmed by other sources
    (0.65, "B2"),  # Usually reliable; probably true
    (0.50, "C3"),  # Fairly reliable; possibly true
    (0.35, "D4"),  # Not usually reliable; doubtful
    (0.00, "E5"),  # Unreliable; improbable
]


def _nato_reliability(confidence: float) -> str:
    for threshold, code in _NATO_RELIABILITY_MAP:
        if confidence >= threshold:
            return code
    return "F6"


# ── Outcome severity weights by escalation level ────────────────────────────
_SEVERITY_WEIGHT = {1: 0.2, 2: 0.4, 3: 0.6, 4: 0.8, 5: 1.0}

# ── 15 Historical Cases — PMESII-PT seed data ───────────────────────────────
_HISTORICAL_CASES: list[dict[str, Any]] = [
    {
        "id": "hc-gulf-war-1991",
        "case_name": "Gulf War 1991",
        "pmesii_vector": [0.90, 0.95, 0.70, 0.60, 0.85, 0.80, 0.70],
        "outcome": (
            "Coalition decisive victory; Iraq expelled from Kuwait; "
            "significant Iraqi military attrition with minimal coalition losses"
        ),
        "escalation_level": 4,
        "region": "Middle East",
        "year_start": 1990,
        "year_end": 1991,
    },
    {
        "id": "hc-kosovo-1999",
        "case_name": "Kosovo 1999",
        "pmesii_vector": [0.80, 0.75, 0.50, 0.70, 0.80, 0.75, 0.60],
        "outcome": (
            "Serbian forces withdrew from Kosovo after 78-day NATO air campaign; "
            "UN interim administration established; Milosevic later indicted for war crimes"
        ),
        "escalation_level": 3,
        "region": "Balkans",
        "year_start": 1999,
        "year_end": 1999,
    },
    {
        "id": "hc-georgia-2008",
        "case_name": "Georgia 2008",
        "pmesii_vector": [0.40, 0.70, 0.30, 0.50, 0.60, 0.50, 0.65],
        "outcome": (
            "Russian military victory in 5 days; South Ossetia and Abkhazia recognized by Russia; "
            "EU-brokered ceasefire; Russian forces retained positions beyond pre-war lines"
        ),
        "escalation_level": 3,
        "region": "Caucasus",
        "year_start": 2008,
        "year_end": 2008,
    },
    {
        "id": "hc-crimea-2014",
        "case_name": "Crimea Annexation 2014",
        "pmesii_vector": [0.30, 0.80, 0.50, 0.60, 0.85, 0.40, 0.70],
        "outcome": (
            "Bloodless annexation of Crimea by Russia; Western sanctions imposed; "
            "de facto status quo established despite non-recognition by most states"
        ),
        "escalation_level": 4,
        "region": "Eastern Europe",
        "year_start": 2014,
        "year_end": 2014,
    },
    {
        "id": "hc-syria-2015",
        "case_name": "Russian Intervention Syria 2015",
        "pmesii_vector": [0.50, 0.80, 0.40, 0.40, 0.70, 0.60, 0.55],
        "outcome": (
            "Assad regime stabilized; significant territorial recovery from opposition; "
            "Russian military presence established; prolonged conflict continues with no political resolution"
        ),
        "escalation_level": 3,
        "region": "Middle East",
        "year_start": 2015,
        "year_end": None,
    },
    {
        "id": "hc-nk-2020",
        "case_name": "Nagorno-Karabakh 2020",
        "pmesii_vector": [0.60, 0.85, 0.55, 0.65, 0.75, 0.70, 0.65],
        "outcome": (
            "Azerbaijan decisive victory via drone-centric warfare; ceasefire signed; "
            "Armenia ceded significant territory; Russian peacekeepers deployed to Lachin corridor"
        ),
        "escalation_level": 4,
        "region": "Caucasus",
        "year_start": 2020,
        "year_end": 2020,
    },
    {
        "id": "hc-ukraine-2022",
        "case_name": "Ukraine Full-Scale Invasion 2022",
        "pmesii_vector": [0.20, 0.90, 0.85, 0.75, 0.90, 0.90, 0.70],
        "outcome": (
            "Ongoing; initial Russian advance on Kyiv failed; grinding war of attrition along 1,000km front; "
            "unprecedented sanctions; NATO expansion; significant casualties both sides"
        ),
        "escalation_level": 5,
        "region": "Eastern Europe",
        "year_start": 2022,
        "year_end": None,
    },
    {
        "id": "hc-iraq-2003",
        "case_name": "Iraq War 2003",
        "pmesii_vector": [0.45, 0.95, 0.70, 0.40, 0.65, 0.75, 0.70],
        "outcome": (
            "Rapid conventional victory in 3 weeks; prolonged insurgency followed; "
            "sectarian civil war; destabilization of region; eventual ISIS emergence"
        ),
        "escalation_level": 4,
        "region": "Middle East",
        "year_start": 2003,
        "year_end": 2011,
    },
    {
        "id": "hc-libya-2011",
        "case_name": "Libya NATO Intervention 2011",
        "pmesii_vector": [0.75, 0.70, 0.60, 0.65, 0.75, 0.60, 0.55],
        "outcome": (
            "Gaddafi regime collapsed; Gaddafi killed; NATO air campaign lasted 7 months; "
            "subsequent failed state and prolonged civil war; regional jihadist spillover"
        ),
        "escalation_level": 4,
        "region": "North Africa",
        "year_start": 2011,
        "year_end": 2011,
    },
    {
        "id": "hc-afghanistan-2001",
        "case_name": "Afghanistan 2001",
        "pmesii_vector": [0.80, 0.90, 0.40, 0.35, 0.85, 0.50, 0.30],
        "outcome": (
            "Taliban ousted rapidly in 2 months; 20-year occupation followed; "
            "Taliban returned to power August 2021; longest US war with inconclusive strategic outcome"
        ),
        "escalation_level": 4,
        "region": "Central Asia",
        "year_start": 2001,
        "year_end": 2021,
    },
    {
        "id": "hc-falklands-1982",
        "case_name": "Falklands War 1982",
        "pmesii_vector": [0.65, 0.75, 0.50, 0.70, 0.70, 0.50, 0.40],
        "outcome": (
            "UK decisive victory after 74-day campaign; Argentine surrender; "
            "Falkland Islands remain British; Argentinian junta collapsed shortly after"
        ),
        "escalation_level": 3,
        "region": "South Atlantic",
        "year_start": 1982,
        "year_end": 1982,
    },
    {
        "id": "hc-iran-iraq-1980",
        "case_name": "Iran-Iraq War 1980-1988",
        "pmesii_vector": [0.30, 0.75, 0.70, 0.50, 0.55, 0.65, 0.60],
        "outcome": (
            "Stalemate after 8 years; UN-mediated ceasefire; approximately 500,000–1,000,000 casualties; "
            "no territorial change; chemical weapons used by Iraq"
        ),
        "escalation_level": 4,
        "region": "Middle East",
        "year_start": 1980,
        "year_end": 1988,
    },
    {
        "id": "hc-yom-kippur-1973",
        "case_name": "Yom Kippur War 1973",
        "pmesii_vector": [0.40, 0.85, 0.75, 0.70, 0.60, 0.55, 0.65],
        "outcome": (
            "Initial Arab coalition gains reversed by Israeli counteroffensive; "
            "UN ceasefire; oil embargo triggered global economic crisis; "
            "eventual Egypt-Israel peace treaty (Camp David 1978)"
        ),
        "escalation_level": 4,
        "region": "Middle East",
        "year_start": 1973,
        "year_end": 1973,
    },
    {
        "id": "hc-chechnya-1999",
        "case_name": "Second Chechen War 1999-2009",
        "pmesii_vector": [0.35, 0.80, 0.45, 0.50, 0.65, 0.80, 0.50],
        "outcome": (
            "Russian military victory; Grozny razed and rebuilt; "
            "Kadyrov installed as head of Chechen Republic; insurgency gradually suppressed by 2009"
        ),
        "escalation_level": 4,
        "region": "Caucasus",
        "year_start": 1999,
        "year_end": 2009,
    },
    {
        "id": "hc-taiwan-strait-1996",
        "case_name": "Taiwan Strait Crisis 1995-1996",
        "pmesii_vector": [0.50, 0.70, 0.65, 0.55, 0.70, 0.30, 0.55],
        "outcome": (
            "PRC backed down after US carrier group deployment; "
            "Taiwan election proceeded; cross-strait tensions reset; "
            "demonstrated US commitment to Taiwan security"
        ),
        "escalation_level": 2,
        "region": "East Asia",
        "year_start": 1995,
        "year_end": 1996,
    },
]


# ── Cosine similarity ────────────────────────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x ** 2 for x in a))
    mag_b = math.sqrt(sum(x ** 2 for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── DB helpers ───────────────────────────────────────────────────────────────

def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


_DDL_HISTORICAL_CASES = """
CREATE TABLE IF NOT EXISTS historical_cases (
    id                      TEXT PRIMARY KEY,
    case_name               TEXT NOT NULL UNIQUE,
    pmesii_vector           TEXT NOT NULL,
    outcome                 TEXT NOT NULL,
    escalation_level        INTEGER NOT NULL DEFAULT 3,
    region                  TEXT,
    year_start              INTEGER,
    year_end                INTEGER,
    outcome_severity_weight REAL NOT NULL DEFAULT 0.6
)
"""


def _ensure_table_and_seed() -> None:
    """Create historical_cases table if absent, then seed if empty."""
    conn = _get_conn()
    try:
        conn.execute(_DDL_HISTORICAL_CASES)
        conn.commit()
    finally:
        conn.close()

    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) FROM historical_cases").fetchone()
        if row[0] > 0:
            return

        for case in _HISTORICAL_CASES:
            weight = _SEVERITY_WEIGHT.get(case["escalation_level"], 0.6)
            conn.execute(
                "INSERT OR IGNORE INTO historical_cases "
                "(id, case_name, pmesii_vector, outcome, escalation_level, "
                " region, year_start, year_end, outcome_severity_weight) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    case["id"],
                    case["case_name"],
                    json.dumps(case["pmesii_vector"]),
                    case["outcome"],
                    case["escalation_level"],
                    case.get("region"),
                    case.get("year_start"),
                    case.get("year_end"),
                    weight,
                ),
            )
        conn.commit()
        logger.info("Seeded %d historical cases.", len(_HISTORICAL_CASES))
    finally:
        conn.close()


def _load_cases() -> list[dict[str, Any]]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, case_name, pmesii_vector, outcome, "
            "       escalation_level, outcome_severity_weight "
            "FROM historical_cases"
        ).fetchall()
        cases = []
        for r in rows:
            try:
                vec = json.loads(r[2])
            except (json.JSONDecodeError, TypeError):
                continue
            if len(vec) != 7:
                continue
            cases.append({
                "id": r[0],
                "case_name": r[1],
                "pmesii_vector": vec,
                "outcome": r[3],
                "escalation_level": r[4],
                "outcome_severity_weight": r[5],
            })
        return cases
    finally:
        conn.close()


# ── Current PMESII-PT vector from live signals ───────────────────────────────

def _derive_current_vector() -> list[float]:
    """
    Derive the current PMESII-PT vector from available signal tables.

    Dimension mapping:
      P (political)      ← negative Goldstein events + diplomatic signals
      M (military)       ← conflict events with military CAMEO codes (1x-19)
      E (economic)       ← economic sanction/trade events (CAMEO 17x)
      S (social)         ← social unrest / protest signals
      I (information)    ← information warfare signals, media saturation
      I2 (infrastructure)← infrastructure attack events (CAMEO 19x)
      PT (phys terrain)  ← geographic concentration (terrain static, default 0.5)

    Falls back to a neutral [0.5]*7 if no signals are found.
    """
    conn = _get_conn()
    try:
        # ── conflict events (last 90 days) ──────────────────────────────────
        ce_rows = []
        try:
            ce_rows = conn.execute(
                "SELECT event_code, goldstein_scale, avg_tone "
                "FROM sg_conflict_events "
                "WHERE event_ts >= date('now', '-90 days') "
                "LIMIT 500"
            ).fetchall()
        except Exception:
            pass

        # ── raw signals (last 30 days) ───────────────────────────────────────
        sig_rows = []
        try:
            sig_rows = conn.execute(
                "SELECT title, body FROM sg_raw_signals "
                "WHERE created_at >= date('now', '-30 days') "
                "LIMIT 200"
            ).fetchall()
        except Exception:
            pass

        if not ce_rows and not sig_rows:
            return [0.5] * 7

        total = max(len(ce_rows), 1)

        # CAMEO code prefix → PMESII dimension
        mil_codes = {"10", "11", "12", "13", "14", "15", "16", "17", "18", "19"}
        infra_codes = {"190", "191", "192", "193", "194", "195"}

        pol_score = mil_score = eco_score = inf_score = 0.0
        pol_n = mil_n = eco_n = inf_n = 0

        for code, gs, tone in ce_rows:
            code_str = str(code or "")[:3]
            prefix2 = code_str[:2]

            # Political: negative Goldstein = escalation signal
            if gs is not None:
                # Goldstein −10 to +10; remap to 0-1 (higher = more hostile)
                pol_score += (10.0 - float(gs)) / 20.0
                pol_n += 1

            # Military: CAMEO 10-19 family
            if prefix2 in mil_codes:
                mil_score += 1.0
                mil_n += 1

            # Economic: CAMEO 17x
            if prefix2 == "17":
                eco_score += 1.0
                eco_n += 1

            # Infrastructure: CAMEO 190-195
            if code_str in infra_codes:
                inf_score += 1.0
                inf_n += 1

        # Normalize to 0-1
        def _norm(val: float, n: int, divisor: float = 1.0) -> float:
            if n == 0:
                return 0.5
            return min(1.0, val / n / divisor)

        p_dim = _norm(pol_score, pol_n)
        m_dim = _norm(mil_score, mil_n) if mil_n else min(1.0, mil_score / total)
        e_dim = _norm(eco_score, eco_n) if eco_n else 0.5
        i2_dim = _norm(inf_score, inf_n) if inf_n else 0.5

        # Social/Information: keyword scan of raw signals
        social_kws = {"protest", "riot", "unrest", "demonstration", "civil", "strike"}
        info_kws = {"propaganda", "disinformation", "cyber", "hack", "psyop", "influence"}
        soc_hits = info_hits = 0
        for title, body in sig_rows:
            text = ((title or "") + " " + (body or "")).lower()
            if any(k in text for k in social_kws):
                soc_hits += 1
            if any(k in text for k in info_kws):
                info_hits += 1

        sig_total = max(len(sig_rows), 1)
        s_dim = min(1.0, soc_hits / sig_total * 5)   # scale: 20% hit rate → 1.0
        i_dim = min(1.0, info_hits / sig_total * 5)

        # Physical terrain: static default (terrain doesn't change operationally)
        pt_dim = 0.5

        return [p_dim, m_dim, e_dim, s_dim, i_dim, i2_dim, pt_dim]

    finally:
        conn.close()


# ── Top-N match ──────────────────────────────────────────────────────────────

def _top_matches(
    current_vec: list[float],
    cases: list[dict[str, Any]],
    n: int = 3,
) -> list[dict[str, Any]]:
    scored = []
    for case in cases:
        sim = _cosine_similarity(current_vec, case["pmesii_vector"])
        scored.append({**case, "similarity": sim})
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:n]


# ── Intent Assessment Score ──────────────────────────────────────────────────

def _intent_score(top_matches: list[dict[str, Any]]) -> float:
    """
    Score = max_similarity × outcome_severity_weight × 10 (clamped 0–10).
    Uses the closest match as the primary signal.
    """
    if not top_matches:
        return 0.0
    best = top_matches[0]
    raw = best["similarity"] * best["outcome_severity_weight"] * 10.0
    return round(min(10.0, max(0.0, raw)), 2)


# ── Narrative builder ────────────────────────────────────────────────────────

def _build_narrative(
    top_matches: list[dict[str, Any]],
    score: float,
    nato_code: str,
) -> str:
    if not top_matches:
        return "Insufficient historical data for precedent matching."

    best = top_matches[0]
    sim_pct = round(best["similarity"] * 100, 1)
    lines = [
        f"Closest precedent: {best['case_name']} ({sim_pct}% match). "
        f"Historical outcome: {best['outcome']}. "
        f"Confidence: {nato_code}."
    ]

    if len(top_matches) > 1:
        alt = top_matches[1]
        alt_pct = round(alt["similarity"] * 100, 1)
        lines.append(
            f"Secondary precedent: {alt['case_name']} ({alt_pct}% match) — "
            f"{alt['outcome']}"
        )

    if len(top_matches) > 2:
        ter = top_matches[2]
        ter_pct = round(ter["similarity"] * 100, 1)
        lines.append(
            f"Tertiary precedent: {ter['case_name']} ({ter_pct}% match) — "
            f"{ter['outcome']}"
        )

    lines.append(f"Intent Assessment Score: {score}/10.")
    return " | ".join(lines)


# ── Persist to sg_sio_assessments ───────────────────────────────────────────

def _persist_assessment(
    score: float,
    confidence: float,
    nato_code: str,
    narrative: str,
    evidence: dict[str, Any],
) -> None:
    conn = _get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO sg_sio_assessments "
            "(id, confidence, nato_reliability, recommendation, lens_source, "
            " timestamp, score, narrative, evidence_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"ia-{uuid.uuid4().hex[:10]}",
                confidence,
                nato_code,
                "Review top precedents and assess escalation trajectory.",
                "intent_assessment",
                now,
                score,
                narrative,
                json.dumps(evidence),
                now,
            ),
        )
        conn.commit()
        # Prune rows older than 24h (SQLite vs PostgreSQL syntax)
        try:
            from tools.db.storage import is_pg
            if is_pg():
                conn.execute(
                    "DELETE FROM sg_sio_assessments "
                    "WHERE lens_source='intent_assessment' "
                    "  AND created_at::timestamptz < NOW() - INTERVAL '24 hours'"
                )
            else:
                conn.execute(
                    "DELETE FROM sg_sio_assessments "
                    "WHERE lens_source='intent_assessment' "
                    "  AND created_at < datetime('now', '-24 hours')"
                )
        except Exception:
            pass
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to persist intent assessment: %s", exc)
    finally:
        conn.close()


# ── Public API ───────────────────────────────────────────────────────────────

def run(current_pmesii: list[float] | None = None) -> dict[str, Any]:
    """
    Execute the Intent Assessment lens.

    Parameters
    ----------
    current_pmesii : list[float] | None
        Optional externally-supplied 7-dim PMESII-PT vector (0–1 each).
        If None, the vector is derived from live sg_raw_signals and
        sg_conflict_events data.

    Returns
    -------
    dict with keys:
        current_vector   — 7-dim list
        top_matches      — list of top-3 dicts (case_name, similarity, outcome, …)
        score            — float 0–10
        confidence       — float 0–1
        nato_reliability — str (e.g. "B2")
        narrative        — str
    """
    _ensure_table_and_seed()
    cases = _load_cases()

    vec = current_pmesii if current_pmesii and len(current_pmesii) == 7 else _derive_current_vector()

    matches = _top_matches(vec, cases, n=3)
    score = _intent_score(matches)

    # Confidence: average similarity of top match, tempered by data richness
    confidence = round(matches[0]["similarity"] * 0.9, 3) if matches else 0.0
    nato_code = _nato_reliability(confidence)
    narrative = _build_narrative(matches, score, nato_code)

    evidence = {
        "current_pmesii_vector": dict(zip(PMESII_DIMS, vec)),
        "top_matches": [
            {
                "case_name": m["case_name"],
                "similarity_pct": round(m["similarity"] * 100, 1),
                "escalation_level": m["escalation_level"],
                "outcome": m["outcome"],
            }
            for m in matches
        ],
    }

    try:
        _persist_assessment(score, confidence, nato_code, narrative, evidence)
    except Exception as exc:
        logger.warning("Persist skipped: %s", exc)

    return {
        "current_vector": vec,
        "top_matches": matches,
        "score": score,
        "confidence": confidence,
        "nato_reliability": nato_code,
        "narrative": narrative,
    }


# ── CLI entrypoint ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Optional: pass 7 floats as CLI args for an override vector
    override = None
    if len(sys.argv) == 8:
        try:
            override = [float(x) for x in sys.argv[1:]]
        except ValueError:
            print("Usage: intent_assessment.py [P M E S I I2 PT]  (7 floats 0-1)")
            sys.exit(1)

    result = run(current_pmesii=override)

    print("\n── Intent Assessment ──────────────────────────────────────────")
    print(f"Score: {result['score']}/10   NATO Reliability: {result['nato_reliability']}")
    dims = dict(zip(PMESII_DIMS, result["current_vector"]))
    print("PMESII-PT vector:", {k: round(v, 3) for k, v in dims.items()})
    print()
    print("Top-3 Historical Precedents:")
    for i, m in enumerate(result["top_matches"], 1):
        print(f"  {i}. {m['case_name']}  ({round(m['similarity']*100,1)}% match)")
        print(f"     Escalation: {m['escalation_level']}/5")
        print(f"     Outcome: {m['outcome'][:120]}...")
    print()
    print("Narrative:")
    print(result["narrative"])
