# CUI // SP-CTI
"""strategos_bridge.py — fan an approved ACF concept out to the Strategos intel channels (acf-ada-06).

A side-channel of the ACF loop. Most approved concepts become a kanban build epic
(spec_generator → task_graph → seeder) and nothing more. But a concept whose dossier
touches **national-security or competitive-intelligence** domains — high
``compliance_risk`` (it brushes a regulated / sensitive surface) or high
``market_score`` (a competitor / market-intel signal worth watching) — should ALSO
enter the ICDEV intelligence workflow rather than only the build pipeline.

This bridge adapts ``tools/strategos/research_bridge.py`` (which routes Industry
Research Engine output) to route ONE ``foundry_concepts`` row to the same four
Strategos output channels:

    foundry concept (approved, high compliance_risk OR market_score)
        ├─► sg_ghost_signals       dark-web / threat monitoring trigger   (keyword-gated)
        ├─► sg_hitl_items          analyst human-review queue             (always on qualify)
        ├─► sg_pir_requirements    priority intelligence requirement      (always on qualify)
        └─► sg_intelligence_briefs auto-generated capability brief        (always on qualify)

Design rules (mirrors research_bridge + the foundry house style):
  * **Qualification gate** — only an ``approved`` concept that clears the
    compliance/market thresholds fans out. Everything else is a clean skip (no
    channels touched), so a low-risk concept produces no intel artifacts.
  * **Failure isolation** — each channel is written in its OWN short-lived
    ``get_connection()`` transaction (commit+close per channel). One channel
    failing (missing table, etc.) is caught, logged, and recorded in ``errors``;
    it can neither poison another channel's transaction (PG aborts a txn on the
    first error) nor crash the cycle.
  * **RLS-aware** — writes go through ``tools.db.storage.get_connection`` exactly
    like research_bridge. The ``sg_*`` tables have no ``tenant_id`` column, so the
    INSERTs (no WHERE → no RLS predicate injected) rely on column defaults, never
    stamping tenant_id.
  * **Deterministic** — same concept → same routing decisions (no LLM, no network).

Public API
----------
    fan_out(concept, *, conn=None, dry_run=False, config=None) -> dict

CLI
---
    python tools/foundry/strategos_bridge.py --concept-json '{...}' --json
    python tools/foundry/strategos_bridge.py --concept-id 7 --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:  # optional — only needed to read args/foundry_config.yaml
    import yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover - yaml is a core dep but stay defensive
    yaml = None  # type: ignore
    _HAS_YAML = False

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.foundry.strategos_bridge")

CHANNELS = ("ghost_signals", "hitl_items", "pir_requirements", "intelligence_briefs")


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

# Defaults for the ``strategos_bridge`` section of args/foundry_config.yaml.
# A concept qualifies when compliance_risk >= compliance_threshold OR
# market_score >= market_threshold. ghost_keywords gate the dark-web monitoring
# channel: a concept only triggers a ghost signal when its prose names a
# national-security / threat surface (the dark-web monitor is for those, not for
# every competitive-intel concept).
_DEFAULT_CFG: dict[str, Any] = {
    "compliance_threshold": 0.6,
    "market_threshold": 0.7,
    "ghost_keywords": [
        "dark web", "darkweb", "dark-web", "leak", "breach", "exfil",
        "threat actor", "adversary", "malware", "ransomware", "exploit",
        "espionage", "surveillance", "classified", "national security",
        "weapon", "missile", "munition", "military", "defense", "warfare",
        "sanction", "smuggl", "interdiction", "maritime", "vessel", "naval",
    ],
    "payload_max_chars": 800,
}


def _load_config(config: Optional[dict]) -> dict:
    """Return the ``strategos_bridge`` config section merged over ``_DEFAULT_CFG``.

    ``config`` may be the full foundry_config dict or just its ``strategos_bridge``
    block; both are accepted so callers can pass whatever they already hold.
    """
    if config is not None:
        section = config.get("strategos_bridge", config) if isinstance(config, dict) else {}
    elif _HAS_YAML and CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open(encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
            section = doc.get("strategos_bridge", {}) if isinstance(doc, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("foundry_config.yaml unreadable (%s); using bridge defaults", exc)
            section = {}
    else:
        section = {}

    merged = dict(_DEFAULT_CFG)
    if isinstance(section, dict):
        merged.update({k: v for k, v in section.items() if v is not None})
    return merged


# =========================================================================
# HELPERS
# =========================================================================
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _concept_text(concept: dict[str, Any]) -> str:
    """All capability-bearing prose for keyword heuristics (lowercased)."""
    parts = [
        concept.get("name"),
        concept.get("slug"),
        concept.get("proposed_capability"),
        concept.get("problem_statement"),
        concept.get("target_users"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _score_to_priority(score: float) -> int:
    """Map a [0..1] score to integer collection priority (1=critical … 4=low).

    Clamped to the sg_pir_requirements CHECK range (1..5)."""
    if score >= 0.75:
        return 1
    if score >= 0.6:
        return 2
    if score >= 0.5:
        return 3
    return 4


def _pir_type(text: str) -> str:
    """Pick a PIR/CCIR/EEI type from the concept prose (matches research_bridge)."""
    if any(k in text for k in ("supply", "logistics", "interdiction", "chain", "sanction")):
        return "CCIR"
    if any(k in text for k in ("electronic", "spectrum", "jamming", "radar", "signal", "cyber")):
        return "EEI"
    return "PIR"


def _qualifies(concept: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """A concept fans out only when approved AND high compliance_risk OR market_score."""
    status = str(concept.get("status") or "").lower()
    if status != "approved":
        return False, f"status={status or 'unknown'} (only approved concepts fan out)"
    cr = _f(concept.get("compliance_risk"))
    ms = _f(concept.get("market_score"))
    ct = _f(cfg.get("compliance_threshold"))
    mt = _f(cfg.get("market_threshold"))
    if cr >= ct or ms >= mt:
        return True, None
    return (
        False,
        f"below thresholds (compliance_risk={cr:.2f}<{ct:.2f} AND market_score={ms:.2f}<{mt:.2f})",
    )


def _matches_ghost(text: str, cfg: dict[str, Any]) -> bool:
    return any(str(kw).lower() in text for kw in cfg.get("ghost_keywords", []) if kw)


# =========================================================================
# PER-CHANNEL DB WRITE (failure-isolated)
# =========================================================================
def _insert(conn: Any, sql: str, params: tuple) -> None:
    """Execute one INSERT. When ``conn`` is None, open a short-lived RLS-aware
    connection, commit, and close it — so each channel is its own transaction and
    a failure can't poison another channel (PG aborts a txn on the first error).
    When ``conn`` is provided (tests), use it and let the caller manage commit."""
    own = conn is None
    if own:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        conn.execute(sql, params)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


# =========================================================================
# CHANNEL ROUTERS — each returns {"routed": bool, ...}; raises on DB failure
# =========================================================================
def _route_ghost_signal(concept: dict[str, Any], cfg: dict[str, Any], *, dry_run: bool, conn: Any) -> dict:
    """sg_ghost_signals — dark-web / threat monitoring trigger. Keyword-gated:
    only concepts naming a national-security / threat surface trigger one."""
    text = _concept_text(concept)
    if not _matches_ghost(text, cfg):
        return {"routed": False, "reason": "no national-security / dark-web keywords"}

    entry = {
        "id": str(uuid.uuid4()),
        "signal_type": "intelligence_signal",
        "source": f"foundry:concept:{concept.get('id', concept.get('slug', 'unknown'))}",
        "confidence": round(max(_f(concept.get("compliance_risk")), 0.6), 4),
        "detected_at": _now(),
        "behavior_profile_json": json.dumps(
            {
                "trigger": "dark_web_monitoring",
                "concept_id": concept.get("id"),
                "slug": concept.get("slug"),
                "name": concept.get("name"),
                "compliance_risk": _f(concept.get("compliance_risk")),
            }
        ),
    }
    if not dry_run:
        _insert(
            conn,
            "INSERT INTO sg_ghost_signals "
            "(id, signal_type, source, confidence, detected_at, behavior_profile_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry["id"], entry["signal_type"], entry["source"],
                entry["confidence"], entry["detected_at"], entry["behavior_profile_json"],
            ),
        )
    return {"routed": True, "id": entry["id"], "signal_type": entry["signal_type"]}


def _route_hitl_item(concept: dict[str, Any], cfg: dict[str, Any], *, dry_run: bool, conn: Any) -> dict:
    """sg_hitl_items — queue the concept for analyst human review."""
    cap = int(_f(cfg.get("payload_max_chars")) or 800)
    payload = json.dumps(
        {
            "name": concept.get("name"),
            "slug": concept.get("slug"),
            "problem_statement": (concept.get("problem_statement") or "")[:cap],
            "proposed_capability": (concept.get("proposed_capability") or "")[:cap],
            "compliance_risk": _f(concept.get("compliance_risk")),
            "market_score": _f(concept.get("market_score")),
            "composite_score": _f(concept.get("composite_score")),
            "source": "acf",
        }
    )
    entry = {
        "id": str(uuid.uuid4()),
        "item_type": "acf_concept",
        "ref_id": str(concept.get("id") or concept.get("slug") or ""),
        "payload": payload,
        "status": "pending",
        "created_at": _now(),
    }
    if not dry_run:
        _insert(
            conn,
            "INSERT INTO sg_hitl_items (id, item_type, ref_id, payload, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry["id"], entry["item_type"], entry["ref_id"],
                entry["payload"], entry["status"], entry["created_at"],
            ),
        )
    return {"routed": True, "id": entry["id"], "item_type": entry["item_type"]}


def _route_pir_requirement(concept: dict[str, Any], cfg: dict[str, Any], *, dry_run: bool, conn: Any) -> dict:
    """sg_pir_requirements — register the concept as a priority intelligence requirement."""
    text = _concept_text(concept)
    pir_type = _pir_type(text)
    priority = _score_to_priority(max(_f(concept.get("compliance_risk")), _f(concept.get("market_score"))))
    name = str(concept.get("name") or "ACF Capability Concept")
    description = (
        (concept.get("problem_statement") or concept.get("proposed_capability") or "")[:1000]
        + f"\n\n[Source: ACF concept {concept.get('id')} ({concept.get('slug')}), "
        f"compliance_risk {_f(concept.get('compliance_risk')):.2f}, "
        f"market_score {_f(concept.get('market_score')):.2f}]"
    )
    entry = {
        "id": str(uuid.uuid4()),
        "pir_type": pir_type,
        "topic": name[:200],
        "description": description,
        "collection_priority": priority,
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
    }
    if not dry_run:
        _insert(
            conn,
            "INSERT INTO sg_pir_requirements "
            "(id, pir_type, topic, description, collection_priority, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry["id"], entry["pir_type"], entry["topic"], entry["description"],
                entry["collection_priority"], entry["status"],
                entry["created_at"], entry["updated_at"],
            ),
        )
    return {
        "routed": True,
        "id": entry["id"],
        "pir_type": entry["pir_type"],
        "priority": entry["collection_priority"],
    }


def _route_intelligence_brief(concept: dict[str, Any], cfg: dict[str, Any], *, dry_run: bool, conn: Any) -> dict:
    """sg_intelligence_briefs — auto-generate an assessment brief for the concept."""
    name = str(concept.get("name") or "ACF Capability Concept")
    title = f"ACF Capability Assessment — {name}"
    content_md = f"""# {title}

CUI // SP-CTI

**Source:** Autonomous Capability Foundry (ACF) — concept `{concept.get('slug')}`
**Concept ID:** `{concept.get('id')}`
**Generated:** {_now()}

---

## Problem Statement
{concept.get('problem_statement') or '_Not specified._'}

## Proposed Capability
{concept.get('proposed_capability') or '_Not specified._'}

## Intelligence Relevance
| Dimension | Score |
|-----------|-------|
| Compliance risk | {_f(concept.get('compliance_risk')):.2f} |
| Market | {_f(concept.get('market_score')):.2f} |
| Novelty | {_f(concept.get('novelty_score')):.2f} |
| Composite | {_f(concept.get('composite_score')):.2f} |

This capability concept brushes a national-security or competitive-intelligence
surface and has been routed into the Strategos intelligence workflow for analyst
review, PIR tasking, and (where applicable) dark-web monitoring.
"""
    entry = {
        "id": str(uuid.uuid4()),
        "brief_type": "assessment",
        "title": title[:300],
        "content_md": content_md,
        "sio_confidence": round(min(max(_f(concept.get("composite_score")), 0.0), 1.0), 4),
        "analyst_reviewed": 0,
        "created_at": _now(),
    }
    if not dry_run:
        _insert(
            conn,
            "INSERT INTO sg_intelligence_briefs "
            "(id, brief_type, title, content_md, sio_confidence, analyst_reviewed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry["id"], entry["brief_type"], entry["title"], entry["content_md"],
                entry["sio_confidence"], entry["analyst_reviewed"], entry["created_at"],
            ),
        )
    return {"routed": True, "id": entry["id"], "brief_type": entry["brief_type"]}


_ROUTERS = {
    "ghost_signals": _route_ghost_signal,
    "hitl_items": _route_hitl_item,
    "pir_requirements": _route_pir_requirement,
    "intelligence_briefs": _route_intelligence_brief,
}


# =========================================================================
# PUBLIC: fan_out
# =========================================================================
def fan_out(
    concept: dict[str, Any],
    *,
    conn: Any = None,
    dry_run: bool = False,
    config: Optional[dict] = None,
) -> dict[str, Any]:
    """Fan one approved ACF *concept* out to the four Strategos intel channels.

    A concept qualifies only when ``status == 'approved'`` AND its
    ``compliance_risk`` or ``market_score`` clears the configured threshold. A
    concept that does not qualify is a clean skip — no channel is touched.

    Channel failures are isolated: each write is its own transaction (when
    ``conn`` is None) and any exception is caught, logged, and recorded in
    ``errors`` without aborting the remaining channels or raising.

    Args:
        concept: a ``foundry_concepts``-shaped dict (name, slug, status, score
            columns, optional ``id``).
        conn: optional open DB connection (caller manages commit/close). When
            None (default/production), each channel opens its own short-lived
            RLS-aware ``get_connection()`` for failure isolation.
        dry_run: when True, decide routing but write nothing.
        config: optional foundry_config dict (or its ``strategos_bridge`` block).

    Returns:
        ``{concept_id, slug, qualified, skip_reason, dry_run, channels{...}, errors[...]}``
        where ``channels`` maps each of the four channel names to its per-channel
        result dict (``{"routed": bool, ...}``).
    """
    cfg = _load_config(config)
    result: dict[str, Any] = {
        "concept_id": concept.get("id"),
        "slug": concept.get("slug"),
        "qualified": False,
        "skip_reason": None,
        "dry_run": dry_run,
        "channels": {name: {"routed": False, "reason": "not qualified"} for name in CHANNELS},
        "errors": [],
    }

    qualified, skip_reason = _qualifies(concept, cfg)
    result["qualified"] = qualified
    result["skip_reason"] = skip_reason
    if not qualified:
        logger.info(
            "concept %s (%s) skipped: %s",
            concept.get("id"), concept.get("slug"), skip_reason,
        )
        return result

    for name in CHANNELS:
        router = _ROUTERS[name]
        try:
            result["channels"][name] = router(concept, cfg, dry_run=dry_run, conn=conn)
        except Exception as exc:  # noqa: BLE001 - one channel must never crash the cycle
            logger.warning(
                "strategos channel %s failed for concept %s: %s",
                name, concept.get("id"), exc,
            )
            result["channels"][name] = {"routed": False, "error": str(exc)}
            result["errors"].append({"channel": name, "error": str(exc)})

    routed = [n for n in CHANNELS if result["channels"][n].get("routed")]
    logger.info(
        "concept %s (%s) fanned out → %s%s",
        concept.get("id"), concept.get("slug"),
        ", ".join(routed) or "none",
        " (dry-run)" if dry_run else "",
    )
    return result


# =========================================================================
# CLI
# =========================================================================
def _load_concept_by_id(concept_id: str) -> Optional[dict]:
    """Read one concept row for the CLI (best-effort)."""
    try:
        from tools.db.storage import get_connection
        from tools.foundry.db.init_db import init_db

        init_db()
        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT id, run_id, name, slug, problem_statement, proposed_capability, "
                "target_users, novelty_score, market_score, fit_score, effort_estimate, "
                "compliance_risk, composite_score, status "
                "FROM foundry_concepts WHERE id = ?",
                (concept_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            return dict(row)
        except (TypeError, ValueError):
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not load concept %s: %s", concept_id, exc)
        return None


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACF → Strategos intel-channel fan-out bridge (CUI // SP-CTI)"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--concept-json", help="Concept as a JSON object")
    src.add_argument("--concept-id", help="Load the concept from foundry_concepts by id")
    parser.add_argument("--dry-run", action="store_true", help="Decide routing but write nothing")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    if args.concept_json:
        try:
            concept = json.loads(args.concept_json)
        except json.JSONDecodeError as exc:
            parser.error(f"invalid --concept-json: {exc}")
            return 2
    else:
        concept = _load_concept_by_id(args.concept_id)
        if concept is None:
            print(json.dumps({"error": f"concept not found: {args.concept_id}"}))
            return 1

    result = fan_out(concept, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        dr = " (DRY RUN)" if args.dry_run else ""
        print(f"\nACF -> Strategos Fan-Out{dr}")
        print(f"  Concept:   {result['concept_id']} ({result['slug']})")
        print(f"  Qualified: {result['qualified']}"
              + (f" - {result['skip_reason']}" if result["skip_reason"] else ""))
        for name in CHANNELS:
            ch = result["channels"][name]
            mark = "[x]" if ch.get("routed") else "[ ]"
            detail = ch.get("reason") or ch.get("error") or ch.get("id") or ""
            print(f"    {mark} {name:20s} {detail}")
        if result["errors"]:
            print("  Errors:")
            for e in result["errors"]:
                print(f"    ! {e['channel']}: {e['error']}")

    return 1 if result["errors"] else 0


if __name__ == "__main__":  # pragma: no cover
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    raise SystemExit(_main())
