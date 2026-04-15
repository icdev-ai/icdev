#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""ICDEV™ SaaS -- Swagger UI Blueprint.

Flask Blueprint that serves the OpenAPI specification as JSON and a
Swagger UI documentation page.  Loads the spec from
``tools.saas.openapi_spec.generate_openapi_spec()``.

Routes:
    GET /api/v1/openapi.json  -- OpenAPI 3.0.3 spec (JSON)
    GET /api/v1/docs          -- Swagger UI interactive documentation

Usage:
    from tools.saas.swagger_ui import swagger_bp
    app.register_blueprint(swagger_bp)

Note:
    For air-gapped deployments, download the swagger-ui-dist package
    from https://unpkg.com/swagger-ui-dist@5/ and serve the assets
    locally.  Update the CDN URLs in the HTML template below.
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, jsonify  # noqa: E402

from tools.saas.openapi_spec import generate_openapi_spec  # noqa: E402

swagger_bp = Blueprint("swagger_ui", __name__, url_prefix="/api/v1")


@swagger_bp.route("/openapi.json", methods=["GET"])
def openapi_json():
    """GET /api/v1/openapi.json -- Return the OpenAPI spec as JSON."""
    spec = generate_openapi_spec()
    return jsonify(spec)


def render_swagger_ui_html(
    openapi_url: str = "/api/v1/openapi.json",
    title: str = "ICDEV\u2122 SaaS API \u2014 Documentation",
    cui_banner: str = "CUI // SP-CTI",
) -> str:
    """Render a Swagger UI HTML page that points at *openapi_url*.

    Reusable across SaaS and dashboard blueprints so both surfaces share
    one Swagger UI template. Callers (dashboard meta blueprint) supply
    their own title + openapi.json URL.

    Air-gap consideration: the ``unpkg.com`` CDN must be replaced with a
    locally vendored ``swagger-ui-dist`` bundle when ``is_airgap()`` is
    true. That switch will land as part of Phase H air-gap frontend work
    (``tools/airgap/npm_mirror_sync.py`` vendors swagger-ui alongside
    node_modules).
    """
    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{title}</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui.css\" />
  <style>
    body {{ margin: 0; padding: 0; }}
    .cui-banner {{
      background: #d4380d;
      color: white;
      text-align: center;
      padding: 4px;
      font-family: monospace;
      font-size: 14px;
      font-weight: bold;
    }}
  </style>
</head>
<body>
  <div class=\"cui-banner\">{cui_banner}</div>
  <div id=\"swagger-ui\"></div>
  <div class=\"cui-banner\">{cui_banner}</div>
  <script src=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js\"></script>
  <script>
    SwaggerUIBundle({{
      url: \"{openapi_url}\",
      dom_id: \"#swagger-ui\",
      deepLinking: true,
      presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIBundle.SwaggerUIStandalonePreset
      ],
      layout: \"BaseLayout\"
    }});
  </script>
</body>
</html>"""


@swagger_bp.route("/docs", methods=["GET"])
def swagger_docs():
    """GET /api/v1/docs -- Render Swagger UI documentation page."""
    html = render_swagger_ui_html(
        openapi_url="/api/v1/openapi.json",
        title="ICDEV\u2122 SaaS API \u2014 Documentation",
    )
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}
