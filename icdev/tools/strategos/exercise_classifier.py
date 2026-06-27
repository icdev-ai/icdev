#!/usr/bin/env python3
# CUI // SP-CTI
"""PLA exercise classifier — detect and classify People's Liberation Army exercise
signatures from raw OSINT text signals.

Recognizes five PLA exercise types via heuristic regex + weighted scoring:
  DONGFENG         — DF-series ballistic missile exercises (PLARF / former Second Artillery)
  JOINT_SWORD      — Taiwan encirclement exercises (Joint Sword 2023-A, 2024-B pattern)
  ISLAND_ATTACK    — Amphibious assault / island seizure exercises
  COMBAT_PATROL    — PLAAF air incursion / ADIZ violation patterns
  MARITIME_INTERDICTION — PLAN surface/submarine blockade exercises

Usage
-----
    from tools.strategos.exercise_classifier import ExerciseClassifier

    clf = ExerciseClassifier()
    result = clf.classify("PLA Rocket Force conducted DF-26 live-fire drills in Western Pacific")
    # {
    #   "exercise_type": "DONGFENG",
    #   "confidence": 0.92,
    #   "signatures_matched": ["DF-26", "Rocket Force", "live-fire"],
    #   "is_pla": True,
    #   "theater": "western_pacific",
    # }

    python tools/strategos/exercise_classifier.py --text "..." --json
    python tools/strategos/exercise_classifier.py --days 7 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Signature registry
# ---------------------------------------------------------------------------

# Each entry: (compiled_pattern, weight)
# Higher weights = stronger signal for the exercise type.
_SIGS: dict[str, list[tuple[re.Pattern, float]]] = {

    "DONGFENG": [
        # DF missile series
        (re.compile(r"\bDF-(?:5|11|15|17|21|26|31|41)\b", re.I), 1.0),
        (re.compile(r"\bdong.?feng\b", re.I), 0.9),
        (re.compile(r"\bdongfeng\b", re.I), 0.9),
        # Rocket Force / former Second Artillery
        (re.compile(r"\bPLA(?:RF)?\s+Rocket\s+Force\b", re.I), 0.8),
        (re.compile(r"\bRocket\s+Force\b", re.I), 0.7),
        (re.compile(r"\bSecond\s+Artillery\b", re.I), 0.8),
        # Ballistic missile launch / live-fire context
        (re.compile(r"\bballistic\s+missile\b.*\b(?:exercise|drill|test|launch|fire)\b", re.I), 0.8),
        (re.compile(r"\b(?:exercise|drill|test|launch|fire)\b.*\bballistic\s+missile\b", re.I), 0.8),
        # TEL (Transporter Erector Launcher) is a PLARF signature
        (re.compile(r"\bTEL\b.*\b(?:PLA|China|Chinese)\b", re.I), 0.6),
        (re.compile(r"\b(?:PLA|China|Chinese)\b.*\bTEL\b", re.I), 0.6),
        # Conventional missile strike exercise vocabulary
        (re.compile(r"\bmissile\s+(?:strike|salvo|volley)\b.*\b(?:PLA|PLARF|China)\b", re.I), 0.7),
    ],

    "JOINT_SWORD": [
        # Named exercises
        (re.compile(r"\bJoint\s+Sword\b", re.I), 1.0),
        (re.compile(r"联合利剑", 0), 1.0),  # Simplified Chinese
        # Encirclement / blockade of Taiwan island
        (re.compile(r"\b(?:surround|encircl|blockad)\w*\b.*\bTaiwan\b", re.I), 0.9),
        (re.compile(r"\bTaiwan\b.*\b(?:surround|encircl|blockad)\w*\b", re.I), 0.9),
        # Multi-domain coordinated exercise
        (re.compile(r"\bmulti.?domain\b.*\bTaiwan\b", re.I), 0.7),
        (re.compile(r"\bjoint\s+(?:exercise|drill|operation)\b.*\bTaiwan\b", re.I), 0.8),
        (re.compile(r"\bTaiwan\b.*\bjoint\s+(?:exercise|drill|operation)\b", re.I), 0.8),
        # Strait closure / sea lane denial
        (re.compile(r"\bTaiwan\s+Strait\b.*\b(?:clos|seal|block|deny|restrict)\w*\b", re.I), 0.8),
        # Triggered by high-level Taiwan-US contact (classic trigger)
        (re.compile(r"\b(?:Pelosi|Speaker|transit|stopover|visit)\b.*\bTaiwan\b", re.I), 0.5),
    ],

    "ISLAND_ATTACK": [
        # Amphibious landing / seizure
        (re.compile(r"\bamphibious\s+(?:assault|landing|exercise|drill|operation)\b", re.I), 0.9),
        (re.compile(r"\bisland\s+(?:seiz|captur|storm|attack|land)\w*\b", re.I), 0.9),
        # PLA specific landing craft
        (re.compile(r"\bType\s+726\b", re.I), 0.8),   # LCAC
        (re.compile(r"\bType\s+071\b", re.I), 0.8),   # LPD
        (re.compile(r"\bType\s+075\b", re.I), 0.9),   # LHD
        (re.compile(r"\bLST\b.*\b(?:PLA|China|PLAN)\b", re.I), 0.7),
        (re.compile(r"\b(?:PLA|China|PLAN)\b.*\bLST\b", re.I), 0.7),
        # PLAN Marine Corps
        (re.compile(r"\bPLA(?:N)?\s+Marine(?:s)?\b", re.I), 0.8),
        (re.compile(r"\bMarine\s+Corps\b.*\b(?:PLA|China|Chinese)\b", re.I), 0.7),
        # Beach assault vocabulary
        (re.compile(r"\bbeach\s+(?:assault|landing|storm)\b", re.I), 0.7),
        (re.compile(r"\bhovercraft\b.*\b(?:PLA|China|Chinese|PLAN)\b", re.I), 0.6),
    ],

    "COMBAT_PATROL": [
        # PLAAF aircraft types
        (re.compile(r"\b(?:H-6K?|J-10|J-11|J-16|J-20|Su-30|KJ-500|Y-9)\b.*\b(?:Taiwan|ADIZ|strait)\b", re.I), 0.9),
        (re.compile(r"\b(?:Taiwan|ADIZ|strait)\b.*\b(?:H-6K?|J-10|J-11|J-16|J-20|Su-30)\b", re.I), 0.9),
        # ADIZ violation patterns
        (re.compile(r"\bADIZ\b.*\b(?:viol|enter|intrude|penetrat|cross)\w*\b", re.I), 0.8),
        (re.compile(r"\b(?:viol|enter|intrude|penetrat|cross)\w*\b.*\bADIZ\b", re.I), 0.8),
        # Air incursion vocabulary
        (re.compile(r"\bPLAAF\b", re.I), 0.6),
        (re.compile(r"\bPLA\s+Air\s+Force\b", re.I), 0.6),
        (re.compile(r"\b(?:Chinese|PRC|PLA)\s+(?:aircraft|warplane|bomber|fighter)\b.*\b(?:Taiwan|Strait|ADIZ)\b", re.I), 0.8),
        # Patrol bomber missions (nuclear signaling)
        (re.compile(r"\b(?:H-6K?)\b.*\b(?:nuclear|patrol|sorties|missions)\b", re.I), 0.7),
        # EW aircraft signature
        (re.compile(r"\bY-9\b.*\b(?:EW|electronic\s+warfare|ELINT|SIGINT)\b", re.I), 0.7),
    ],

    "MARITIME_INTERDICTION": [
        # Carrier group operations
        (re.compile(r"\b(?:Liaoning|Shandong|Fujian)\b.*\b(?:exercise|drill|deploy|operate)\b", re.I), 0.9),
        (re.compile(r"\bPLAN\s+carrier\b", re.I), 0.8),
        (re.compile(r"\bChinese\s+carrier\b.*\b(?:exercise|drill|deploy)\b", re.I), 0.8),
        # Submarine operations
        (re.compile(r"\b(?:094A?|Jin.class|Tang.class|093|Shang.class)\b", re.I), 0.8),
        (re.compile(r"\bPLAN\b.*\bsubmarine\b.*\b(?:patrol|exercise|drill)\b", re.I), 0.7),
        # Blockade / interdiction vocabulary
        (re.compile(r"\b(?:blockade|sea\s+lane|choke\s+point|SLOC)\b.*\b(?:PLA|China|PLAN)\b", re.I), 0.8),
        (re.compile(r"\b(?:PLA|China|PLAN)\b.*\b(?:blockade|sea\s+lane|choke\s+point|SLOC)\b", re.I), 0.8),
        # Type 055/052D destroyer exercises
        (re.compile(r"\b(?:Type\s+055|Type\s+052D?)\b.*\b(?:exercise|drill|fire)\b", re.I), 0.7),
        # Anti-submarine warfare exercise
        (re.compile(r"\bASW\b.*\b(?:PLA|China|PLAN)\b", re.I), 0.6),
        # Mine warfare
        (re.compile(r"\b(?:mine.?laying|mine\s+warfare)\b.*\b(?:PLA|China|PLAN)\b", re.I), 0.7),
    ],
}

# PLA indicator patterns — confirms this is a PLA/Chinese military event
_PLA_INDICATORS = [
    re.compile(r"\b(?:PLA|PLAN|PLAAF|PLARF|PLASSF|PRC)\b"),
    re.compile(r"\b(?:Chinese|China)\s+(?:military|army|navy|air\s+force|troops|soldiers|forces)\b", re.I),
    re.compile(r"\bPeople's\s+Liberation\s+Army\b", re.I),
    re.compile(r"\b(?:Beijing|CCP|CPC)\b.*\b(?:military|forces|troops)\b", re.I),
]

# Theater detection patterns → theater label
_THEATERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bTaiwan\s+Strait\b|\bFormosa\s+Strait\b", re.I), "taiwan_strait"),
    (re.compile(r"\bSouth\s+China\s+Sea\b|\bSCS\b|\bScarborough\b|\bSpratly\b|\bParacel\b", re.I), "south_china_sea"),
    (re.compile(r"\bEast\s+China\s+Sea\b|\bECS\b|\bSenkaku\b|\bDiaoyu\b|\bOkinawa\b", re.I), "east_china_sea"),
    (re.compile(r"\b(?:Taiwan|Taipei)\b", re.I), "taiwan_strait"),
    (re.compile(r"\bWestern\s+Pacific\b|\bWestPac\b|\bIndo.Pacific\b|\bINDOPACOM\b", re.I), "western_pacific"),
    (re.compile(r"\b(?:Yellow\s+Sea|Bohai|Korea(?:n)?\s+(?:Strait|Peninsula))\b", re.I), "northeast_asia"),
    (re.compile(r"\b(?:LEO|Low\s+Earth\s+Orbit|satellite|ASAT|space)\b", re.I), "space_leo"),
]

# Minimum confidence to report a positive classification
_CONFIDENCE_THRESHOLD = 0.25


# ---------------------------------------------------------------------------
# ExerciseClassifier
# ---------------------------------------------------------------------------

class ExerciseClassifier:
    """Heuristic classifier for PLA exercise type from raw text signals."""

    def classify(self, text: str) -> dict[str, Any]:
        """Classify a single text string. Returns type, confidence, and supporting evidence."""
        if not text or not text.strip():
            return self._null_result()

        # Score each exercise type
        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}

        for ex_type, sig_list in _SIGS.items():
            total_weight = sum(w for _, w in sig_list)
            hit_weight = 0.0
            hits: list[str] = []
            for pattern, weight in sig_list:
                m = pattern.search(text)
                if m:
                    hit_weight += weight
                    hits.append(m.group(0))
            scores[ex_type] = min(1.0, hit_weight / max(total_weight * 0.3, 1.0))
            matched[ex_type] = hits

        best_type = max(scores, key=lambda t: scores[t])
        confidence = round(scores[best_type], 4)

        # Detect PLA affiliation
        is_pla = any(p.search(text) for p in _PLA_INDICATORS)

        # Detect theater
        theater = "unknown"
        for pattern, label in _THEATERS:
            if pattern.search(text):
                theater = label
                break

        if confidence < _CONFIDENCE_THRESHOLD or not matched[best_type]:
            return {
                "exercise_type": None,
                "confidence": 0.0,
                "signatures_matched": [],
                "is_pla": is_pla,
                "theater": theater,
                "all_scores": {t: round(s, 4) for t, s in scores.items()},
            }

        return {
            "exercise_type": best_type,
            "confidence": confidence,
            "signatures_matched": matched[best_type],
            "is_pla": is_pla,
            "theater": theater,
            "all_scores": {t: round(s, 4) for t, s in scores.items()},
        }

    def classify_batch(self, texts: list[str]) -> list[dict[str, Any]]:
        """Classify a list of text strings."""
        return [self.classify(t) for t in texts]

    def classify_from_db(self, days: int = 7) -> list[dict[str, Any]]:
        """Read sg_raw_signals for the last N days and classify each signal.

        Returns a list of classification results enriched with signal metadata.
        """
        results: list[dict[str, Any]] = []
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415
            conn = get_connection()
            cutoff = datetime.now(timezone.utc).isoformat()[:10]
            rows = conn.execute(
                "SELECT id, signal_text, source, collected_at "
                "FROM sg_raw_signals "
                "WHERE date(collected_at) >= date(%s, %s) "
                "ORDER BY collected_at DESC",
                (cutoff, f"-{days} days"),
            ).fetchall()
            conn.close()
        except Exception:
            rows = []

        for row in rows:
            row = dict(row)
            classification = self.classify(row.get("signal_text") or "")
            results.append({
                "signal_id": row.get("id"),
                "source": row.get("source"),
                "collected_at": row.get("collected_at"),
                **classification,
            })

        return results

    # ------------------------------------------------------------------

    @staticmethod
    def _null_result() -> dict[str, Any]:
        return {
            "exercise_type": None,
            "confidence": 0.0,
            "signatures_matched": [],
            "is_pla": False,
            "theater": "unknown",
            "all_scores": {t: 0.0 for t in _SIGS},
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Classify PLA exercise signatures from text")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="Classify a single text string")
    group.add_argument("--days", type=int, metavar="N",
                       help="Classify sg_raw_signals from last N days")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    clf = ExerciseClassifier()

    if args.text:
        result = clf.classify(args.text)
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            et = result["exercise_type"] or "NONE"
            conf = result["confidence"]
            sigs = ", ".join(result["signatures_matched"]) or "—"
            print(f"Type: {et}  Confidence: {conf:.2f}  Theater: {result['theater']}")
            print(f"Signatures: {sigs}")
    else:
        results = clf.classify_from_db(days=args.days)
        positive = [r for r in results if r["exercise_type"]]
        if args.as_json:
            print(json.dumps({
                "signals_scanned": len(results),
                "exercise_signals": len(positive),
                "results": positive,
            }, indent=2))
        else:
            print(f"Scanned {len(results)} signals — {len(positive)} PLA exercise signals detected")
            for r in positive:
                print(f"  [{r['exercise_type']}] conf={r['confidence']:.2f} "
                      f"theater={r['theater']} sigs={r['signatures_matched']}")


if __name__ == "__main__":
    main()
