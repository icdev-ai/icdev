#!/usr/bin/env python3
"""IP allowlist middleware — restricts access to configured IP addresses.

Lambda Function URLs pass the client IP via the requestContext in the
Mangum event. For direct Lambda Function URL calls, the source IP is in
`event.requestContext.http.sourceIp` which Mangum maps to the ASGI scope.

Env var: ALLOWED_IP (comma-separated list of allowed IPs, or empty for no filter)
"""
import logging
import os

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("intaas.ip_filter")


def _get_allowed_ips() -> set[str]:
    raw = os.environ.get("ALLOWED_IP", "")
    if not raw:
        return set()
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


class IPFilterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        allowed = _get_allowed_ips()
        if not allowed:
            return await call_next(request)

        # Get client IP from multiple sources
        client_ip = None

        # 1. X-Forwarded-For (CloudFront / ALB)
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            client_ip = xff.split(",")[0].strip()

        # 2. Direct client (ASGI scope)
        if not client_ip and request.client:
            client_ip = request.client.host

        if client_ip not in allowed:
            logger.warning(
                "Blocked request from %s (allowed: %s)",
                client_ip, allowed,
            )
            return JSONResponse(
                status_code=403,
                content={"error": "Access denied", "ip": client_ip},
            )

        return await call_next(request)
