# CUI // SP-CTI
"""Shared render fixture for ``tools/dashboard/templates/proposals/detail.html``.

Two suites render this template head-on —
``test_proposals_detail_extract_requirements.py`` and
``test_proposals_detail_map_capabilities.py`` — and each carried its own
hand-written ``opp`` dict listing ~18 keys. ``/proposals/<id>`` renders with
``opp = dict(row)`` from ``SELECT * FROM proposal_opportunities``, so the real
context has every column of that table. When ``PROPOSALS_ALTER_SQL`` added the
capture fields (``win_probability``, ``ptw_low/high``, ``win_themes``,
``key_discriminators``, ``capture_notes``, ``capture_phase``) and the template
started reading them, both hand-written dicts went stale in the same commit and
both suites went red with::

    jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'win_probability'

Re-listing the keys by hand would only reset the clock. :func:`opp_stub` instead
derives them from the schema the route actually queries — the
``proposal_opportunities`` DDL in ``tools/db/init_icdev_db.py`` plus the
``ALTER``s in ``PROPOSALS_ALTER_SQL`` — so a column added tomorrow is in the
stub tomorrow. Building the table in an in-memory SQLite and reading
``PRAGMA table_info`` costs ~0.2s once per session and cannot drift from the
DDL the way a regex over it could.

A template field that is *not* a column (a value the view computes) is still an
``UndefinedError`` — deliberately. That one is a genuine context change and
should be declared explicitly by the suite that needs it, via ``**overrides``.
"""
from __future__ import annotations

import functools
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

__all__ = ["TEMPLATES_DIR", "DETAIL_TEMPLATE", "opportunity_columns", "opp_stub", "render_detail"]

TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
DETAIL_TEMPLATE = TEMPLATES_DIR / "proposals" / "detail.html"

_TABLE = "proposal_opportunities"


@functools.lru_cache(maxsize=1)
def opportunity_columns() -> tuple[str, ...]:
    """Every live column of ``proposal_opportunities``, in declaration order."""
    from tools.db.init_icdev_db import PROPOSALS_ALTER_SQL, SCHEMA_SQL

    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {_TABLE} \(.*?\n\);", SCHEMA_SQL, re.S
    )
    if not match:
        raise AssertionError(
            f"{_TABLE} DDL not found in init_icdev_db.SCHEMA_SQL — the table was "
            "renamed or moved, and every proposals/detail.html render test is "
            "standing on it."
        )

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(match.group(0))
        for stmt in PROPOSALS_ALTER_SQL:
            if f"ALTER TABLE {_TABLE} " in stmt:
                conn.execute(stmt)
        return tuple(r[1] for r in conn.execute(f"PRAGMA table_info({_TABLE})"))
    finally:
        conn.close()


# Values the template formats rather than merely prints (dates it parses, enum
# columns it maps to a colour). Everything else defaults to None, which is what
# a freshly-created opportunity row actually holds.
_DEFAULTS: dict[str, Any] = {
    "title": "Test RFP",
    "solicitation_number": "FA8650-26-R-0001",
    "agency": "USAF",
    "due_date": "2026-06-30",
    "due_time": "17:00",
    "status": "writing",
    "proposal_type": "FFP",
    "naics_code": "541512",
    "domain": "general",
    "classification": "CUI",
    "compartments": "[]",
    "amendment_count": 0,
    "question_count": 0,
}


def opp_stub(opp_id: str, **overrides: Any) -> dict[str, Any]:
    """A ``proposal_opportunities`` row as the detail route would hand it over."""
    opp = {col: None for col in opportunity_columns()}
    opp.update(_DEFAULTS)
    opp["id"] = opp_id
    opp.update(overrides)
    return opp


def render_detail(opp: Optional[dict] = None, **context: Any) -> str:
    """Render proposals/detail.html against a stub base template."""
    from jinja2 import (
        ChoiceLoader,
        DictLoader,
        Environment,
        FileSystemLoader,
        select_autoescape,
    )

    stub_base = "{% block title %}{% endblock %}{% block content %}{% endblock %}"
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"base.html": stub_base}),
                FileSystemLoader(str(TEMPLATES_DIR)),
            ]
        ),
        autoescape=select_autoescape(["html"]),
    )

    ctx: dict[str, Any] = {
        "opp": opp if opp is not None else opp_stub("opp-render-stub"),
        "sections": [],
        "volumes": [],
        "compliance_items": [],
        "reviews": [],
        "findings": [],
        "stats": {
            "sections_total": 0,
            "sections_complete": 0,
            "compliance_coverage_pct": 0,
            "open_findings": 0,
            "critical_findings": 0,
            "section_status_distribution": {},
            "finding_severity_distribution": {},
        },
        "compliance_stats": {
            "total": 0,
            "compliant": 0,
            "partial": 0,
            "non_compliant": 0,
            "not_addressed": 0,
            "not_applicable": 0,
            "gap_pct": 0,
        },
        "reviews_data": [],
        "days_left": 40,
        "questions": [],
        "question_stats": {
            "total": 0,
            "high_priority": 0,
            "draft": 0,
            "approved": 0,
            "submitted": 0,
            "answered": 0,
        },
        "questions_days_left": None,
        "amendments": [],
        "responses": {},
    }
    ctx.update(context)
    return env.get_template("proposals/detail.html").render(**ctx)
