#!/usr/bin/env python3
# CUI // SP-CTI
"""Feature Task Template Generator — creates Kanban tasks with mandatory acceptance criteria.

Every dashboard feature task MUST include:
1. Clear acceptance criteria (what "working" means)
2. A Playwright spec path that will be created and must pass
3. The 8-component checklist from CLAUDE.md

Usage:
    # Generate a task description for a new dashboard page
    python tools/testing/feature_task_template.py --canvas govcon --title "GovCon Hub"

    # Post directly to Kanban
    python tools/testing/feature_task_template.py --canvas govcon --title "GovCon Hub" --post
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from textwrap import dedent

BASE_DIR = Path(__file__).resolve().parent.parent.parent

TEMPLATE = dedent("""\
    # Feature: {title}

    ## Objective
    Implement a fully working `/{canvas}` dashboard page that users can navigate to
    and interact with without errors.

    ## 8-Component Checklist (ALL must ship together — CLAUDE.md §8)

    - [ ] 1. `tools/dashboard/templates/{canvas}/page.html` — page template
    - [ ] 2. `icdev/tools/dashboard/templates/{canvas}/page.html` — icdev/ mirror (copy or companion sync)
    - [ ] 3. `tools/{canvas}/blueprint.py` — Flask blueprint with `@bp.route("/")`
    - [ ] 4. `tools/{canvas}/{canvas}_api.py` — backing module (no ImportError at startup)
    - [ ] 5. Nav link in `tools/dashboard/templates/base.html` → `/{canvas}`
    - [ ] 6. `tools/iqe/adapters/{canvas}.py` — IQE adapter (registers collections)
    - [ ] 7. `context/iqe/queries/{canvas}/` — at least 3 seed queries (.yaml)
    - [ ] 8. `{{% include "includes/iqe_query_widget.html" %}}` in template

    ## Acceptance Criteria (ALL must pass before task is "done")

    ### AC-1: Page loads without errors
    - `GET /{canvas}` returns HTTP 200
    - Response body does NOT contain: "Internal Server Error", "Traceback", "TemplateNotFound"
    - CUI banner present in response: "CUI"
    - Page loads in < 5 seconds

    ### AC-2: Core UI elements present
    - Page has a heading (`<h1>` or `<h2>`) containing "{title}"
    - No JavaScript console errors (window.onerror not triggered)
    - IQE query widget rendered (`.iqe-widget` or `#iqe-minibar` in DOM)

    ### AC-3: Data loads (no empty panels)
    - At least one data element is present (table row, card, stat, or list item)
    - If data comes from an API: `GET /api/{canvas}/...` returns HTTP 200 + valid JSON
    - No "Error loading data" or "Failed to fetch" text visible in UI

    ### AC-4: Playwright spec passes
    - Spec file created at: `tests/e2e/{canvas}_smoke.spec.ts`
    - Spec must include at minimum:
      - Test 1: page loads and CUI banner present
      - Test 2: heading containing "{title}" is visible
      - Test 3: no server error text in body
    - `npx playwright test tests/e2e/{canvas}_smoke.spec.ts --project=chromium` exits 0

    ## Playwright Spec Template

    ```typescript
    // CUI // SP-CTI
    // E2E Smoke: {title}
    import {{ test, expect }} from '@playwright/test';

    const BASE = 'http://localhost:5050';
    const CUI = 'CUI';

    test.describe('{title}', () => {{
      test.beforeEach(async ({{ page }}) => {{
        await page.addInitScript(() => {{
          localStorage.setItem('icdev_tour_completed', '1');
        }});
      }});

      test('page loads and CUI banner present', async ({{ page }}) => {{
        await page.goto(`${{BASE}}/{canvas}`);
        await page.waitForLoadState('domcontentloaded');
        const body = await page.textContent('body');
        expect(body).toContain(CUI);
        expect(body).not.toContain('Internal Server Error');
        expect(body).not.toContain('Traceback');
      }});

      test('heading is visible', async ({{ page }}) => {{
        await page.goto(`${{BASE}}/{canvas}`);
        await page.waitForLoadState('domcontentloaded');
        const body = await page.textContent('body');
        expect(body).toContain('{title}');
      }});

      test('no JS errors on load', async ({{ page }}) => {{
        const errors: string[] = [];
        page.on('pageerror', err => errors.push(err.message));
        await page.goto(`${{BASE}}/{canvas}`);
        await page.waitForLoadState('domcontentloaded');
        expect(errors).toHaveLength(0);
      }});
    }});
    // CUI // SP-CTI
    ```

    ## Verification Steps (run in order — stop on first failure)

    ```bash
    # 1. Route smoke (< 30s)
    python tools/testing/route_smoke.py --routes /{canvas} --api

    # 2. Blueprint import check
    python tools/workflow/coherence_checker.py --check blueprint_imports --json

    # 3. 8-component completeness gate
    python tools/workflow/coherence_checker.py --check new_page_completeness --json

    # 4. Nav route parity
    python tools/workflow/coherence_checker.py --check nav_route_parity --json

    # 5. Feature-specific Playwright spec
    npx playwright test tests/e2e/{canvas}_smoke.spec.ts --project=chromium --reporter=line

    # 6. Capture results to build log
    python -c "from tools.logging.build_logger import capture_playwright; capture_playwright(0, '1 passed')"
    ```

    **Task is ONLY done when all 6 verification steps pass.**
    Do NOT mark done based on code completion alone.
""")


def generate(canvas: str, title: str) -> str:
    return TEMPLATE.format(canvas=canvas, title=title)


def post_task(canvas: str, title: str, base: str = "http://localhost:5050") -> None:
    description = generate(canvas, title)
    payload = json.dumps({
        "title": f"[FEATURE] {title} — /{canvas}",
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "description": description,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/kanban/tasks",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
        print(f"Created task: {body.get('id')} — {body.get('title')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate feature task with acceptance criteria")
    parser.add_argument("--canvas", required=True, help="Canvas/feature slug, e.g. govcon")
    parser.add_argument("--title", required=True, help="Human-readable feature name")
    parser.add_argument("--post", action="store_true", help="POST to Kanban API")
    parser.add_argument("--base", default="http://localhost:5050")
    args = parser.parse_args()

    description = generate(args.canvas, args.title)

    if args.post:
        post_task(args.canvas, args.title, args.base)
    else:
        print(description)


if __name__ == "__main__":
    main()
