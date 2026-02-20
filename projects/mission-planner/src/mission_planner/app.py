"""Main application entry point for mission-planner."""

import os

from flask import Flask


def create_app(config_name: str = None) -> Flask:
    """Application factory pattern."""
    app = Flask(__name__)

    config_name = config_name or os.environ.get("FLASK_CONFIG", "development")

    # Register blueprints
    # from .api import api_bp
    # app.register_blueprint(api_bp, url_prefix="/api/v1")

    @app.route("/health")
    def health():
        return {"status": "healthy", "service": "mission-planner"}, 200

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
