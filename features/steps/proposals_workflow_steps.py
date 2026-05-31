# [TEMPLATE: CUI // SP-CTI]
"""Step definitions for Proposals and GovCon Workflow BDD scenarios."""

import os
import sys
from urllib.parse import urlparse

from behave import given, then, when

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _ensure_client(context):
    """Set up authenticated Flask test client if not already done."""
    if hasattr(context, "client") and context.client is not None:
        return
    from tools.dashboard.app import create_app
    from tools.dashboard.auth import create_user
    from tools.db.storage import get_connection

    app = create_app()
    app.config["TESTING"] = True
    context.client = app.test_client()

    user_id = None
    try:
        user = create_user("prop-bdd@icdev.local", "Prop BDD", role="admin")
        user_id = user["id"]
    except Exception:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM dashboard_users WHERE email = ?",
                ("prop-bdd@icdev.local",),
            ).fetchone()
            if row:
                user_id = row["id"]
        finally:
            conn.close()

    if user_id:
        with context.client.session_transaction() as sess:
            sess["user_id"] = user_id


@given("the ICDEV dashboard is running at {base_url}")
def step_dashboard_running(context, base_url):
    """Set up authenticated Flask test client pointed at the app."""
    _ensure_client(context)
    context.base_url = base_url.strip()


@given('I navigate to "{url}"')
def step_navigate_to_url(context, url):
    """Navigate to a URL using the Flask test client (extracts path)."""
    _ensure_client(context)
    parsed = urlparse(url)
    path = parsed.path
    if parsed.query:
        path = f"{path}?{parsed.query}"
    context.response = context.client.get(path)


@then("the page should display an opportunities table or pipeline view")
def step_page_has_opportunities(context):
    """Verify govcon pipeline page has relevant content."""
    html = context.response.data.decode("utf-8", errors="replace")
    assert any(
        kw in html.lower()
        for kw in ["opportunit", "pipeline", "govcon", "capture", "rfp", "solicitation"]
    ), "No opportunities/pipeline content found in GovCon page"


@then("the page should display proposal opportunity cards or a list")
def step_page_has_proposals(context):
    """Verify proposals page has relevant content."""
    html = context.response.data.decode("utf-8", errors="replace")
    assert any(
        kw in html.lower()
        for kw in ["proposal", "opportunit", "capture", "bid", "rfp", "solicitation"]
    ), "No proposal content found in proposals page"


@then("the page should display review score cards with pass/fail statistics")
def step_page_has_reviews(context):
    """Verify reviews dashboard has relevant content."""
    html = context.response.data.decode("utf-8", errors="replace")
    assert any(
        kw in html.lower()
        for kw in ["review", "score", "pass", "fail", "color", "evaluation", "pink"]
    ), "No review content found in reviews dashboard page"


@then("the page should display active contract cards")
def step_page_has_contracts(context):
    """Verify CPMP page has relevant content."""
    html = context.response.data.decode("utf-8", errors="replace")
    assert any(
        kw in html.lower()
        for kw in ["contract", "cpmp", "active", "deliverable", "milestone", "program"]
    ), "No contract content found in CPMP page"


@then("the page should display COR assignments or deliverable tracking")
def step_page_has_cor(context):
    """Verify COR portal page has relevant content."""
    html = context.response.data.decode("utf-8", errors="replace")
    assert any(
        kw in html.lower()
        for kw in ["cor", "contract", "deliverable", "assignment", "officer", "representative"]
    ), "No COR content found in COR portal page"


@when('I GET API "{path}"')
def step_get_api(context, path):
    """GET an API endpoint using the Flask test client."""
    _ensure_client(context)
    context.response = context.client.get(path)


@then("the compliance coverage should be at least {pct:d} percent")
def step_compliance_coverage(context, pct):
    """Verify compliance coverage percentage from API response."""
    import json
    data = json.loads(context.response.data)
    items = data.get("items", data.get("compliance_items", []))
    total = len(items)
    compliant = len([i for i in items if i.get("compliance_status") == "compliant"])
    coverage = (compliant / total * 100) if total > 0 else 0
    assert coverage >= pct, (
        f"Expected compliance coverage >={pct}%, got {coverage:.1f}% ({compliant}/{total})"
    )


@then("the section response should contain at least {count:d} compliance items")
def step_section_compliance_items(context, count):
    """Verify section API response contains linked compliance items."""
    import json
    data = json.loads(context.response.data)
    items = data.get("compliance_items", [])
    assert len(items) >= count, (
        f"Expected at least {count} compliance items, got {len(items)}"
    )


@then('the page should contain "{text}" at least {count:d} times')
def step_page_contains_text_count(context, text, count):
    """Verify a string appears at least N times in the response body."""
    body = context.response.data.decode("utf-8", errors="replace")
    occurrences = body.count(text)
    assert occurrences >= count, (
        f"Expected {text!r} at least {count} times, found {occurrences}"
    )
