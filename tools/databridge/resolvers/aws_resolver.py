# CUI // SP-CTI
"""AWS Secrets Manager secret resolver.

Resolves ``aws:secret-name[#json-key]`` refs via boto3.  Uses GovCloud
endpoint by default (us-gov-west-1).  Credentials are sourced from
environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) or the
EC2/ECS instance profile — never hardcoded.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
from typing import Optional

logger = get_logger("databridge.resolvers.aws")


class SecretResolverError(Exception):
    """Raised when the AWS resolver cannot return a value."""


def resolve(secret_ref: str) -> str:
    """Resolve an ``aws:secret-name[#json-key]`` reference.

    Args:
        secret_ref: Full reference including ``aws:`` prefix,
            e.g. ``aws:prod/db/password`` or ``aws:prod/db/creds#password``.

    Returns:
        Plaintext secret value.

    Raises:
        SecretResolverError: on misconfiguration, network failure, or
            missing key.
    """
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SecretResolverError(
            "boto3 is not installed — cannot use aws resolver. "
            "Install it: pip install boto3"
        ) from exc

    # Parse aws:secret-name[#json-key]
    ref_body = secret_ref[len("aws:"):]
    if "#" in ref_body:
        secret_name, json_key = ref_body.rsplit("#", 1)
    else:
        secret_name, json_key = ref_body, None

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        # Load from databridge_config.yaml if not in env
        region = _get_config_region()

    endpoint_url: Optional[str] = os.environ.get("AWS_SECRETS_ENDPOINT_URL")  # test override only

    try:
        kwargs = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        client = boto3.client("secretsmanager", **kwargs)
        response = client.get_secret_value(SecretId=secret_name)
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        raise SecretResolverError(
            f"AWS Secrets Manager error for {secret_name!r}: {error_code} — {exc}"
        ) from exc
    except Exception as exc:
        raise SecretResolverError(
            f"AWS Secrets Manager call failed for {secret_ref!r}: {exc}"
        ) from exc

    raw: Optional[str] = response.get("SecretString")
    if raw is None:
        # Binary secret — not supported for credential use
        raise SecretResolverError(
            f"Secret {secret_name!r} is a binary secret; only string secrets are supported"
        )

    if not json_key:
        # Treat the whole string as the secret value
        if not raw:
            raise SecretResolverError(f"AWS returned empty secret for {secret_ref!r}")
        return raw

    # Decode as JSON and extract the specified key
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretResolverError(
            f"Secret {secret_name!r} is not valid JSON; cannot extract key {json_key!r}: {exc}"
        ) from exc

    if json_key not in data:
        raise SecretResolverError(
            f"Key {json_key!r} not found in AWS secret {secret_name!r}. "
            f"Available keys: {list(data.keys())}"
        )

    value = str(data[json_key])
    if not value:
        raise SecretResolverError(f"AWS returned empty value for key {json_key!r} in {secret_name!r}")

    return value


def _get_config_region() -> str:
    """Read aws_region from args/databridge_config.yaml; default to us-gov-west-1."""
    try:
        import yaml  # type: ignore[import-untyped]
        from pathlib import Path

        config_path = Path(__file__).resolve().parents[3] / "args" / "databridge_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
                return cfg.get("aws_region", "us-gov-west-1")
    except Exception:
        pass
    return "us-gov-west-1"
