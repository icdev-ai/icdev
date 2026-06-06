# CUI // SP-CTI
"""harvester.py — ACF signal harvester (acf-harvest-01).

Pulls raw capability signals from the stores that ICDEV's EXISTING discovery
engines already populate — it does NOT re-scan the web. The three engines wired
here, with the public modules that own each store:

  * innovation — ``tools/innovation/signal_ranker`` (``innovation_signals``) +
    ``tools/innovation/trend_detector`` (``innovation_trends``)
  * creative   — ``tools/creative/pain_extractor`` / ``gap_scorer``
    (``creative_pain_points``, ``creative_feature_gaps``)
  * research   — ``tools/research/challenge_scorer`` (``research_challenges``) +
    ``tools/research/dossier_generator`` (``research_dossiers``)

Reading the engines' persisted stores (rather than invoking their scoring entry
points) keeps the harvest deterministic, air-gap safe and side-effect free: those
entry points re-score and append to their own append-only tables, which a harvest
must never trigger.

Each source row is normalized into the ``foundry_signals`` shape —
``(source_engine, source_ref, theme, raw_score, keywords)`` — and persisted
append-only under ``run_id`` with ``tenant_id`` / ``classification`` stamped from
the active security context. Per-source caps come from ``args/foundry_config.yaml``
(``sources.<engine>.enabled`` + ``sources.<engine>.max_signals``) so one noisy
store can't dominate a cycle. Reads are best-effort: a disabled, empty or
not-yet-migrated source contributes zero signals, never an error.

Public API
----------
    harvest(run_id, *, config=None, conn=None) -> list[dict]

CLI
---
    python tools/foundry/harvester.py --run-id <id> --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

try:  # optional — only needed to read args/foundry_config.yaml
    import yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover
    yaml = None  # type: ignore
    _HAS_YAML = False

from tools.foundry.db.init_db import init_db
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.foundry.harvester")


# =========================================================================
# PATH / CONFIG
# =========================================================================
def _find_repo_root() -> Path:
    """Anchor on a marker that exists only at the true repo root (handles the
    tools/ + icdev/tools/ mirror)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CLAUDE.md").exists() or (parent / "goals" / "manifest.md").exists():
            return parent
    return here.parent.parent.parent


BASE_DIR = _find_repo_root()
CONFIG_PATH = BASE_DIR / "args" / "foundry_config.yaml"

# Default per-source behaviour if the config file is missing/unreadable: every
# engine enabled, capped at 50 (mirrors the committed args/foundry_config.yaml).
_DEFAULT_SOURCE_CFG = {"enabled": True, "max_signals": 50}


def _load_config(config: Optional[dict]) -> dict:
    if config is not None:
        return config
    if _HAS_YAML and CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("foundry_config.yaml unreadable (%s); using defaults", exc)
    return {}


def _source_cfg(config: dict, engine: str) -> dict:
    src = ((config.get("sources") or {}).get(engine)) or {}
    return {
        "enabled": src.get("enabled", _DEFAULT_SOURCE_CFG["enabled"]),
        "max_signals": int(src.get("max_signals", _DEFAULT_SOURCE_CFG["max_signals"])),
    }


# =========================================================================
# SOURCE STORE DESCRIPTORS
# =========================================================================
# Each descriptor maps one existing engine store table onto the foundry_signals
# shape. ``keywords_col`` is the table's JSON keyword array when it has one;
# otherwise ``tag_cols`` lists plain columns whose values become the keyword list.
_SOURCES: tuple[dict, ...] = (
    {
        "engine": "innovation",
        "table": "innovation_signals",
        "theme_col": "title",
        "score_col": "innovation_score",
        "keywords_col": None,
        "tag_cols": ("category", "source"),
    },
    {
        "engine": "innovation",
        "table": "innovation_trends",
        "theme_col": "name",
        "score_col": "velocity",
        "keywords_col": "keywords",
        "tag_cols": (),
    },
    {
        "engine": "creative",
        "table": "creative_pain_points",
        "theme_col": "title",
        "score_col": "composite_score",
        "keywords_col": "keywords",
        "tag_cols": (),
    },
    {
        "engine": "creative",
        "table": "creative_feature_gaps",
        "theme_col": "feature_name",
        "score_col": "gap_score",
        "keywords_col": None,
        "tag_cols": (),
    },
    {
        "engine": "research",
        "table": "research_challenges",
        "theme_col": "title",
        "score_col": "composite_score",
        "keywords_col": "keywords",
        "tag_cols": (),
    },
    {
        "engine": "research",
        "table": "research_dossiers",
        "theme_col": "title",
        "score_col": "overall_opportunity_score",
        "keywords_col": None,
        "tag_cols": (),
    },
)


def _caller_context() -> tuple[str, str]:
    """(tenant_id, classification) from the active security context, with the
    platform defaults when no request context is bound."""
    try:
        from tools.security.security_context import get_security_context

        ctx = get_security_context()
    except Exception:
        ctx = None
    tenant_id = (getattr(ctx, "tenant_id", None) or "default") if ctx else "default"
    classification = (getattr(ctx, "classification", None) or "CUI") if ctx else "CUI"
    return tenant_id, classification


def _coerce_score(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_keywords(raw: Any, tags: list[str]) -> list[str]:
    """Normalize a keyword payload into a clean list of strings.

    Prefers the store's JSON keyword array; falls back to the plain ``tag_cols``
    values (e.g. category/source) when the table has no keyword column.
    """
    out: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                out = [str(k).strip() for k in parsed if str(k).strip()]
            elif isinstance(parsed, str) and parsed.strip():
                out = [parsed.strip()]
        except (ValueError, TypeError):
            if isinstance(raw, str) and raw.strip():
                out = [raw.strip()]
    if not out:
        out = [str(t).strip() for t in tags if t is not None and str(t).strip()]
    return out


def _harvest_source(conn: Any, spec: dict, cap: int) -> list[dict]:
    """Read + normalize one engine store table. Returns [] on any error (missing
    table, unmigrated schema, empty store)."""
    cols = ["id", spec["theme_col"], spec["score_col"]]
    if spec["keywords_col"]:
        cols.append(spec["keywords_col"])
    cols.extend(spec["tag_cols"])
    select_cols = ", ".join(cols)
    table = spec["table"]
    # Pull a bounded window ordered by the store's own score so the per-source
    # cap keeps the strongest signals. cap*4 leaves headroom for NULL scores
    # that sort last; the hard cap is applied after normalization.
    sql = (
        f"SELECT {select_cols} FROM {table} "
        f"ORDER BY {spec['score_col']} DESC LIMIT {max(cap * 4, cap)}"
    )
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - store may not exist yet
        logger.debug("harvest skip %s: %s", table, exc)
        return []

    n_tags = len(spec["tag_cols"])
    kw_idx = 3 if spec["keywords_col"] else None
    tag_start = 4 if spec["keywords_col"] else 3

    signals: list[dict] = []
    for row in rows:
        row = list(row)
        raw_kw = row[kw_idx] if kw_idx is not None else None
        tags = list(row[tag_start: tag_start + n_tags]) if n_tags else []
        signals.append(
            {
                "source_engine": spec["engine"],
                "source_ref": f"{table}:{row[0]}",
                "theme": row[1],
                "raw_score": _coerce_score(row[2]),
                "keywords": _parse_keywords(raw_kw, tags),
            }
        )
    return signals


_INSERT_SQL = (
    "INSERT INTO foundry_signals "
    "(run_id, source_engine, source_ref, theme, raw_score, keywords, "
    "tenant_id, classification) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def harvest(
    run_id: Any,
    *,
    config: Optional[dict] = None,
    conn: Any = None,
) -> list[dict]:
    """Harvest innovation / creative / research signals into ``foundry_signals``.

    Reads each enabled engine's persisted store, normalizes every row into the
    foundry_signals shape, applies the per-source cap, and appends the result
    under ``run_id`` (stamped with the caller's tenant_id / classification).

    Returns the list of normalized signal dicts that were persisted
    ``{source_engine, source_ref, theme, raw_score, keywords}``.
    """
    cfg = _load_config(config)
    init_db()  # idempotent — guarantees foundry_signals exists

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()

    tenant_id, classification = _caller_context()
    run_id_str = str(run_id)
    harvested: list[dict] = []

    try:
        # Collect per engine so the cap is applied across that engine's stores.
        for engine in ("innovation", "creative", "research"):
            sc = _source_cfg(cfg, engine)
            if not sc["enabled"]:
                logger.debug("harvest: source '%s' disabled", engine)
                continue
            cap = sc["max_signals"]
            engine_signals: list[dict] = []
            for spec in _SOURCES:
                if spec["engine"] != engine:
                    continue
                engine_signals.extend(_harvest_source(conn, spec, cap))
            # Strongest signals first, then enforce the hard per-source cap.
            engine_signals.sort(key=lambda s: s["raw_score"], reverse=True)
            engine_signals = engine_signals[:cap]

            for sig in engine_signals:
                conn.execute(
                    _INSERT_SQL,
                    (
                        run_id_str,
                        sig["source_engine"],
                        sig["source_ref"],
                        sig["theme"],
                        sig["raw_score"],
                        json.dumps(sig["keywords"]),
                        tenant_id,
                        classification,
                    ),
                )
            harvested.extend(engine_signals)

        conn.commit()
    finally:
        if own_conn:
            conn.close()

    logger.info(
        "harvest run=%s -> %d signals (%s)",
        run_id_str,
        len(harvested),
        ", ".join(sorted({s["source_engine"] for s in harvested})) or "none",
    )
    return harvested


# =========================================================================
# CLI
# =========================================================================
def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ACF signal harvester")
    parser.add_argument("--run-id", required=True, help="foundry run identifier")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    signals = harvest(args.run_id)
    if args.json:
        print(json.dumps({"run_id": args.run_id, "count": len(signals), "signals": signals}, indent=2))
    else:
        print(f"harvested {len(signals)} signals for run {args.run_id}")
        for s in signals:
            print(f"  [{s['source_engine']}] {s['source_ref']}  score={s['raw_score']:.3f}  {s['theme']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    raise SystemExit(_main())
