# [TEMPLATE: CUI // SP-CTI]
"""BDD step definitions for JISE Portal Data Retrieval feature.

NIST 800-53: SA-11 (Developer Security Testing), CM-3 (Configuration Change Control)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from behave import given, then, when

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


@given("the JISE portal API is initialized for testing")
def step_jise_api_initialized(context):
    """Set up a minimal Flask test client for JISE portal endpoint."""
    import os
    os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

    from flask import Flask
    from tools.intelligence.jise_portal import jise_bp

    app = Flask("test_jise")
    app.config["TESTING"] = True
    app.register_blueprint(jise_bp)
    context.client = app.test_client()


@when('I send a GET request to "{path}"')
def step_get_request(context, path):
    context.response = context.client.get(path)


@then("the response status code is {code:d}")
def step_response_status(context, code):
    assert context.response.status_code == code, (
        f"Expected {code}, got {context.response.status_code}. "
        f"Body: {context.response.data}"
    )


@then('the response body contains a "{key}" key')
def step_response_has_key(context, key):
    body = json.loads(context.response.data)
    assert key in body, f"Key '{key}' not found in response body: {body}"


@then('each record in "data" contains {fields}')
def step_records_have_fields(context, fields):
    body = json.loads(context.response.data)
    required = [f.strip().strip('"') for f in fields.split(",")]
    for record in body.get("data", []):
        for field in required:
            assert field in record, f"Field '{field}' missing from record: {record}"


@then('the response content type is "{content_type}"')
def step_content_type(context, content_type):
    ct = context.response.content_type
    assert content_type in ct, f"Expected content type '{content_type}', got '{ct}'"


@then("the response body is valid JSON")
def step_valid_json(context):
    try:
        json.loads(context.response.data)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Response body is not valid JSON: {exc}") from exc


@then('all records in "data" have classification equal to "{value}"')
def step_records_classification(context, value):
    body = json.loads(context.response.data)
    for record in body.get("data", []):
        assert record.get("classification") == value, (
            f"Expected classification='{value}', got '{record.get('classification')}'"
        )


@then('the "{field}" field is an empty list')
def step_field_is_empty_list(context, field):
    body = json.loads(context.response.data)
    assert body.get(field) == [], (
        f"Expected '{field}' to be [], got {body.get(field)}"
    )


@then('the response body contains an "error" key')
def step_response_has_error(context):
    body = json.loads(context.response.data)
    assert "error" in body, f"'error' key not found in response body: {body}"
