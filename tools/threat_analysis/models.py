# CUI // SP-CTI
"""ICDEV™ Threat Analysis — Data Models.

Pydantic models for operator-defined threat-level thresholds (ThreatBaseline)
and observed indicator scores (IndicatorScore) that feed PIR alert triggers.

NIST 800-53: SI-4 (System Monitoring), RA-5 (Vulnerability Monitoring).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ScopeLevel(str, Enum):
    """Hierarchy of baseline specificity — higher specificity wins."""

    GLOBAL = "global"
    PLATFORM = "platform"
    TENANT = "tenant"
    PROJECT = "project"
    USER = "user"


class SeverityBand(str, Enum):
    """Operator-defined severity classification for a threshold."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScoreType(str, Enum):
    """How the indicator score was derived."""

    RAW = "raw"
    NORMALIZED = "normalized"
    AGGREGATED = "aggregated"


# ---------------------------------------------------------------------------
# ThreatBaseline — operator-configured threshold
# ---------------------------------------------------------------------------


class ThreatBaseline(BaseModel):
    """Operator-defined threshold for an indicator.

    Maps to the ``indicator_baselines`` table.  The most specific active
    baseline wins during validation (user → project → tenant → platform → global).
    """

    id: str
    indicator_name: str = Field(..., min_length=1, description="Human-readable indicator key.")
    indicator_category: str = Field(default="general", description="Logical grouping, e.g. 'network', 'endpoint'.")
    scope: ScopeLevel = Field(default=ScopeLevel.PROJECT, description="Specificity level of this baseline.")
    scope_id: Optional[str] = Field(default=None, description="Identifier for the scoped entity (optional for global).")
    threshold_score: float = Field(..., ge=0.0, description="Numeric threshold the indicator must not exceed.")
    severity_band: SeverityBand = Field(default=SeverityBand.MEDIUM, description="Classification when threshold is breached.")
    operator_id: str = Field(..., min_length=1, description="User or system that created the baseline.")
    rationale: Optional[str] = Field(default=None, description="Why this threshold was chosen.")
    is_active: bool = Field(default=True, description="Inactive baselines are ignored during validation.")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# IndicatorScore — observed score that may trigger a PIR alert
# ---------------------------------------------------------------------------


class IndicatorScore(BaseModel):
    """A recorded observation of an indicator value.

    Maps to the ``indicator_scores`` table.  Scores are evaluated against the
    active baseline hierarchy at ingestion time; the evaluation result
    (``exceeded``, ``delta``, ``severity_at_time``) is snapshotted so that
    historical records remain interpretable even if baselines change later.
    """

    id: str
    indicator_name: str = Field(..., min_length=1, description="Indicator key that was observed.")
    indicator_category: str = Field(default="general", description="Logical grouping.")
    scope: ScopeLevel = Field(default=ScopeLevel.PROJECT, description="Scope at which the score was observed.")
    scope_id: Optional[str] = Field(default=None, description="Entity ID for the scoped observation.")
    score: float = Field(..., ge=0.0, description="Observed numeric score.")
    score_type: ScoreType = Field(default=ScoreType.RAW, description="How the score was derived.")
    source: Optional[str] = Field(default=None, description="Origin of the observation (sensor, feed, API, etc.).")
    operator_id: Optional[str] = Field(default=None, description="Analyst or system that ingested the score.")
    baseline_id: Optional[str] = Field(default=None, description="Baseline used for evaluation at ingestion time.")
    exceeded: Optional[bool] = Field(default=None, description="Whether the score exceeded the baseline threshold.")
    delta: Optional[float] = Field(default=None, description="Amount by which the score exceeded the threshold (zero if not exceeded).")
    severity_at_time: Optional[SeverityBand] = Field(default=None, description="Severity band from the baseline at the moment of evaluation.")
    evaluated_at: Optional[datetime] = Field(default=None, description="Timestamp when the score was evaluated against the baseline.")
    created_at: Optional[datetime] = None
