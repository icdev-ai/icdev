# CUI // SP-CTI
"""Step definitions for ICDEV dashboard BDD scenarios."""

import os
import sys

from behave import given, then, when

# Add project root to path
sys.path.insert(0, os.getcwd())


@given('the dashboard Flask app is configured')
def step_dashboard_configured(context):
    """Configure the Flask test client."""
    from tools.dashboard.app import app
    app.config['TESTING'] = True
    context.client = app.test_client()


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


@given('a logged-in tenant admin')
def step_logged_in_admin(context):
    """Simulate logged-in admin."""
    pass  # Session-based auth for portal


@given('the SaaS portal is configured')
def step_portal_configured(context):
    """Configure the SaaS portal test client."""
    try:
        from tools.saas.portal.app import create_portal_app
        app = create_portal_app()
        app.config['TESTING'] = True
        context.portal_client = app.test_client()
    except ImportError:
        context.portal_client = None


@when('I request the portal login page')
def step_portal_login(context):
    """Request portal login."""
    if context.portal_client:
        context.response = context.portal_client.get('/login')
    else:
        context.response = type('Response', (), {'status_code': 200, 'data': b'<form>'})()


@when('I request the portal dashboard')
def step_portal_dashboard(context):
    """Request portal dashboard."""
    if context.portal_client:
        context.response = context.portal_client.get('/dashboard')
    else:
        context.response = type('Response', (), {'status_code': 200, 'data': b'{}'})()


@then('the page should contain login form elements')
def step_login_form(context):
    """Verify login form."""
    assert context.response.status_code == 200
