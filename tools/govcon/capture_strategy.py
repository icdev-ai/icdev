# CUI // SP-CTI
"""Capture strategy and win themes — the message architecture for generated prose.

Every RFI part and proposal section is drafted independently, each with its own
prompt and evidence. Without a shared strategy they read as separately-authored
documents. This module resolves one strategy per pursuit and renders it into each
generation prompt, so the whole response carries a single message.

Resolution order (later wins):
    1. Company-wide strategy — args/govcon/capture_strategy.yaml, else the active
       `capture_strategy` row. Re-read on every call so dashboard edits are hot.
    2. Per-opportunity overrides — `pg_win_themes` (the existing registry) plus the
       free-text `proposal_opportunities.win_themes` / `.key_discriminators`.

Themes are NOT injected into every section. Part 1 is administrative fact (entity
data, CAGE code, FOCI) and Part 4.2 is a cost table; positioning there reads as
marketing pollution to an evaluator. Part 6 is questions *to* the Government, so the
strategy selects which questions to ask rather than asserting our capabilities. See
PART_THEME_POLICY.

A theme with no supporting evidence renders an instruction to emit the literal
token `[VERIFY]`, which the existing export placeholder gate
(`rfi_workbench._check_placeholder_gate` -> `find_placeholders`) already blocks. The
token must be bare and uppercase: the placeholder regex is
`\\[([A-Z][A-Z0-9 _/&#.-]{1,40})\\]`, so a form like `[VERIFY: foo]` would slip
through the gate unnoticed.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.capture_strategy")

_STRATEGY_YAML = _ROOT / "args" / "govcon" / "capture_strategy.yaml"

# The literal token the RFI export gate looks for. Must satisfy
# content_grounding._PLACEHOLDER_RE (uppercase, no colon).
VERIFY_TOKEN = "[VERIFY]"

# How each section carries the strategy, keyed by item_number prefix.
#   none      — do not mention positioning at all
#   full      — weave themes, discriminators and proof points
#   risk      — themes framed as risk retirement
#   light     — a single relevant discriminator, no theme stack
#   questions — strategy shapes which questions we ask; assert nothing
_FULL, _NONE, _RISK, _LIGHT, _QUESTIONS = "full", "none", "risk", "light", "questions"

# Public alias for callers with no RFI item number (e.g. proposal shall-statement drafts).
MODE_FULL = _FULL

PART_THEME_POLICY: dict[str, str] = {
    "1": _NONE,        # Administrative — entity data, CAGE, FOCI, clearances
    "2": _FULL,        # Technical approach — the primary theme battleground
    "3": _RISK,        # Feasibility, schedule, risk
    "4.1": _LIGHT,     # Data rights — IP-posture discriminator only
    "4.2": _NONE,      # ROM cost table — numbers only
    "4.3": _LIGHT,     # Teaming / cost share — IR&D discriminator
    "5": _FULL,        # Industry insights — highest theme density
    "6": _QUESTIONS,   # Questions TO the Government
    "A": _FULL,        # Appendix — technical proof
    "B": _FULL,
}

_PART_INSTRUCTIONS = {
    _FULL: (
        "Weave the win themes above into this section's argument. Lead with the customer "
        "benefit, support it with the discriminator, and close on the proof point. Do not "
        "restate a theme verbatim more than once."
    ),
    _RISK: (
        "Frame the win themes as risk retirement: for each risk you identify, show how the "
        "discriminator retires it earlier or more cheaply than the alternative."
    ),
    _LIGHT: (
        "Reference at most one discriminator, only where it is directly relevant. Do not "
        "introduce the full theme stack here."
    ),
    _QUESTIONS: (
        "This section contains questions addressed TO the Government. Use the strategy only "
        "to decide WHICH questions sharpen our position. Do NOT assert, imply, or advertise "
        "our capabilities inside a question."
    ),
}

_EMPTY: dict = {
    "golden_thread": "",
    "win_themes": [],
    "discriminators": [],
    "proof_points": [],
    "ghosting": [],
    "hot_buttons": [],
}

_JSON_FIELDS = ("win_themes", "discriminators", "proof_points", "ghosting", "hot_buttons")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def policy_for(item_number: str) -> str:
    """Return the theme-treatment policy for a section item number ('2.4' -> 'full')."""
    if not item_number:
        return _NONE
    item = str(item_number).strip()
    # Most specific first: '4.2' must beat '4'.
    if item in PART_THEME_POLICY:
        return PART_THEME_POLICY[item]
    for key in sorted(PART_THEME_POLICY, key=len, reverse=True):
        if item.startswith(key):
            return PART_THEME_POLICY[key]
    return _NONE


# ── Company-wide strategy ─────────────────────────────────────────────────────

def _load_yaml_strategy() -> dict:
    if not _STRATEGY_YAML.exists():
        return {}
    try:
        import yaml

        with open(_STRATEGY_YAML, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("Could not load capture_strategy.yaml: %s", exc)
        return {}


def _coerce(raw: dict) -> dict:
    """Normalise a strategy dict: JSON-decode text columns, fill missing keys."""
    out = dict(_EMPTY)
    out.update({k: v for k, v in raw.items() if k in _EMPTY})
    for field in _JSON_FIELDS:
        value = out.get(field)
        if isinstance(value, str):
            try:
                out[field] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                out[field] = []
        elif value is None:
            out[field] = []
    out["golden_thread"] = out.get("golden_thread") or ""
    return out


def get_company_strategy() -> dict:
    """Return the company-wide capture strategy. YAML wins over DB when present."""
    from_yaml = _load_yaml_strategy()
    if from_yaml:
        return _coerce(from_yaml)

    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM capture_strategy WHERE is_active = %s ORDER BY updated_at DESC LIMIT 1",
            (1,),
        ).fetchone()
        return _coerce(dict(row)) if row else dict(_EMPTY)
    except Exception as exc:
        logger.warning("Could not load capture strategy from DB: %s", exc)
        return dict(_EMPTY)


def save_company_strategy(strategy: dict, actor: str = "dashboard") -> dict:
    """Persist a new active strategy revision and audit it.

    The previous row is deactivated rather than updated, so `audit_trail` plus the
    superseded rows form a readable history without making this an append-only table.
    """
    from tools.db.storage import get_connection

    payload = _coerce(strategy)
    conn = get_connection()
    conn.execute("UPDATE capture_strategy SET is_active = %s WHERE is_active = %s", (0, 1))
    row_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO capture_strategy "
        "(id, name, is_active, golden_thread, win_themes, discriminators, proof_points, "
        " ghosting, hot_buttons, updated_by, created_at, updated_at, classification) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            row_id,
            strategy.get("name", "default"),
            1,
            payload["golden_thread"],
            *[json.dumps(payload[f]) for f in _JSON_FIELDS],
            actor,
            _now(),
            _now(),
            "CUI",
        ),
    )
    _audit(conn, "save_company_strategy", f"revision {row_id}", actor)
    conn.commit()
    return {"status": "ok", "id": row_id}


def _audit(conn, action: str, details: str = "", actor: str = "capture_strategy") -> None:
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (_now(), "govcon.capture_strategy", actor, action, details, "capture_strategy"),
        )
    except Exception as exc:  # audit must never break the action
        logger.warning("capture_strategy audit write failed: %s", exc)


# ── Per-opportunity resolution ────────────────────────────────────────────────

def _resolve_opportunity_id(conn, opportunity_id: str) -> str:
    """Map whatever id we were handed onto proposal_opportunities.id.

    pg_win_themes.opportunity_id is a FK to proposal_opportunities(id), but
    response_drafter carries `sam_opportunity_id` from rfp_shall_statements. Passing
    the SAM id straight through matched nothing and silently yielded zero themes.
    """
    if not opportunity_id:
        return ""
    row = conn.execute(
        "SELECT id FROM proposal_opportunities WHERE id = %s", (opportunity_id,)
    ).fetchone()
    if row:
        return opportunity_id
    row = conn.execute(
        "SELECT id FROM proposal_opportunities WHERE solicitation_number = %s", (opportunity_id,)
    ).fetchone()
    if row:
        return dict(row)["id"]
    logger.warning("No proposal_opportunities row for id/solicitation %r", opportunity_id)
    return ""


def _session_opportunity_id(session_id: str) -> str:
    try:
        from tools.db.storage import get_canvas_connection

        db = get_canvas_connection("ICDEV_DB_URL")
        row = db.execute(
            "SELECT opportunity_id FROM rfi_workbench_sessions WHERE id = %s", (session_id,)
        ).fetchone()
        return (dict(row).get("opportunity_id") or "") if row else ""
    except Exception as exc:
        logger.warning("Could not read opportunity_id for session %s: %s", session_id, exc)
        return ""


def _split_free_text(value: str) -> list[dict]:
    """Turn a newline/semicolon-separated capture field into theme dicts."""
    if not value:
        return []
    parts = [p.strip(" -•\t") for chunk in str(value).split("\n") for p in chunk.split(";")]
    return [{"statement": p, "evidence": ""} for p in parts if p]


def resolve_strategy(opportunity_id: str = "", session_id: str = "") -> dict:
    """Return the effective strategy: company base merged with per-pursuit overrides.

    Accepts either a proposal_opportunities.id or a SAM solicitation number. An RFI
    session resolves its opportunity via rfi_workbench_sessions.opportunity_id; an
    unlinked session simply uses the global strategy.
    """
    base = get_company_strategy()

    if not opportunity_id and session_id:
        opportunity_id = _session_opportunity_id(session_id)
    if not opportunity_id:
        return base

    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        resolved = _resolve_opportunity_id(conn, opportunity_id)
        if not resolved:
            return base

        merged = {k: list(v) if isinstance(v, list) else v for k, v in base.items()}

        themes = conn.execute(
            "SELECT theme_type, theme_statement, supporting_evidence, target_eval_factor, priority "
            "FROM pg_win_themes WHERE opportunity_id = %s AND status = %s ORDER BY priority",
            (resolved, "active"),
        ).fetchall()

        buckets: dict[str, list] = {"win_theme": [], "discriminator": [], "ghost_strategy": []}
        for raw in themes:
            theme = dict(raw)
            entry = {
                "statement": theme.get("theme_statement", ""),
                "evidence": theme.get("supporting_evidence") or "",
                "target_eval_factor": theme.get("target_eval_factor") or "",
                "priority": theme.get("priority") or 1,
            }
            buckets.get(theme.get("theme_type", ""), []).append(entry)

        # Registry entries override the company defaults for this pursuit.
        if buckets["win_theme"]:
            merged["win_themes"] = buckets["win_theme"]
        if buckets["discriminator"]:
            merged["discriminators"] = buckets["discriminator"]
        if buckets["ghost_strategy"]:
            merged["ghosting"] = buckets["ghost_strategy"]

        opp = conn.execute(
            "SELECT win_themes, key_discriminators FROM proposal_opportunities WHERE id = %s",
            (resolved,),
        ).fetchone()
        if opp:
            opp = dict(opp)
            merged["win_themes"] = merged["win_themes"] + _split_free_text(opp.get("win_themes"))
            merged["discriminators"] = merged["discriminators"] + _split_free_text(
                opp.get("key_discriminators")
            )
        return merged
    except Exception as exc:
        logger.warning("Could not resolve overrides for opportunity %s: %s", opportunity_id, exc)
        return base


# ── Prompt block ──────────────────────────────────────────────────────────────

def _render_theme(entry: dict) -> str:
    statement = (entry.get("statement") or "").strip()
    evidence = (entry.get("evidence") or "").strip()
    factor = (entry.get("target_eval_factor") or "").strip()
    line = f"  - {statement}"
    if factor:
        line += f" (targets evaluation factor: {factor})"
    if evidence:
        line += f"\n    Proof: {evidence}"
    else:
        line += (
            f"\n    Proof: NONE ON FILE. Write the literal token {VERIFY_TOKEN} where the proof "
            "point belongs. Do not invent a metric, a program name, or a customer."
        )
    return line


def build_strategy_block(strategy: dict, item_number: str = "", mode: str = "") -> str:
    """Render the [CAPTURE STRATEGY] prompt block for one section.

    Returns "" for sections where positioning does not belong (Part 1, Part 4.2).

    `mode` overrides the item-number lookup. Proposal drafts answer shall statements
    and carry no RFI item number, so they pass mode="full" explicitly rather than
    borrowing an unrelated part number.
    """
    mode = mode or policy_for(item_number)
    if mode == _NONE or not strategy:
        return ""

    themes = strategy.get("win_themes") or []
    discriminators = strategy.get("discriminators") or []
    ghosting = strategy.get("ghosting") or []
    hot_buttons = strategy.get("hot_buttons") or []
    golden = (strategy.get("golden_thread") or "").strip()

    if not (themes or discriminators or golden):
        return ""

    lines = ["[CAPTURE STRATEGY — every section of this response must carry one message]"]

    if golden:
        lines.append(f"Golden thread (this exact idea must be recognisable in this section): {golden}")

    if mode == _QUESTIONS:
        lines.append("")
        lines.append("Our positioning, for choosing which questions to ask:")
        for entry in themes[:5]:
            lines.append(f"  - {(entry.get('statement') or '').strip()}")
        lines.append("")
        lines.append(_PART_INSTRUCTIONS[_QUESTIONS])
        return "\n".join(lines)

    if themes and mode in (_FULL, _RISK):
        lines.append("")
        lines.append("Win themes (ordered by priority):")
        lines.extend(_render_theme(t) for t in themes[:5])

    if discriminators:
        lines.append("")
        limit = 1 if mode == _LIGHT else 5
        lines.append("Discriminators:")
        lines.extend(_render_theme(d) for d in discriminators[:limit])

    if ghosting and mode == _FULL:
        lines.append("")
        lines.append(
            "Ghosting — argue why these alternative approaches fall short. NEVER name a "
            "competitor; differentiate on merit only:"
        )
        lines.extend(f"  - {(g.get('statement') or '').strip()}" for g in ghosting[:4])

    if hot_buttons and mode in (_FULL, _RISK):
        lines.append("")
        lines.append(f"Customer hot buttons: {', '.join(str(h) for h in hot_buttons[:6])}")

    lines.append("")
    lines.append(_PART_INSTRUCTIONS[mode])
    return "\n".join(lines)


# ── Coverage report (advisory) ────────────────────────────────────────────────

def _themes_for_scoring(strategy: dict) -> list[dict]:
    """Flatten a strategy into the theme dicts check_theme_presence expects."""
    scored = []
    for kind in ("win_themes", "discriminators"):
        for index, entry in enumerate(strategy.get(kind) or []):
            statement = (entry.get("statement") or "").strip()
            if statement:
                scored.append(
                    {
                        "id": entry.get("id") or f"{kind}:{index}",
                        "theme_type": "win_theme" if kind == "win_themes" else "discriminator",
                        "theme_statement": statement,
                    }
                )
    return scored


def theme_coverage(sections: list[dict], strategy: dict) -> dict:
    """Report which themes actually landed in which section.

    Advisory: never blocks. Delegates matching to the existing deterministic scorer
    in win_theme_manager rather than re-implementing keyword density.

    Returns {"score": int, "findings": [...], "matrix": {theme_id: [item_number, ...]}}
    mirroring rfi_style_engine.check_style_compliance so the existing findings UI
    can render it unchanged.
    """
    from tools.govcon.win_theme_manager import check_theme_presence

    themes = _themes_for_scoring(strategy)
    if not themes:
        return {"score": 100, "findings": [], "matrix": {}}

    matrix: dict[str, list] = {t["id"]: [] for t in themes}
    findings: list[dict] = []
    narrative_items = 0

    for section in sections:
        item = section.get("item_number", "")
        mode = policy_for(item)
        if mode in (_NONE, _QUESTIONS):
            continue  # excluded by design, not a coverage failure
        narrative_items += 1
        content = section.get("content") or section.get("ai_draft") or ""
        present = check_theme_presence(content, themes)
        for match in present:
            matrix.setdefault(match["theme_id"], []).append(item)
        if not present:
            findings.append(
                {
                    "type": "theme_absent",
                    "severity": "warning",
                    "message": f"No win theme detected in {item} — this section reads off-message",
                    "item_number": item,
                }
            )

    for theme in themes:
        if not matrix[theme["id"]]:
            findings.append(
                {
                    "type": "theme_unused",
                    "severity": "warning",
                    "message": f"Theme never appears in any section: {theme['theme_statement'][:70]}",
                    "item_number": "",
                }
            )

    if not narrative_items:
        return {"score": 100, "findings": [], "matrix": matrix}

    score = max(0, 100 - 5 * len(findings))
    return {"score": score, "findings": findings, "matrix": matrix}
