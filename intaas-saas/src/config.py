#!/usr/bin/env python3
"""Configuration loader — reads from environment variables (set by SAM template).
RSA private key loaded from SSM Parameter Store on first use, then cached.
"""
import os


STAGE = os.environ.get("STAGE", "dev")

# DynamoDB tables
TOPICS_TABLE = os.environ.get("TOPICS_TABLE", f"intaas-topics-{STAGE}")
SOURCES_TABLE = os.environ.get("SOURCES_TABLE", f"intaas-sources-{STAGE}")
ARTICLES_TABLE = os.environ.get("ARTICLES_TABLE", f"intaas-articles-{STAGE}")
PERSPECTIVES_TABLE = os.environ.get("PERSPECTIVES_TABLE", f"intaas-perspectives-{STAGE}")
SOCRATIC_TABLE = os.environ.get("SOCRATIC_TABLE", f"intaas-socratic-{STAGE}")
ANNOTATIONS_TABLE = os.environ.get("ANNOTATIONS_TABLE", f"intaas-annotations-{STAGE}")
JUDGE_TABLE = os.environ.get("JUDGE_TABLE", f"intaas-judge-evals-{STAGE}")
TENANTS_TABLE = os.environ.get("TENANTS_TABLE", f"intaas-tenants-{STAGE}")

# S3 buckets
ARTICLES_BUCKET = os.environ.get("ARTICLES_BUCKET", f"intaas-articles-{STAGE}")

# SageMaker (Prometheus-2 — future)
SAGEMAKER_ENDPOINT = os.environ.get("SAGEMAKER_ENDPOINT", "intaas-prometheus-judge")
SAGEMAKER_ENABLED = os.environ.get("SAGEMAKER_ENABLED", "false").lower() == "true"

# Anthropic API (Claude Haiku as LLM judge — immediate, no infra needed)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_JUDGE_ENABLED = os.environ.get("LLM_JUDGE_ENABLED", "false").lower() == "true"
LLM_JUDGE_MODEL = os.environ.get("LLM_JUDGE_MODEL", "claude-haiku-4-5-20251001")

# Usage cap (125 hrs/month free tier)
USAGE_CAP_HOURS = int(os.environ.get("USAGE_CAP_HOURS", "125"))

# GDELT API
GDELT_API_BASE = os.environ.get("GDELT_API_BASE", "https://api.gdeltproject.org/api/v2")
GDELT_DOC_API = os.environ.get("GDELT_DOC_API", "https://api.gdeltproject.org/api/v2/doc/doc")

# Classification banner — blank by default
CLASSIFICATION_BANNER = os.environ.get("CLASSIFICATION_BANNER", "")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Bias scoring thresholds
BIAS_DIMENSIONS = [
    "loaded_language",
    "framing_balance",
    "statistical_honesty",
    "source_transparency",
    "omission_severity",
]

# Color ratings (FAR 15.305)
COLOR_RATINGS = {
    "blue": {"min_score": 4.5, "label": "Exceptional", "description": "Comprehensive multiperspectivity"},
    "purple": {"min_score": 3.8, "label": "Outstanding", "description": "Minor blind spots only"},
    "green": {"min_score": 3.0, "label": "Acceptable", "description": "Some perspectives missing"},
    "yellow": {"min_score": 2.0, "label": "Marginal", "description": "Significant bias detected"},
    "red": {"min_score": 0.0, "label": "Unacceptable", "description": "One-sided or misleading"},
}
