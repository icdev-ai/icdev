#!/usr/bin/env python3
"""Test fixtures for INTaaS SaaS tests."""
import hashlib
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Set up mock AWS environment for all tests."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("STAGE", "test")
    monkeypatch.setenv("SAGEMAKER_ENABLED", "false")
    monkeypatch.setenv("TOPICS_TABLE", "intaas-topics-test")
    monkeypatch.setenv("SOURCES_TABLE", "intaas-sources-test")
    monkeypatch.setenv("ARTICLES_TABLE", "intaas-articles-test")
    monkeypatch.setenv("PERSPECTIVES_TABLE", "intaas-perspectives-test")
    monkeypatch.setenv("SOCRATIC_TABLE", "intaas-socratic-test")
    monkeypatch.setenv("ANNOTATIONS_TABLE", "intaas-annotations-test")
    monkeypatch.setenv("JUDGE_TABLE", "intaas-judge-evals-test")
    monkeypatch.setenv("TENANTS_TABLE", "intaas-tenants-test")


@pytest.fixture
def mock_dynamodb(aws_env):
    """Create mocked DynamoDB tables."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        # Import table definitions
        import importlib
        import src.db.dynamo as dynamo_mod
        importlib.reload(dynamo_mod)
        from src.db.dynamo import TABLE_DEFINITIONS

        table_map = {
            "topics": "intaas-topics-test",
            "sources": "intaas-sources-test",
            "articles": "intaas-articles-test",
            "perspectives": "intaas-perspectives-test",
            "socratic": "intaas-socratic-test",
            "annotations": "intaas-annotations-test",
            "judge_evals": "intaas-judge-evals-test",
            "tenants": "intaas-tenants-test",
        }

        for key, table_name in table_map.items():
            schema = TABLE_DEFINITIONS[key]
            dynamodb.create_table(
                TableName=table_name,
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                **schema,
            )

        # Seed test tenant
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        tenants = ddb.Table("intaas-tenants-test")
        tenants.put_item(Item={
            "PK": "TENANT#test",
            "api_key_hash": hashlib.sha256(b"test-key").hexdigest(),
            "name": "Test Tenant",
            "email": "test@test.com",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        # Reload dynamo module to pick up mocked boto3
        importlib.reload(dynamo_mod)

        yield dynamodb


@pytest.fixture
def api_client(mock_dynamodb):
    """FastAPI test client with mocked DynamoDB."""
    from fastapi.testclient import TestClient

    import importlib
    import src.app as app_mod
    importlib.reload(app_mod)

    client = TestClient(app_mod.app)
    return client


@pytest.fixture
def auth_headers():
    """Authorization headers for test API calls."""
    return {"Authorization": "Bearer test-key"}
