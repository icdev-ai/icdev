# CUI // SP-CTI
"""GCP Dataplex CSP adapter — graceful degradation."""
from __future__ import annotations

import importlib.util
import os

_HAS_DATAPLEX = bool(importlib.util.find_spec("google"))
_PROJECT = os.environ.get("GCP_PROJECT_ID", "")
_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")


def _unavailable(op: str, dry_run: bool) -> dict:
    return {"provider": "gcp_dataplex", "status": "sdk_unavailable",
            "synced_count": 0, "errors": ["google-cloud-dataplex not installed"],
            "dry_run": dry_run, "operation": op}


def sync_domains_to_dataplex(domain_list: list[dict], dry_run: bool = True) -> dict:
    if not _HAS_DATAPLEX:
        return _unavailable("sync_domains", dry_run)
    if dry_run:
        return {"provider": "gcp_dataplex", "status": "dry_run",
                "synced_count": len(domain_list), "errors": [], "dry_run": True,
                "note": f"Would create {len(domain_list)} Dataplex Lake(s) in {_PROJECT}/{_LOCATION}"}
    try:
        from google.cloud import dataplex_v1  # type: ignore
        client = dataplex_v1.DataplexServiceClient()
        parent = f"projects/{_PROJECT}/locations/{_LOCATION}"
        synced, errors = 0, []
        for d in domain_list:
            try:
                lake = dataplex_v1.Lake(
                    display_name=d.get("name", "unnamed"),
                    description=d.get("description", ""),
                )
                client.create_lake(parent=parent,
                                   lake_id=d.get("id", "")[:63].replace("-", "_"),
                                   lake=lake)
                synced += 1
            except Exception as exc:
                errors.append(str(exc))
        return {"provider": "gcp_dataplex", "status": "ok" if not errors else "partial",
                "synced_count": synced, "errors": errors, "dry_run": False}
    except Exception as exc:
        return {"provider": "gcp_dataplex", "status": "error",
                "synced_count": 0, "errors": [str(exc)], "dry_run": False}


def sync_products_to_dataplex(product_list: list[dict], dry_run: bool = True) -> dict:
    if not _HAS_DATAPLEX:
        return _unavailable("sync_products", dry_run)
    if dry_run:
        return {"provider": "gcp_dataplex", "status": "dry_run",
                "synced_count": len(product_list), "errors": [], "dry_run": True,
                "note": f"Would create {len(product_list)} Dataplex Zone(s)"}
    return {"provider": "gcp_dataplex", "status": "not_implemented",
            "synced_count": 0, "errors": [], "dry_run": False}
