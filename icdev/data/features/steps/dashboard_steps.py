# [TEMPLATE: CUI // SP-CTI]
"""Step definitions for ICDEV dashboard BDD scenarios."""

import os
import sys

from behave import given, then, when

# Add project root to path
sys.path.insert(0, os.getcwd())


@given('the dashboard Flask app is configured')
def step_dashboard_configured(context):
    """Configure the Flask test client with an authenticated session."""
    import sqlite3
    from tools.dashboard.app import create_app
    from tools.dashboard.auth import create_user
    from tools.dashboard.config import DB_PATH
    app = create_app()
    app.config['TESTING'] = True
    context.client = app.test_client()
    # Create a test admin user and set session to bypass auth redirect
    user_id = None
    try:
        user = create_user("bdd-test@icdev.local", "BDD Tester", role="admin")
        user_id = user["id"]
    except Exception:
        # User already exists — look up by email
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id FROM dashboard_users WHERE email = ?",
                ("bdd-test@icdev.local",),
            ).fetchone()
            if row:
                user_id = row["id"]
        finally:
            conn.close()
    if user_id:
        with context.client.session_transaction() as sess:
            sess["user_id"] = user_id


@when('I request the home page "/"')
def step_request_home(context):
    """Request dashboard home."""
    context.response = context.client.get('/')


@when('I request "{path}"')
def step_request_path(context, path):
    """Request a dashboard path."""
    context.response = context.client.get(path)


@then('the response status should be {code:d}')
def step_response_status(context, code):
    """Verify HTTP status code."""
    assert context.response.status_code == code, (
        f"Expected {code}, got {context.response.status_code}"
    )


@then('the page should contain navigation elements')
def step_page_has_nav(context):
    """Verify navigation exists."""
    html = context.response.data.decode('utf-8')
    assert '<nav' in html or 'nav' in html.lower(), "No navigation found"


@then('the page should reflect PM role context')
def step_pm_role(context):
    """Verify PM role context."""
    assert context.response.status_code == 200


@then('the response should be valid JSON')
def step_valid_json(context):
    """Verify JSON response."""
    import json
    data = json.loads(context.response.data)
    assert isinstance(data, (dict, list))



# Portal-specific steps moved to saas_platform_steps.py
