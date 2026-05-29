# CUI // SP-CTI
"""Azure Purview (Microsoft Purview) CSP adapter — graceful degradation."""
from __future__ import annotations

import importlib.util
import os

_HAS_PURVIEW = bool(importlib.util.find_spec("azure"))
_ENDPOINT = os.environ.get("AZURE_PURVIEW_ENDPOINT", "")
_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")


def _unavailable(op: str, dry_run: bool) -> dict:
    return {"provider": "azure_purview", "status": "sdk_unavailable",
            "synced_count": 0, "errors": ["azure-purview-catalog not installed"],
            "dry_run": dry_run, "operation": op}


def sync_domains_to_purview(domain_list: list[dict], dry_run: bool = True) -> dict:
    if not _HAS_PURVIEW:
        return _unavailable("sync_domains", dry_run)
    if dry_run:
        return {"provider": "azure_purview", "status": "dry_run",
                "synced_count": len(domain_list), "errors": [], "dry_run": True,
                "note": f"Would create {len(domain_list)} Purview collection(s) at {_ENDPOINT}"}
    try:
        from azure.purview.catalog import PurviewCatalogClient  # type: ignore
        from azure.identity import ClientSecretCredential  # type: ignore
        cred = ClientSecretCredential(_TENANT_ID, _CLIENT_ID, _CLIENT_SECRET)
        client = PurviewCatalogClient(endpoint=_ENDPOINT, credential=cred)
        synced, errors = 0, []
        for d in domain_list:
            try:
                client.entity.create_or_update({"entity": {
                    "typeName": "DataSet",
                    "attributes": {"name": d.get("name", "unnamed"),
                                   "qualifiedName": f"purview://{d.get('id', '')}",
                                   "description": d.get("description", "")}}})
                synced += 1
            except Exception as exc:
                errors.append(str(exc))
        return {"provider": "azure_purview", "status": "ok" if not errors else "partial",
                "synced_count": synced, "errors": errors, "dry_run": False}
    except Exception as exc:
        return {"provider": "azure_purview", "status": "error",
                "synced_count": 0, "errors": [str(exc)], "dry_run": False}


def sync_products_to_purview(product_list: list[dict], dry_run: bool = True) -> dict:
    if not _HAS_PURVIEW:
        return _unavailable("sync_products", dry_run)
    if dry_run:
        return {"provider": "azure_purview", "status": "dry_run",
                "synced_count": len(product_list), "errors": [], "dry_run": True,
                "note": f"Would create {len(product_list)} Purview DataSet entity/entities"}
    return {"provider": "azure_purview", "status": "not_implemented",
            "synced_count": 0, "errors": [], "dry_run": False}
