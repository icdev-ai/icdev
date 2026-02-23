#!/usr/bin/env python3
# CUI // SP-CTI
"""Schema validation utilities (Phase 44 — D275).

Validates tool output dicts against shared dataclass models.
Backward compatible — existing dict returns still work.
"""

import json
from dataclasses import fields as dc_fields
from typing import Any, Optional, Type


class SchemaValidationError(Exception):
    """Raised when output does not match expected schema."""
    pass


def validate_output(
    data: dict,
    schema_class: Type,
    strict: bool = False,
) -> dict:
    """Validate a dict against a schema dataclass.

    Args:
        data: The dict to validate.
        schema_class: A dataclass type to validate against.
        strict: If True, raise SchemaValidationError on missing required fields.
                If False (default), return data with missing fields set to defaults.

    Returns:
        Validated dict (possibly enriched with defaults).
    """
    if not isinstance(data, dict):
        if strict:
            raise SchemaValidationError(f"Expected dict, got {type(data).__name__}")
        return data

    known_fields = {f.name: f for f in dc_fields(schema_class)}
    missing_required = []

    for name, f in known_fields.items():
        if name not in data:
            # Check if field has a default
            has_default = (
                f.default is not f.default_factory  # type: ignore[attr-defined]
                if hasattr(f, "default_factory") and f.default_factory is not None  # type: ignore[attr-defined]
                else f.default is not f.default.__class__  # always has default via dataclass
            )
            # Simpler check: try to construct and see if it works
            pass

    if strict:
        # Try to construct the dataclass — will raise on missing required fields
        try:
            filtered = {k: v for k, v in data.items() if k in known_fields}
            instance = schema_class(**filtered)
            return instance.to_dict() if hasattr(instance, "to_dict") else data
        except TypeError as exc:
            raise SchemaValidationError(f"Validation failed for {schema_class.__name__}: {exc}")

    # Non-strict: return data as-is with any schema-provided defaults for missing fields
    try:
        filtered = {k: v for k, v in data.items() if k in known_fields}
        instance = schema_class(**filtered)
        result = instance.to_dict() if hasattr(instance, "to_dict") else filtered
        # Merge back any extra keys from original data that aren't in schema
        for k, v in data.items():
            if k not in result:
                result[k] = v
        return result
    except (TypeError, Exception):
        # If construction fails in non-strict mode, return original data
        return data


def wrap_mcp_response(
    data: Any,
    schema_class: Optional[Type] = None,
) -> dict:
    """Wrap tool output in MCP content format with optional validation.

    Used by base_server.py _handle_tools_call to validate before returning.

    Args:
        data: Tool output (dict, str, or any JSON-serializable value).
        schema_class: Optional dataclass type for validation.

    Returns:
        MCP-formatted response dict.
    """
    if schema_class is not None and isinstance(data, dict):
        try:
            data = validate_output(data, schema_class, strict=False)
        except Exception:
            pass  # Validation failure must not crash the response

    if isinstance(data, dict):
        text = json.dumps(data, indent=2, default=str)
    elif isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, indent=2, default=str)

    return {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }
