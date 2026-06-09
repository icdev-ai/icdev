# CUI // SP-CTI
"""Smoke test for /agentic-ai* HTTP routes on PostgreSQL dashboard.

Regression: PGP-vfy-08-d3 found that GET /agentic-ai/ato/<design_id> returned
HTTP 500 with TypeError: 'builtin_function_or_method' object is not iterable
because ato.html used `result.items` (Python dict's method) instead of
`result['items']` (the items list returned by run_ato_checklist).

This test enumerates every /agentic-ai* GET route, posts a design, then
exercises per-design routes — all must return 200 (or 404 when the resource
is intentionally absent).
"""
from __future__ import annotations

import logging
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Suppress noisy startup logs (agent registration, init_db, etc.)
logging.disable(logging.CRITICAL)


def _route_is_get(rule: str) -> bool:
    return "GET" in {m for m in rule.methods if m not in ("HEAD", "OPTIONS")}


def _substitute_path_params(rule: str) -> str:
    url = re.sub(r"<[^>]+>", "test-id", rule)
    if url.endswith("/canvas/<id>/versions/diff"):
        url = url + "?v1=1&v2=2"
    return url


@unittest.skipIf(os.environ.get("ICDEV_SKIP_INTEGRATION"), "integration disabled")
class TestAadcRoutesSmoke(unittest.TestCase):
    """Walk every /agentic-ai* GET route and assert no HTTP 500."""

    @classmethod
    def setUpClass(cls) -> None:
        from flask import g
        from tools.dashboard.app import create_app
        cls.app = create_app()

        # Provide a stub user context so the security middleware before_request
        # does not crash (it reads g.current_user, g.tenant_id, etc.)
        @cls.app.before_request
        def _stub_user():  # noqa: ANN202
            g.current_user = {
                "id": "test-user",
                "role": "developer",
                "compartments": ["CUI"],
            }
            g.tenant_id = "test-tenant"
            g.user_role = "developer"
            g.user_id = "test-user"

        cls.client = cls.app.test_client()

    def test_list_level_routes_return_200(self):
        """All collection-level GETs must succeed (no design needed)."""
        list_paths = [
            "/agentic-ai/",
            "/agentic-ai/canvas",
            "/agentic-ai/canvas/new",
            "/agentic-ai/templates",
            "/agentic-ai/snippets",
            "/agentic-ai/analytics",
            "/agentic-ai/solutions",
            "/agentic-ai/quick-start",
            "/agentic-ai/api/templates",
            "/agentic-ai/api/snippets",
            "/agentic-ai/api/portfolio",
            "/agentic-ai/api/analytics",
            "/agentic-ai/api/solution-packs",
        ]
        failures = []
        for url in list_paths:
            r = self.client.get(url)
            if r.status_code >= 500:
                failures.append((url, r.status_code, r.get_data(as_text=True)[:300]))
        self.assertFalse(failures, f"5xx on list routes: {failures}")

    def test_per_design_routes_return_200(self):
        """Seed a design and exercise every per-design GET — the ato page
        must render (regression for PGP-vfy-08-d3 dict.items() vs ['items'])."""
        post = self.client.post("/agentic-ai/api/designs", json={
            "name": "PGP-vfy-08-d3 regression",
            "description": "smoke test design",
            "domain": "test",
            "classification": "CUI",
            "graph": {
                "nodes": [
                    {"id": "n1", "type": "llm", "label": "LLM",
                     "x": 100, "y": 100},
                    {"id": "n2", "type": "hitl-gate", "label": "HITL",
                     "x": 250, "y": 100},
                ],
                "edges": [],
            },
        })
        self.assertIn(post.status_code, (200, 201),
                       f"design create failed: {post.status_code}")
        did = post.get_json()["id"]

        per_design = [
            f"/agentic-ai/canvas/{did}",
            f"/agentic-ai/api/designs/{did}",
            f"/agentic-ai/api/designs/{did}/safety-redundancy",
            f"/agentic-ai/api/designs/{did}/coordination-matrix",
            f"/agentic-ai/api/designs/{did}/provenance",
            f"/agentic-ai/api/designs/{did}/simulations",
            f"/agentic-ai/api/designs/{did}/threat-model",
            f"/agentic-ai/api/designs/{did}/risks",
            f"/agentic-ai/api/designs/{did}/ato",
            f"/agentic-ai/api/designs/{did}/regulatory",
            f"/agentic-ai/api/designs/{did}/exec-summary",
            f"/agentic-ai/api/designs/{did}/red-team",
            f"/agentic-ai/api/designs/{did}/lint",
            f"/agentic-ai/api/designs/{did}/patterns",
            f"/agentic-ai/api/designs/{did}/impact",
            f"/agentic-ai/api/designs/{did}/scorecard",
            f"/agentic-ai/api/designs/{did}/artifacts",
            f"/agentic-ai/api/designs/{did}/oscal",
            f"/agentic-ai/api/designs/{did}/oscal/control-coverage",
            f"/agentic-ai/api/designs/{did}/checkpoints",
            f"/agentic-ai/api/designs/{did}/parallel-groups",
            f"/agentic-ai/canvas/{did}/ft-link",
            f"/agentic-ai/canvas/{did}/kanban-status",
            f"/agentic-ai/canvas/{did}/versions",
            f"/agentic-ai/assessments/{did}",
            f"/agentic-ai/artifacts/{did}",
            f"/agentic-ai/risks/{did}",
            f"/agentic-ai/ato/{did}",                    # PGP-vfy-08-d3 regression
            f"/agentic-ai/exec-summary/{did}",
            f"/agentic-ai/red-team/{did}",
            f"/agentic-ai/patterns/{did}",
            f"/agentic-ai/impact/{did}",
            f"/agentic-ai/scorecard/{did}",
        ]
        failures = []
        for url in per_design:
            r = self.client.get(url)
            if r.status_code >= 500:
                failures.append((url, r.status_code, r.get_data(as_text=True)[:400]))
        self.assertFalse(
            failures,
            f"5xx on per-design routes: {failures}\n\n"
            "The /agentic-ai/ato/<id> failure is the PGP-vfy-08-d3 regression: "
            "tools/dashboard/templates/agentic_ai_canvas/ato.html used "
            "`result.items` (Python dict method) instead of `result['items']` "
            "(the list returned by run_ato_checklist).",
        )

    def test_ato_page_renders_checklist_table(self):
        """The ATO page must render a framework-grouped table that iterates
        result['items'] (not result.items). Locks in the fix for
        PGP-vfy-08-d3."""
        post = self.client.post("/agentic-ai/api/designs", json={
            "name": "ato-table-test",
            "description": "table render",
            "domain": "test",
            "classification": "CUI",
            "graph": {
                "nodes": [
                    {"id": "n1", "type": "llm", "label": "LLM",
                     "x": 100, "y": 100},
                ],
                "edges": [],
            },
        })
        self.assertIn(post.status_code, (200, 201))
        did = post.get_json()["id"]

        r = self.client.get(f"/agentic-ai/ato/{did}")
        self.assertEqual(r.status_code, 200,
                         f"ato page returned {r.status_code}: "
                         f"{r.get_data(as_text=True)[:500]}")
        body = r.get_data(as_text=True)
        # Body must NOT contain the runtime error
        self.assertNotIn("TypeError", body)
        self.assertNotIn("builtin_function_or_method", body)
        # And must contain the section header that only renders after a
        # successful framework loop
        self.assertIn("ATO Status", body)


if __name__ == "__main__":
    unittest.main()
