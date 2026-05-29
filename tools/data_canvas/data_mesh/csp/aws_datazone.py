# CUI // SP-CTI
"""AWS DataZone CSP adapter — graceful degradation when boto3 not installed."""
from __future__ import annotations

import importlib.util
import os

_HAS_BOTO3 = bool(importlib.util.find_spec("boto3"))
_DOMAIN_ID = os.environ.get("AWS_DATAZONE_DOMAIN_ID", "")
_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _unavailable(op: str, dry_run: bool) -> dict:
    return {"provider": "aws_datazone", "status": "sdk_unavailable",
            "synced_count": 0, "errors": ["boto3 not installed"],
            "dry_run": dry_run, "operation": op}


def sync_domains_to_datazone(domain_list: list[dict], dry_run: bool = True) -> dict:
    if not _HAS_BOTO3:
        return _unavailable("sync_domains", dry_run)
    if dry_run:
        return {"provider": "aws_datazone", "status": "dry_run",
                "synced_count": len(domain_list), "errors": [], "dry_run": True,
                "note": f"Would create {len(domain_list)} DataZone project(s) in {_REGION}"}
    try:
        import boto3  # type: ignore[import-untyped]
        client = boto3.client("datazone", region_name=_REGION)
        synced, errors = 0, []
        for d in domain_list:
            try:
                client.create_project(domainIdentifier=_DOMAIN_ID,
                                      name=d.get("name", "unnamed"),
                                      description=d.get("description", ""))
                synced += 1
            except Exception as exc:
                errors.append(str(exc))
        return {"provider": "aws_datazone", "status": "ok" if not errors else "partial",
                "synced_count": synced, "errors": errors, "dry_run": False}
    except Exception as exc:
        return {"provider": "aws_datazone", "status": "error",
                "synced_count": 0, "errors": [str(exc)], "dry_run": False}


def sync_products_to_datazone(product_list: list[dict], dry_run: bool = True) -> dict:
    if not _HAS_BOTO3:
        return _unavailable("sync_products", dry_run)
    if dry_run:
        return {"provider": "aws_datazone", "status": "dry_run",
                "synced_count": len(product_list), "errors": [], "dry_run": True,
                "note": f"Would create {len(product_list)} DataZone asset(s)"}
    try:
        import boto3  # type: ignore[import-untyped]
        client = boto3.client("datazone", region_name=_REGION)
        synced, errors = 0, []
        for p in product_list:
            try:
                client.create_asset(domainIdentifier=_DOMAIN_ID,
                                    name=p.get("name", "unnamed"),
                                    typeIdentifier="amazon.datazone.RedshiftTableAssetType",
                                    owningProjectIdentifier=_DOMAIN_ID)
                synced += 1
            except Exception as exc:
                errors.append(str(exc))
        return {"provider": "aws_datazone", "status": "ok" if not errors else "partial",
                "synced_count": synced, "errors": errors, "dry_run": False}
    except Exception as exc:
        return {"provider": "aws_datazone", "status": "error",
                "synced_count": 0, "errors": [str(exc)], "dry_run": False}
