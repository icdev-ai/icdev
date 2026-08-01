# CUI // SP-CTI
"""Deterministic stub fixtures for the IRIS DataBridge connector.

IRIS (a third-party AI decision-support platform) has no published API yet; the
connector ships in stub mode returning these fixtures so consumers (compass
staffing/writing pillars, dashboard feeds) can build and test against stable
shapes today. When the real API lands, only the connector's ``_endpoints``
and ``stub_mode`` config change — these shapes are the contract to preserve.

Shapes are derived from the three integration use cases:
  * staffing_alignment  — staff-to-LCAT alignment recommendations (feeds
                          proposal staffing matrices).
  * performance_reviews — review workflow packets (submit/fetch status).
  * dashboard_feed      — customer-facing reporting widgets/series.
"""
from __future__ import annotations

from typing import Any, Dict, List

STAFFING_ALIGNMENT_ROWS: List[Dict[str, Any]] = [
    {
        "person_id": "stub-p-001",
        "person_name": "A. Analyst",
        "current_lcat": "Systems Analyst III",
        "recommended_lcat": "Systems Analyst III",
        "alignment_score": 0.92,
        "rationale": "Resume skills and years of experience match the assigned LCAT.",
        "as_of": "2026-07-01",
    },
    {
        "person_id": "stub-p-002",
        "person_name": "B. Engineer",
        "current_lcat": "Software Engineer II",
        "recommended_lcat": "Software Engineer III",
        "alignment_score": 0.61,
        "rationale": "Certifications and recent task history exceed the assigned category.",
        "as_of": "2026-07-01",
    },
]

PERFORMANCE_REVIEW_ROWS: List[Dict[str, Any]] = [
    {
        "review_id": "stub-r-100",
        "subject_id": "stub-p-001",
        "period": "2026-Q2",
        "status": "in_review",
        "workflow_stage": "manager_draft",
        "quality_flags": [],
    },
    {
        "review_id": "stub-r-101",
        "subject_id": "stub-p-002",
        "period": "2026-Q2",
        "status": "submitted",
        "workflow_stage": "employee_input",
        "quality_flags": ["missing_core_values_reference"],
    },
]

DASHBOARD_FEED_ROWS: List[Dict[str, Any]] = [
    {
        "widget_id": "stub-w-staffing-compliance",
        "title": "Staffing Compliance",
        "series": [
            {"label": "aligned", "value": 42},
            {"label": "misaligned", "value": 3},
            {"label": "unresolved", "value": 5},
        ],
        "as_of": "2026-07-01T00:00:00Z",
    },
]

HEALTH_ROW: Dict[str, Any] = {"service": "iris", "status": "ok", "mode": "stub"}

# table_name -> fixture rows, mirrors IRISConnector._endpoints keys.
FIXTURES: Dict[str, List[Dict[str, Any]]] = {
    "staffing_alignment": STAFFING_ALIGNMENT_ROWS,
    "performance_reviews": PERFORMANCE_REVIEW_ROWS,
    "dashboard_feed": DASHBOARD_FEED_ROWS,
    "health": [HEALTH_ROW],
}
