# CUI // SP-CTI
"""harvester.py — ACF signal harvester (acf-harvest-01 + acf-harvest-02).

Pulls raw capability signals from the stores that ICDEV's EXISTING discovery
engines already populate — it does NOT re-scan the web. The five engines wired
here, with the public modules that own each store:

  * innovation — ``tools/innovation/signal_ranker`` (``innovation_signals``) +
    ``tools/innovation/trend_detector`` (``innovation_trends``)
  * creative   — ``tools/creative/pain_extractor`` / ``gap_scorer``
    (``creative_pain_points``, ``creative_feature_gaps``)
  * research   — ``tools/research/challenge_scorer`` (``research_challenges``) +
    ``tools/research/dossier_generator`` (``research_dossiers``)
  * genesis    — Oracle lens predictions + Internal Awareness gap nodes, both
    persisted in ``oracle_predictions`` (gap nodes carry
    ``lens_name='internal_awareness'`` / ``prediction_type='gap::<rule>'``).
  * telemetry  — ``tools/innovation/introspective_analyzer`` read-only analyses
    (gate failures, unused tools, slow pipelines, knowledge gaps). The analyses
    only SELECT from audit/knowledge stores and return findings; the harvester
    never calls ``generate_introspective_signals`` (which would append).

Reading the engines' persisted stores (rather than invoking their scoring entry
points) keeps the harvest deterministic, air-gap safe and side-effect free: those
entry points re-score and append to their own append-only tables, which a harvest
must never trigger. The telemetry source follows the same rule by invoking only
the analyzer's pure read functions.

Each source row is normalized into the ``foundry_signals`` shape —
``(source_engine, source_ref, theme, raw_score, keywords)`` — and persisted
append-only under ``run_id`` with ``tenant_id`` / ``classification`` stamped from
the active security context. Per-source caps come from ``args/foundry_config.yaml``
(``sources.<engine>.enabled`` + ``sources.<engine>.max_signals``) so one noisy
store can't dominate a cycle. Reads are best-effort: a disabled, empty or
not-yet-migrated source contributes zero signals, never an error.

Cross-source dedup (acf-harvest-02): after per-source caps are applied, signals
are collapsed by a SHA-256 of their normalized ``theme`` + sorted ``keywords`` so
the SAME capability surfaced by two engines (e.g. an innovation signal and a
research challenge naming the same gap) is persisted once. The highest-scoring
representative wins; first-seen order is preserved.

Public API
----------
    harvest(run_id, *, config=None, conn=None) -> list[dict]

CLI
---
    python tools/foundry/harvester.py --run-id <id> --json
"""
from __future__ import annotations

import argparse
import hashlib
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
    # Genesis: Oracle lens predictions AND Internal Awareness gap nodes both live
    # in oracle_predictions (gap_detector writes gaps as oracle_predictions rows
    # under lens_name='internal_awareness'). confidence [0..1] is the raw score;
    # prediction_type ('gap::route_not_listed', 'risk::…', …) + severity become
    # the clustering keywords.
    {
        "engine": "genesis",
        "table": "oracle_predictions",
        "theme_col": "prediction_text",
        "score_col": "confidence",
        "keywords_col": None,
        "tag_cols": ("prediction_type", "severity"),
    },
    # RFI capability-gap demand signals (tools/govcon/rfi_demand.py). Recurring
    # unmet RFI requirements aggregate here with a frequency-weighted `priority`;
    # harvesting them lets the Foundry novelty-gate and cluster real customer
    # demand alongside internally-discovered gaps. PK is content_hash, not id.
    {
        "engine": "rfi",
        "table": "rfi_capability_gaps",
        "id_col": "content_hash",
        "theme_col": "capability_need",
        "score_col": "priority",
        "keywords_col": "keywords",
        "tag_cols": ("domain",),
    },
)

# Engines harvested each cycle, in deterministic order. innovation/creative/
# research/genesis are table-backed (``_SOURCES`` descriptors); telemetry is
# computed from the introspective analyzer's read-only analyses.
_ENGINES: tuple[str, ...] = ("innovation", "creative", "research", "genesis", "rfi", "telemetry")


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
    cols = [spec.get("id_col", "id"), spec["theme_col"], spec["score_col"]]
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


# =========================================================================
# TELEMETRY SOURCE (introspective analyzer — read-only analyses)
# =========================================================================
# The four internal-telemetry analyses the foundry harvests. Each is a PURE READ
# (SELECTs audit_trail / knowledge_patterns, returns findings) — none of them
# persists, so invoking them keeps the harvest side-effect free. We deliberately
# skip generate_introspective_signals(), which WOULD append to innovation_signals.
_TELEMETRY_ANALYSES: tuple[str, ...] = (
    "gate_failures",
    "unused_tools",
    "slow_pipelines",
    "knowledge_gaps",
)

# Finding fields (in priority order) that make good cluster keywords per analysis
# type. The analysis type itself is always the first keyword.
_TELEMETRY_TAG_FIELDS: tuple[str, ...] = (
    "gate_event_type",
    "event_type",
    "stage",
    "pattern_type",
    "check",
)


def _finding_ref(atype: str, finding: dict) -> str:
    """Stable, unique source_ref for one telemetry finding (hash of its payload
    so re-running the same analysis maps to the same ref)."""
    payload = json.dumps(finding, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"introspective:{atype}:{digest}"


def _telemetry_keywords(atype: str, finding: dict) -> list[str]:
    """Cluster keywords for a telemetry finding: the analysis type plus any
    salient string tags present on the finding."""
    out = [atype]
    for field in _TELEMETRY_TAG_FIELDS:
        val = finding.get(field)
        if val is not None and str(val).strip():
            out.append(str(val).strip())
    return out


def _harvest_telemetry(cap: int) -> list[dict]:
    """Normalize internal-telemetry findings into foundry_signals.

    Invokes the introspective analyzer's read-only analyses and reuses its own
    ``_signal_title`` / ``_signal_score`` so telemetry signals are scored exactly
    as they would be in the innovation pipeline. Best-effort: a missing module,
    missing DB or skipped analysis contributes zero signals, never an error.
    """
    try:
        from tools.innovation import introspective_analyzer as ia
    except Exception as exc:  # noqa: BLE001 - analyzer may be absent in slim envs
        logger.debug("telemetry skip (analyzer unavailable): %s", exc)
        return []

    signals: list[dict] = []
    for atype in _TELEMETRY_ANALYSES:
        fn = getattr(ia, f"analyze_{atype}", None)
        if fn is None:
            continue
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - one analysis failing must not abort
            logger.debug("telemetry analysis %s skip: %s", atype, exc)
            continue
        if not result or result.get("skipped"):
            continue
        for finding in result.get("findings", []):
            if not isinstance(finding, dict):
                continue
            try:
                theme = ia._signal_title(atype, finding)
                score = ia._signal_score(atype, finding)
            except Exception:  # noqa: BLE001 - skip a malformed finding, keep the rest
                continue
            signals.append(
                {
                    "source_engine": "telemetry",
                    "source_ref": _finding_ref(atype, finding),
                    "theme": theme,
                    "raw_score": _coerce_score(score),
                    "keywords": _telemetry_keywords(atype, finding),
                }
            )
    return signals


# =========================================================================
# CROSS-SOURCE DEDUP
# =========================================================================
def _dedupe_key(sig: dict) -> str:
    """SHA-256 of the normalized theme + sorted keywords. Two signals with the
    same key describe the same capability regardless of which engine found it."""
    theme = (str(sig.get("theme") or "")).strip().lower()
    kws = sorted({str(k).strip().lower() for k in sig.get("keywords") or [] if str(k).strip()})
    payload = theme + "\x1f" + ",".join(kws)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dedupe_cross_source(signals: list[dict]) -> list[dict]:
    """Collapse signals sharing a dedup key to one, keeping the highest-scoring
    representative. First-seen order is preserved so output stays deterministic."""
    best: dict[str, dict] = {}
    order: list[str] = []
    for sig in signals:
        key = _dedupe_key(sig)
        existing = best.get(key)
        if existing is None:
            best[key] = sig
            order.append(key)
        elif sig["raw_score"] > existing["raw_score"]:
            best[key] = sig
    return [best[k] for k in order]


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
    collected: list[dict] = []

    try:
        # Collect per engine so the cap is applied across that engine's stores
        # BEFORE cross-source dedup — one noisy store can't dominate a cycle.
        for engine in _ENGINES:
            sc = _source_cfg(cfg, engine)
            if not sc["enabled"]:
                logger.debug("harvest: source '%s' disabled", engine)
                continue
            cap = sc["max_signals"]
            if engine == "telemetry":
                engine_signals = _harvest_telemetry(cap)
            else:
                engine_signals = []
                for spec in _SOURCES:
                    if spec["engine"] != engine:
                        continue
                    engine_signals.extend(_harvest_source(conn, spec, cap))
            # Strongest signals first, then enforce the hard per-source cap.
            engine_signals.sort(key=lambda s: s["raw_score"], reverse=True)
            collected.extend(engine_signals[:cap])

        # Cross-source dedup: the same capability surfaced by two engines collapses
        # to one row (highest score wins). Applied AFTER per-source caps so dedup
        # never lets one source eat another's budget.
        before = len(collected)
        harvested = _dedupe_cross_source(collected)
        if before != len(harvested):
            logger.debug("harvest dedup: %d -> %d signals", before, len(harvested))

        for sig in harvested:
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
