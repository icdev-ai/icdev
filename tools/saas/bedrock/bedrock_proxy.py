#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV SaaS Phase 5 -- Bedrock LLM Proxy.

CUI // SP-CTI

Proxy layer for Amazon Bedrock model invocations.  Routes LLM calls to
either the tenant's own AWS account (BYOK / Bring Your Own Key) or the
ICDEV shared Bedrock pool, based on the tenant's ``bedrock_config`` in
platform.db.

Credential modes:
  - **BYOK**:   STS assume-role into the tenant's AWS account using the
                 role ARN stored in ``bedrock_config.credentials_secret``.
  - **Shared**: Uses ICDEV pool credentials from environment variables
                 ``BEDROCK_ACCESS_KEY_ID`` and ``BEDROCK_SECRET_ACCESS_KEY``.

All invocations are metered via ``token_metering.record_token_usage()``
and audit-logged to platform.db.

Usage (library):
    from tools.saas.bedrock.bedrock_proxy import invoke_model

    result = invoke_model(
        tenant_id="tenant-abc123",
        model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        prompt="Summarize this SSP...",
        max_tokens=512,
    )

Usage (CLI):
    python tools/saas/bedrock/bedrock_proxy.py \\
        --tenant-id tenant-abc123 \\
        --model anthropic.claude-3-sonnet-20240229-v1:0 \\
        --prompt "List NIST 800-53 AC controls"
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.saas.platform_db import get_platform_connection, log_platform_audit  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("saas.bedrock.proxy")

# ---------------------------------------------------------------------------
# Optional dependency: boto3
# ---------------------------------------------------------------------------
try:
    import boto3  # noqa: F401
    from botocore.exceptions import ClientError  # noqa: F401
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    logger.debug("boto3 not available -- Bedrock proxy disabled")

# ---------------------------------------------------------------------------
# Shared pool credentials from environment
# ---------------------------------------------------------------------------
SHARED_POOL_REGION = os.environ.get("BEDROCK_REGION", "us-gov-west-1")
SHARED_POOL_ACCESS_KEY = os.environ.get("BEDROCK_ACCESS_KEY_ID")
SHARED_POOL_SECRET_KEY = os.environ.get("BEDROCK_SECRET_ACCESS_KEY")

# Default model if none specified — reads from LLM config or falls back
try:
    from tools.llm.router import LLMRouter
    _router = LLMRouter()
    _p, _mid, _mc = _router.get_provider_for_function("saas_proxy")
    DEFAULT_MODEL_ID = _mid if _mid else "anthropic.claude-sonnet-4-5-20250929-v1:0"
except Exception:
    DEFAULT_MODEL_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_tenant_bedrock_config(tenant_id: str) -> dict:
    """Load and parse bedrock_config JSON for a tenant.

    Returns:
        dict with at least ``mode`` key (byok | shared).

    Raises:
        ValueError: if tenant not found or config is empty/invalid.
    """
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT bedrock_config FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        if not row:
            raise ValueError(
                "Tenant not found: {}".format(tenant_id))

        raw = row[0] if isinstance(row, (list, tuple)) else row["bedrock_config"]
        if not raw or raw in ("{}", "null", ""):
            # Default to shared pool if no config set
            return {"mode": "shared", "region": SHARED_POOL_REGION}

        config = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(config, dict):
            return {"mode": "shared", "region": SHARED_POOL_REGION}

        # Ensure mode exists
        if "mode" not in config:
            config["mode"] = "shared"

        # Phase 32: Check tenant_llm_keys for Bedrock BYOK credentials
        if config.get("mode") == "shared":
            try:
                from tools.saas.tenant_llm_keys import get_active_key_for_provider
                bedrock_key = get_active_key_for_provider(tenant_id, "bedrock")
                if bedrock_key:
                    config["mode"] = "byok"
                    config["_tenant_byok_key"] = bedrock_key
                    logger.debug(
                        "Tenant %s has BYOK Bedrock key — switching to byok mode",
                        tenant_id,
                    )
            except Exception as exc:
                logger.debug("tenant_llm_keys check skipped: %s", exc)

        return config
    finally:
        conn.close()


# ============================================================================
# Bedrock Client Construction
# ============================================================================

def _get_bedrock_client(tenant_id: str):
    """Build a boto3 bedrock-runtime client configured for a tenant.

    Routes to BYOK or shared pool based on tenant's bedrock_config.

    Args:
        tenant_id: Platform tenant identifier.

    Returns:
        Tuple of (boto3_client, config_dict).

    Raises:
        RuntimeError: if boto3 is not installed or credentials missing.
    """
    if not HAS_BOTO3:
        raise RuntimeError(
            "boto3 is required for Bedrock proxy. "
            "Install with: pip install boto3")

    config = _load_tenant_bedrock_config(tenant_id)
    mode = config.get("mode", "shared").lower()
    region = config.get("region", SHARED_POOL_REGION)

    if mode == "byok":
        client = _build_byok_client(config, region)
    else:
        client = _build_shared_client(region)

    return client, config


def _build_byok_client(config: dict, region: str):
    """Build Bedrock client using tenant's own credentials via STS.

    The ``credentials_secret`` field in bedrock_config should contain
    the IAM role ARN to assume in the tenant's AWS account.

    Phase 32: Also supports direct access key pairs stored as
    ``ACCESS_KEY_ID:SECRET_ACCESS_KEY`` in tenant_llm_keys.
    """
    # Phase 32: Support tenant BYOK via direct access key pair
    tenant_key = config.get("_tenant_byok_key", "")
    if tenant_key and ":" in tenant_key:
        parts = tenant_key.split(":", 1)
        return boto3.client(
            "bedrock-runtime",
            region_name=region,
            aws_access_key_id=parts[0],
            aws_secret_access_key=parts[1],
        )

    role_arn = config.get("credentials_secret")
    if not role_arn:
        raise ValueError(
            "BYOK mode requires 'credentials_secret' (IAM role ARN) "
            "in bedrock_config or an access key pair in LLM Provider Keys.")

    sts = boto3.client("sts", region_name=region)
    assumed = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName="icdev-bedrock-byok-{}".format(
            uuid.uuid4().hex[:8]),
        DurationSeconds=900,
    )
    creds = assumed["Credentials"]

    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    return client


def _build_shared_client(region: str):
    """Build Bedrock client using ICDEV shared pool credentials."""
    kwargs = {"region_name": region}
    if SHARED_POOL_ACCESS_KEY and SHARED_POOL_SECRET_KEY:
        kwargs["aws_access_key_id"] = SHARED_POOL_ACCESS_KEY
        kwargs["aws_secret_access_key"] = SHARED_POOL_SECRET_KEY
    # If no explicit creds, boto3 falls back to instance profile / env

    return boto3.client("bedrock-runtime", **kwargs)


# ============================================================================
# Invocation
# ============================================================================

def _invoke_shared_pool(model_id: str, prompt: str,
                        max_tokens: int, temperature: float) -> dict:
    """Invoke Bedrock model using shared pool credentials."""
    client = _build_shared_client(SHARED_POOL_REGION)
    return _call_bedrock(client, model_id, prompt, max_tokens, temperature)


def _invoke_byok(config: dict, model_id: str, prompt: str,
                 max_tokens: int, temperature: float) -> dict:
    """Invoke Bedrock model using tenant's own credentials."""
    region = config.get("region", SHARED_POOL_REGION)
    client = _build_byok_client(config, region)
    return _call_bedrock(client, model_id, prompt, max_tokens, temperature)


def _call_bedrock(client, model_id: str, prompt: str,
                  max_tokens: int, temperature: float) -> dict:
    """Execute the actual Bedrock invoke_model API call.

    Constructs the request body based on the model provider prefix
    (anthropic, amazon, meta, etc.) and parses the response.
    """
    # Build request body based on model provider
    if model_id.startswith("anthropic."):
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
    elif model_id.startswith("amazon."):
        body = {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": max_tokens,
                "temperature": temperature,
            },
        }
    elif model_id.startswith("meta."):
        body = {
            "prompt": prompt,
            "max_gen_len": max_tokens,
            "temperature": temperature,
        }
    else:
        # Generic fallback
        body = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )

    response_body = json.loads(response["body"].read())

    # Parse output text based on model provider
    if model_id.startswith("anthropic."):
        output_text = ""
        for block in response_body.get("content", []):
            if block.get("type") == "text":
                output_text += block.get("text", "")
        input_tokens = response_body.get("usage", {}).get(
            "input_tokens", 0)
        output_tokens = response_body.get("usage", {}).get(
            "output_tokens", 0)
    elif model_id.startswith("amazon."):
        results = response_body.get("results", [{}])
        output_text = results[0].get("outputText", "") if results else ""
        input_tokens = response_body.get(
            "inputTextTokenCount", len(prompt.split()) // 4)
        output_tokens = results[0].get(
            "tokenCount", len(output_text.split()) // 4) if results else 0
    elif model_id.startswith("meta."):
        output_text = response_body.get("generation", "")
        input_tokens = response_body.get(
            "prompt_token_count", len(prompt.split()) // 4)
        output_tokens = response_body.get(
            "generation_token_count", len(output_text.split()) // 4)
    else:
        output_text = response_body.get(
            "output", response_body.get("text", str(response_body)))
        input_tokens = len(prompt.split()) // 4
        output_tokens = len(str(output_text).split()) // 4

    return {
        "output": output_text,
        "model_id": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "stop_reason": response_body.get("stop_reason", "end_turn"),
    }


# ============================================================================
# Public API
# ============================================================================

def invoke_model(tenant_id: str, model_id: str = None,
                 prompt: str = "", max_tokens: int = 1024,
                 temperature: float = 0.7) -> dict:
    """Invoke a Bedrock LLM model on behalf of a tenant.

    Routes to BYOK or shared pool based on tenant's bedrock_config.
    Records token usage for billing and audit.

    Args:
        tenant_id:   Platform tenant identifier.
        model_id:    Bedrock model identifier (e.g. anthropic.claude-...).
        prompt:      The prompt text to send.
        max_tokens:  Maximum tokens in the response.
        temperature: Sampling temperature (0.0 - 1.0).

    Returns:
        dict with output text, model_id, token counts, and metadata.

    Raises:
        RuntimeError: If boto3 is unavailable.
        ValueError:   If tenant not found or BYOK config incomplete.
    """
    if not HAS_BOTO3:
        raise RuntimeError(
            "boto3 is required for Bedrock proxy. "
            "Install with: pip install boto3")

    if not model_id:
        model_id = DEFAULT_MODEL_ID

    invocation_id = "brk-" + uuid.uuid4().hex[:12]
    started_at = _utcnow()

    config = _load_tenant_bedrock_config(tenant_id)
    mode = config.get("mode", "shared").lower()

    try:
        if mode == "byok":
            result = _invoke_byok(
                config, model_id, prompt, max_tokens, temperature)
        else:
            result = _invoke_shared_pool(
                model_id, prompt, max_tokens, temperature)

        result.update({
            "invocation_id": invocation_id,
            "tenant_id": tenant_id,
            "mode": mode,
            "started_at": started_at,
            "completed_at": _utcnow(),
        })

        # Record token usage for billing
        try:
            from tools.saas.bedrock.token_metering import record_token_usage
            record_token_usage(
                tenant_id=tenant_id,
                user_id=None,
                model_id=model_id,
                input_tokens=result.get("input_tokens", 0),
                output_tokens=result.get("output_tokens", 0),
                endpoint="bedrock_proxy",
            )
        except Exception as meter_exc:
            logger.warning("Token metering failed: %s", meter_exc)

        # Audit log
        try:
            log_platform_audit(
                event_type="bedrock.invoke",
                action="Invoked {} via {} mode".format(model_id, mode),
                tenant_id=tenant_id,
                details={
                    "invocation_id": invocation_id,
                    "model_id": model_id,
                    "mode": mode,
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                },
            )
        except Exception as audit_exc:
            logger.warning("Audit logging failed: %s", audit_exc)

        logger.info(
            "Bedrock invocation %s complete: model=%s mode=%s "
            "in_tokens=%d out_tokens=%d",
            invocation_id, model_id, mode,
            result.get("input_tokens", 0),
            result.get("output_tokens", 0),
        )
        return result

    except Exception as exc:
        logger.error("Bedrock invocation %s failed: %s", invocation_id, exc)
        try:
            log_platform_audit(
                event_type="bedrock.invoke.failed",
                action="Failed to invoke {} via {} mode: {}".format(
                    model_id, mode, str(exc)[:200]),
                tenant_id=tenant_id,
                details={
                    "invocation_id": invocation_id,
                    "model_id": model_id,
                    "error": str(exc)[:500],
                },
            )
        except Exception:
            pass
        raise


# ============================================================================
# CLI
# ============================================================================

def main():
    """CLI entry point for Bedrock proxy."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ICDEV Bedrock LLM Proxy",
    )
    parser.add_argument("--tenant-id", required=True,
                        help="Target tenant ID")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL_ID,
                        help="Bedrock model ID")
    parser.add_argument("--prompt", type=str, required=True,
                        help="Prompt text to send")
    parser.add_argument("--max-tokens", type=int, default=1024,
                        help="Max response tokens (default 1024)")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="Sampling temperature (default 0.7)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output as JSON")

    args = parser.parse_args()

    try:
        result = invoke_model(
            tenant_id=args.tenant_id,
            model_id=args.model,
            prompt=args.prompt,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
        )

        if args.as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print("Invocation: {}".format(result.get("invocation_id")))
            print("Model:      {}".format(result.get("model_id")))
            print("Mode:       {}".format(result.get("mode")))
            print("Tokens:     {} in / {} out".format(
                result.get("input_tokens", 0),
                result.get("output_tokens", 0)))
            print("-" * 60)
            print(result.get("output", ""))

    except (ValueError, RuntimeError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("FATAL: {}".format(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
