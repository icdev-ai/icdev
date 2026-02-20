# CUI // SP-CTI
"""
ICDEV SaaS Prometheus Metrics Blueprint.

Exposes GET /metrics endpoint for Prometheus scraping.
"""

from flask import Blueprint, Response

from tools.saas.metrics import get_collector

metrics_bp = Blueprint("metrics", __name__)


@metrics_bp.route("/metrics", methods=["GET"])
def prometheus_metrics() -> Response:
    """Serve Prometheus text exposition metrics."""
    collector = get_collector()
    collector.update_circuit_breaker_metrics()
    collector.update_tenant_metrics()
    output = collector.format_metrics()
    return Response(
        output,
        status=200,
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )
