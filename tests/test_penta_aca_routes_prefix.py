# CUI // SP-CTI
"""penta-aca-01 — FORGE Academy nav/url-prefix mismatch regression tests.

Historically the registry declared ``url_prefix: /forge-academy`` for the
``forge_academy`` child app, but the blueprint registers WITHOUT a url_prefix
and every route is hardcoded to ``/academy``. The derived nav link therefore
pointed at ``/forge-academy/`` and 404'd.

These tests lock in the fix:

  1. The registry nav link for ``forge_academy`` is ``/academy`` (no trailing
     slash — Flask ``strict_slashes`` would 404 a trailing-slash variant).
  2. That nav link maps to a real endpoint (``forge_academy.hub``) — i.e. it
     resolves rather than 404s.
  3. ``/forge-academy`` and ``/forge-academy/<path>`` 301-redirect to the
     matching ``/academy`` path for stale bookmarks.
"""

from __future__ import annotations

import pytest
from flask import Flask
from werkzeug.exceptions import NotFound
from werkzeug.routing import RequestRedirect


@pytest.fixture()
def academy_app():
    """Bare Flask app with only the forge_academy blueprint registered.

    The blueprint is registered WITHOUT a url_prefix, exactly as the dashboard's
    child-app registration loop does (tools/dashboard/app.py), so the resulting
    url_map mirrors production route paths.
    """
    from apps.forge_academy.blueprint import bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app


# ---------------------------------------------------------------------------
# Registry nav link
# ---------------------------------------------------------------------------

def _forge_academy_nav_item():
    from tools.config.component_registry import ComponentRegistry

    reg = ComponentRegistry()
    nav = reg.get_nav_context()
    for section in nav["sections"].values():
        for group in section["groups"]:
            for item in group["items"]:
                if item["key"] == "forge_academy":
                    return item
    return None


def test_registry_url_prefix_is_academy():
    from tools.config.component_registry import ComponentRegistry

    reg = ComponentRegistry()
    prefixes = reg.get_url_prefixes()
    assert prefixes.get("forge_academy") == "/academy"


def test_nav_link_is_academy_without_trailing_slash():
    item = _forge_academy_nav_item()
    assert item is not None, "forge_academy missing from nav context"
    hrefs = [link["href"] for link in item["links"]]
    assert "/academy" in hrefs, hrefs
    # A trailing-slash variant would 404 against the strict-slashes /academy rule.
    assert "/forge-academy" not in hrefs
    assert "/forge-academy/" not in hrefs
    assert "/academy/" not in hrefs


# ---------------------------------------------------------------------------
# Nav link resolves (not a 404)
# ---------------------------------------------------------------------------

def test_nav_link_resolves_to_hub_endpoint(academy_app):
    """The derived nav link must map to a real endpoint, i.e. not 404."""
    item = _forge_academy_nav_item()
    href = next(link["href"] for link in item["links"])
    adapter = academy_app.url_map.bind("localhost")
    endpoint, _args = adapter.match(href)
    assert endpoint == "forge_academy.hub"


def test_academy_trailing_slash_would_404(academy_app):
    """Documents WHY the nav link must omit the trailing slash."""
    adapter = academy_app.url_map.bind("localhost")
    with pytest.raises((NotFound, RequestRedirect)):
        adapter.match("/academy/")


# ---------------------------------------------------------------------------
# Legacy /forge-academy 301 redirects
# ---------------------------------------------------------------------------

def test_legacy_root_redirects_301(academy_app):
    client = academy_app.test_client()
    resp = client.get("/forge-academy")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/academy")


def test_legacy_root_slash_redirects_301(academy_app):
    client = academy_app.test_client()
    resp = client.get("/forge-academy/")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/academy")


@pytest.mark.parametrize("sub", ["missions", "oracle", "org-readiness", "patterns/12"])
def test_legacy_subpath_redirects_301(academy_app, sub):
    client = academy_app.test_client()
    resp = client.get(f"/forge-academy/{sub}")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith(f"/academy/{sub}")


def test_redirect_targets_are_real_routes(academy_app):
    """The /academy targets the legacy paths redirect to must themselves exist."""
    adapter = academy_app.url_map.bind("localhost")
    for path in ["/academy", "/academy/missions", "/academy/oracle"]:
        try:
            adapter.match(path)
        except NotFound:  # pragma: no cover - guards against a broken target
            pytest.fail(f"redirect target {path} is not a registered route")
        except RequestRedirect:
            pass
