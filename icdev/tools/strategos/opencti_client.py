#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Thin GraphQL client for the OpenCTI threat intelligence platform."""

import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

_BASE_DIR = Path(__file__).resolve().parents[2]


def _load_opencti_config() -> dict:
    try:
        import yaml
        cfg_path = _BASE_DIR / "args" / "strategos_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("opencti", {})
    except Exception:
        return {}


class OpenCTIError(Exception):
    pass


class OpenCTIClient:
    def __init__(self, url: str = "", api_key: str = ""):
        cfg = _load_opencti_config()
        self._url = url or os.getenv("OPENCTI_URL", "") or cfg.get("url", "")
        self._api_key = api_key or os.getenv("OPENCTI_API_KEY", "") or cfg.get("api_key", "")
        self._enabled: bool = cfg.get("enabled", False)
        self.sync_interval_h: int = int(cfg.get("sync_interval_h", 6))
        self.min_confidence: int = int(cfg.get("min_confidence", 70))
        self.indicator_limit: int = int(cfg.get("indicator_limit", 200))

    def is_configured(self) -> bool:
        return self._enabled and bool(self._url and self._api_key)

    def _query(self, query: str, variables: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = requests.post(
            f"{self._url.rstrip('/')}/graphql",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            raise OpenCTIError(f"OpenCTI HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if "errors" in body:
            raise OpenCTIError(f"OpenCTI GraphQL errors: {body['errors']}")
        return body.get("data", {})

    def list_indicators(self, limit: int = 100, types: list[str] | None = None) -> list[dict]:
        filters = ""
        if types:
            type_vals = ", ".join(f'"{t}"' for t in types)
            filters = f', filters: {{key: "indicator_types", values: [{type_vals}]}}'
        query = f"""
        query {{
          indicators(first: {limit}{filters}) {{
            edges {{
              node {{
                id
                name
                description
                indicator_types
                pattern
                valid_from
                valid_until
                confidence
                created
                modified
              }}
            }}
          }}
        }}
        """
        data = self._query(query)
        edges = data.get("indicators", {}).get("edges", [])
        return [e["node"] for e in edges]

    def list_reports(self, limit: int = 50) -> list[dict]:
        query = f"""
        query {{
          reports(first: {limit}) {{
            edges {{
              node {{
                id
                name
                description
                report_types
                published
                confidence
                created
                modified
              }}
            }}
          }}
        }}
        """
        data = self._query(query)
        edges = data.get("reports", {}).get("edges", [])
        return [e["node"] for e in edges]
