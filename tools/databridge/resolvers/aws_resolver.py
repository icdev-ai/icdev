# CUI // SP-CTI
"""AWS Secrets Manager resolver for DataBridge.

Resolves secret refs of the form  aws:secret-name[#json-key]
using boto3 against the GovCloud endpoint (us-gov-west-1 default).
Credentials come from env vars or instance profile — never hardcoded.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("databridge.resolvers.aws")


class SecretResolverError(Exception):
    """Raised when a secret reference cannot be resolved."""


try:
    import boto3 as _boto3  # type: ignore[import-untyped]

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


def resolve(secret_ref: str) -> str:
    """Resolve an ``aws:secret-name[#json-key]`` reference to plaintext.

    Args:
        secret_ref: Reference of the form ``aws:my-secret`` or
                    ``aws:my-secret#json_field`` for JSON secrets.

    Returns:
        Plaintext secret value (never empty).

    Raises:
        SecretResolverError: on any failure (missing dep, connection error,
                             key not found, or empty value).
    """
    if not secret_ref.startswith("aws:"):
        raise SecretResolverError(f"Not an aws ref: {secret_ref!r}")

    body = secret_ref[4:]
    json_key: Optional[str] = None
    if "#" in body:
        body, json_key = body.rsplit("#", 1)

    secret_name = body
    if not secret_name:
        raise SecretResolverError(f"Empty secret name in aws ref: {secret_ref!r}")

    if not _BOTO3_AVAILABLE:
        raise SecretResolverError(
            "boto3 package is not installed; run: pip install boto3"
        )

    region = os.environ.get("AWS_REGION", "us-gov-west-1")
    endpoint_url = os.environ.get("AWS_SECRETS_ENDPOINT_URL")  # optional override for testing

    try:
        kwargs: dict = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        client = _boto3.client("secretsmanager", **kwargs)
        response = client.get_secret_value(SecretId=secret_name)
    except Exception as exc:
        raise SecretResolverError(
            f"AWS Secrets Manager error for {secret_name!r}: {exc}"
        ) from exc

    raw = response.get("SecretString") or response.get("SecretBinary")
    if not raw:
        raise SecretResolverError(
            f"AWS Secrets Manager returned empty value for {secret_name!r}"
        )

    if json_key:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SecretResolverError(
                f"Secret {secret_name!r} is not JSON (cannot extract key {json_key!r}): {exc}"
            ) from exc
        if json_key not in data:
            raise SecretResolverError(
                f"Key {json_key!r} not found in secret {secret_name!r}"
            )
        value = data[json_key]
    else:
        value = raw

    if not value:
        raise SecretResolverError(
            f"AWS Secrets Manager returned empty value for {secret_name!r}"
        )

    return str(value)
