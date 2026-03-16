#!/usr/bin/env python3
"""Local development server — runs INTaaS API with moto-mocked AWS services.

No AWS account needed. Spins up DynamoDB in-process using moto,
seeds test data, then starts the FastAPI server on http://localhost:8900.

Usage:
    python scripts/local_server.py
    python scripts/local_server.py --port 8900
    python scripts/local_server.py --api-key my-test-key
"""
import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SAAS_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SAAS_DIR.parent
sys.path.insert(0, str(SAAS_DIR))
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="INTaaS local dev server")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--api-key", default="test-api-key")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    region = "us-east-1"
    os.environ.update({
        "AWS_DEFAULT_REGION": region,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "STAGE": "local",
        "TOPICS_TABLE": "intaas-topics-local",
        "SOURCES_TABLE": "intaas-sources-local",
        "ARTICLES_TABLE": "intaas-articles-local",
        "PERSPECTIVES_TABLE": "intaas-perspectives-local",
        "SOCRATIC_TABLE": "intaas-socratic-local",
        "ANNOTATIONS_TABLE": "intaas-annotations-local",
        "JUDGE_TABLE": "intaas-judge-evals-local",
        "TENANTS_TABLE": "intaas-tenants-local",
        "SAGEMAKER_ENABLED": "false",  # No SageMaker locally
        "LOG_LEVEL": "INFO",
    })

    import boto3
    from moto import mock_aws

    print("Starting moto mock AWS services...")
    mock = mock_aws()
    mock.start()

    try:
        dynamodb = boto3.client("dynamodb", region_name=region)

        # Create all 8 DynamoDB tables
        from src.db.dynamo import TABLE_DEFINITIONS
        table_name_map = {
            "topics": "intaas-topics-local",
            "sources": "intaas-sources-local",
            "articles": "intaas-articles-local",
            "perspectives": "intaas-perspectives-local",
            "socratic": "intaas-socratic-local",
            "annotations": "intaas-annotations-local",
            "judge_evals": "intaas-judge-evals-local",
            "tenants": "intaas-tenants-local",
        }

        for key, table_name in table_name_map.items():
            schema = TABLE_DEFINITIONS[key]
            dynamodb.create_table(
                TableName=table_name,
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                **schema,
            )
            print(f"  Created table: {table_name}")

        # Seed default tenant
        ddb_resource = boto3.resource("dynamodb", region_name=region)
        tenants = ddb_resource.Table("intaas-tenants-local")
        tenants.put_item(Item={
            "PK": "TENANT#default",
            "api_key_hash": hashlib.sha256(args.api_key.encode("utf-8")).hexdigest(),
            "name": "Local Dev Tenant",
            "email": "dev@localhost",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"  Seeded tenant 'default' (API key: {args.api_key})")

        # Seed default OSINT sources
        from src.db import dynamo
        for source in [
            ("GDELT", "api", "https://api.gdeltproject.org", "neutral"),
            ("MediaCloud", "api", "https://search.mediacloud.org", "neutral"),
            ("Semantic Scholar", "api", "https://api.semanticscholar.org", "academic"),
        ]:
            dynamo.register_source(*source)
        print("  Seeded 3 default OSINT sources")

        import uvicorn
        from src.app import app

        print(f"\n{'=' * 60}")
        print("  INTaaS SaaS — Local Dev Server")
        print(f"  URL:     http://{args.host}:{args.port}")
        print(f"  API Key: {args.api_key}")
        print("  SageMaker: DISABLED (deterministic scoring only)")
        print(f"{'=' * 60}")
        print("\n  Test commands:")
        print(f"    curl http://{args.host}:{args.port}/health")
        print(f"    curl -X POST http://{args.host}:{args.port}/api/v1/analyze \\")
        print(f"      -H 'Authorization: Bearer {args.api_key}' \\")
        print("      -H 'Content-Type: application/json' \\")
        print('      -d \'{"url": "https://example.com/article", "title": "Test Topic"}\'')
        print("\n  Endpoints:")
        print("    POST /api/v1/analyze           Submit URL/text for analysis")
        print("    GET  /api/v1/topics            List analyzed topics")
        print("    GET  /api/v1/perspectives/ID   Side-by-side comparison")
        print("    GET  /api/v1/bias/ID           Article bias forensics")
        print("    POST /api/v1/socratic/ID       Socratic dialogue")
        print("    POST /api/v1/judge/evaluate    LLM-as-a-Judge")
        print("    GET  /api/v1/sources           OSINT sources")
        print(f"{'=' * 60}\n")

        uvicorn.run(app, host=args.host, port=args.port, log_level="info")

    finally:
        mock.stop()


if __name__ == "__main__":
    main()
