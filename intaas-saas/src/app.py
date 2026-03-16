#!/usr/bin/env python3
"""INTaaS SaaS — FastAPI + Mangum Lambda handler.

Intelligence-as-a-Service Multiperspectivity Platform.
Serves the API at intaas.icdev.ai.

Endpoints:
  POST /api/v1/analyze                    Submit URL/text for analysis
  GET  /api/v1/topics                     List analyzed topics
  GET  /api/v1/topics/{topic_id}          Topic detail with perspectives
  GET  /api/v1/perspectives/{topic_id}    Side-by-side source comparison
  GET  /api/v1/perspectives/{topic_id}/gaps  Coverage gaps + blind spots
  GET  /api/v1/bias/{article_id}          Article-level bias forensics
  GET  /api/v1/bias/source/{source_id}    Source historical bias profile
  POST /api/v1/socratic/{topic_id}        Start/continue Socratic dialogue
  GET  /api/v1/socratic/{session_id}      Get session transcript
  POST /api/v1/judge/evaluate             LLM-as-a-Judge evaluation
  GET  /api/v1/judge/{eval_id}            Get evaluation result
  POST /api/v1/claims/verify              Fact-check a claim
  POST /api/v1/annotations                Add analyst annotation
  GET  /api/v1/sources                    List OSINT sources
  GET  /health                            Health check
"""
import logging

from fastapi import FastAPI
from mangum import Mangum

from src.config import CLASSIFICATION_BANNER
from src.middleware.auth import BearerAuthMiddleware
from src.middleware.cui_headers import add_classification_headers
from src.middleware.ip_filter import IPFilterMiddleware
from src.routes import analysis, bias, judge, perspectives, socratic, sources

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("intaas")

app = FastAPI(
    title="INTaaS — Intelligence-as-a-Service",
    description="Multiperspectivity intelligence platform with bias forensics and Socratic AI",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)

# Middleware (order: IP filter → classification headers → auth)
app.add_middleware(BearerAuthMiddleware)
app.add_middleware(IPFilterMiddleware)
app.middleware("http")(add_classification_headers)

# Routes
app.include_router(analysis.router, prefix="/api/v1", tags=["analysis"])
app.include_router(perspectives.router, prefix="/api/v1/perspectives", tags=["perspectives"])
app.include_router(bias.router, prefix="/api/v1/bias", tags=["bias"])
app.include_router(socratic.router, prefix="/api/v1/socratic", tags=["socratic"])
app.include_router(judge.router, prefix="/api/v1/judge", tags=["judge"])
app.include_router(sources.router, prefix="/api/v1/sources", tags=["sources"])


@app.get("/")
async def root():
    return {
        "service": "INTaaS",
        "description": "Intelligence-as-a-Service Multiperspectivity Platform",
        "version": "2.0.0",
        "endpoints": {
            "health": "/health",
            "analyze": "POST /api/v1/analyze",
            "topics": "GET /api/v1/topics",
            "perspectives": "GET /api/v1/perspectives/{topic_id}",
            "bias": "GET /api/v1/bias/{article_id}",
            "socratic": "POST /api/v1/socratic/{topic_id}",
            "judge": "POST /api/v1/judge/evaluate",
            "sources": "GET /api/v1/sources",
        },
    }


@app.get("/health")
async def health():
    resp = {"status": "healthy", "service": "intaas", "version": "2.0.0"}
    if CLASSIFICATION_BANNER:
        resp["classification"] = CLASSIFICATION_BANNER
    return resp


# Mangum adapter — Lambda handler entrypoint
handler = Mangum(app, lifespan="off")
