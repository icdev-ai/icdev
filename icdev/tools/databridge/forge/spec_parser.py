# CUI // SP-CTI
"""Spec Parser — Stage 1 of Connector Forge pipeline (D-CF-1).

Accepts OpenAPI JSON/YAML, WSDL XML, HTML API docs, or structured YAML.
Normalizes all inputs to a ForgeApiManifest for the base selector.
Air-gap safe: stdlib only (urllib, xml.etree, html.parser, json).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

logger = get_logger("databridge.forge.spec_parser")


@dataclass
class ForgeEndpoint:
    """Single API endpoint extracted from a spec."""

    path: str
    method: str = "GET"
    operation_id: str = ""
    summary: str = ""
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    request_body_schema: Optional[Dict[str, Any]] = None
    response_schema: Optional[Dict[str, Any]] = None


@dataclass
class ForgeApiManifest:
    """Normalized API description for base class selection and code generation."""

    title: str = ""
    version: str = ""
    base_url: str = ""
    auth_methods: List[str] = field(default_factory=list)
    endpoints: List[ForgeEndpoint] = field(default_factory=list)
    protocol: str = "rest"  # rest, graphql, soap, grpc, ftp, odbc, hl7, fhir, tcp
    wsdl_url: str = ""
    soap_operations: List[str] = field(default_factory=list)
    fhir_resources: List[str] = field(default_factory=list)
    raw_schema: Optional[Dict[str, Any]] = None
    input_type: str = "openapi"  # openapi, wsdl, html, yaml, manual
    parse_warnings: List[str] = field(default_factory=list)


def parse_spec(
    content: str,
    input_type: str = "auto",
    source_url: str = "",
) -> ForgeApiManifest:
    """Parse any supported spec format into a ForgeApiManifest.

    Args:
        content: Raw spec content (JSON, YAML, XML, HTML string)
        input_type: 'openapi', 'wsdl', 'html', 'yaml', 'manual', 'auto'
        source_url: Optional URL hint for auto-detection

    Returns:
        ForgeApiManifest with normalized API description
    """
    if input_type == "auto":
        input_type = _detect_type(content, source_url)

    if input_type == "wsdl":
        return _parse_wsdl(content, source_url)
    if input_type == "html":
        return _parse_html(content, source_url)
    if input_type in ("openapi", "yaml"):
        return _parse_openapi_or_yaml(content, input_type, source_url)
    if input_type == "manual":
        return _parse_manual(content)

    return ForgeApiManifest(
        input_type=input_type,
        parse_warnings=[f"Unknown input type: {input_type}"],
    )


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


def _detect_type(content: str, url: str) -> str:
    """Heuristic format detection."""
    stripped = content.strip()
    lower = stripped.lower()

    if stripped.startswith("<"):
        if "wsdl" in lower or "definitions" in lower:
            return "wsdl"
        if "html" in lower:
            return "html"

    if "swagger" in lower or "openapi" in lower:
        return "openapi"

    if url:
        if url.endswith(".wsdl"):
            return "wsdl"
        if url.endswith((".html", ".htm")):
            return "html"

    return "yaml"


# ---------------------------------------------------------------------------
# OpenAPI / structured YAML
# ---------------------------------------------------------------------------


def _parse_openapi_or_yaml(content: str, input_type: str, source_url: str) -> ForgeApiManifest:
    """Parse OpenAPI 2/3 or structured YAML spec."""
    data = _load_json_or_yaml(content)
    if not data:
        return ForgeApiManifest(input_type=input_type, parse_warnings=["Failed to parse JSON/YAML"])

    manifest = ForgeApiManifest(input_type=input_type, raw_schema=data)
    manifest.title = data.get("info", {}).get("title", "") or data.get("title", "")
    manifest.version = data.get("info", {}).get("version", "") or data.get("version", "")

    manifest.base_url = _extract_base_url(data, source_url)
    manifest.auth_methods = _extract_auth_methods(data)
    manifest.endpoints = _extract_endpoints(data)
    _detect_protocol(manifest, data)

    return manifest


def _extract_base_url(data: Dict[str, Any], source_url: str) -> str:
    """Extract base URL from OpenAPI 3 servers or OpenAPI 2 host."""
    servers = data.get("servers", [])
    if servers and isinstance(servers, list):
        return servers[0].get("url", source_url)
    if "host" in data:
        scheme = data.get("schemes", ["https"])[0]
        return f"{scheme}://{data['host']}{data.get('basePath', '')}"
    return data.get("base_url", source_url)


def _extract_auth_methods(data: Dict[str, Any]) -> List[str]:
    """Extract authentication methods from security definitions."""
    security_defs = data.get("securityDefinitions") or data.get("components", {}).get("securitySchemes", {})
    if not isinstance(security_defs, dict):
        return []

    _SCHEME_MAP = {"oauth2": "oauth2", "apikey": "api_key"}
    methods: List[str] = []
    for _name, scheme in security_defs.items():
        if not isinstance(scheme, dict):
            continue
        scheme_type = scheme.get("type", "").lower()
        if scheme_type in _SCHEME_MAP:
            methods.append(_SCHEME_MAP[scheme_type])
        elif scheme_type == "http":
            methods.append(scheme.get("scheme", "basic"))
    return methods


_VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


def _extract_endpoints(data: Dict[str, Any]) -> List[ForgeEndpoint]:
    """Extract API endpoints from OpenAPI paths."""
    endpoints: List[ForgeEndpoint] = []
    for path, path_item in data.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.upper() not in _VALID_METHODS or not isinstance(op, dict):
                continue
            endpoints.append(
                ForgeEndpoint(
                    path=path,
                    method=method.upper(),
                    operation_id=op.get("operationId", ""),
                    summary=op.get("summary", ""),
                    parameters=op.get("parameters", []),
                    request_body_schema=op.get("requestBody", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema"),
                )
            )
    return endpoints


def _detect_protocol(manifest: ForgeApiManifest, data: Dict[str, Any]) -> None:
    """Set protocol field based on spec hints."""
    if data.get("graphql") or "graphql" in manifest.base_url.lower():
        manifest.protocol = "graphql"
    elif data.get("protocol"):
        manifest.protocol = data["protocol"]
    elif data.get("fhir_resources") or data.get("fhirVersion"):
        manifest.protocol = "fhir"
        manifest.fhir_resources = data.get("fhir_resources", [])
    else:
        manifest.protocol = "rest"


# ---------------------------------------------------------------------------
# WSDL
# ---------------------------------------------------------------------------


def _parse_wsdl(content: str, source_url: str) -> ForgeApiManifest:
    """Parse WSDL XML into ForgeApiManifest using stdlib ElementTree."""
    manifest = ForgeApiManifest(input_type="wsdl", protocol="soap")
    manifest.wsdl_url = source_url

    try:
        root = ET.fromstring(content)  # nosec B314 -- parsing trusted internal MBSE/config XML
        manifest.title = _wsdl_service_name(root)
        manifest.soap_operations = _wsdl_operations(root)
        manifest.base_url = _wsdl_address(root) or source_url
    except ET.ParseError as exc:
        manifest.parse_warnings.append(f"WSDL parse error: {exc}")

    return manifest


def _wsdl_service_name(root: ET.Element) -> str:
    """Extract service name from WSDL root."""
    for elem in root.iter():
        if _local_name(elem.tag) == "service":
            return elem.get("name", "")
    return ""


def _wsdl_operations(root: ET.Element) -> List[str]:
    """Extract SOAP operations from WSDL portType elements."""
    operations: List[str] = []
    for elem in root.iter():
        if _local_name(elem.tag) not in ("portType", "porttype"):
            continue
        for child in elem:
            if _local_name(child.tag) != "operation":
                continue
            op_name = child.get("name", "")
            if op_name:
                operations.append(op_name)
    return operations


def _wsdl_address(root: ET.Element) -> str:
    """Extract endpoint URL from WSDL service/port/address."""
    for elem in root.iter():
        if _local_name(elem.tag) == "address":
            loc = elem.get("location", "")
            if loc:
                return loc
    return ""


# ---------------------------------------------------------------------------
# HTML (heuristic API doc scraping)
# ---------------------------------------------------------------------------


def _parse_html(content: str, source_url: str) -> ForgeApiManifest:
    """Extract API endpoints from HTML documentation (heuristic)."""
    manifest = ForgeApiManifest(input_type="html", protocol="rest")
    manifest.base_url = _infer_base_url(source_url)

    # Find HTTP method + path patterns in code blocks, tables, etc.
    method_pattern = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\b\s+(/[^\s<>\"']+)")
    seen: set = set()
    for match in method_pattern.finditer(content):
        key = (match.group(1), match.group(2))
        if key not in seen:
            seen.add(key)
            manifest.endpoints.append(ForgeEndpoint(path=match.group(2), method=match.group(1)))

    # Cap at 50 endpoints from scraping
    manifest.endpoints = manifest.endpoints[:50]

    if not manifest.endpoints:
        manifest.parse_warnings.append("No API endpoints found in HTML — try OpenAPI spec instead")

    return manifest


# ---------------------------------------------------------------------------
# Manual / pre-structured dict
# ---------------------------------------------------------------------------


def _parse_manual(content: str) -> ForgeApiManifest:
    """Parse a pre-structured dict (JSON or YAML) directly as a manifest."""
    data = _load_json_or_yaml(content)
    if not data:
        return ForgeApiManifest(input_type="manual", parse_warnings=["Empty or unparseable manifest"])

    endpoints = []
    for ep in data.get("endpoints", []):
        if isinstance(ep, dict):
            endpoints.append(
                ForgeEndpoint(
                    path=ep.get("path", ""),
                    method=ep.get("method", "GET"),
                    operation_id=ep.get("operation_id", ""),
                    summary=ep.get("summary", ""),
                )
            )

    return ForgeApiManifest(
        title=data.get("title", ""),
        version=data.get("version", ""),
        base_url=data.get("base_url", ""),
        auth_methods=data.get("auth_methods", []),
        endpoints=endpoints,
        protocol=data.get("protocol", "rest"),
        wsdl_url=data.get("wsdl_url", ""),
        soap_operations=data.get("soap_operations", []),
        fhir_resources=data.get("fhir_resources", []),
        input_type="manual",
        raw_schema=data,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json_or_yaml(content: str) -> Optional[Dict[str, Any]]:
    """Try JSON first, then YAML, then naive key:value parsing."""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        import yaml

        result = yaml.safe_load(content)
        if isinstance(result, dict):
            return result
    except (ImportError, Exception):
        pass

    # Naive key: value fallback (air-gap, no yaml installed)
    result: Dict[str, Any] = {}
    for line in content.splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip().strip("\"'")
    return result or None


def _local_name(tag: str) -> str:
    """Strip XML namespace prefix from a tag."""
    if "}" in tag:
        return tag.split("}")[-1]
    return tag


def _infer_base_url(url: str) -> str:
    """Extract scheme + host from a URL."""
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else url
