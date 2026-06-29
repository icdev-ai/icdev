# CUI // SP-CTI
"""OSINT Harvester — ingests pre-staged signals from data/osint_inbox/ into the DB.

Reads JSON batch files written by osint_prestage.py, deduplicates by url_hash,
writes new signals to sg_raw_signals, scores each signal across four dimensions,
and writes prioritized entries to sg_prioritized_signals so the OSINT page
immediately shows results.

Usage:
    python -m tools.strategos.osint_harvester --target example.com --json
    python -m tools.strategos.osint_harvester --target example.com --dry-run --json

Always exits 0 — structured errors are returned in the JSON envelope.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_INBOX = BASE_DIR / "data" / "osint_inbox"

logger = get_logger(__name__)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

# ── Source tier scoring ────────────────────────────────────────────────────────
# Tier 1: primary military/conflict intelligence sources
_TIER1_SOURCES = {
    "ISW (Institute for the Study of War)",
    "Institute for the Study of War",
    "Bellingcat",
    "Defense One",
    "CISA Alerts",
    "Jane's Defence",
}
# Tier 2: major international news with strong conflict coverage
_TIER2_SOURCES = {
    "Reuters World News",
    "BBC World News",
    "Al Jazeera",
    "Kyiv Independent",
    "RFE/RL (Radio Free Europe)",
    "UN OCHA ReliefWeb",
}

# High-priority conflict/military keywords for posterior-shift scoring
_CONFLICT_KEYWORDS = frozenset({
    "attack", "missile", "drone", "strike", "troops", "military", "war", "combat",
    "nuclear", "escalation", "sanctions", "invasion", "ceasefire", "offensive",
    "artillery", "navy", "air force", "cyber", "espionage", "intelligence",
    "nato", "deployment", "blockade", "insurgency", "airstrike", "submarine",
    "amphibious", "ballistic", "hypersonic", "electromagnetic", "detonation",
    "wounded", "killed", "casualties", "front line", "counteroffensive",
    "territorial", "occupation", "liberation", "siege", "shelling",
})


# ── Utilities ──────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _url_hash(sig: Dict[str, Any]) -> str:
    """Stable deduplication key — matches osint_prestage.py hash logic."""
    url = (sig.get("url") or "").strip()
    title = (sig.get("title") or "").strip()
    date = (sig.get("date") or "").strip()
    text = url if url else (title + date)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score_signal(sig: Dict[str, Any]) -> Dict[str, Any]:
    """Compute four-dimensional priority score for a signal.

    Returns dict with composite (0-10) and four sub-scores used to populate
    sg_prioritized_signals columns.
    """
    title = (sig.get("title") or "").lower()
    body = (sig.get("body") or "").lower()
    source = (sig.get("source") or "")
    date_str = (sig.get("date") or "").strip()
    geo_hint = sig.get("geo_hint")
    text = title + " " + body

    # 1. Source discriminability (0–3)
    if source in _TIER1_SOURCES:
        source_disc = 3.0
    elif source in _TIER2_SOURCES:
        source_disc = 2.0
    else:
        source_disc = 1.0

    # 2. Posterior shift via keyword density (0–3)
    hits = sum(1 for kw in _CONFLICT_KEYWORDS if kw in text)
    posterior_shift = min(3.0, round(hits * 0.4, 2))

    # 3. Temporal recency (0–3)
    temporal = 1.0
    if date_str:
        try:
            from email.utils import parsedate_to_datetime  # RFC 2822 (RSS pubDate)
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt).days
            if age_days <= 0:
                temporal = 3.0
            elif age_days <= 3:
                temporal = 2.5
            elif age_days <= 7:
                temporal = 2.0
            elif age_days <= 30:
                temporal = 1.0
            else:
                temporal = 0.5
        except Exception:
            pass

    # 4. Domain/geo coverage (0–1)
    if geo_hint:
        domain = 1.0
    elif any(c.isupper() for c in (sig.get("title") or "")[:60]):
        domain = 0.5  # likely contains a proper noun / place name
    else:
        domain = 0.0

    composite = min(10.0, round(source_disc + posterior_shift + temporal + domain, 2))

    kw_found = [kw for kw in _CONFLICT_KEYWORDS if kw in text][:5]
    rationale = (
        f"src_disc={source_disc} shift={posterior_shift} "
        f"temporal={temporal} domain={domain}"
    )
    if kw_found:
        rationale += f" | keywords: {', '.join(kw_found)}"

    return {
        "composite": composite,
        "source_disc": source_disc,
        "posterior_shift": posterior_shift,
        "temporal": temporal,
        "domain": domain,
        "rationale": rationale,
    }


# ── Inbox loader ───────────────────────────────────────────────────────────────

def _load_inbox(inbox_dir: Path) -> List[Dict[str, Any]]:
    """Return all signals from all batch files in inbox_dir."""
    signals: List[Dict[str, Any]] = []
    if not inbox_dir.exists():
        return signals
    for batch_file in sorted(inbox_dir.glob("osint_prestage_*.json")):
        try:
            with open(batch_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            signals.extend(data.get("signals", []))
        except Exception as exc:
            logger.warning("Failed to read %s: %s", batch_file.name, exc)
    return signals


# ── DB write ───────────────────────────────────────────────────────────────────

def _write_signals(
    signals: List[Dict[str, Any]],
    tier: str = "internet",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Write signals to sg_raw_signals and sg_prioritized_signals.

    Deduplicates on url_hash. Only newly inserted raw signals get a
    prioritized entry. Returns inserted/skipped/scored counts.
    """
    if not signals:
        return {"inserted": 0, "skipped": 0, "scored": 0}

    if dry_run:
        return {
            "inserted": len(signals),
            "skipped": 0,
            "scored": len(signals),
            "dry_run": True,
        }

    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
    except ImportError as exc:
        logger.error("Cannot import get_connection: %s", exc)
        return {"inserted": 0, "skipped": 0, "scored": 0, "error": str(exc)}

    conn = get_connection()
    inserted = skipped = scored = 0
    run_at = _utcnow_iso()

    try:
        for sig in signals:
            url_hash = _url_hash(sig)
            title = (sig.get("title") or "")[:1000]
            body = (sig.get("body") or "")[:4000]
            source = (sig.get("source") or "")[:255]
            signal_date = (sig.get("date") or "")[:64]
            geo_hint: Optional[str] = sig.get("geo_hint")

            # Insert raw signal — silently skips if url_hash already exists
            conn.execute(
                "INSERT OR IGNORE INTO sg_raw_signals "
                "(url_hash, title, body, source, signal_date, geo_hint, tier_used, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (url_hash, title, body, source, signal_date, geo_hint, tier, run_at),
            )

            # Fetch the raw signal id (present whether just inserted or pre-existing)
            row = conn.execute(
                "SELECT id FROM sg_raw_signals WHERE url_hash = %s",
                (url_hash,),
            ).fetchone()
            if row is None:
                skipped += 1
                continue
            raw_id = row["id"] if isinstance(row, dict) else row[0]

            # Skip prioritization if this raw signal already has a prioritized entry
            existing = conn.execute(
                "SELECT id FROM sg_prioritized_signals WHERE raw_signal_id = %s",
                (raw_id,),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            inserted += 1

            # Score and write to sg_prioritized_signals
            scores = _score_signal(sig)
            conn.execute(
                "INSERT INTO sg_prioritized_signals "
                "(raw_signal_id, composite_score, posterior_shift_score, "
                "source_discriminability_score, temporal_recency_score, "
                "domain_coverage_score, rationale, run_at, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    raw_id,
                    scores["composite"],
                    scores["posterior_shift"],
                    scores["source_disc"],
                    scores["temporal"],
                    scores["domain"],
                    scores["rationale"],
                    run_at,
                    run_at,
                ),
            )
            scored += 1

        conn.commit()

    except Exception as exc:
        logger.error("DB write error: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"inserted": inserted, "skipped": skipped, "scored": scored, "error": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {"inserted": inserted, "skipped": skipped, "scored": scored}


# ── Public API ─────────────────────────────────────────────────────────────────

def harvest(
    target: str,
    dry_run: bool = False,
    inbox_dir: Path = _DEFAULT_INBOX,
) -> Dict[str, Any]:
    """Harvest OSINT signals for *target* from the inbox directory.

    Loads all batch files from inbox_dir, writes new signals to sg_raw_signals,
    scores them, and writes prioritized entries to sg_prioritized_signals.

    Returns a result envelope with status='ok' on success.
    """
    signals = _load_inbox(inbox_dir)

    result: Dict[str, Any] = {
        "status": "ok",
        "target": target,
        "dry_run": dry_run,
        "signal_count": len(signals),
        "harvested_at": _utcnow_iso(),
    }

    if dry_run:
        result["message"] = "dry-run: no writes performed"
        result["ingested"] = len(signals)
        return result

    write_result = _write_signals(signals, tier="internet", dry_run=False)
    result["ingested"] = write_result.get("inserted", 0)
    result["skipped"] = write_result.get("skipped", 0)
    result["scored"] = write_result.get("scored", 0)

    if "error" in write_result:
        result["status"] = "partial"
        result["write_error"] = write_result["error"]

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OSINT Harvester — ingest pre-staged signals for a target",
    )
    p.add_argument("--target", required=True, help="Hostname or entity to scope harvest")
    p.add_argument("--dry-run", action="store_true", help="Simulate harvest without writes")
    p.add_argument("--json", action="store_true", help="Emit JSON output to stdout")
    p.add_argument(
        "--inbox-dir",
        default=str(_DEFAULT_INBOX),
        help="Override inbox directory (default: data/osint_inbox/)",
    )
    return p


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    inbox_dir = Path(args.inbox_dir)

    result = harvest(
        target=args.target,
        dry_run=args.dry_run,
        inbox_dir=inbox_dir,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        mode = " [dry-run]" if args.dry_run else ""
        ingested = result.get("ingested", 0)
        skipped = result.get("skipped", 0)
        total = result.get("signal_count", 0)
        print(
            f"OSINT harvest{mode}: {result['status']} — "
            f"{ingested} new / {skipped} skipped / {total} total "
            f"for target={args.target}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
