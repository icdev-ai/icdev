#!/usr/bin/env python3
# CUI // SP-CTI
"""Thin GraphQL client for the OpenCTI threat intelligence platform."""

import os
import requests
from dotenv import load_dotenv

load_dotenv()


class OpenCTIError(Exception):
    pass


class OpenCTIClient:
    def __init__(self, url: str = "", api_key: str = ""):
        self._url = url or os.getenv("OPENCTI_URL", "")
        self._api_key = api_key or os.getenv("OPENCTI_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self._url and self._api_key)

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
