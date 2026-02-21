#!/usr/bin/env python3
"""ICDEV SaaS Platform — Pydantic Models.

CUI // SP-CTI
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ImpactLevel(str, Enum):
    IL2 = "IL2"
    IL4 = "IL4"
    IL5 = "IL5"
    IL6 = "IL6"


class TenantStatus(str, Enum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"
    DELETED = "deleted"


class SubscriptionTier(str, Enum):
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class UserRole(str, Enum):
    TENANT_ADMIN = "tenant_admin"
    DEVELOPER = "developer"
    COMPLIANCE_OFFICER = "compliance_officer"
    AUDITOR = "auditor"
    VIEWER = "viewer"


class AuthMethod(str, Enum):
    API_KEY = "api_key"
    OAUTH = "oauth"
    CAC_PIV = "cac_piv"


class KeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ArtifactDestType(str, Enum):
    S3 = "s3"
    GIT = "git"
    SFTP = "sftp"


class BedrockMode(str, Enum):
    SHARED = "shared"
    BYOK = "byok"


class LLMProvider(str, Enum):
    """Supported LLM providers for tenant BYOK keys (Phase 32 — D141)."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    BEDROCK = "bedrock"
    OLLAMA = "ollama"
    VLLM = "vllm"


# ---- Request/Response Models ----

class ArtifactConfig(BaseModel):
    type: ArtifactDestType
    bucket: Optional[str] = None
    region: Optional[str] = None
    repo_url: Optional[str] = None
    sftp_host: Optional[str] = None
    sftp_path: Optional[str] = None
    webhook_url: Optional[str] = None


class BedrockConfig(BaseModel):
    mode: BedrockMode = BedrockMode.BYOK
    region: str = "us-gov-west-1"
    credentials_secret: Optional[str] = None  # AWS Secrets Manager ARN


class IdPConfig(BaseModel):
    type: str = "oauth"  # "oauth" or "cac"
    issuer_url: Optional[str] = None
    client_id: Optional[str] = None
    jwks_uri: Optional[str] = None


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    impact_level: ImpactLevel = ImpactLevel.IL4
    tier: SubscriptionTier = SubscriptionTier.STARTER
    admin_email: str = Field(..., min_length=5)
    admin_name: Optional[str] = None


class Tenant(BaseModel):
    id: str
    name: str
    slug: str
    impact_level: ImpactLevel
    status: TenantStatus
    tier: SubscriptionTier
    db_host: Optional[str] = None
    db_name: Optional[str] = None
    db_port: int = 5432
    k8s_namespace: Optional[str] = None
    aws_account_id: Optional[str] = None
    artifact_config: Optional[ArtifactConfig] = None
    bedrock_config: Optional[BedrockConfig] = None
    idp_config: Optional[IdPConfig] = None
    settings: Dict[str, Any] = {}
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None


class UserCreate(BaseModel):
    email: str = Field(..., min_length=5)
    display_name: Optional[str] = None
    role: UserRole = UserRole.DEVELOPER
    auth_method: AuthMethod = AuthMethod.API_KEY


class User(BaseModel):
    id: str
    tenant_id: str
    email: str
    display_name: Optional[str] = None
    role: UserRole
    auth_method: AuthMethod
    status: str = "active"
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None


class APIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    scopes: List[str] = []
    expires_in_days: Optional[int] = None  # None = no expiry


class APIKeyResponse(BaseModel):
    id: str
    key_prefix: str
    name: str
    scopes: List[str] = []
    status: KeyStatus = KeyStatus.ACTIVE
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    # Full key only returned on creation
    key: Optional[str] = None


class Subscription(BaseModel):
    id: str
    tenant_id: str
    tier: SubscriptionTier
    max_projects: int
    max_users: int
    allowed_il_levels: List[str]
    allowed_frameworks: List[str]
    bedrock_pool_enabled: bool = False
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    status: str = "active"


class UsageRecord(BaseModel):
    tenant_id: str
    user_id: Optional[str] = None
    endpoint: str
    method: str  # "REST" or "MCP"
    tokens_used: int = 0
    status_code: Optional[int] = None
    duration_ms: Optional[int] = None
    recorded_at: Optional[datetime] = None


class UsageSummary(BaseModel):
    tenant_id: str
    period: str  # "daily", "monthly"
    total_api_calls: int = 0
    total_tokens: int = 0
    projects_count: int = 0
    users_count: int = 0
    top_endpoints: List[Dict[str, Any]] = []


# ---- Tier Limits ----

TIER_LIMITS = {
    SubscriptionTier.STARTER: {
        "max_projects": 5,
        "max_users": 3,
        "allowed_il_levels": ["IL2", "IL4"],
        "allowed_frameworks": ["nist_800_53"],
        "bedrock_pool_enabled": False,
        "byok_llm_enabled": False,
        "rate_limit_per_minute": 60,
        "rate_limit_per_hour": 500,
        # CLI capabilities ceiling (D132) — Starter: scripted intake only
        "cli_ceiling": {
            "cicd_automation": False,
            "parallel_agents": False,
            "container_execution": False,
            "scripted_intake": True,
        },
    },
    SubscriptionTier.PROFESSIONAL: {
        "max_projects": 25,
        "max_users": 15,
        "allowed_il_levels": ["IL2", "IL4", "IL5"],
        "allowed_frameworks": [
            "nist_800_53", "fedramp_moderate", "fedramp_high",
            "nist_800_171", "cmmc_l2", "cmmc_l3", "dod_cssp",
            "cisa_sbd", "ieee_ivv", "dodi_des", "oscal", "emass",
        ],
        "bedrock_pool_enabled": True,
        "byok_llm_enabled": True,
        "rate_limit_per_minute": 300,
        "rate_limit_per_hour": 5000,
        # CLI capabilities ceiling (D132) — Professional: all except container execution
        "cli_ceiling": {
            "cicd_automation": True,
            "parallel_agents": True,
            "container_execution": False,
            "scripted_intake": True,
        },
    },
    SubscriptionTier.ENTERPRISE: {
        "max_projects": -1,  # unlimited
        "max_users": -1,
        "allowed_il_levels": ["IL2", "IL4", "IL5", "IL6"],
        "allowed_frameworks": [
            "nist_800_53", "fedramp_moderate", "fedramp_high",
            "nist_800_171", "cmmc_l2", "cmmc_l3", "dod_cssp",
            "cisa_sbd", "ieee_ivv", "dodi_des", "oscal", "emass",
            "cnssi_1253", "cato", "fips_199", "fips_200",
        ],
        "bedrock_pool_enabled": True,
        "byok_llm_enabled": True,
        "rate_limit_per_minute": -1,  # unlimited
        "rate_limit_per_hour": -1,
        # CLI capabilities ceiling (D132) — Enterprise: all capabilities
        "cli_ceiling": {
            "cicd_automation": True,
            "parallel_agents": True,
            "container_execution": True,
            "scripted_intake": True,
        },
    },
}
