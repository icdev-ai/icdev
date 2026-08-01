# CUI // SP-CTI
"""Shared PRD / kanban-promotion helpers for the AI-ify canvas (penta-aiify-06).

Extracted to remove three duplications that had drifted between the
``send-to-kanban`` and ``prd-dry-run`` blueprint routes:

  * ``PHASE_PRIORITY`` — the phase→priority map (was defined twice).
  * ``phase_key`` — deriving the ``P1``/``P2``/``P3`` key from a phase dict.
  * ``build_task_steps`` — the canonical 4-step Design→Implement→Test→Review
    decomposition (with the sequential dependency chain), so the dry-run preview
    shows exactly the titles the real promotion will create.
  * ``load_engine_enrichment`` — the best-effort Innovation/Research/Creative
    engine reader (optionally HITL-filtered), previously copy-pasted into both
    ``generate-prd`` and ``prd-dry-run``.

Keeping these in one module means the preview and the real promotion can never
silently diverge again.
"""
from __future__ import annotations

# Phase → task priority. Single source of truth for both the real promotion
# (send-to-kanban) and the dry-run preview.
PHASE_PRIORITY: dict[str, str] = {"P1": "high", "P2": "medium", "P3": "low"}


def phase_key(phase: dict) -> str:
    """Return the P1/P2/P3 key for a phase dict.

    Prefers an explicit ``phase_id``; otherwise falls back to the first token of
    the label (e.g. ``"P2 — Strategic"`` → ``"P2"``); defaults to ``"P3"``.
    """
    label = phase.get("label", "") or ""
    return phase.get("phase_id") or (label.split(" ")[0] if label else "P3")


def build_task_steps(
    base_id: str,
    *,
    pattern: str,
    paradigm: str,
    module: str,
    fn: str = "",
    model: str = "",
    criterion: str = "",
) -> list[dict]:
    """Return the canonical 4 atomic child-task steps for one opportunity.

    Each step is ``{"suffix", "step", "dep", "title"}`` where ``dep`` is the full
    child id of the preceding step (``None`` for the first), forming the
    d2→d1, d3→d2, d4→d3 dependency chain. Both the real promotion and the preview
    consume this so their titles/ordering stay identical.
    """
    target = f"{module}:{fn}" if fn else module
    model_txt = model or "recommended model"
    crit = criterion or f"Replace {pattern} with {paradigm}"
    specs = [
        ("d1", "Design", None,
         f"Define interface contract and test cases for {pattern} replacement in {target}"),
        ("d2", "Implement", f"{base_id}-d1",
         f"Replace {pattern} with {paradigm} ({model_txt}) in {target}"),
        ("d3", "Test", f"{base_id}-d2",
         f"Validate AI output parity; {crit[:60]}"),
        ("d4", "Review", f"{base_id}-d3",
         f"Security scan + compliance gate for {paradigm} integration in {module}"),
    ]
    return [
        {"suffix": s, "step": name, "dep": dep, "title": title}
        for (s, name, dep, title) in specs
    ]


# Engine tables read for PRD enrichment, in (table, order_by, columns) form.
_ENRICH_SOURCES = {
    "innovation": (
        "innovation_signals",
        "ORDER BY id DESC",
        "id, source_type, title, description, composite_score",
    ),
    "research": (
        "research_regulatory_map",
        "",
        "id, regulation_name, regulatory_body, deadline, nist_controls",
    ),
    "creative": (
        "creative_pain_points",
        "ORDER BY composite_score DESC",
        "id, description, composite_score",
    ),
}


def load_engine_enrichment(
    hitl: dict[tuple, str] | None = None,
    limit: int = 3,
) -> dict[str, list[dict]]:
    """Best-effort read of Innovation/Research/Creative engine rows for a PRD.

    Reads from the main ICDEV DB (never raises — every failure degrades to empty
    lists). When ``hitl`` is provided (keyed by ``(source_type, str(id))``),
    rejected rows are excluded and collected under ``rejected_*``, accepted rows
    are flagged ``hitl_accepted``; a wider window is scanned so ``limit`` survivors
    remain after filtering. When ``hitl`` is None, the top ``limit`` rows are
    returned unfiltered (dry-run preview).

    Returns a dict with keys: ``innovation``, ``research``, ``creative`` and
    ``rejected_innovation``, ``rejected_research``, ``rejected_creative``.
    """
    out: dict[str, list[dict]] = {
        "innovation": [], "research": [], "creative": [],
        "rejected_innovation": [], "rejected_research": [], "rejected_creative": [],
    }
    scan_window = max(limit * 3, 10) if hitl is not None else limit

    try:
        from tools.db.storage import get_connection as _icdev_conn
        icdev = _icdev_conn()
        try:
            for source_type, (table, order_by, columns) in _ENRICH_SOURCES.items():
                accepted = out[source_type]
                rejected = out[f"rejected_{source_type}"]
                try:
                    sql = f"SELECT {columns} FROM {table} {order_by} LIMIT {int(scan_window)}"  # nosec B608 — table/columns are module constants, not user input
                    for r in icdev.execute(sql).fetchall():
                        row = dict(r)
                        if hitl is not None:
                            dec = hitl.get((source_type, str(row.get("id"))))
                            if dec == "reject":
                                rejected.append(row)
                                continue
                            row["hitl_accepted"] = dec == "accept"
                        accepted.append(row)
                        if len(accepted) >= limit:
                            break
                except Exception:
                    pass
        finally:
            icdev.close()
    except Exception:
        pass
    return out
