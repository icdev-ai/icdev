
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Tech Radar engine (D352 pattern).

RadarEngine.run():
  1. Fetch external signals via SOURCE_SCANNERS.
  2. For each tech_radar_entries row: recompute composite_score using
     external signals, ICDEV fit keywords, and airgap_compat flag.
  3. If composite_score warrants a ring change: UPDATE tech_radar_entries,
     INSERT append-only row to tech_radar_history.
  4. For ring movements → 'adopt': cross-register to innovation_signals.
  5. Return {entries_assessed, ring_changes, new_signals}.

CLI:
    python radar_engine.py --run --json
    python radar_engine.py --run --dry-run --json
    python radar_engine.py --status --json
    python radar_engine.py --list --ring adopt --json
    python radar_engine.py --daemon --interval 3600 --json
"""

import hashlib
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = get_logger(__name__)

# ── Scoring constants ─────────────────────────────────────────────────────────

_WEIGHTS = {
    "ecosystem_maturity": 0.35,
    "icdev_fit": 0.30,
    "airgap_compat": 0.25,
    "il_compliance": 0.10,
}

# Composite score thresholds → ring assignment (evaluated in order)
_RING_THRESHOLDS = [
    (0.75, "adopt"),
    (0.60, "trial"),
    (0.45, "assess"),
    (0.00, "hold"),
]

# External signal label → ecosystem_maturity additive boost
_SIGNAL_BOOST: Dict[str, float] = {
    # Thoughtworks rings
    "ADOPT": 0.15,
    "TRIAL": 0.08,
    "ASSESS": 0.02,
    "HOLD": -0.10,
    # CNCF project maturity
    "GRADUATED": 0.15,
    "INCUBATING": 0.08,
    "SANDBOX": 0.02,
    # GitHub star-velocity buckets
    "VIRAL": 0.12,
    "HIGH_ADOPTION": 0.08,
    "GROWING": 0.04,
    "EMERGING": 0.01,
}

# ICDEV-relevant keywords → icdev_fit additive boost
_ICDEV_FIT_KEYWORDS: Dict[str, float] = {
    "hypothesis": 0.05,
    "playwright": 0.08,
    "selenium": 0.06,
    "test": 0.04,
    "ruff": 0.08,
    "trivy": 0.08,
    "opentelemetry": 0.08,
    "otel": 0.08,
    "terraform": 0.08,
    "ansible": 0.08,
    "kubernetes": 0.06,
    "k8s": 0.06,
    "uv": 0.05,
    "rspack": 0.05,
    "ast-grep": 0.06,
    "bandit": 0.07,
    "sbom": 0.07,
    "fips": 0.07,
    "nist": 0.06,
    "vault": 0.06,
    "opa": 0.06,
}

# Below this airgap_compat score, apply a mild composite penalty
_AIRGAP_COMPAT_THRESHOLD = 0.70
_AIRGAP_COMPAT_PENALTY = 0.05

# Minimum composite delta before a change is considered significant
_SCORE_DELTA_MIN = 0.02


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _ring_from_score(composite: float) -> str:
    for threshold, ring in _RING_THRESHOLDS:
        if composite >= threshold:
            return ring
    return "hold"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Database helpers ──────────────────────────────────────────────────────────

def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def _load_entries(conn) -> List[dict]:
    cursor = conn.execute(
        """
        SELECT id, name, category, current_ring, previous_ring,
               ecosystem_maturity, icdev_fit, airgap_compat, il_compliance,
               composite_score, rationale, last_assessed
        FROM   tech_radar_entries
        ORDER  BY name
        """
    )
    return [dict(r) for r in cursor.fetchall()]


def _update_entry(
    conn,
    entry_id: str,
    new_ring: str,
    new_score: float,
    old_ring: str,
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE tech_radar_entries
        SET    current_ring    = ?,
               previous_ring   = ?,
               composite_score = ?,
               last_assessed   = ?
        WHERE  id = ?
        """,
        (new_ring, old_ring, round(new_score, 4), now, entry_id),
    )


def _touch_entry(conn, entry_id: str, new_score: float, now: str) -> None:
    """Update score + last_assessed without changing the ring."""
    conn.execute(
        "UPDATE tech_radar_entries SET composite_score=?, last_assessed=? WHERE id=?",
        (round(new_score, 4), now, entry_id),
    )


def _insert_history(
    conn,
    entry_id: str,
    from_ring: str,
    to_ring: str,
    composite_score: float,
    innovation_signal_id: Optional[str],
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO tech_radar_history
            (id, entry_id, from_ring, to_ring, composite_score,
             innovation_signal_id, changed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            entry_id,
            from_ring,
            to_ring,
            round(composite_score, 4),
            innovation_signal_id,
            now,
        ),
    )


def _register_innovation_signal(
    conn,
    entry: dict,
    new_score: float,
    now: str,
) -> str:
    """Cross-register a ring→adopt transition as an innovation signal.

    Returns the signal id (existing or newly created).
    """
    title = f"Tech Radar: {entry['name']} promoted to ADOPT"
    content_hash = _content_hash(title)

    existing = conn.execute(
        "SELECT id FROM innovation_signals WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    if existing:
        return dict(existing)["id"]

    signal_id = str(uuid.uuid4())
    description = (
        f"{entry['name']} ({entry.get('category') or 'unknown'}) promoted to adopt ring. "
        f"Composite score: {new_score:.3f}. "
        f"Previous ring: {entry.get('current_ring') or 'unknown'}."
    )
    conn.execute(
        """
        INSERT INTO innovation_signals
            (id, source, source_type, title, description, url, metadata,
             community_score, content_hash, discovered_at, status,
             category, innovation_score, classification)
        VALUES (?, 'tech_radar', 'technology_promotion', ?, ?, NULL, NULL,
                ?, ?, ?, 'new', ?, ?, 'CUI // SP-CTI')
        """,
        (
            signal_id,
            title,
            description,
            round(new_score, 4),
            content_hash,
            now,
            entry.get("category") or "general",
            round(new_score, 4),
        ),
    )
    return signal_id


# ── Core engine ───────────────────────────────────────────────────────────────

class RadarEngine:
    """Assess and update Tech Radar entries using external source signals."""

    def __init__(
        self,
        enabled_sources: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> None:
        """
        Args:
            enabled_sources: scanner names to run; None = all.
            dry_run: compute scores without writing to DB.
        """
        self.enabled_sources = enabled_sources
        self.dry_run = dry_run

    # ── public API ────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """Execute one full assessment cycle.

        Returns:
            {"entries_assessed": int, "ring_changes": list, "new_signals": list}
        """
        signals = self._gather_signals()
        signal_index = self._index_signals(signals)

        conn = _get_conn()
        entries = _load_entries(conn)

        entries_assessed = 0
        ring_changes: List[dict] = []
        new_signals: List[str] = []
        now = _now_iso()

        for entry in entries:
            entries_assessed += 1
            new_score = self._recompute_score(entry, signal_index)
            new_ring = _ring_from_score(new_score)
            old_ring = entry.get("current_ring") or "assess"
            old_score = float(entry.get("composite_score") or 0.0)

            ring_changed = new_ring != old_ring
            score_shifted = abs(new_score - old_score) >= _SCORE_DELTA_MIN

            if not ring_changed and not score_shifted:
                continue

            logger.info(
                "radar_engine: %-20s  %s → %s  (%.3f → %.3f)",
                entry["name"],
                old_ring,
                new_ring,
                old_score,
                new_score,
            )

            if not self.dry_run:
                if ring_changed:
                    # Register adopt promotion before inserting history so the
                    # signal id can be embedded in the append-only history row.
                    innovation_signal_id: Optional[str] = None
                    if new_ring == "adopt" and old_ring != "adopt":
                        innovation_signal_id = _register_innovation_signal(
                            conn, entry, new_score, now
                        )
                        new_signals.append(innovation_signal_id)

                    _update_entry(conn, entry["id"], new_ring, new_score, old_ring, now)
                    _insert_history(
                        conn,
                        entry["id"],
                        old_ring,
                        new_ring,
                        new_score,
                        innovation_signal_id,
                        now,
                    )
                else:
                    _touch_entry(conn, entry["id"], new_score, now)

            if ring_changed:
                ring_changes.append(
                    {
                        "entry_id": entry["id"],
                        "name": entry["name"],
                        "from_ring": old_ring,
                        "to_ring": new_ring,
                        "composite_score": round(new_score, 4),
                        "innovation_signal_id": (
                            new_signals[-1] if new_ring == "adopt" and new_signals else None
                        ),
                    }
                )

        if not self.dry_run:
            conn.commit()
        conn.close()

        return {
            "entries_assessed": entries_assessed,
            "ring_changes": ring_changes,
            "new_signals": new_signals,
        }

    def status(self) -> dict:
        """Return ring summary counts and latest assessment timestamp."""
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT current_ring,
                   COUNT(*)        AS count,
                   MAX(last_assessed) AS last_assessed
            FROM   tech_radar_entries
            GROUP  BY current_ring
            ORDER  BY current_ring
            """
        ).fetchall()
        conn.close()
        rings = {
            r["current_ring"]: {
                "count": r["count"],
                "last_assessed": r["last_assessed"],
            }
            for r in rows
        }
        return {"status": "ok", "rings": rings}

    def list_entries(self, ring: Optional[str] = None) -> List[dict]:
        """Return all entries, optionally filtered by ring."""
        conn = _get_conn()
        if ring:
            rows = conn.execute(
                """
                SELECT * FROM tech_radar_entries
                WHERE  current_ring = ?
                ORDER  BY composite_score DESC
                """,
                (ring,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM tech_radar_entries
                ORDER  BY current_ring, composite_score DESC
                """
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── private helpers ───────────────────────────────────────────────────────

    def _gather_signals(self) -> List[dict]:
        from tools.tech_radar.source_scanner import SOURCE_SCANNERS

        sources = (
            self.enabled_sources
            if self.enabled_sources is not None
            else list(SOURCE_SCANNERS)
        )
        all_signals: List[dict] = []
        for src in sources:
            fn = SOURCE_SCANNERS.get(src)
            if fn is None:
                logger.warning("radar_engine: unknown source %r — skipping", src)
                continue
            try:
                result = fn()
                all_signals.extend(result)
                logger.info("radar_engine: %s → %d signals", src, len(result))
            except Exception as exc:  # noqa: BLE001
                logger.warning("radar_engine: scanner %s failed: %s", src, exc)
        return all_signals

    def _index_signals(self, signals: List[dict]) -> Dict[str, List[dict]]:
        """Build {normalized_name: [signal, ...]} for fast lookup."""
        index: Dict[str, List[dict]] = {}
        for sig in signals:
            key = (sig.get("name") or "").lower().strip()
            if key:
                index.setdefault(key, []).append(sig)
        return index

    def _match_signals(
        self, entry_name: str, index: Dict[str, List[dict]]
    ) -> List[dict]:
        """Return external signals matching entry_name (exact, then substring)."""
        name_lower = entry_name.lower().strip()
        if name_lower in index:
            return index[name_lower]
        matched: List[dict] = []
        for key, sigs in index.items():
            if name_lower in key or key in name_lower:
                matched.extend(sigs)
        return matched

    def _recompute_score(
        self, entry: dict, signal_index: Dict[str, List[dict]]
    ) -> float:
        """Recompute composite_score from DB base values + external adjustments."""
        eco = float(entry.get("ecosystem_maturity") or 0.5)
        fit = float(entry.get("icdev_fit") or 0.5)
        compat = float(entry.get("airgap_compat") or 0.5)
        il = float(entry.get("il_compliance") or 0.5)

        # Ecosystem maturity: apply best signal boost from external sources
        matched = self._match_signals(entry["name"], signal_index)
        eco_boost = max(
            (_SIGNAL_BOOST.get((s.get("ecosystem_maturity_signal") or "").upper(), 0.0) for s in matched),
            default=0.0,
        )
        eco = _clamp(eco + eco_boost)

        # ICDEV fit: keyword presence in tech name
        name_lower = entry["name"].lower()
        fit_boost = sum(
            boost for kw, boost in _ICDEV_FIT_KEYWORDS.items() if kw in name_lower
        )
        fit = _clamp(fit + fit_boost)

        # Air-gap compat: penalise entries below threshold
        if compat < _AIRGAP_COMPAT_THRESHOLD:
            compat = _clamp(compat - _AIRGAP_COMPAT_PENALTY)

        composite = (
            _WEIGHTS["ecosystem_maturity"] * eco
            + _WEIGHTS["icdev_fit"] * fit
            + _WEIGHTS["airgap_compat"] * compat
            + _WEIGHTS["il_compliance"] * il
        )
        return round(composite, 4)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Tech Radar engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python radar_engine.py --run --json\n"
            "  python radar_engine.py --run --dry-run --json\n"
            "  python radar_engine.py --status --json\n"
            "  python radar_engine.py --list --ring adopt --json\n"
            "  python radar_engine.py --daemon --interval 3600 --json\n"
        ),
    )
    parser.add_argument("--run", action="store_true", help="Run one assessment cycle")
    parser.add_argument("--status", action="store_true", help="Show ring summary")
    parser.add_argument("--list", action="store_true", help="List entries")
    parser.add_argument("--ring", metavar="RING", help="Filter by ring (adopt/trial/assess/hold)")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=3600, metavar="SECONDS", help="Daemon interval (default 3600)")
    parser.add_argument("--source", action="append", metavar="NAME", dest="sources", help="Limit to specific scanner(s)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Compute scores without writing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    engine = RadarEngine(enabled_sources=args.sources, dry_run=args.dry_run)

    def _emit(data: object) -> None:
        if args.as_json:
            print(json.dumps(data, indent=2, default=str))
        else:
            if isinstance(data, dict):
                for k, v in data.items():
                    print(f"{k}: {v}")
            elif isinstance(data, list):
                for item in data:
                    print(item)
            else:
                print(data)

    if args.run:
        _emit(engine.run())
    elif args.status:
        _emit(engine.status())
    elif args.list:
        entries = engine.list_entries(ring=args.ring)
        _emit(entries)
    elif args.daemon:
        logger.info("radar_engine: daemon starting, interval=%ds", args.interval)
        while True:
            try:
                result = engine.run()
                _emit(result)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("radar_engine: cycle failed: %s", exc)
            time.sleep(args.interval)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    _cli()
