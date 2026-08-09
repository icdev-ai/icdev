# [CUI // SP-CTI]
"""ICDEV™ Network Canvas — Naming Convention Engine.

User-defined, field-level naming conventions for network devices.
Supports character-level type/width/padding definitions, auto-incrementing
sequences, validation, parsing, and bulk generation.

Field types: alpha, numeric, alphanumeric, enum, freetext, sequence.

Usage::

    python tools/network/naming_engine.py --convention nc-site-seq --generate '{"SITE":"NYC"}' --json
    python tools/network/naming_engine.py --convention nc-site-seq --validate NYC01 --json
    python tools/network/naming_engine.py --list --json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = BASE_DIR / "data" / "network_canvas.db"


def _get_conn() -> sqlite3.Connection:
    conn = get_connection(str(DB_PATH))
    return conn


def _load_convention(convention_id: str, conn: sqlite3.Connection | None = None) -> dict | None:
    own = conn is None
    if own:
        conn = _get_conn()
    row = conn.execute("SELECT * FROM nc_naming_conventions WHERE id = %s", (convention_id,)).fetchone()
    if own:
        conn.close()
    if not row:
        return None
    d = dict(row)
    d["fields"] = json.loads(d.get("fields_json") or "[]")
    return d


def list_conventions() -> list[dict]:
    """List all naming conventions."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, name, description, pattern, separator, example, is_builtin "
        "FROM nc_naming_conventions ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def generate_name(
    convention_id: str,
    variables: dict[str, Any],
    topology_id: str = "",
) -> dict[str, Any]:
    """Generate a device name from a naming convention and variables.

    Args:
        convention_id: Convention ID (e.g., 'nc-site-seq').
        variables: Field values (e.g., {"SITE": "NYC", "SEQ": 1}).
        topology_id: Optional topology scope for sequence auto-increment.

    Returns:
        Dict with 'name', 'valid', 'errors', 'fields_used'.
    """
    conv = _load_convention(convention_id)
    if not conv:
        return {"error": f"Convention not found: {convention_id}", "name": None}

    fields = conv["fields"]
    pattern = conv["pattern"]
    separator = conv.get("separator", "")
    case_rule = conv.get("case_rule", "upper")
    max_length = conv.get("max_length", 63)

    errors: list[str] = []
    field_values: dict[str, str] = {}

    for field in fields:
        fname = field["name"]
        ftype = field.get("type", "freetext")
        fwidth = field.get("width", 0)
        fpad = field.get("pad", "none")
        freq = field.get("required", False)
        fvalues = field.get("values", [])

        raw_value = variables.get(fname)

        # Auto-sequence
        if ftype == "sequence" and raw_value is None:
            scope_key = "_".join(
                str(variables.get(f["name"], "")) for f in fields if f["name"] != fname
            )
            raw_value = next_sequence(convention_id, scope_key, topology_id)

        if raw_value is None and freq:
            errors.append(f"Required field '{fname}' is missing")
            field_values[fname] = ""
            continue

        value = str(raw_value) if raw_value is not None else ""

        # Type validation
        if ftype == "alpha" and value and not re.match(r"^[A-Za-z]+$", value):
            errors.append(f"Field '{fname}' must be letters only, got '{value}'")
        elif ftype == "numeric" and value and not re.match(r"^[0-9]+$", value):
            errors.append(f"Field '{fname}' must be digits only, got '{value}'")
        elif ftype == "alphanumeric" and value and not re.match(r"^[A-Za-z0-9]+$", value):
            errors.append(f"Field '{fname}' must be letters/digits only, got '{value}'")
        elif ftype in ("enum", "sequence") and fvalues and value and value.upper() not in [v.upper() for v in fvalues]:
            if ftype != "sequence":
                errors.append(f"Field '{fname}' must be one of {fvalues}, got '{value}'")

        # Width enforcement
        if fwidth > 0:
            if ftype in ("numeric", "sequence"):
                # Pad numeric/sequence with pad char (usually '0')
                pad_char = fpad if fpad != "none" else "0"
                value = value.zfill(fwidth) if pad_char == "0" else value.rjust(fwidth, pad_char)
            value = value[:fwidth]  # Truncate to max width

        field_values[fname] = value

    # Build name from pattern
    name = pattern
    for fname, fval in field_values.items():
        name = name.replace("{" + fname + "}", fval)
    name = name.replace("{SEP}", separator)

    # Apply case rule
    if case_rule == "upper":
        name = name.upper()
    elif case_rule == "lower":
        name = name.lower()

    # Length check
    if len(name) > max_length:
        errors.append(f"Generated name '{name}' exceeds max length {max_length} (got {len(name)})")

    return {
        "name": name,
        "valid": len(errors) == 0,
        "errors": errors,
        "fields_used": field_values,
        "convention": convention_id,
        "pattern": pattern,
    }


def validate_name(name: str, convention_id: str) -> dict[str, Any]:
    """Validate a name against a naming convention.

    Returns dict with 'valid', 'errors', 'parsed_fields'.
    """
    conv = _load_convention(convention_id)
    if not conv:
        return {"error": f"Convention not found: {convention_id}", "valid": False}

    parsed = parse_name(name, convention_id)
    errors: list[str] = []
    case_rule = conv.get("case_rule", "upper")
    max_length = conv.get("max_length", 63)

    if case_rule == "upper" and name != name.upper():
        errors.append("Name must be uppercase (convention rule)")
    elif case_rule == "lower" and name != name.lower():
        errors.append("Name must be lowercase (convention rule)")

    if len(name) > max_length:
        errors.append(f"Name exceeds max length {max_length}")

    if parsed.get("error"):
        errors.append(parsed["error"])

    # Validate each parsed field
    fields = conv["fields"]
    for field in fields:
        fname = field["name"]
        ftype = field.get("type", "freetext")
        fvalues = field.get("values", [])
        freq = field.get("required", False)
        parsed_val = parsed.get("fields", {}).get(fname, "")

        if freq and not parsed_val:
            errors.append(f"Required field '{fname}' could not be parsed")
        if ftype == "enum" and fvalues and parsed_val:
            if parsed_val.upper() not in [v.upper() for v in fvalues]:
                errors.append(f"Field '{fname}' value '{parsed_val}' not in allowed values: {fvalues}")

    return {
        "name": name,
        "convention": convention_id,
        "valid": len(errors) == 0,
        "errors": errors,
        "parsed_fields": parsed.get("fields", {}),
    }


def parse_name(name: str, convention_id: str) -> dict[str, Any]:
    """Reverse-parse a name into its field components.

    Uses field widths and types to split the name back into variables.
    """
    conv = _load_convention(convention_id)
    if not conv:
        return {"error": f"Convention not found: {convention_id}"}

    fields = conv["fields"]
    separator = conv.get("separator", "")
    case_rule = conv.get("case_rule", "upper")

    work_name = name.upper() if case_rule == "upper" else name.lower() if case_rule == "lower" else name

    parsed_fields: dict[str, str] = {}

    if separator:
        # Split by separator and map to fields (excluding SEP tokens)
        parts = work_name.split(separator)
        field_names = [f["name"] for f in fields]
        if len(parts) != len(field_names):
            return {
                "error": f"Expected {len(field_names)} parts separated by '{separator}', got {len(parts)}",
                "fields": {},
            }
        for i, field in enumerate(fields):
            parsed_fields[field["name"]] = parts[i]
    else:
        # No separator — use field widths to split positionally
        pos = 0
        for field in fields:
            fwidth = field.get("width", 0)
            ftype = field.get("type", "freetext")
            if fwidth > 0:
                parsed_fields[field["name"]] = work_name[pos:pos + fwidth]
                pos += fwidth
            elif ftype in ("numeric", "sequence"):
                # Consume all digits
                match = re.match(r"(\d+)", work_name[pos:])
                if match:
                    parsed_fields[field["name"]] = match.group(1)
                    pos += len(match.group(1))
            else:
                # Consume remaining
                parsed_fields[field["name"]] = work_name[pos:]
                pos = len(work_name)

    return {"name": name, "convention": convention_id, "fields": parsed_fields}


def next_sequence(convention_id: str, scope_key: str, topology_id: str = "") -> int:
    """Get and increment the next sequence number for a convention+scope.

    Scope key is typically the concatenation of other field values
    (e.g., 'NYC_CORE' for site=NYC, role=CORE).
    """
    conn = _get_conn()
    topo = topology_id or ""
    row = conn.execute(
        "SELECT id, current_value FROM nc_naming_sequences "
        "WHERE convention_id = %s AND scope_key = %s AND topology_id = %s",
        (convention_id, scope_key, topo),
    ).fetchone()

    if row:
        new_val = row["current_value"] + 1
        conn.execute(
            "UPDATE nc_naming_sequences SET current_value = %s WHERE id = %s",
            (new_val, row["id"]),
        )
    else:
        # Find start value from convention fields
        conv = _load_convention(convention_id, conn)
        start = 1
        if conv:
            for f in conv["fields"]:
                if f.get("type") == "sequence":
                    start = f.get("start", 1)
                    break
        new_val = start
        import uuid

        conn.execute(
            "INSERT INTO nc_naming_sequences (id, convention_id, scope_key, topology_id, current_value) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(uuid.uuid4())[:12], convention_id, scope_key, topo, new_val),
        )

    conn.commit()
    conn.close()
    return new_val


def bulk_generate(
    convention_id: str,
    count: int,
    variables: dict[str, Any],
    topology_id: str = "",
) -> list[dict]:
    """Generate multiple sequential names."""
    results = []
    for _ in range(count):
        result = generate_name(convention_id, variables, topology_id)
        results.append(result)
        # Remove SEQ from variables so next iteration auto-increments
        variables.pop("SEQ", None)
    return results


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Naming Convention Engine")
    parser.add_argument("--convention", help="Convention ID")
    parser.add_argument("--generate", help="Generate name from JSON variables")
    parser.add_argument("--validate", help="Validate a name")
    parser.add_argument("--parse", help="Parse a name into fields")
    parser.add_argument("--bulk", type=int, help="Generate N names")
    parser.add_argument("--topology-id", default="", help="Topology scope for sequences")
    parser.add_argument("--list", action="store_true", help="List all conventions")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.list:
        result = list_conventions()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for c in result:
                print(f"  {c['id']:25s} {c['name']:25s} ex={c.get('example', '')}")
        return

    if not args.convention:
        parser.print_help()
        return

    if args.generate:
        variables = json.loads(args.generate)
        if args.bulk:
            result = bulk_generate(args.convention, args.bulk, variables, args.topology_id)
        else:
            result = generate_name(args.convention, variables, args.topology_id)
        print(json.dumps(result, indent=2, default=str) if args.json else result.get("name", result))
    elif args.validate:
        result = validate_name(args.validate, args.convention)
        print(json.dumps(result, indent=2, default=str) if args.json else f"Valid: {result['valid']}")
    elif args.parse:
        result = parse_name(args.parse, args.convention)
        print(json.dumps(result, indent=2, default=str) if args.json else result)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
