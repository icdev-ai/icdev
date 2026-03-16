#!/usr/bin/env python3
"""Bearer token authentication middleware.
Validates API key against SHA-256 hash in the tenants DynamoDB table.
Extracts tenant_id and injects it into request.state.
"""
import hashlib
import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("intaas.auth")

PUBLIC_PATHS = {"/health", "/"}

# GET endpoints are public (anyone can view results)
# POST endpoints require API key (only authenticated users can create)
PUBLIC_PREFIXES = (
    "/api/v1/topics",
    "/api/v1/perspectives",
    "/api/v1/bias",
    "/api/v1/sources",
    "/api/v1/judge/",
    "/api/v1/socratic/",
)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        method = request.method

        # Always public
        if path in PUBLIC_PATHS or method == "OPTIONS":
            return await call_next(request)

        # GET requests to read endpoints are public
        if method == "GET" and any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Missing or invalid Authorization header"},
            )

        api_key = auth_header[7:]
        tenant = _find_tenant_by_key(api_key)
        if not tenant:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid API key"},
            )

        request.state.tenant_id = tenant["PK"].replace("TENANT#", "")
        request.state.api_key = api_key
        return await call_next(request)


def _find_tenant_by_key(api_key: str) -> dict | None:
    """Find tenant by API key hash. Scan is acceptable for low traffic."""
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    import boto3
    from src import config

    table = boto3.resource("dynamodb").Table(config.TENANTS_TABLE)
    resp = table.scan(
        FilterExpression="api_key_hash = :h",
        ExpressionAttributeValues={":h": key_hash},
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None
