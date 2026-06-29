
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Regulatory Foresight — main engine (D352, pint-regfore-05/06).

ForesightEngine.run() scans all configured sources, scores signals,
deduplicates, persists to regulatory_foresight_signals, and cross-registers
high-score signals (composite_score >= auto_signal_threshold) into
innovation_signals.

CLI:
    python foresight_engine.py --run --json
    python foresight_engine.py --status --json
    python foresight_engine.py --signals --min-score 0.5 --json
    python foresight_engine.py --daemon --json
"""

import hashlib
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = get_logger(__name__)

_CONFIG_PATH = (
    Path(__file__).parent.parent.parent / "args" / "regulatory_foresight_config.yaml"
)

_DEFAULT_THRESHOLD = 0.70


# ── config helpers ────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        import yaml  # type: ignore
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("foresight_engine: could not load config: %s", exc)
        return {}


def _in_quiet_hours(cfg: dict) -> bool:
    """True if current local time falls within configured quiet_hours."""
    qh = cfg.get("quiet_hours", {})
    if not qh.get("enabled"):
        return False
    try:
        from datetime import time as _time
        now_t = datetime.now().time().replace(second=0, microsecond=0)
        sh, sm = [int(x) for x in qh.get("start", "22:00").split(":")]
        eh, em = [int(x) for x in qh.get("end", "06:00").split(":")]
        start = _time(sh, sm)
        end = _time(eh, em)
        # overnight range (e.g. 22:00–06:00)
        if start > end:
            return now_t >= start or now_t <= end
        return start <= now_t <= end
    except Exception as exc:
        logger.warning("foresight_engine: quiet_hours check failed: %s", exc)
        return False


def _ttm_days(signal: dict) -> Optional[int]:
    """Compute time_to_mandate_days from estimated_mandate_date vs today."""
    emd = signal.get("estimated_mandate_date")
    if not emd:
        return None
    try:
        mandate = datetime.fromisoformat(str(emd).rstrip("Z")).date()
        today = datetime.now(timezone.utc).date()
        return max(0, (mandate - today).days)
    except Exception:
        return None


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# ── cross-registration ────────────────────────────────────────────────────────

def _cross_register(conn, signal: dict) -> Optional[str]:
    """INSERT signal into innovation_signals; return new id (or existing id on dup)."""
    ch = _content_hash(signal["id"])
    existing = conn.execute(
        "SELECT id FROM innovation_signals WHERE content_hash = %s", (ch,)
    ).fetchone()
    if existing:
        return existing[0]

    innovation_id = f"sig-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT INTO innovation_signals
               (id, source, source_type, title, url, metadata,
                community_score, content_hash, discovered_at,
                status, category, innovation_score, classification)
               VALUES (%s,%s,%s,%s,%s,%s,0.0,%s,%s,'new',%s,%s,%s)""",
            (
                innovation_id,
                "regfore_engine",
                "regulatory_foresight",
                signal.get("title", ""),
                signal.get("url"),
                json.dumps(signal, default=str),
                ch,
                now,
                "regulatory_foresight",
                signal.get("composite_score"),
                signal.get("classification", "CUI // SP-CTI"),
            ),
        )
        conn.commit()
        return innovation_id
    except Exception as exc:
        logger.warning(
            "foresight_engine: cross-register failed for %s: %s", signal.get("id"), exc
        )
        return None


# ── engine ────────────────────────────────────────────────────────────────────

class ForesightEngine:
    """Main entry point for the Regulatory Foresight pipeline."""

    def run(self) -> dict:
        """
        One scan cycle: scan → score → dedup → persist → cross-register.
        Returns {scanned, new, high_score_count, signals[]}.
        """
        cfg = _load_config()

        if _in_quiet_hours(cfg):
            logger.info("foresight_engine: in quiet hours — skipping scan")
            return {"scanned": 0, "new": 0, "high_score_count": 0, "signals": [], "skipped": "quiet_hours"}

        threshold = float(cfg.get("auto_signal_threshold", _DEFAULT_THRESHOLD))

        from tools.regulatory_foresight.source_scanner import SOURCE_SCANNERS
        from tools.regulatory_foresight.impact_scorer import ImpactScorer
        from tools.db.storage import get_connection

        scorer = ImpactScorer()
        raw: List[dict] = []
        for name, scan_fn in SOURCE_SCANNERS.items():
            try:
                batch = scan_fn()
                raw.extend(batch)
                logger.info("foresight_engine: %s → %d signals", name, len(batch))
            except Exception as exc:
                logger.warning("foresight_engine: scanner %s failed: %s", name, exc)

        # Compute time_to_mandate_days then score
        for sig in raw:
            sig["time_to_mandate_days"] = _ttm_days(sig)

        scored: List[dict] = [scorer.score(s) for s in raw]

        # Deduplicate within batch by id
        seen: set = set()
        unique: List[dict] = []
        for sig in scored:
            if sig["id"] not in seen:
                seen.add(sig["id"])
                unique.append(sig)

        new_count = 0
        high_score_count = 0
        persisted: List[dict] = []

        conn = get_connection()
        try:
            for sig in unique:
                if conn.execute(
                    "SELECT id FROM regulatory_foresight_signals WHERE id = %s",
                    (sig["id"],),
                ).fetchone():
                    continue

                # INSERT into regulatory_foresight_signals (append-only)
                conn.execute(
                    """INSERT INTO regulatory_foresight_signals
                       (id, source, doc_id, title, url,
                        proposed_at, comment_deadline, estimated_mandate_date,
                        affected_frameworks, icdev_impact_areas,
                        time_to_mandate_days, icdev_impact_score,
                        blast_radius_score, composite_score,
                        status, innovation_signal_id, scanned_at, classification)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',NULL,%s,%s)""",
                    (
                        sig["id"],
                        sig["source"],
                        sig["doc_id"],
                        sig["title"],
                        sig.get("url"),
                        sig.get("proposed_at"),
                        sig.get("comment_deadline"),
                        sig.get("estimated_mandate_date"),
                        sig.get("affected_frameworks"),
                        sig.get("icdev_impact_areas"),
                        sig.get("time_to_mandate_days"),
                        sig.get("icdev_impact_score"),
                        sig.get("blast_radius_score"),
                        sig.get("composite_score"),
                        sig.get("scanned_at", datetime.now(timezone.utc).isoformat()),
                        sig.get("classification", "CUI // SP-CTI"),
                    ),
                )
                conn.commit()
                new_count += 1

                # Cross-register high-score signals into innovation_signals
                composite = sig.get("composite_score") or 0.0
                if composite >= threshold:
                    innovation_id = _cross_register(conn, sig)
                    if innovation_id:
                        conn.execute(
                            "UPDATE regulatory_foresight_signals "
                            "SET innovation_signal_id = %s WHERE id = %s",
                            (innovation_id, sig["id"]),
                        )
                        conn.commit()
                        sig["innovation_signal_id"] = innovation_id
                        high_score_count += 1

                persisted.append(sig)
        finally:
            conn.close()

        return {
            "scanned": len(raw),
            "new": new_count,
            "high_score_count": high_score_count,
            "signals": persisted,
        }

    def status(self) -> dict:
        """Return aggregate stats from regulatory_foresight_signals."""
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM regulatory_foresight_signals"
            ).fetchone()[0]
            high = conn.execute(
                "SELECT COUNT(*) FROM regulatory_foresight_signals WHERE composite_score >= %s",
                (_DEFAULT_THRESHOLD,),
            ).fetchone()[0]
            cross = conn.execute(
                "SELECT COUNT(*) FROM regulatory_foresight_signals WHERE innovation_signal_id IS NOT NULL"
            ).fetchone()[0]
            latest = conn.execute(
                "SELECT MAX(scanned_at) FROM regulatory_foresight_signals"
            ).fetchone()[0]
            return {
                "total_signals": total,
                "high_score_signals": high,
                "cross_registered": cross,
                "latest_scan": latest,
            }
        finally:
            conn.close()

    def signals(self, min_score: float = 0.0) -> List[dict]:
        """Return scored signals at or above min_score."""
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM regulatory_foresight_signals "
                "WHERE composite_score >= %s ORDER BY composite_score DESC",
                (min_score,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Regulatory Foresight engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python foresight_engine.py --run --json\n"
            "  python foresight_engine.py --status --json\n"
            "  python foresight_engine.py --signals --min-score 0.5 --json\n"
            "  python foresight_engine.py --daemon --json\n"
        ),
    )
    parser.add_argument("--run", action="store_true", help="Execute one scan cycle")
    parser.add_argument("--status", action="store_true", help="Show aggregate stats")
    parser.add_argument("--signals", action="store_true", help="List persisted signals")
    parser.add_argument("--min-score", type=float, default=0.0, metavar="SCORE")
    parser.add_argument("--daemon", action="store_true", help="Run continuously on scan_interval_hours")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    engine = ForesightEngine()

    if args.run:
        result = engine.run()
        if args.as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"scanned={result['scanned']} new={result['new']} high_score={result['high_score_count']}")

    elif args.status:
        result = engine.status()
        if args.as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")

    elif args.signals:
        rows = engine.signals(min_score=args.min_score)
        if args.as_json:
            print(json.dumps({"signals": rows, "count": len(rows)}, indent=2, default=str))
        else:
            for r in rows:
                print(f"[{r.get('composite_score', 0):.2f}] {r.get('title', '')[:80]}")

    elif args.daemon:
        cfg = _load_config()
        interval = int(cfg.get("scan_interval_hours", 24)) * 3600
        logger.info("foresight_engine: daemon mode, interval=%ds", interval)
        while True:
            try:
                result = engine.run()
                if args.as_json:
                    print(json.dumps(result, indent=2, default=str), flush=True)
                else:
                    print(
                        f"[{datetime.now(timezone.utc).isoformat()}] "
                        f"scanned={result['scanned']} new={result['new']} "
                        f"high_score={result['high_score_count']}",
                        flush=True,
                    )
            except Exception as exc:
                logger.error("foresight_engine: daemon run failed: %s", exc)
            time.sleep(interval)

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()
