#!/usr/bin/env python3
"""Deploy INTaaS SaaS to AWS Lambda.

Follows the proven marketplace-saas deploy pattern:
  1. pip install deps with --python-version 3.12 --platform manylinux2014_x86_64
  2. Bundle src/ into build dir
  3. Create .zip with Python zipfile (Windows safe)
  4. Upload to S3 with unique timestamp key
  5. aws cloudformation deploy (NOT sam deploy — SAM CLI broken on 3.14)
  6. Seed default tenant

Usage:
    python scripts/deploy.py
    python scripts/deploy.py --stage prod
    python scripts/deploy.py --seed-only
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
BUILD_DIR = PROJECT_DIR / ".build"
REGION = "us-east-1"
DEPLOY_BUCKET = "icdev-sam-deploy-dev"
STACK_NAME_TPL = "intaas-saas-{stage}"

# Lambda-provided packages — exclude from zip
LAMBDA_PROVIDED = {
    "boto3", "botocore", "s3transfer", "jmespath",
    "urllib3", "dateutil", "six", "docutils",
}


def clean():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)
    print("Cleaned build directory.")


def install_deps():
    """Install deps for Lambda (Python 3.12, Linux x86_64).

    Two-step: binary packages with platform constraint,
    then pure-Python packages without platform constraint.
    """
    print("Installing dependencies...")
    req_file = PROJECT_DIR / "requirements.txt"

    # Step 1: Binary packages (need platform-specific wheels)
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "-r", str(req_file),
        "--target", str(BUILD_DIR),
        "--python-version", "3.12",
        "--platform", "manylinux2014_x86_64",
        "--only-binary=:all:",
        "--no-cache-dir",
        "--quiet",
    ])

    # Step 2: Pure-Python packages (no platform constraint)
    pure_python = ["youtube-transcript-api", "pyyaml"]
    for pkg in pure_python:
        subprocess.call([
            sys.executable, "-m", "pip", "install",
            pkg,
            "--target", str(BUILD_DIR),
            "--no-cache-dir",
            "--quiet",
        ])

    # Remove Lambda-provided packages
    for pkg in BUILD_DIR.iterdir():
        name = pkg.name.split("-")[0].lower().replace("_", "")
        if any(name.startswith(lp.replace("-", "").replace("_", "")) for lp in LAMBDA_PROVIDED):
            if pkg.is_dir():
                shutil.rmtree(pkg)
            else:
                pkg.unlink()
    print("Dependencies installed (Lambda-provided excluded).")


def copy_source():
    """Copy src/ and scripts/ to build dir."""
    for subdir in ["src", "scripts"]:
        src = PROJECT_DIR / subdir
        dst = BUILD_DIR / subdir
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)
    print("Source code copied.")


def create_zip() -> Path:
    """Create .zip with Python zipfile (Windows-safe, forward slashes)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    zip_path = PROJECT_DIR / f".build-intaas-{ts}.zip"

    print(f"Creating deployment package: {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(BUILD_DIR):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".pyc"):
                    continue
                fp = Path(root) / f
                arcname = str(fp.relative_to(BUILD_DIR)).replace(os.sep, "/")
                zf.write(fp, arcname)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Package: {size_mb:.1f} MB")
    return zip_path


def upload_to_s3(zip_path: Path, stage: str) -> str:
    """Upload zip to S3 with unique key."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    s3_key = f"intaas-saas/{stage}/intaas-{ts}.zip"

    print(f"Uploading to s3://{DEPLOY_BUCKET}/{s3_key}...")
    subprocess.check_call([
        "aws", "s3", "cp", str(zip_path),
        f"s3://{DEPLOY_BUCKET}/{s3_key}",
        "--region", REGION,
    ])
    print("Upload complete.")
    return s3_key


def deploy_stack(s3_key: str, stage: str):
    """Deploy via aws cloudformation package + deploy.

    Step 1: package (resolves CodeUri → S3)
    Step 2: deploy packaged template
    """
    stack_name = STACK_NAME_TPL.format(stage=stage)
    template = PROJECT_DIR / "template.yaml"
    packaged = PROJECT_DIR / ".packaged-template.yaml"

    # Write CodeUri into template before packaging
    template_text = template.read_text(encoding="utf-8")
    if "CodeUri:" not in template_text:
        template_text = template_text.replace(
            "      Handler: src.app.handler",
            f"      Handler: src.app.handler\n      CodeUri: s3://{DEPLOY_BUCKET}/{s3_key}",
        )
        template.write_text(template_text, encoding="utf-8")

    # Package
    print("Packaging template...")
    subprocess.check_call([
        "aws", "cloudformation", "package",
        "--template-file", str(template),
        "--s3-bucket", DEPLOY_BUCKET,
        "--s3-prefix", f"intaas-saas/{stage}",
        "--output-template-file", str(packaged),
        "--region", REGION,
    ])

    # Deploy
    print(f"Deploying stack: {stack_name}...")
    subprocess.check_call([
        "aws", "cloudformation", "deploy",
        "--template-file", str(packaged),
        "--stack-name", stack_name,
        "--region", REGION,
        "--capabilities", "CAPABILITY_IAM",
        "--parameter-overrides",
        f"Stage={stage}",
        "AllowedIP=71.244.231.47",
        "--no-fail-on-empty-changeset",
    ])
    print("Stack deployed.")

    # Cleanup
    packaged.unlink(missing_ok=True)


def update_lambda_code(zip_path: Path, s3_key: str, stage: str):
    """Update Lambda function code from S3."""
    func_name = f"intaas-saas-{stage}"
    print(f"Updating Lambda code: {func_name}...")
    subprocess.check_call([
        "aws", "lambda", "update-function-code",
        "--function-name", func_name,
        "--s3-bucket", DEPLOY_BUCKET,
        "--s3-key", s3_key,
        "--region", REGION,
    ])
    print("Lambda code updated.")


def get_function_url(stage: str) -> str:
    """Get the Lambda Function URL."""
    func_name = f"intaas-saas-{stage}"
    result = subprocess.run(
        [
            "aws", "lambda", "get-function-url-config",
            "--function-name", func_name,
            "--region", REGION,
        ],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return data.get("FunctionUrl", "")
    return ""


def seed_tenant(stage: str, api_key: str = "intaas-test-key"):
    """Seed default tenant in DynamoDB."""
    import boto3

    table_name = f"intaas-tenants-{stage}"
    print(f"Seeding tenant in {table_name}...")

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(table_name)
    table.put_item(Item={
        "PK": "TENANT#default",
        "api_key_hash": hashlib.sha256(
            api_key.encode("utf-8")
        ).hexdigest(),
        "name": "INTaaS Dev Tenant",
        "email": "dev@intaas.icdev.ai",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"Tenant seeded (API key: {api_key})")


def main():
    parser = argparse.ArgumentParser(description="Deploy INTaaS SaaS")
    parser.add_argument(
        "--stage", default="dev",
        choices=["dev", "staging", "prod"],
    )
    parser.add_argument(
        "--seed-only", action="store_true",
        help="Only seed tenant, don't deploy",
    )
    parser.add_argument(
        "--code-only", action="store_true",
        help="Only update Lambda code, skip CloudFormation",
    )
    parser.add_argument(
        "--api-key", default="intaas-test-key",
        help="API key for default tenant",
    )
    args = parser.parse_args()

    if args.seed_only:
        seed_tenant(args.stage, args.api_key)
        return

    # Full deploy pipeline
    clean()
    install_deps()
    copy_source()
    zip_path = create_zip()
    s3_key = upload_to_s3(zip_path, args.stage)

    if args.code_only:
        update_lambda_code(zip_path, s3_key, args.stage)
    else:
        deploy_stack(s3_key, args.stage)

    # Seed default tenant
    try:
        seed_tenant(args.stage, args.api_key)
    except Exception as e:
        print(f"Tenant seeding deferred: {e}")

    # Get URL
    url = get_function_url(args.stage)
    if url:
        print(f"\n{'=' * 60}")
        print("  INTaaS SaaS deployed!")
        print(f"  URL: {url}")
        print(f"  API Key: {args.api_key}")
        print("  IP Restricted: 71.244.231.47")
        print(f"{'=' * 60}")
        print("\n  Test:")
        print(f"  curl {url}health")
        print(
            f"  curl -X POST {url}api/v1/analyze "
            f"-H 'Authorization: Bearer {args.api_key}' "
            "-H 'Content-Type: application/json' "
            "-d '{\"title\": \"Test\"}'"
        )
    else:
        print("\nDeploy complete. Check AWS Console for Function URL.")

    # Cleanup zip
    zip_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
