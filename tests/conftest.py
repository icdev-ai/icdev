#!/usr/bin/env python3
# CUI // SP-CTI
"""Shared pytest fixtures for ICDEV test suite.

D155: Project-root conftest.py centralizes test DB setup, Flask test clients,
and auth header helpers. Prevents duplication across 20+ test files.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Minimal ICDEV schema (subset for fast test DB creation)
# ---------------------------------------------------------------------------
MINIMAL_ICDEV_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL DEFAULT 'webapp',
    classification TEXT NOT NULL DEFAULT 'CUI',
    status TEXT NOT NULL DEFAULT 'active',
    tech_stack_backend TEXT,
    tech_stack_frontend TEXT,
    tech_stack_database TEXT,
    directory_path TEXT NOT NULL DEFAULT '/tmp',
    created_by TEXT,
    impact_level TEXT DEFAULT 'IL5',
    cloud_environment TEXT DEFAULT 'aws-govcloud',
    target_frameworks TEXT,
    ato_status TEXT DEFAULT 'none',
    accrediting_authority TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    url TEXT NOT NULL DEFAULT 'http://localhost:8443',
    status TEXT NOT NULL DEFAULT 'inactive',
    capabilities TEXT,
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    session_id TEXT,
    source_ip TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    title TEXT,
    severity TEXT,
    source TEXT,
    status TEXT DEFAULT 'active',
    project_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS poam_items (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    finding TEXT,
    status TEXT DEFAULT 'open',
    severity TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nist_controls (
    id TEXT PRIMARY KEY,
    control_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'not_assessed',
    implementation_status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stig_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    rule_id TEXT,
    severity TEXT,
    status TEXT DEFAULT 'open',
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Minimal Platform DB schema (SaaS)
# ---------------------------------------------------------------------------
MINIMAL_PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    tier TEXT DEFAULT 'starter',
    impact_level TEXT DEFAULT 'IL4',
    status TEXT DEFAULT 'active',
    settings TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT,
    role TEXT DEFAULT 'developer',
    password_hash TEXT,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    scopes TEXT DEFAULT '["*"]',
    is_active INTEGER DEFAULT 1,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL DEFAULT 'starter',
    status TEXT DEFAULT 'active',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER,
    response_time_ms INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS audit_platform (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
SEED_TENANT_ID = "tenant-test-001"
SEED_USER_ID = "user-test-001"
SEED_API_KEY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SEED_API_KEY_PREFIX = "icdev_test"
SEED_PROJECT_ID = "proj-test-001"


def _seed_platform_db(conn):
    """Insert minimal seed data into platform DB."""
    conn.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, tier, impact_level, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "Test Org", "test-org", "professional", "IL4", "active"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, tenant_id, email, role, display_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (SEED_USER_ID, SEED_TENANT_ID, "dev@test.gov", "admin", "Test Admin"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO api_keys (id, tenant_id, user_id, name, key_hash, key_prefix, scopes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("key-test-001", SEED_TENANT_ID, SEED_USER_ID, "test-key",
         SEED_API_KEY_HASH, SEED_API_KEY_PREFIX, '["*"]'),
    )
    conn.execute(
        "INSERT OR IGNORE INTO subscriptions (id, tenant_id, tier, status) "
        "VALUES (?, ?, ?, ?)",
        ("sub-test-001", SEED_TENANT_ID, "professional", "active"),
    )
    conn.commit()


def _seed_icdev_db(conn):
    """Insert minimal seed data into ICDEV DB."""
    conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, type, classification, status, directory_path, impact_level) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (SEED_PROJECT_ID, "Test Project", "webapp", "CUI", "active", "/tmp/test", "IL5"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO agents (id, name, url, status) VALUES (?, ?, ?, ?)",
        ("builder-agent", "Builder", "http://localhost:8445", "active"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def icdev_db(tmp_path):
    """Temporary ICDEV database with minimal schema and seed data."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    _seed_icdev_db(conn)
    conn.close()
    return db_path


@pytest.fixture
def platform_db(tmp_path):
    """Temporary platform database with minimal schema and seed data."""
    db_path = tmp_path / "platform.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_PLATFORM_SCHEMA)
    _seed_platform_db(conn)
    conn.close()
    return db_path


@pytest.fixture
def api_gateway_app(platform_db, icdev_db):
    """Flask test app for the SaaS API gateway with mocked auth."""
    os.environ["PLATFORM_DB_PATH"] = str(platform_db)

    from tools.saas.api_gateway import create_app
    app = create_app(config={"TESTING": True})

    yield app

    os.environ.pop("PLATFORM_DB_PATH", None)


@pytest.fixture
def api_client(api_gateway_app):
    """Flask test client for the API gateway."""
    return api_gateway_app.test_client()


@pytest.fixture
def dashboard_app(icdev_db):
    """Dashboard Flask test app with patched DB path."""
    with patch("tools.dashboard.app.DB_PATH", str(icdev_db)):
        from tools.dashboard.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def dashboard_client(dashboard_app):
    """Dashboard test client."""
    return dashboard_app.test_client()


@pytest.fixture
def auth_headers():
    """Default Bearer token headers for authenticated requests."""
    return {
        "Authorization": "Bearer icdev_test_key_for_testing",
        "Content-Type": "application/json",
    }


@pytest.fixture
def admin_headers():
    """Admin-level auth headers."""
    return {
        "Authorization": "Bearer icdev_admin_test_key",
        "Content-Type": "application/json",
        "X-Tenant-ID": SEED_TENANT_ID,
    }


# ---------------------------------------------------------------------------
# Phase 4 fixtures — compliance_db, llm_config, rate_limiter_backend
# ---------------------------------------------------------------------------
@pytest.fixture
def compliance_db(tmp_path):
    """ICDEV database seeded with compliance data for testing.

    Seeds nist_controls with AC-2, AC-3, SC-7, SI-4 in mixed statuses
    and STIG findings at various severity levels.
    """
    db_path = tmp_path / "compliance.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    _seed_icdev_db(conn)

    # Seed NIST controls in various statuses
    controls = [
        ("ctrl-ac2", "AC-2", SEED_PROJECT_ID, "Account Management", "satisfied", "implemented"),
        ("ctrl-ac3", "AC-3", SEED_PROJECT_ID, "Access Enforcement", "partially_satisfied", "partial"),
        ("ctrl-sc7", "SC-7", SEED_PROJECT_ID, "Boundary Protection", "not_satisfied", "planned"),
        ("ctrl-si4", "SI-4", SEED_PROJECT_ID, "Information System Monitoring", "satisfied", "implemented"),
        ("ctrl-au2", "AU-2", SEED_PROJECT_ID, "Audit Events", "not_assessed", None),
        ("ctrl-ia2", "IA-2", SEED_PROJECT_ID, "Identification and Authentication", "satisfied", "implemented"),
    ]
    for c in controls:
        conn.execute(
            "INSERT OR IGNORE INTO nist_controls (id, control_id, project_id, title, status, implementation_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            c,
        )

    # Seed STIG findings
    findings = [
        ("stig-001", SEED_PROJECT_ID, "SV-230221r1", "high", "open", "CAT-I: Disable root login"),
        ("stig-002", SEED_PROJECT_ID, "SV-230222r1", "medium", "open", "CAT-II: Set password complexity"),
        ("stig-003", SEED_PROJECT_ID, "SV-230223r1", "medium", "closed", "CAT-II: Enable audit logging"),
        ("stig-004", SEED_PROJECT_ID, "SV-230224r1", "low", "open", "CAT-III: Set login banner"),
    ]
    for f in findings:
        conn.execute(
            "INSERT OR IGNORE INTO stig_findings (id, project_id, rule_id, severity, status, title) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            f,
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def llm_config(tmp_path):
    """Write a mock llm_config.yaml to tmp_path for LLM router tests."""
    import yaml

    config = {
        "default_provider": "mock-openai",
        "providers": {
            "mock-openai": {
                "type": "openai_compat",
                "base_url": "http://localhost:11434/v1",
                "models": {
                    "mock-model": {
                        "model_id": "mock-model-v1",
                        "capabilities": ["text_generation", "code_generation"],
                        "max_tokens": 4096,
                    }
                },
            }
        },
        "routing": {
            "code_generation": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "task_decomposition": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "collaboration": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "narrative_generation": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
            "compliance_export": {
                "provider": "mock-openai",
                "model": "mock-model",
                "fallback_chain": [],
            },
        },
        "agent_effort_defaults": {
            "orchestrator-agent": "high",
            "builder-agent": "max",
        },
    }

    config_path = tmp_path / "llm_config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@pytest.fixture
def rate_limiter_backend():
    """Fresh in-memory rate limiter backend for testing."""
    try:
        from tools.saas.rate_limiter import InMemoryBackend
        return InMemoryBackend()
    except ImportError:
        pytest.skip("rate_limiter module not available")
