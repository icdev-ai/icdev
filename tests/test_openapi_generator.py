# CUI // SP-CTI
"""Tests for tools.dashboard.api.openapi_generator — Phase B3.

Covers:
  - extract_query_params: AST-based request.args detection
  - infer_response_schema: homegrown JSON Schema inferrer
  - sample_get_route: live GET helper (mocked)
  - walk_api_v1_routes: Flask url_map walker
  - generate_openapi_spec: end-to-end spec generation (mocked server)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tools.dashboard.api.openapi_generator import (
    _fill_path_params,
    _flask_path_to_openapi,
    _make_operation_id,
    _parse_source_for_query_params,
    extract_query_params,
    generate_openapi_spec,
    infer_response_schema,
    sample_get_route,
    sample_response_schema,
    walk_api_v1_routes,
)


# ===========================================================================
# Helpers — tiny synthetic Flask-like stubs for unit tests
# ===========================================================================


def _make_app(rules: list[tuple[str, set[str], callable]]):
    """Build a minimal Flask-app stub with the given URL rules.

    Each entry in *rules* is (path, methods, view_fn).
    """
    app = MagicMock()
    mock_rules = []
    view_fns = {}
    for path, methods, fn in rules:
        rule = MagicMock()
        rule.rule = path
        rule.methods = methods | {"HEAD", "OPTIONS"}
        rule.endpoint = fn.__name__
        mock_rules.append(rule)
        view_fns[fn.__name__] = fn

    app.url_map.iter_rules.return_value = mock_rules
    app.view_functions = view_fns
    return app


# ===========================================================================
# 1. _flask_path_to_openapi
# ===========================================================================


class TestFlaskPathToOpenapi:
    def test_no_params(self):
        assert _flask_path_to_openapi("/api/v1/projects") == "/api/v1/projects"

    def test_simple_param(self):
        assert (
            _flask_path_to_openapi("/api/v1/projects/<project_id>")
            == "/api/v1/projects/{project_id}"
        )

    def test_typed_param(self):
        assert (
            _flask_path_to_openapi("/api/v1/tasks/<int:task_id>")
            == "/api/v1/tasks/{task_id}"
        )

    def test_multiple_params(self):
        result = _flask_path_to_openapi("/api/v1/a/<a_id>/b/<b_id>")
        assert result == "/api/v1/a/{a_id}/b/{b_id}"


# ===========================================================================
# 2. extract_query_params
# ===========================================================================


class TestExtractQueryParams:
    def _params(self, source: str) -> list[dict]:
        """Parse *source* text directly for query-param AST nodes."""
        return _parse_source_for_query_params(source)

    def test_simple_string_param(self):
        src = """
from flask import request
def handler():
    name = request.args.get("name")
"""
        params = self._params(src)
        assert len(params) == 1
        p = params[0]
        assert p["name"] == "name"
        assert p["in"] == "query"
        assert p["schema"]["type"] == "string"

    def test_string_param_with_default(self):
        src = """
from flask import request
def handler():
    status = request.args.get("status", "open")
"""
        params = self._params(src)
        assert len(params) == 1
        assert params[0]["schema"]["default"] == "open"
        assert params[0]["required"] is False

    def test_int_cast_pattern(self):
        """int(request.args.get("limit", "100")) → integer with default 100."""
        src = """
from flask import request
def handler():
    limit = int(request.args.get("limit", "100"))
"""
        params = self._params(src)
        assert len(params) == 1
        p = params[0]
        assert p["name"] == "limit"
        assert p["schema"]["type"] == "integer"
        assert p["schema"]["default"] == 100

    def test_float_cast_pattern(self):
        """float(request.args.get("threshold", "0.5")) → number with default 0.5."""
        src = """
from flask import request
def handler():
    t = float(request.args.get("threshold", "0.5"))
"""
        params = self._params(src)
        assert len(params) == 1
        p = params[0]
        assert p["schema"]["type"] == "number"
        assert p["schema"]["default"] == pytest.approx(0.5)

    def test_flask_type_kwarg(self):
        """request.args.get("page", type=int) → integer."""
        src = """
from flask import request
def handler():
    page = request.args.get("page", type=int)
"""
        params = self._params(src)
        assert len(params) == 1
        assert params[0]["schema"]["type"] == "integer"

    def test_multiple_params(self):
        src = """
from flask import request
def handler():
    source = request.args.get("source")
    limit = int(request.args.get("limit", "50"))
    cursor = request.args.get("cursor", "")
"""
        params = self._params(src)
        names = [p["name"] for p in params]
        assert "source" in names
        assert "limit" in names
        assert "cursor" in names

    def test_deduplication(self):
        """Same param name in two branches → emitted once."""
        src = """
from flask import request
def handler():
    if True:
        pid = request.args.get("project_id")
    else:
        pid = request.args.get("project_id")
"""
        params = self._params(src)
        names = [p["name"] for p in params]
        assert names.count("project_id") == 1

    def test_no_args_returns_empty(self):
        def plain():
            return "hello"

        assert extract_query_params(plain) == []

    def test_invalid_source_returns_empty(self):
        fn = MagicMock()
        fn.__name__ = "mock_fn"
        # inspect.getsource on a Mock raises TypeError
        assert extract_query_params(fn) == []

    def test_int_default_as_int_literal(self):
        """int(request.args.get("months", 6)) → integer default 6."""
        src = """
from flask import request
def handler():
    months = int(request.args.get("months", 6))
"""
        params = self._params(src)
        assert len(params) == 1
        assert params[0]["schema"]["type"] == "integer"
        assert params[0]["schema"]["default"] == 6


# ===========================================================================
# 3. infer_response_schema
# ===========================================================================


class TestInferResponseSchema:
    def test_none(self):
        assert infer_response_schema(None) == {"type": "null"}

    def test_bool(self):
        assert infer_response_schema(True) == {"type": "boolean"}
        assert infer_response_schema(False) == {"type": "boolean"}

    def test_int(self):
        assert infer_response_schema(42) == {"type": "integer"}

    def test_float(self):
        assert infer_response_schema(3.14) == {"type": "number"}

    def test_str(self):
        assert infer_response_schema("hello") == {"type": "string"}

    def test_empty_list(self):
        result = infer_response_schema([])
        assert result == {"type": "array", "items": {}}

    def test_list_of_strings(self):
        result = infer_response_schema(["a", "b", "c"])
        assert result["type"] == "array"
        assert result["items"] == {"type": "string"}

    def test_list_of_ints(self):
        result = infer_response_schema([1, 2, 3])
        assert result["items"] == {"type": "integer"}

    def test_mixed_list_falls_back_to_empty_items(self):
        result = infer_response_schema([1, "two", 3.0])
        assert result["type"] == "array"
        assert result["items"] == {}

    def test_simple_object(self):
        payload = {"projects": [], "total": 0}
        result = infer_response_schema(payload)
        assert result["type"] == "object"
        assert "projects" in result["properties"]
        assert "total" in result["properties"]
        assert result["properties"]["total"] == {"type": "integer"}

    def test_nested_object(self):
        payload = {"data": {"id": 1, "name": "test"}}
        result = infer_response_schema(payload)
        inner = result["properties"]["data"]
        assert inner["type"] == "object"
        assert inner["properties"]["id"] == {"type": "integer"}

    def test_bool_is_not_int(self):
        """bool must be inferred as 'boolean', not 'integer'."""
        assert infer_response_schema(True)["type"] == "boolean"

    def test_depth_cap_returns_empty(self):
        """Exceeding max recursion depth returns {}."""
        result = infer_response_schema({"a": 1}, _depth=7)
        assert result == {}

    def test_list_sampled_from_first_three(self):
        """Items inferred from first 3 elements only."""
        payload = [1, 2, 3, "four", "five"]
        result = infer_response_schema(payload)
        # First 3 are all int → items is integer
        assert result["items"] == {"type": "integer"}


# ===========================================================================
# 4. sample_get_route
# ===========================================================================


class TestSampleGetRoute:
    def test_returns_parsed_json(self):
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}

        with patch("requests.get", return_value=mock_resp):
            result = sample_get_route("http://localhost:5050/api/v1/projects")

        assert result == {"ok": True}

    def test_returns_none_on_connection_error(self):
        import requests

        with patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("connection refused"),
        ):
            result = sample_get_route("http://localhost:5050/api/v1/projects")

        assert result is None

    def test_returns_none_on_timeout(self):
        import requests

        with patch(
            "requests.get",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            result = sample_get_route("http://localhost:5050/api/v1/projects")

        assert result is None

    def test_rejects_file_scheme(self):
        """B310: file:// URLs must be rejected before requests.get is called."""
        result = sample_get_route("file:///etc/passwd")
        assert result is None

    def test_rejects_ftp_scheme(self):
        """B310: ftp:// URLs must be rejected before requests.get is called."""
        result = sample_get_route("ftp://example.com/data.json")
        assert result is None

    def test_rejects_relative_url(self):
        """B310: Relative URLs without a safe scheme must be rejected."""
        result = sample_get_route("/etc/passwd")
        assert result is None


class TestSampleResponseSchema:
    def test_rejects_file_scheme(self):
        """B310: file:// base_url must be rejected before urlopen is called."""
        result = sample_response_schema("/api/v1/projects", "file:///etc/")
        assert result is None

    def test_rejects_ftp_scheme(self):
        """B310: ftp:// base_url must be rejected before urlopen is called."""
        result = sample_response_schema("/api/v1/projects", "ftp://example.com")
        assert result is None

    def test_returns_none_on_unsafe_url(self):
        """B310: Any non-http(s) scheme must be rejected."""
        result = sample_response_schema("/api/v1/projects", "data:text/html,test")
        assert result is None


# ===========================================================================
# 5. _fill_path_params
# ===========================================================================


class TestFillPathParams:
    def test_id_gets_numeric_placeholder(self):
        result = _fill_path_params("/api/v1/projects/<project_id>/status")
        assert result == "/api/v1/projects/1/status"

    def test_non_id_gets_example_placeholder(self):
        result = _fill_path_params("/api/v1/things/<slug>/detail")
        assert result == "/api/v1/things/example/detail"

    def test_typed_param(self):
        result = _fill_path_params("/api/v1/tasks/<int:task_id>")
        assert result == "/api/v1/tasks/1"

    def test_no_params(self):
        result = _fill_path_params("/api/v1/projects")
        assert result == "/api/v1/projects"


# ===========================================================================
# 6. _make_operation_id
# ===========================================================================


class TestMakeOperationId:
    def test_simple(self):
        assert _make_operation_id("get", "/api/v1/projects") == "getApiV1Projects"

    def test_with_path_var_excluded(self):
        result = _make_operation_id("get", "/api/v1/projects/{project_id}/status")
        assert "project_id" not in result.lower()
        assert result.startswith("get")

    def test_post(self):
        assert _make_operation_id("post", "/api/v1/kanban") == "postApiV1Kanban"


# ===========================================================================
# 7. walk_api_v1_routes
# ===========================================================================


class TestWalkApiV1Routes:
    def _dummy_get(self):
        """A dummy GET handler."""

    def _dummy_post(self):
        """A dummy POST handler."""

    def test_filters_non_v1_routes(self):
        app = _make_app(
            [
                ("/api/v1/projects", {"GET"}, self._dummy_get),
                ("/api/legacy/projects", {"GET"}, self._dummy_post),
            ]
        )
        routes = walk_api_v1_routes(app)
        paths = [r["path"] for r in routes]
        assert "/api/v1/projects" in paths
        assert "/api/legacy/projects" not in paths

    def test_method_lowercased(self):
        app = _make_app([("/api/v1/projects", {"GET", "POST"}, self._dummy_get)])
        routes = walk_api_v1_routes(app)
        methods = {r["method"] for r in routes}
        assert "get" in methods
        assert "post" in methods
        # HEAD and OPTIONS should be excluded
        assert "head" not in methods
        assert "options" not in methods

    def test_path_params_extracted(self):
        app = _make_app(
            [("/api/v1/projects/<project_id>", {"GET"}, self._dummy_get)]
        )
        routes = walk_api_v1_routes(app)
        assert len(routes) == 1
        pp = routes[0]["path_params"]
        assert len(pp) == 1
        assert pp[0]["name"] == "project_id"
        assert pp[0]["in"] == "path"
        assert pp[0]["required"] is True

    def test_openapi_path_uses_curly_braces(self):
        app = _make_app(
            [("/api/v1/tasks/<int:task_id>", {"GET"}, self._dummy_get)]
        )
        routes = walk_api_v1_routes(app)
        assert routes[0]["path"] == "/api/v1/tasks/{task_id}"

    def test_deduplication(self):
        """Same (method, path) registered twice → appears once."""
        app = _make_app(
            [
                ("/api/v1/projects", {"GET"}, self._dummy_get),
                ("/api/v1/projects", {"GET"}, self._dummy_get),
            ]
        )
        routes = walk_api_v1_routes(app)
        get_routes = [r for r in routes if r["method"] == "get"]
        assert len(get_routes) == 1


# ===========================================================================
# 8. generate_openapi_spec — end-to-end (sampling mocked)
# ===========================================================================


class TestGenerateOpenAPISpec:
    def _handler_with_args(self):
        """List projects with optional filter."""
        from flask import request  # noqa: F401 (inside exec'd handler)

        project_id = request.args.get("project_id")  # noqa: F841
        limit = int(request.args.get("limit", "50"))  # noqa: F841

    def test_spec_structure(self):
        app = _make_app(
            [("/api/v1/projects", {"GET"}, self._handler_with_args)]
        )
        spec = generate_openapi_spec(app, sample_get=False)

        assert spec["openapi"] == "3.1.0"
        assert "paths" in spec
        assert "/api/v1/projects" in spec["paths"]

    def test_query_params_in_spec(self):
        app = _make_app(
            [("/api/v1/projects", {"GET"}, self._handler_with_args)]
        )
        spec = generate_openapi_spec(app, sample_get=False)
        op = spec["paths"]["/api/v1/projects"]["get"]
        param_names = [p["name"] for p in op.get("parameters", [])]
        assert "project_id" in param_names
        assert "limit" in param_names

    def test_post_has_no_response_schema(self):
        def post_handler():
            """Create a project."""

        app = _make_app([("/api/v1/projects", {"POST"}, post_handler)])
        spec = generate_openapi_spec(app, sample_get=True)
        op = spec["paths"]["/api/v1/projects"]["post"]
        # No content key → no sampled schema
        assert "content" not in op["responses"].get("200", {})

    def test_get_sampling_fills_response_schema(self):

        payload = {"projects": [{"id": 1}], "total": 1}
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}

        def list_handler():
            """List projects."""

        app = _make_app([("/api/v1/projects", {"GET"}, list_handler)])

        with patch("requests.get", return_value=mock_resp):
            spec = generate_openapi_spec(app, sample_get=True)

        op = spec["paths"]["/api/v1/projects"]["get"]
        assert "content" in op["responses"]["200"]
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert "projects" in schema["properties"]

    def test_sampling_failure_leaves_ok_description(self):
        import requests

        def list_handler():
            """List items."""

        app = _make_app([("/api/v1/items", {"GET"}, list_handler)])

        with patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            spec = generate_openapi_spec(app, sample_get=True)

        op = spec["paths"]["/api/v1/items"]["get"]
        assert op["responses"]["200"]["description"] == "OK"
        assert "content" not in op["responses"]["200"]

    def test_error_responses_present(self):
        def get_handler():
            """Get something."""

        app = _make_app([("/api/v1/things", {"GET"}, get_handler)])
        spec = generate_openapi_spec(app, sample_get=False)
        op = spec["paths"]["/api/v1/things"]["get"]
        assert "400" in op["responses"]
        assert "500" in op["responses"]

    def test_spec_is_json_serializable(self):
        import json as _json

        def get_handler():
            """Get data."""

        app = _make_app([("/api/v1/data", {"GET"}, get_handler)])
        spec = generate_openapi_spec(app, sample_get=False)
        # Should not raise
        _json.dumps(spec)

    def test_components_schemas_present(self):
        def get_handler():
            """Get."""

        app = _make_app([("/api/v1/x", {"GET"}, get_handler)])
        spec = generate_openapi_spec(app, sample_get=False)
        assert "ErrorResponse" in spec["components"]["schemas"]
