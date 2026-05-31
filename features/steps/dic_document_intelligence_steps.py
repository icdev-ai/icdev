# [TEMPLATE: CUI // SP-CTI]
"""Step definitions for DIC Document Intelligence Canvas BDD scenarios."""

import json
import os
import sys

from behave import given, then, when

sys.path.insert(0, os.getcwd())


@given('the DIC blueprint is configured')
def step_dic_configured(context):
    """Set up a Flask test client with DIC blueprint registered."""
    from tools.dashboard.app import create_app
    app = create_app()
    app.config['TESTING'] = True
    context.client = app.test_client()
    # Set a dummy security context so RLS doesn't block everything.
    with context.client.session_transaction() as sess:
        sess["user_id"] = "bdd-dic-user"
        sess["role"] = "admin"


@given('I am a DIC viewer')
def step_dic_viewer(context):
    """Set client to viewer role."""
    with context.client.session_transaction() as sess:
        sess["user_id"] = "bdd-viewer"
        sess["role"] = "viewer"


@given('I am a DIC editor')
def step_dic_editor(context):
    """Set client to editor role."""
    with context.client.session_transaction() as sess:
        sess["user_id"] = "bdd-editor"
        sess["role"] = "editor"


@when('I request DIC page "{path}"')
def step_request_dic_page(context, path):
    """GET a DIC page."""
    context.response = context.client.get(path)


@when('I GET DIC API "{path}"')
def step_get_dic_api(context, path):
    """GET a DIC API endpoint."""
    context.response = context.client.get(path)


@when('I POST DIC API "{path}" with body')
def step_post_dic_api(context, path):
    """POST to a DIC API endpoint with the provided text as JSON body."""
    body = context.text or "{}"
    context.response = context.client.post(
        path,
        data=body.encode('utf-8'),
        content_type='application/json',
    )


@when('I POST DIC API "{path}" with body "{body}"')
def step_post_dic_api_inline(context, path, body):
    """POST to a DIC API endpoint with an inline JSON body."""
    context.response = context.client.post(
        path,
        data=body.encode('utf-8'),
        content_type='application/json',
    )


@then('the DIC response status should be {code:d}')
def step_dic_status(context, code):
    """Assert HTTP status code."""
    assert context.response.status_code == code, (
        f"Expected {code}, got {context.response.status_code}"
    )


@then('the DIC JSON should contain key "{key}"')
def step_json_has_key(context, key):
    """Assert JSON response contains the expected key."""
    data = json.loads(context.response.data)
    assert key in data, f"JSON missing key '{key}'; keys: {list(data.keys())}"
