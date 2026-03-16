#!/usr/bin/env python3
"""Add optional classification headers to all responses."""
from fastapi import Request, Response

from src.config import CLASSIFICATION_BANNER


async def add_classification_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    if CLASSIFICATION_BANNER:
        response.headers["X-Classification"] = CLASSIFICATION_BANNER
    return response
