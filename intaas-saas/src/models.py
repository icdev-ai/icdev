#!/usr/bin/env python3
"""Pydantic models for INTaaS API request/response validation."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Requests ──────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """Submit a URL or text for multiperspectivity analysis."""
    url: Optional[str] = None
    text: Optional[str] = None
    title: Optional[str] = None
    language: str = "en"
    include_video: bool = False


class SocraticRequest(BaseModel):
    """Continue or start a Socratic dialogue session."""
    topic_id: str
    user_response: Optional[str] = None
    session_id: Optional[str] = None


class JudgeRequest(BaseModel):
    """Request LLM-as-a-Judge evaluation of an article."""
    article_id: Optional[str] = None
    text: Optional[str] = None
    rubric: str = "bias_forensics"
    reference: str = ""


class ClaimVerifyRequest(BaseModel):
    """Submit a claim for fact-checking."""
    claim: str
    context: str = ""
    topic_id: Optional[str] = None


class AnnotationRequest(BaseModel):
    """Add analyst annotation to a topic."""
    topic_id: str
    content: str
    annotation_type: str = "observation"


# ── Responses ─────────────────────────────────────────────────

class BiasScore(BaseModel):
    """Multi-dimensional bias score for a single article."""
    loaded_language: float = Field(ge=1.0, le=5.0)
    framing_balance: float = Field(ge=1.0, le=5.0)
    statistical_honesty: float = Field(ge=1.0, le=5.0)
    source_transparency: float = Field(ge=1.0, le=5.0)
    omission_severity: float = Field(ge=1.0, le=5.0)
    composite: float = Field(ge=1.0, le=5.0)
    color: str
    color_label: str


class PerspectiveView(BaseModel):
    """A single source's perspective on a topic."""
    source_id: str
    source_name: str
    title: str
    url: str
    summary: str
    bias_score: Optional[BiasScore] = None
    language: str = "en"
    published_at: str = ""


class CoverageGap(BaseModel):
    """A gap in coverage — something most sources omit."""
    fact_omitted: str
    sources_mentioning: int
    sources_total: int
    significance: float = Field(ge=0.0, le=1.0)
    explanation: str = ""


class TopicAnalysis(BaseModel):
    """Full multiperspectivity analysis of a topic."""
    topic_id: str
    title: str
    perspectives: list[PerspectiveView] = []
    coverage_gaps: list[CoverageGap] = []
    source_count: int = 0
    language_count: int = 0
    status: str = "analyzing"


class SocraticTurn(BaseModel):
    """A single turn in a Socratic dialogue."""
    turn_number: int
    role: str  # "ai" or "user"
    content: str
    question_type: Optional[str] = None  # assumption, evidence, contrarian, synthesis


class SocraticSession(BaseModel):
    """Full Socratic dialogue session."""
    session_id: str
    topic_id: str
    turns: list[SocraticTurn] = []
    status: str = "active"
    critical_thinking_score: Optional[float] = None
