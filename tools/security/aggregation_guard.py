#!/usr/bin/env python3
# CUI // SP-CTI
"""Aggregation Guard — classification-by-compilation / mosaic-effect detector (prop-sec-04..06).

Detects when two or more individually unclassified/low-sensitivity items,
combined within a single document/session, reveal something more sensitive
than any individual item (DoD 5200.01 / DoDM 5200.01 Vol 1 aggregation
guidance; 32 CFR 2002 CUI aggregation practice).

Rule-based, deterministic, no LLM judgment call in the enforcement path.
Rules are declared in args/classification_aggregation.yaml (prop-sec-03).

Public API:
    evaluate_rules(result_set, rules_file=None) -> [fired_rule, ...]
    compute_derived_classification(elements, *, ctx=None, rules_file=None) -> str
    guard_result(result_set, ctx=None, surface="") -> dict

CLI:
    python tools/security/aggregation_guard.py --evaluate-rules --json
    python tools/security/aggregation_guard.py --guard --surface "rfi/export" --json
    python tools/security/aggregation_guard.py --guard --surface "rfi/export" --gate --json
    python tools/security/aggregation_guard.py --events --limit 50 --json
    python tools/security/aggregation_guard.py --health --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Aliases for classification vocabulary mismatches between callers (e.g. MCP
# tool schemas using short forms) and tools.compliance.classification_manager's
# canonical VALID_CLASSIFICATIONS vocabulary.
_CLASSIFICATION_ALIASES = {
    "TS": "TOP SECRET",
    "TS//SCI": "TOP SECRET//SCI",
    "UNCLASSIFIED": "PUBLIC",
    "U": "PUBLIC",
}


def _normalize_classification(value: Optional[str]) -> str:
    if not value:
        return "CUI"
    v = value.strip().upper()
    return _CLASSIFICATION_ALIASES.get(v, v)


def _clearance_order(classification: str) -> int:
    from tools.compliance.classification_manager import get_clearance_order

    return get_clearance_order(_normalize_classification(classification))


# ---------------------------------------------------------------------------
# Rules config loading (cached)
# ---------------------------------------------------------------------------

_RULES_CACHE: Optional[Dict[str, Any]] = None
_RULES_CACHE_PATH: Optional[Path] = None


def _load_rules(rules_file: Optional[str] = None) -> Dict[str, Any]:
    global _RULES_CACHE, _RULES_CACHE_PATH
    path = Path(rules_file) if rules_file else BASE_DIR / "args" / "classification_aggregation.yaml"
    if _RULES_CACHE is not None and _RULES_CACHE_PATH == path:
        return _RULES_CACHE
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    _RULES_CACHE = config
    _RULES_CACHE_PATH = path
    return config


# ---------------------------------------------------------------------------
# Entity extraction (reuses the redaction subsystem, not reimplemented)
# ---------------------------------------------------------------------------

_DETECTOR = None


def _get_detector():
    """Lazy singleton RedactionDetector with Ollama NER disabled.

    None of this module's rule conditions need PERSON/ORGANIZATION NER
    (only regex/deny-list entity types: LOCATION*, PROGRAM_NAME,
    PROTECTED_ORG, AGENCY_NAME, DOD_CONTRACT, CAGE_CODE,
    SOLICITATION_NUMBER, DOLLAR_AMOUNT, ...), so NER is disabled for speed
    and determinism.

    * LOCATION detection depends on Presidio (optional, not air-gap-safe)
      being installed — same pre-existing limitation as the redaction
      subsystem; SCG-AGG-001 (the only rule using LOCATION) will not fire
      without it.
    """
    global _DETECTOR
    if _DETECTOR is None:
        from tools.redaction.detector import RedactionDetector

        _DETECTOR = RedactionDetector(use_ollama_ner=False)
    return _DETECTOR


def _entities_for_element(el: Dict[str, Any]) -> List[Dict[str, Any]]:
    if el.get("entities"):
        return el["entities"]
    text = el.get("text") or ""
    if not text.strip():
        return []
    detector = _get_detector()
    results = detector.detect(text)
    return [{"entity_type": r.entity_type, "score": r.score} for r in results]


# ---------------------------------------------------------------------------
# Condition matching
# ---------------------------------------------------------------------------


def _condition_hits(condition: Dict[str, Any], el: Dict[str, Any], entities: List[Dict[str, Any]]) -> bool:
    """Return True if a single rule condition is satisfied by one element."""
    kind = condition.get("kind")
    if kind == "entity_type":
        wanted = condition.get("entity_type")
        return any(e.get("entity_type") == wanted for e in entities)
    if kind == "keyword_list":
        terms = [t.lower() for t in condition.get("terms", [])]
        text = (el.get("text") or "").lower()
        return any(t in text for t in terms)
    if kind == "regex":
        pattern = condition.get("pattern", "")
        text = el.get("text") or ""
        try:
            return re.search(pattern, text, re.IGNORECASE) is not None
        except re.error:
            return False
    if kind == "field_present":
        field = condition.get("field")
        fields = el.get("fields", {}) or {}
        return field in fields and fields.get(field) not in (None, "")
    return False


def _match_spec_satisfied(match_spec: Any, hits: List[bool]) -> bool:
    if match_spec == "all":
        return all(hits)
    if isinstance(match_spec, dict) and "at_least" in match_spec:
        return sum(1 for h in hits if h) >= int(match_spec["at_least"])
    # Default: all conditions required
    return all(hits)


# ---------------------------------------------------------------------------
# Rule evaluation (document/session scope only — prop rules v1)
# ---------------------------------------------------------------------------


def evaluate_rules(result_set: List[Dict[str, Any]], rules_file: Optional[str] = None) -> List[Dict[str, Any]]:
    """Evaluate SCG aggregation rules against a result set (one document/session).

    result_set: list of element dicts, each optionally carrying:
        element_id, label, text, entities, fields, classification

    Returns a list of fired-rule dicts:
        {rule_id, description, citation, derive, action, severity,
         matched_conditions, matched_elements}
    """
    config = _load_rules(rules_file)
    fired: List[Dict[str, Any]] = []

    # Pre-compute entities per element once.
    element_entities = [(el, _entities_for_element(el)) for el in result_set]

    for rule in config.get("rules", []) or []:
        if rule.get("kind") != "entity_cooccurrence":
            continue
        conditions = rule.get("conditions", [])
        matched_condition_names: List[str] = []
        matched_element_ids: List[str] = []
        hits = []
        for cond in conditions:
            cond_hit = False
            for el, entities in element_entities:
                if _condition_hits(cond, el, entities):
                    cond_hit = True
                    eid = el.get("element_id") or el.get("label") or "?"
                    if eid not in matched_element_ids:
                        matched_element_ids.append(eid)
            hits.append(cond_hit)
            if cond_hit:
                matched_condition_names.append(cond.get("name") or cond.get("entity_type") or cond.get("kind"))

        if _match_spec_satisfied(rule.get("match", "all"), hits):
            fired.append(
                {
                    "rule_id": rule["id"],
                    "description": rule.get("description", "").strip(),
                    "citation": rule.get("citation", ""),
                    "derive": rule.get("derive", "CUI"),
                    "action": rule.get("action", "warn"),
                    "severity": rule.get("severity", "medium"),
                    "matched_conditions": matched_condition_names,
                    "matched_elements": matched_element_ids,
                }
            )

    # Structured field-co-occurrence rules — reuse the same condition/match
    # machinery over `fields` dicts (field_present conditions) rather than
    # detected entities. count_threshold / entity_diversity rule kinds are
    # cross-document/time-window by nature and are explicitly out of scope
    # for this document/session-scoped evaluator (see classification_aggregation.yaml
    # scope_controls) — not evaluated here.
    for rule in config.get("structured_rules", []) or []:
        if rule.get("kind") != "field_cooccurrence":
            continue
        when = rule.get("when", {})
        wanted_columns = when.get("columns", [])
        matched_element_ids = []
        hits = []
        for col in wanted_columns:
            col_hit = False
            for el in result_set:
                fields = el.get("fields", {}) or {}
                if col in fields and fields.get(col) not in (None, ""):
                    col_hit = True
                    eid = el.get("element_id") or el.get("label") or "?"
                    if eid not in matched_element_ids:
                        matched_element_ids.append(eid)
            hits.append(col_hit)
        operator = when.get("operator", "contains_all")
        satisfied = all(hits) if operator == "contains_all" else any(hits)
        if satisfied and wanted_columns:
            fired.append(
                {
                    "rule_id": rule["id"],
                    "description": rule.get("description", "").strip(),
                    "citation": rule.get("citation", ""),
                    "derive": rule.get("derive", "CUI"),
                    "action": rule.get("action", "warn"),
                    "severity": rule.get("severity", "medium"),
                    "matched_conditions": wanted_columns,
                    "matched_elements": matched_element_ids,
                }
            )

    return fired


def compute_derived_classification(
    elements: List[Dict[str, Any]], *, ctx: Optional[Dict[str, Any]] = None, rules_file: Optional[str] = None
) -> str:
    """max(each element's stored classification, each fired rule's derive)."""
    best = "PUBLIC"
    for el in elements:
        c = _normalize_classification(el.get("classification") or "CUI")
        if _clearance_order(c) > _clearance_order(best):
            best = c
    for rule in evaluate_rules(elements, rules_file=rules_file):
        c = _normalize_classification(rule.get("derive", "CUI"))
        if _clearance_order(c) > _clearance_order(best):
            best = c
    return best


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def _write_event(
    *,
    surface: str,
    rule_name: str,
    derived_classification: str,
    surface_ceiling: Optional[str],
    action: str,
    element_summary: str,
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    classification: str = "CUI",
) -> None:
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute(
        """INSERT INTO aggregation_events
           (id, occurred_at, user_id, tenant_id, surface, rule_name,
            derived_classification, surface_ceiling, action, element_summary, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            str(uuid.uuid4()),
            datetime.now(timezone.utc).isoformat(),
            user_id,
            tenant_id,
            surface,
            rule_name,
            derived_classification,
            surface_ceiling,
            action,
            element_summary,
            classification,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Volume/diversity throttle (per-user, best-effort — operates on the
# provided result_set only; does not itself poll a time-windowed DB query)
# ---------------------------------------------------------------------------


def _check_throttle(result_set: List[Dict[str, Any]], ctx: Dict[str, Any]) -> Tuple[bool, str]:
    """Best-effort volume/diversity throttle over the given result_set.

    Returns (throttled, reason). NO silent caps: callers must log/surface
    the reason whenever throttled is True (CLAUDE.md guardrail).
    """
    max_rows = ctx.get("max_rows_per_call")
    if max_rows and len(result_set) > int(max_rows):
        return True, f"result_set size {len(result_set)} exceeds max_rows_per_call={max_rows}"
    distinct_attr = ctx.get("diversity_attribute")
    max_distinct = ctx.get("max_distinct")
    if distinct_attr and max_distinct:
        values = {el.get("fields", {}).get(distinct_attr) for el in result_set if el.get("fields")}
        values.discard(None)
        if len(values) > int(max_distinct):
            return True, f"distinct {distinct_attr} count {len(values)} exceeds max_distinct={max_distinct}"
    return False, ""


# ---------------------------------------------------------------------------
# guard_result — the runtime gate
# ---------------------------------------------------------------------------


def guard_result(
    result_set: List[Dict[str, Any]], ctx: Optional[Dict[str, Any]] = None, surface: str = ""
) -> Dict[str, Any]:
    """Evaluate + gate a result set for a surface (route/pipeline).

    ctx: {surface_ceiling, clearance_level, user_id, tenant_id,
          max_rows_per_call, diversity_attribute, max_distinct}

    Returns {derived, action, fired_rules, throttled, throttle_reason, events_written}.
    action is 'derive' (no rule fired / within ceiling), 'warn', or 'block'
    (highest-severity action among fired rules that exceed the ceiling wins;
    block beats warn).
    """
    ctx = ctx or {}
    fired = evaluate_rules(result_set)
    derived = compute_derived_classification(result_set, ctx=ctx)

    surface_ceiling = ctx.get("surface_ceiling")
    ceiling_order = _clearance_order(surface_ceiling) if surface_ceiling else None
    clearance_order = _clearance_order(ctx.get("clearance_level")) if ctx.get("clearance_level") else None
    derived_order = _clearance_order(derived)

    exceeds_ceiling = ceiling_order is not None and derived_order > ceiling_order
    exceeds_clearance = clearance_order is not None and derived_order > clearance_order

    action = "derive"
    if exceeds_ceiling or exceeds_clearance:
        # Highest-severity action among the rules that actually fired wins;
        # if none fired but a stored element classification alone exceeds
        # the ceiling, default to block (safest).
        actions = [r["action"] for r in fired] or ["block"]
        action = "block" if "block" in actions else "warn"

    throttled, throttle_reason = _check_throttle(result_set, ctx)
    if throttled and action == "derive":
        action = "warn"

    events_written = 0
    element_summary = json.dumps(
        {"fired_rule_ids": [r["rule_id"] for r in fired], "element_ids": [el.get("element_id") for el in result_set]}
    )
    if fired or action != "derive":
        rule_name = ",".join(r["rule_id"] for r in fired) or ("throttle" if throttled else "")
        try:
            _write_event(
                surface=surface,
                rule_name=rule_name,
                derived_classification=derived,
                surface_ceiling=surface_ceiling,
                action=action,
                element_summary=element_summary,
                user_id=ctx.get("user_id"),
                tenant_id=ctx.get("tenant_id"),
                classification=derived,
            )
            events_written = 1
        except Exception:  # pragma: no cover - audit failure must not silently mask a security decision
            from tools.logging.icdev_logger import get_logger

            get_logger(__name__).exception("aggregation_guard: failed to write audit event")

    return {
        "derived": derived,
        "action": action,
        "fired_rules": fired,
        "throttled": throttled,
        "throttle_reason": throttle_reason,
        "events_written": events_written,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_events(limit: int) -> Dict[str, Any]:
    from tools.db.storage import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM aggregation_events ORDER BY occurred_at DESC LIMIT %s", (limit,)
    ).fetchall()
    return {"events": [dict(r) for r in rows], "count": len(rows)}


def _cli_health() -> Dict[str, Any]:
    try:
        config = _load_rules()
        n_rules = len(config.get("rules", [])) + len(config.get("structured_rules", []))
        return {"status": "ok", "rules_loaded": n_rules}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Aggregation Guard — mosaic-effect classification detector")
    ap.add_argument("--evaluate-rules", action="store_true")
    ap.add_argument("--guard", action="store_true")
    ap.add_argument("--surface", default="")
    ap.add_argument("--gate", action="store_true", help="Exit non-zero if action == block")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--result-set", help="Path to a JSON file with the result_set (for --evaluate-rules/--guard)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result_set = []
    if args.result_set:
        with open(args.result_set, encoding="utf-8") as f:
            result_set = json.load(f)

    if args.health:
        out = _cli_health()
    elif args.events:
        out = _cli_events(args.limit)
    elif args.evaluate_rules:
        out = {"fired_rules": evaluate_rules(result_set), "count": len(evaluate_rules(result_set))}
    elif args.guard:
        out = guard_result(result_set, ctx={}, surface=args.surface)
        if args.gate and out["action"] == "block":
            print(json.dumps(out, indent=2) if args.json else out)
            return 1
    else:
        ap.print_help()
        return 1

    print(json.dumps(out, indent=2) if args.json else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
