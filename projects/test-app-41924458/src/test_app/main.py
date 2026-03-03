# //CUI
# CONTROLLED UNCLASSIFIED INFORMATION
# Authorized distribution limited to authorized personnel only.
# Handling: CUI Basic per 32 CFR Part 2002
# //CUI

"""FastAPI microservice entry point for test-app."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="test-app",
    version="0.1.0",
    docs_url="/docs" if os.environ.get("ENVIRONMENT") != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else [],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "test-app", "version": "0.1.0"}


@app.get("/ready")
async def readiness():
    """Readiness probe for Kubernetes."""
    return {"ready": True}
