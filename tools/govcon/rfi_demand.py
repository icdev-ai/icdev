# CUI // SP-CTI
"""rfi_demand.py — RFI Capability-Gap -> Demand Signal -> Kanban SUGGESTED pipeline.

When ICDEV drafts an RFI response, some requirements / shall-statements describe
capabilities ICDEV does not yet have. Left implicit, those become lost sales signals.
This module turns each genuinely-unmet requirement into a durable **demand signal**,
aggregates the same gap across multiple RFIs (so recurring unmet needs rank higher),
and decomposes the top gaps into atomic, HITL-gated kanban SUGGESTED tasks.

Hybrid gap rule (both signals must agree, to avoid noise):
    (a) capability-catalog match is grade 'N' (coverage < 0.40) — ICDEV lacks the
        feature, judged deterministically by tools/govcon/capability_mapper.py; AND
    (b) the RFI Workbench LLM marked the requirement uncovered/partial — the drafted
        prose cannot cover it (rfi_workbench_sections.requirements[].covered).

A confirmed gap is:
    1. aggregated into rfi_capability_gaps (content_hash dedup, frequency/priority),
    2. emitted as an oracle_predictions gap node (provenance for the SUGGESTED cards
       and harvestable by the ACF Foundry), and
    3. decomposed into atomic SUGGESTED kanban tasks via the canonical task_factory.
       The Foundry independently harvests rfi_capability_gaps (see harvester.py) so
       recurring gaps also flow through its novelty gate — the "both" build pipeline.

Reuses (does NOT reimplement):
    capability_mapper.map_pattern_to_capabilities / load_capability_catalog — grading
    requirement_extractor._extract_keywords / _classify_domain          — keywords
    kanban.task_factory.create_tasks                                    — SUGGESTED cards
    the oracle_predictions gap-node shape from awareness.gap_detector

All runtime SQL is PostgreSQL-authored; JSON columns are parsed in Python (no
json_extract at call sites). Reads/writes go through tools.db.storage.get_connection.

CLI:
    python tools/govcon/rfi_demand.py --section <section_id> --json
    python tools/govcon/rfi_demand.py --scan --json      # re-process open gaps
    python tools/govcon/rfi_demand.py --list --json      # list demand signals
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.rfi_demand")

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"

# Runtime demand-generated kanban tasks use this prefix (distinct from the build
# project prefix `rfidem-`), so they never collide with, or inflate, the roadmap
# project that ships this pipeline.
_TASK_PREFIX = "rfigap-"

_DEFAULTS = {
    "enabled": True,
    "gap_grade": "N",
    "min_priority_for_task": 0.6,
    "high_demand_frequency": 3,
    "decompose_route": "proposal_drafting",
    "max_tasks_per_gap": 8,
}


# ── config ────────────────────────────────────────────────────────────


def _load_config() -> dict:
    try:
        import yaml

        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = (yaml.safe_load(f) or {}).get("rfi_demand", {}) or {}
    except Exception:
        cfg = {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if v is not None})
    return merged


def is_enabled() -> bool:
    """Env override wins so operators can disable without editing YAML."""
    env = os.environ.get("ICDEV_RFI_DEMAND_ENABLED")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return bool(_load_config().get("enabled", True))


# ── small helpers ─────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _keywords_for(text: str) -> list[str]:
    """Reuse the govcon requirement keyword extractor (falls back to naive tokens)."""
    try:
        from tools.govcon.requirement_extractor import _extract_keywords

        kws = _extract_keywords(text or "")
        if kws:
            return kws
    except Exception:
        pass
    return sorted({t for t in (text or "").lower().split() if len(t) >= 3})[:15]


def _domain_for(text: str, keywords: list[str]) -> str:
    try:
        from tools.govcon.requirement_extractor import _classify_domain

        return _classify_domain(text or "", keywords, {}) or "other"
    except Exception:
        return "other"


def gap_hash(keywords: list[str], fallback_text: str = "") -> str:
    """Content-addressable id that collapses the SAME capability need across RFIs.

    Hashes the sorted, de-duplicated keyword set so differently-worded requirements
    that share a capability fingerprint aggregate into one demand signal. Falls back
    to normalized text when a requirement yields no keywords.
    """
    canonical = "|".join(sorted({k.lower().strip() for k in keywords if k}))
    if not canonical:
        canonical = " ".join((fallback_text or "").lower().split())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# ── detection (rfidem-det-01) ─────────────────────────────────────────

_UNCOVERED = (False, "partial")


def grade_requirement(text: str, catalog=None) -> dict:
    """Grade one requirement against the ICDEV capability catalog.

    Returns {grade, coverage, capability_id, capability_name, keywords}. A requirement
    that matches nothing grades 'N' with coverage 0.0 (the strongest possible gap).
    """
    from tools.govcon.capability_mapper import (
        load_capability_catalog,
        map_pattern_to_capabilities,
    )

    if catalog is None:
        catalog = load_capability_catalog()
    keywords = _keywords_for(text)
    matches = map_pattern_to_capabilities({"keywords": keywords}, catalog or [])
    if matches:
        best = matches[0]
        return {
            "grade": best["grade"],
            "coverage": best["score"],
            "capability_id": best["capability_id"],
            "capability_name": best["capability_name"],
            "keywords": keywords,
        }
    return {
        "grade": "N",
        "coverage": 0.0,
        "capability_id": None,
        "capability_name": None,
        "keywords": keywords,
    }


def is_gap(requirement: dict, grade_result: dict, gap_grade: str = "N") -> bool:
    """Hybrid rule: capability grade == gap_grade AND workbench marked it uncovered.

    `covered is None` (not yet judged) is deliberately NOT a gap — we wait for the
    workbench coverage pass so a signal is only raised when both judgments agree.
    """
    return (
        grade_result.get("grade") == gap_grade
        and requirement.get("covered") in _UNCOVERED
    )


def detect_gaps_for_section(section_id: str) -> list[dict]:
    """Pure detection: return the confirmed capability gaps for one workbench section.

    Does not write anything — see aggregate_gap / process_section for persistence.
    """
    from tools.govcon.capability_mapper import load_capability_catalog
    from tools.govcon.rfi_workbench import get_requirements, get_section

    cfg = _load_config()
    section = get_section(section_id)
    if not section:
        return []
    catalog = load_capability_catalog()
    gaps: list[dict] = []
    for req in get_requirements(section_id):
        text = (req.get("text") or "").strip()
        if not text:
            continue
        gr = grade_requirement(text, catalog)
        if is_gap(req, gr, cfg["gap_grade"]):
            gaps.append(
                {
                    "requirement_id": req.get("id"),
                    "capability_need": text,
                    "keywords": gr["keywords"],
                    "domain": _domain_for(text, gr["keywords"]),
                    "coverage": gr["coverage"],
                    "nearest_capability": gr["capability_name"],
                }
            )
    return gaps


# ── aggregation (rfidem-det-03) ───────────────────────────────────────


def _compute_priority(frequency: int, best_coverage: float) -> float:
    return round(float(frequency) * (1.0 - float(best_coverage)), 4)


def aggregate_gap(gap: dict, rfi_ref: str, conn=None) -> str:
    """Upsert a confirmed gap into rfi_capability_gaps; return its content_hash.

    Idempotent per (gap, rfi_ref): a repeated ref does not double-count frequency, but
    a NEW ref (a different RFI/section raising the same capability need) increments
    frequency and recomputes priority so recurring unmet needs rank higher.
    """
    cfg = _load_config()
    keywords = gap.get("keywords") or _keywords_for(gap.get("capability_need", ""))
    h = gap_hash(keywords, gap.get("capability_need", ""))
    coverage = float(gap.get("coverage") or 0.0)
    ref = str(rfi_ref or "")

    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT frequency, best_coverage, rfi_refs FROM rfi_capability_gaps "
            "WHERE content_hash=%s",
            (h,),
        ).fetchone()

        if row is None:
            frequency = 1
            best_cov = coverage
            refs = [ref] if ref else []
            priority = _compute_priority(frequency, best_cov)
            high = 1 if frequency >= cfg["high_demand_frequency"] else 0
            conn.execute(
                "INSERT INTO rfi_capability_gaps "
                "(content_hash, capability_need, keywords, domain, frequency, velocity, "
                " best_coverage, priority, is_high_demand, rfi_refs, prediction_id, "
                " status, first_seen, last_seen, metadata, classification) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    h,
                    gap.get("capability_need", "")[:2000],
                    json.dumps(keywords),
                    gap.get("domain", "other"),
                    frequency,
                    0.0,
                    best_cov,
                    priority,
                    high,
                    json.dumps(refs),
                    None,
                    "open",
                    _now(),
                    _now(),
                    json.dumps({"nearest_capability": gap.get("nearest_capability")}),
                    "CUI // SP-CTI",
                ),
            )
        else:
            d = dict(row) if hasattr(row, "keys") else {
                "frequency": row[0],
                "best_coverage": row[1],
                "rfi_refs": row[2],
            }
            try:
                refs = json.loads(d.get("rfi_refs") or "[]")
            except (json.JSONDecodeError, TypeError):
                refs = []
            if ref and ref in refs:
                # Same ref re-processed — no change to demand strength.
                if _close:
                    conn.commit()
                return h
            if ref:
                refs.append(ref)
            frequency = len(refs) if refs else int(d.get("frequency") or 1)
            # best_coverage tracks the STRONGEST match ICDEV has for this need.
            best_cov = max(float(d.get("best_coverage") or 0.0), coverage)
            priority = _compute_priority(frequency, best_cov)
            high = 1 if frequency >= cfg["high_demand_frequency"] else 0
            conn.execute(
                "UPDATE rfi_capability_gaps SET frequency=%s, best_coverage=%s, "
                "priority=%s, is_high_demand=%s, rfi_refs=%s, last_seen=%s "
                "WHERE content_hash=%s",
                (frequency, best_cov, priority, high, json.dumps(refs), _now(), h),
            )
        if _close:
            conn.commit()
        return h
    finally:
        if _close:
            conn.close()


# ── emit prediction node (rfidem-emit-01) ─────────────────────────────


def _severity_for(priority: float, is_high_demand: int) -> str:
    if is_high_demand or priority >= 2.0:
        return "high"
    if priority >= 1.0:
        return "medium"
    return "low"


def emit_gap_prediction(content_hash: str, conn=None) -> str | None:
    """Write an idempotent oracle_predictions gap node for a demand signal.

    The node is the provenance anchor for the SUGGESTED cards (source_prediction_id)
    and a harvest surface. Stable id `op-gap-rfi-<hash>` — re-emitting is a no-op.
    """
    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT capability_need, keywords, domain, frequency, best_coverage, "
            "priority, is_high_demand, rfi_refs, prediction_id "
            "FROM rfi_capability_gaps WHERE content_hash=%s",
            (content_hash,),
        ).fetchone()
        if row is None:
            return None
        g = dict(row) if hasattr(row, "keys") else {
            "capability_need": row[0], "keywords": row[1], "domain": row[2],
            "frequency": row[3], "best_coverage": row[4], "priority": row[5],
            "is_high_demand": row[6], "rfi_refs": row[7], "prediction_id": row[8],
        }
        if g.get("prediction_id"):
            return g["prediction_id"]

        pred_id = f"op-gap-rfi-{content_hash[:16]}"
        exists = conn.execute(
            "SELECT id FROM oracle_predictions WHERE id=%s", (pred_id,)
        ).fetchone()
        if not exists:
            priority = float(g.get("priority") or 0.0)
            best_cov = float(g.get("best_coverage") or 0.0)
            try:
                kws = json.loads(g.get("keywords") or "[]")
            except (json.JSONDecodeError, TypeError):
                kws = []
            evidence = {
                "capability_need": g.get("capability_need"),
                "keywords": kws,
                "domain": g.get("domain"),
                "frequency": g.get("frequency"),
                "best_coverage": best_cov,
                "priority": priority,
            }
            conn.execute(
                "INSERT INTO oracle_predictions "
                "(id, lens_id, lens_name, prediction_text, confidence, created_at, "
                " subject_type, subject_id, prediction_type, severity, horizon_days, "
                " evidence_json, classification) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    pred_id,
                    "rfi_demand",
                    "rfi_demand",
                    f"RFI capability gap: {(g.get('capability_need') or '')[:180]}",
                    round(1.0 - best_cov, 4),
                    _now(),
                    "gap",
                    content_hash,
                    "gap::rfi_capability",
                    _severity_for(priority, int(g.get("is_high_demand") or 0)),
                    0,
                    json.dumps(evidence, ensure_ascii=False),
                    "CUI // SP-CTI",
                ),
            )
        conn.execute(
            "UPDATE rfi_capability_gaps SET prediction_id=%s WHERE content_hash=%s",
            (pred_id, content_hash),
        )
        if _close:
            conn.commit()
        return pred_id
    finally:
        if _close:
            conn.close()


# ── decompose to atomic SUGGESTED tasks (rfidem-emit-02/03) ───────────


def _llm_decompose(capability_need: str, route: str, max_tasks: int) -> list[dict]:
    """Ask the LLM for atomic build specs. Returns [] on any failure (caller falls
    back to a single deterministic task so the direct path always yields >=1 card)."""
    try:
        from tools.govcon.rfi_workbench import _get_router
        from tools.llm.router import LLMRequest

        router = _get_router()
        if not router:
            return []
        prompt = (
            "You are a senior engineer planning atomic build tasks to add a missing "
            "product capability. Break the capability below into 3 to "
            f"{max_tasks} atomic, independently-shippable tasks. Return ONLY a JSON "
            "array; each item {\"title\": str (<=80 chars, imperative), "
            "\"description\": str (what & why, acceptance criteria)}.\n\n"
            f"Capability to build: {capability_need}"
        )
        resp = router.invoke(route, LLMRequest(messages=[{"role": "user", "content": prompt}], max_tokens=800))
        raw = resp.content if hasattr(resp, "content") else str(resp)
        import re

        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE).strip().rstrip("`").strip()
        start, end = raw.find("["), raw.rfind("]")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
        items = json.loads(raw)
        out = []
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get("title"):
                out.append({"title": str(it["title"])[:120], "description": str(it.get("description", ""))})
        return out[:max_tasks]
    except Exception as exc:
        logger.warning("LLM decomposition failed: %s — using deterministic fallback", exc)
        return []


def decompose_gap_to_tasks(content_hash: str) -> list[str]:
    """Emit atomic SUGGESTED kanban cards for a demand signal (gated by priority).

    Idempotent via rfi_gap_task_links + task_factory idempotency keys — re-running a
    gap never duplicates cards. Records every (gap -> task) link with route='direct'.

    Self-manages connections in three phases (read -> create -> link). The canonical
    task_factory.create_tasks opens its OWN connection, so this function must not hold
    another open connection across it — on SQLite that would deadlock the writer.
    """
    from tools.kanban.task_factory import create_tasks

    cfg = _load_config()

    # ── Phase 1: read gap + short-circuit checks (short-lived read connection) ──
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT capability_need, priority, prediction_id, keywords, domain "
            "FROM rfi_capability_gaps WHERE content_hash=%s",
            (content_hash,),
        ).fetchone()
        if row is None:
            return []
        g = dict(row) if hasattr(row, "keys") else {
            "capability_need": row[0], "priority": row[1],
            "prediction_id": row[2], "keywords": row[3], "domain": row[4],
        }
        if float(g.get("priority") or 0.0) < float(cfg["min_priority_for_task"]):
            logger.info("gap %s below task threshold (priority=%s)", content_hash[:8], g.get("priority"))
            return []
        # Already emitted? (append-only links are the source of truth)
        linked = conn.execute(
            "SELECT task_id FROM rfi_gap_task_links WHERE gap_hash=%s AND route='direct'",
            (content_hash,),
        ).fetchall()
        if linked:
            return [(r[0] if not hasattr(r, "keys") else r["task_id"]) for r in linked]
    finally:
        conn.close()

    # ── Phase 2: decompose + create tasks (create_tasks owns its connection) ──
    need = g.get("capability_need") or "unspecified capability"
    specs = _llm_decompose(need, cfg["decompose_route"], int(cfg["max_tasks_per_gap"]))
    if not specs:
        specs = [{
            "title": f"Build capability: {need[:70]}",
            "description": (
                f"Add ICDEV capability to satisfy the RFI requirement: {need}\n\n"
                "This card was auto-generated from an RFI capability gap (demand "
                "signal). Decompose further on pickup. Acceptance: the requirement "
                "grades L/M against the capability catalog once built."
            ),
        }]

    short = content_hash[:8]
    pred_id = g.get("prediction_id")
    high_priority = float(g.get("priority") or 0) >= 2.0
    task_specs = []
    for i, s in enumerate(specs, start=1):
        task_specs.append({
            "id": f"{_TASK_PREFIX}{short}-{i:02d}",
            "title": s["title"],
            "description": s["description"],
            "task_type": "build",
            "priority": "high" if high_priority else "medium",
            "status": "suggested",
            "source_prediction_id": pred_id,
            "dispatch_source": "rfi_demand",
            "idempotency_key": f"{content_hash}:{i:02d}",
        })

    created = create_tasks(task_specs)

    # ── Phase 3: record provenance + mark emitted (short-lived write connection) ──
    conn = get_connection()
    try:
        for s in task_specs:
            try:
                conn.execute(
                    "INSERT INTO rfi_gap_task_links (id, gap_hash, task_id, route, "
                    "emitted_at, classification) VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), content_hash, s["id"], "direct", _now(), "CUI // SP-CTI"),
                )
            except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # UNIQUE(gap_hash, task_id) — link already recorded.
                logger.warning(
                    "decompose_gap_to_tasks: best-effort INSERT into rfi_gap_task_links failed (non-blocking): %s",
                    exc,
                )
        conn.execute(
            "UPDATE rfi_capability_gaps SET status='emitted' WHERE content_hash=%s",
            (content_hash,),
        )
        conn.commit()
    finally:
        conn.close()
    return created or [s["id"] for s in task_specs]


# ── orchestration ─────────────────────────────────────────────────────


def process_section(section_id: str, rfi_ref: str | None = None) -> dict:
    """Full per-section pipeline: detect -> aggregate -> emit -> decompose.

    Called from the workbench background coverage step. Never raises — a failure here
    must not break RFI drafting; it returns a summary with an `error` key instead.
    """
    if not is_enabled():
        return {"enabled": False, "gaps": 0}
    try:
        from tools.govcon.rfi_workbench import get_section

        section = get_section(section_id)
        ref = rfi_ref or (section or {}).get("session_id") or section_id
        gaps = detect_gaps_for_section(section_id)
        emitted, tasks = [], []
        # Each step self-manages its own short-lived connection (no shared handle):
        # decompose calls task_factory which opens its own connection, so holding one
        # here would deadlock the SQLite writer.
        for gap in gaps:
            h = aggregate_gap(gap, ref)
            pred = emit_gap_prediction(h)
            created = decompose_gap_to_tasks(h)
            emitted.append({"gap_hash": h, "prediction_id": pred, "tasks": created})
            tasks.extend(created)
        return {"enabled": True, "section_id": section_id, "gaps": len(gaps),
                "tasks_created": len(tasks), "detail": emitted}
    except Exception as exc:
        logger.warning("rfi_demand.process_section failed for %s: %s", section_id, exc)
        return {"enabled": True, "error": str(exc), "gaps": 0}


def scan(min_priority: float | None = None) -> dict:
    """Re-process open demand signals that have no cards yet (e.g. after a threshold
    change or a Foundry-only gap). Emits prediction + SUGGESTED cards where eligible."""
    cfg = _load_config()
    threshold = cfg["min_priority_for_task"] if min_priority is None else min_priority
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT content_hash FROM rfi_capability_gaps "
            "WHERE status='open' AND priority >= %s ORDER BY priority DESC",
            (threshold,),
        ).fetchall()
        hashes = [(r[0] if not hasattr(r, "keys") else r["content_hash"]) for r in rows]
    finally:
        conn.close()
    processed = []
    for h in hashes:
        emit_gap_prediction(h)
        created = decompose_gap_to_tasks(h)
        processed.append({"gap_hash": h, "tasks": created})
    return {"processed": len(processed), "detail": processed}


def list_demand_signals(limit: int = 50) -> list[dict]:
    """Cross-RFI demand rollup (highest priority first) for UI / MCP surfaces."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT content_hash, capability_need, domain, frequency, best_coverage, "
            "priority, is_high_demand, status, rfi_refs FROM rfi_capability_gaps "
            "ORDER BY priority DESC LIMIT %s",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {}
        if not d:
            (ch, need, dom, freq, cov, prio, high, st, refs) = r
            d = {"content_hash": ch, "capability_need": need, "domain": dom,
                 "frequency": freq, "best_coverage": cov, "priority": prio,
                 "is_high_demand": high, "status": st, "rfi_refs": refs}
        try:
            d["rfi_refs"] = json.loads(d.get("rfi_refs") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["rfi_refs"] = []
        out.append(d)
    return out


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RFI capability-gap demand pipeline")
    ap.add_argument("--section", help="Process one workbench section id")
    ap.add_argument("--scan", action="store_true", help="Re-process open demand signals")
    ap.add_argument("--list", action="store_true", help="List demand signals")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.section:
        result = process_section(args.section)
    elif args.scan:
        result = scan()
    elif args.list:
        result = {"signals": list_demand_signals()}
    else:
        ap.print_help()
        return 1

    print(json.dumps(result, indent=None if args.json else 2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
