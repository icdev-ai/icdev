#!/usr/bin/env python3
# CUI // SP-CTI
"""Compiled artifacts -> source_extractor IR, basis ``decompiled`` (rea-xlate-01, card ``rea``).

WHAT THIS IS. Every ICDEV understanding path (translation, modernization,
architecture extraction) starts from SOURCE. This module turns what REA
(``tools/analyzers/rea_adapter.py``) observed in an artifact that has none into
the same IR ``source_extractor.extract_source`` returns -- units with ``name``,
``kind``, ``signature`` (``parameters`` / ``return_type``), ``dependencies``,
``idioms`` and ``complexity`` -- so ``code_translator.translate_units``,
``legacy_analyzer.register_binary_application`` and
``architecture_extractor.extract_call_graph`` consume it unchanged.

Three recoveries, one per family the adapter runs at ``depth="xlate"``:

    binary            ``batch_decompile`` / ``procedure_pseudo_code`` ->
                      one ``function`` unit per procedure, its pseudocode as
                      ``source_code``. ``recovery: ghidra_pseudocode``.
    dotnet_assembly   ``inspect_managed_members`` -> one ``class`` unit per
                      TypeDef and one ``function`` unit per MethodDef, with the
                      decoded signature, call edges and an IL-derived
                      complexity. REA does not decompile C#, so ``source_code``
                      is a CIL skeleton that SAYS so. ``recovery: cil_metadata``.
    js_bundle         source-map recovery FIRST: ``sourcesContent`` of every map
                      inside the authorized target, run through the JavaScript
                      extractor. Without a map, the shipped bundle text itself.
                      ``recovery: source_map`` | ``bundle_text``.

REA ``get_call_graph`` / ``trace_call_path`` (and the .NET ``call_edges``)
become ``ir["call_graph"]`` -- nodes and ``method_call`` edges -- which
``legacy_analyzer`` writes as ``legacy_dependencies`` rows, the table
``extract_call_graph`` reads.

THE BASIS IS NEVER DROPPED. Every unit carries ``basis: decompiled``, a
``confidence`` (``low`` for pseudocode and CIL skeletons -- the
``rea_adapter`` / ``ghidra_headless`` value -- ``medium`` for source-map
content, which the bundle CLAIMS and nothing verified), the REA
``evidence_id`` it came from and the ``recovery`` used. A unit is a
reconstruction; it is never presented as recovered source.

RAILS. The report must carry the target sha256 and an operator authorization
basis (``owned`` | ``licensed`` | ``customer-authorized``) -- refused
(``authorization_missing``) otherwise. The JS path reads files ONLY inside the
authorized target, and only after re-digesting it and matching the report's
sha256 (``target_changed`` otherwise). Nothing is executed and nothing leaves
the machine. An unmeasured list is ``None``, never ``[]``.

Usage:
    python -m tools.translation.decompiled_extractor --report rea_report.json --output-ir ir.json --json
    python -m tools.translation.decompiled_extractor --target Fixture.dll --family dotnet_assembly \\
        --authorization-basis owned --output-ir ir.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

__all__ = [
    "BASIS",
    "extract_from_report",
    "call_graph_from_records",
]

BASIS = "decompiled"
CONFIDENCE_LOW = "low"
CONFIDENCE_SOURCE_MAP = "medium"
AUTHORIZATION_BASES: Tuple[str, ...] = ("owned", "licensed", "customer-authorized")

RECOVERY_PSEUDOCODE = "ghidra_pseudocode"
RECOVERY_CIL = "cil_metadata"
RECOVERY_SOURCE_MAP = "source_map"
RECOVERY_BUNDLE = "bundle_text"

#: Files the JS path will read inside a target, and how large.
MAX_JS_FILE_BYTES = 2_000_000
JS_SKIP_DIRS = {"node_modules", ".git"}

#: CIL opcodes that add a decision point. ``switch`` adds its target count.
_CIL_BRANCHES = re.compile(r"^(brtrue|brfalse|beq|bne|blt|bgt|ble|bge)(\.|$)")
_PSEUDO_SIGNATURE = re.compile(r"^\s*([\w\s\*]+?)\s*\b(\w+)\s*\(([^)]*)\)\s*$")
_PSEUDO_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_UNMINIFY = re.compile(r"(?<=[;{}])\s*(?=(?:async\s+)?function\s)")
_C_KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "do", "else", "case"}


def _source_extractor():
    from icdev.tools.translation import source_extractor

    return source_extractor


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _payload(entry: Mapping[str, Any]) -> Any:
    return entry.get("normalized_result")


def _by_tool(report: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for entry in report.get("body") or []:
        out.setdefault(entry.get("tool"), []).append(entry)
    return out


def _basis_fields(entry: Mapping[str, Any], recovery: str, confidence: str) -> Dict[str, Any]:
    return {
        "basis": BASIS,
        "confidence": confidence,
        "recovery": recovery,
        "evidence_id": entry.get("evidence_id"),
        "rea_tool": entry.get("tool"),
        "rea_confidence": entry.get("rea_confidence"),
    }


# ---------------------------------------------------------------------------
# Native: Ghidra pseudocode
# ---------------------------------------------------------------------------


def _pseudo_signature(code: str) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
    for line in code.splitlines():
        if not line.strip():
            continue
        m = _PSEUDO_SIGNATURE.match(line)
        if not m:
            return None, None
        params: List[Dict[str, Any]] = []
        raw = m.group(3).strip()
        if raw and raw != "void":
            for part in raw.split(","):
                part = part.strip()
                pm = re.match(r"^(.*?)(\w+)$", part)
                if pm and pm.group(1).strip():
                    params.append({"name": pm.group(2), "type": pm.group(1).strip()})
                else:
                    params.append({"name": None, "type": part})
        return m.group(1).strip(), params
    return None, None


def _pseudo_idioms(code: str) -> List[str]:
    idioms = []
    if re.search(r"\b(FUN|DAT|LAB|PTR)_[0-9a-fA-F]+", code):
        idioms.append("unrecovered_symbol_names")
    if "(*(code *)" in code:
        idioms.append("indirect_call")
    if re.search(r"\bundefined\d*\b", code):
        idioms.append("unrecovered_types")
    if re.search(r"\bswitch\b|\bcase\b", code):
        idioms.append("switch_dispatch")
    return idioms


def _native_units(by_tool: Mapping[str, List[Dict[str, Any]]], sha: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    se = _source_extractor()
    items: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    failed: List[str] = []
    for entry in by_tool.get("batch_decompile", []):
        payload = _payload(entry)
        batch = payload.get("items") if isinstance(payload, dict) else None
        for item in batch or []:
            if item.get("status") == "ok" and isinstance(item.get("pseudocode"), str):
                items.append((entry, item))
            else:
                failed.append(str(item.get("address")))
    for entry in by_tool.get("procedure_pseudo_code", []):
        if isinstance(_payload(entry), str):
            items.append((entry, {"address": None, "procedure": {}, "pseudocode": _payload(entry)}))

    units: List[Dict[str, Any]] = []
    seen = set()
    for entry, item in items:
        code = item["pseudocode"].replace("\r\n", "\n").strip("\n")
        proc = item.get("procedure") or {}
        ret, params = _pseudo_signature(code)
        sig_name = None
        m = _PSEUDO_SIGNATURE.match(next((ln for ln in code.splitlines() if ln.strip()), ""))
        if m:
            sig_name = m.group(2)
        name = proc.get("name") or sig_name or str(item.get("address"))
        if name in seen:
            continue
        seen.add(name)
        address = proc.get("address") or item.get("address")
        lines = code.splitlines()
        units.append(
            {
                "kind": "function",
                "name": name,
                "is_async": False,
                "parameters": params,
                "return_type": ret,
                "line_start": None,
                "line_end": None,
                "line_count": len(lines),
                "source_code": code,
                "source_hash": _hash(code),
                "idioms": _pseudo_idioms(code),
                "complexity": se._count_branches(code),
                "dependencies": sorted(
                    {c for c in _PSEUDO_CALL.findall("\n".join(lines[1:])) if c not in _C_KEYWORDS and c != name}
                ),
                "address": address,
                "file_path": f"decompiled://{sha[:12]}/{address or name}",
                "source_file": name,
                **_basis_fields(entry, RECOVERY_PSEUDOCODE, CONFIDENCE_LOW),
            }
        )
    return units, failed


# ---------------------------------------------------------------------------
# .NET: CIL metadata (REA does not decompile C#)
# ---------------------------------------------------------------------------


def _cil_complexity(body: Mapping[str, Any]) -> Optional[int]:
    if body.get("status") != "present":
        return None
    count = 1
    for opcode, n in (body.get("opcode_counts") or {}).items():
        if _CIL_BRANCHES.match(opcode):
            count += int(n)
    for anchor in body.get("anchors") or []:
        if anchor.get("opcode") == "switch":
            try:
                count += int(anchor.get("operand"))
            except (TypeError, ValueError):
                pass
    return count


def _cil_idioms(method: Mapping[str, Any], body: Mapping[str, Any]) -> List[str]:
    idioms = []
    name = method.get("name") or ""
    if body.get("status") == "absent":
        idioms.append("no_cil_body")
    if name in (".ctor", ".cctor"):
        idioms.append("constructor")
    if name.startswith(("get_", "set_")):
        idioms.append("property_accessor")
    if body.get("exception_regions"):
        idioms.append("exception_handling")
    if any(a.get("opcode") == "switch" for a in body.get("anchors") or []):
        idioms.append("switch_dispatch")
    return idioms


def _cil_skeleton(
    qualified: str, sig: Mapping[str, Any], body: Mapping[str, Any], calls: List[str], fields: List[str]
) -> str:
    params = ", ".join(sig.get("parameter_types") or [])
    lines = [
        "// basis: decompiled -- CIL metadata reconstruction. REA read the metadata and",
        "// instruction anchors only; it did NOT decompile C#. This is not source.",
        f"// {qualified}({params}) -> {sig.get('return_type')}",
    ]
    if body.get("status") == "present":
        lines.append(
            f"// il_size={body.get('il_size')} instructions={body.get('instruction_count')}"
            f" max_stack={body.get('max_stack')}"
        )
        ops = " ".join(f"{k}x{v}" for k, v in sorted((body.get("opcode_counts") or {}).items()))
        lines.append(f"// opcodes: {ops}")
        consts = [a.get("operand") for a in body.get("anchors") or [] if a.get("operand_kind") == "constant"]
        if consts:
            lines.append(f"// constants: {', '.join(str(c) for c in consts)}")
    else:
        lines.append(f"// no CIL body (status={body.get('status')}): extern / P/Invoke / abstract")
    if calls:
        lines.append(f"// calls: {', '.join(calls)}")
    if fields:
        lines.append(f"// fields: {', '.join(fields)}")
    return "\n".join(lines)


def _dotnet_units(
    by_tool: Mapping[str, List[Dict[str, Any]]], sha: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    units: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    imports: List[str] = []
    for entry in by_tool.get("inspect_managed_members", []):
        nr = _payload(entry)
        if not isinstance(nr, dict):
            continue
        token_name: Dict[str, str] = {}
        for method in nr.get("methods") or []:
            short_type = (method.get("declaring_type") or "").rsplit(".", 1)[-1]
            token_name[method.get("token")] = f"{short_type}.{method.get('name')}"
        calls_by: Dict[str, List[Dict[str, Any]]] = {}
        for edge in nr.get("call_edges") or []:
            calls_by.setdefault(edge.get("caller_token"), []).append(edge)
        fields_by: Dict[str, List[str]] = {}
        for access in nr.get("field_accesses") or []:
            fields_by.setdefault(access.get("method_token"), []).append(access.get("field_name"))
        for ref in nr.get("member_refs") or []:
            if ref.get("name") and ref["name"] not in imports and not ref["name"].startswith("."):
                imports.append(ref["name"])

        for typ in nr.get("types") or []:
            if typ.get("name") == "<Module>":
                continue
            methods = [
                {"name": m.get("name"), "line_start": None, "line_end": None}
                for m in nr.get("methods") or []
                if m.get("declaring_type_token") == typ.get("token")
            ]
            text = f"// basis: decompiled -- CIL TypeDef {typ.get('full_name')}; not source."
            units.append(
                {
                    "kind": "class",
                    "name": typ.get("name"),
                    "qualified_name": typ.get("full_name"),
                    "bases": [],
                    "methods": methods,
                    "method_count": len(methods),
                    "line_start": None,
                    "line_end": None,
                    "line_count": None,
                    "source_code": text,
                    "source_hash": _hash(text),
                    "idioms": [],
                    "complexity": None,
                    "dependencies": [],
                    "token": typ.get("token"),
                    "file_path": f"decompiled://{sha[:12]}/{typ.get('full_name')}",
                    "source_file": typ.get("full_name"),
                    **_basis_fields(entry, RECOVERY_CIL, CONFIDENCE_LOW),
                }
            )
        for method in nr.get("methods") or []:
            sig = method.get("signature") or {}
            body = method.get("body") or {}
            name = token_name[method.get("token")]
            qualified = f"{method.get('declaring_type')}.{method.get('name')}"
            mine = calls_by.get(method.get("token"), [])
            internal = [
                token_name[e["target_token"]]
                for e in mine
                if e.get("target_kind") == "method-def" and e.get("target_token") in token_name
            ]
            dependencies = [e.get("target_name") for e in mine]
            code = _cil_skeleton(qualified, sig, body, dependencies, fields_by.get(method.get("token"), []))
            types = sig.get("parameter_types")
            units.append(
                {
                    "kind": "function",
                    "name": name,
                    "qualified_name": qualified,
                    "parent": method.get("declaring_type"),
                    "is_async": False,
                    "parameters": [{"name": None, "type": t} for t in types] if isinstance(types, list) else None,
                    "return_type": sig.get("return_type"),
                    "line_start": None,
                    "line_end": None,
                    "line_count": body.get("instruction_count"),
                    "source_code": code,
                    "source_hash": _hash(code),
                    "idioms": _cil_idioms(method, body),
                    "complexity": _cil_complexity(body),
                    "dependencies": dependencies,
                    "calls": internal,
                    "token": method.get("token"),
                    "il_sha256": body.get("il_sha256"),
                    "file_path": f"decompiled://{sha[:12]}/{qualified}",
                    "source_file": method.get("declaring_type"),
                    **_basis_fields(entry, RECOVERY_CIL, CONFIDENCE_LOW),
                }
            )
            for e in mine:
                if e.get("target_kind") == "method-def" and e.get("target_token") in token_name:
                    edges.append(
                        {
                            "source": name,
                            "target": token_name[e["target_token"]],
                            "type": "method_call",
                            "basis": "observed",
                            "evidence_id": entry.get("evidence_id"),
                            "via": "call_edges",
                        }
                    )
    return units, edges, imports


# ---------------------------------------------------------------------------
# JavaScript: source map first, bundle text second
# ---------------------------------------------------------------------------


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _js_files(root: Path) -> List[Path]:
    if root.is_file():
        return [root]
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in (".js", ".mjs", ".cjs", ".map")
        and not any(part in JS_SKIP_DIRS for part in p.relative_to(root).parts)
        and p.stat().st_size <= MAX_JS_FILE_BYTES
    )


def _load_map(js: Path, root: Path) -> Optional[Dict[str, Any]]:
    text = js.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"//[#@]\s*sourceMappingURL=(\S+)\s*$", text)
    if m and m.group(1).startswith("data:application/json"):
        raw = m.group(1).split(",", 1)[1] if "," in m.group(1) else ""
        try:
            return json.loads(base64.b64decode(raw).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
    candidate = js.parent / (m.group(1) if m else js.name + ".map")
    if "://" in str(candidate) or not _inside(root if root.is_dir() else root.parent, candidate):
        return None
    if not candidate.is_file():
        return None
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _js_units(
    report: Mapping[str, Any], by_tool: Mapping[str, List[Dict[str, Any]]], sha: str
) -> Tuple[List[Dict[str, Any]], List[str], List[str], List[str]]:
    se = _source_extractor()
    root = Path(str(report.get("target")))
    entry = (by_tool.get("analyze_javascript_application") or [{}])[0]
    units: List[Dict[str, Any]] = []
    imports: List[str] = []
    residual: List[str] = []
    recoveries: List[str] = []
    scripts = [p for p in _js_files(root) if p.suffix != ".map"]
    for js in scripts:
        rel = js.relative_to(root).as_posix() if root.is_dir() else js.name
        smap = _load_map(js, root)
        sources = (smap or {}).get("sources") or []
        contents = (smap or {}).get("sourcesContent") or []
        recovered = False
        for i, source in enumerate(sources):
            content = contents[i] if i < len(contents) else None
            if not isinstance(content, str):
                residual.append(f"source_map_without_content: {rel} -> {source}")
                continue
            recovered = True
            found, imps = se._extract_regex(content, source, "javascript")
            imports.extend(x for x in imps if x and x not in imports)
            module_name = Path(source).stem
            units.append(
                _js_module_unit(
                    module_name, source, content, imps, entry, sha, RECOVERY_SOURCE_MAP, CONFIDENCE_SOURCE_MAP, rel
                )
            )
            for unit in found:
                units.append(_js_unit(unit, source, entry, sha, RECOVERY_SOURCE_MAP, CONFIDENCE_SOURCE_MAP, rel))
        if recovered:
            recoveries.append(RECOVERY_SOURCE_MAP)
            continue
        text = js.read_text(encoding="utf-8", errors="replace")
        # A minified bundle is one line; the JS extractor reads one declaration per line.
        found, imps = se._extract_regex(_UNMINIFY.sub("\n", text), rel, "javascript")
        imports.extend(x for x in imps if x and x not in imports)
        units.append(_js_module_unit(js.stem, rel, text, imps, entry, sha, RECOVERY_BUNDLE, CONFIDENCE_LOW, rel))
        for unit in found:
            units.append(_js_unit(unit, rel, entry, sha, RECOVERY_BUNDLE, CONFIDENCE_LOW, rel))
        recoveries.append(RECOVERY_BUNDLE)
    return units, imports, residual, recoveries


def _js_module_unit(name, source, content, imps, entry, sha, recovery, confidence, bundle):
    return {
        "kind": "module",
        "name": name,
        "line_start": 1,
        "line_end": content.count("\n") + 1,
        "line_count": content.count("\n") + 1,
        "source_code": content,
        "source_hash": _hash(content),
        "idioms": [],
        "complexity": _source_extractor()._count_branches(content),
        "dependencies": [x for x in imps if x],
        "file_path": f"decompiled://{sha[:12]}/{source}",
        "source_file": source,
        "bundle": bundle,
        **_basis_fields(entry, recovery, confidence),
    }


def _js_unit(unit, source, entry, sha, recovery, confidence, bundle):
    out = dict(unit)
    out.update(
        {
            "file_path": f"decompiled://{sha[:12]}/{source}",
            "source_file": source,
            "bundle": bundle,
            **_basis_fields(entry, recovery, confidence),
        }
    )
    return out


# ---------------------------------------------------------------------------
# Call graph
# ---------------------------------------------------------------------------


def call_graph_from_records(
    records: Iterable[Mapping[str, Any]], names: Optional[Mapping[str, str]] = None
) -> Optional[Dict[str, Any]]:
    """REA ``get_call_graph`` / ``trace_call_path`` evidence -> ``{nodes, edges}``.

    ``names`` maps an address to a unit name (from the decompiled units), so an
    edge lands on the same component the unit registers. ``None`` when no call
    graph record was given -- not measured is not "no calls".
    """
    names = dict(names or {})
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
    failed: List[str] = []
    seen_record = False

    def node(address: str) -> str:
        label = names.get(address, address)
        nodes.setdefault(address, {"id": label, "address": address, "name": label})
        return label

    for rec in records:
        nr = rec.get("normalized_result")
        evidence = rec.get("evidence_id")
        if rec.get("tool") == "get_call_graph" and isinstance(nr, dict):
            seen_record = True
            for level in sorted(nr, key=lambda k: int(k) if str(k).isdigit() else 0):
                for entry in nr[level] or []:
                    src = entry.get("address")
                    node(src)
                    if entry.get("status") != "ok":
                        failed.append(src)
                        continue
                    for target in entry.get("calls") or []:
                        key = (node(src), node(target))
                        edges.setdefault(
                            key,
                            {
                                "source": key[0],
                                "target": key[1],
                                "type": "method_call",
                                "basis": "observed",
                                "evidence_id": evidence,
                                "via": "get_call_graph",
                            },
                        )
        elif rec.get("tool") == "trace_call_path" and isinstance(nr, dict):
            seen_record = True
            for n in nr.get("nodes") or []:
                node(n.get("address"))
            for e in nr.get("edges") or []:
                key = (node(e.get("source_address")), node(e.get("target_address")))
                edges.setdefault(
                    key,
                    {
                        "source": key[0],
                        "target": key[1],
                        "type": "method_call",
                        "basis": "observed",
                        "evidence_id": evidence,
                        "via": "trace_call_path",
                    },
                )
    if not seen_record:
        return None
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "failed_nodes": failed,
        "limitations": ["Direct provider call relationships may omit unresolved indirect calls."],
    }


# ---------------------------------------------------------------------------
# The entrypoint
# ---------------------------------------------------------------------------


def _refusal(report: Mapping[str, Any], status: str, reason: str, detail: str) -> Dict[str, Any]:
    return {
        "basis": BASIS,
        "status": status,
        "reason": reason,
        "reason_detail": detail,
        "family": report.get("family"),
        "source_path": report.get("target"),
        "provenance": report.get("provenance"),
        "units": None,
        "total_units": None,
        "imports": None,
        "call_graph": None,
        "limitations": report.get("limitations"),
        "residual_unknowns": report.get("residual_unknowns"),
    }


def _calls_from_source(units: List[Dict[str, Any]]) -> None:
    """``calls`` (what ``code_translator`` orders by) for units that lack it."""
    names = {u["name"] for u in units if u.get("kind") == "function"}
    for unit in units:
        if "calls" in unit:
            continue
        deps = set(unit.get("dependencies") or [])
        code = unit.get("source_code") or ""
        unit["calls"] = sorted(
            n for n in names if n != unit["name"] and (n in deps or re.search(rf"\b{re.escape(n)}\s*\(", code))
        )


def extract_from_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    """One ``rea_adapter`` report -> source_extractor-shaped IR, basis ``decompiled``."""
    provenance = report.get("provenance") or {}
    sha = provenance.get("target_sha256")
    auth = provenance.get("authorization_basis")
    if not sha or auth not in AUTHORIZATION_BASES:
        return _refusal(
            report,
            "error",
            "authorization_missing",
            "the report carries no target sha256 and operator authorization basis "
            f"({' | '.join(AUTHORIZATION_BASES)}); nothing was normalized.",
        )
    if report.get("status") not in ("ok", "partial"):
        return _refusal(
            report,
            report.get("status") or "error",
            report.get("reason") or "rea_not_ok",
            report.get("reason_detail") or "the REA run produced no evidence to normalize.",
        )

    family = report.get("family")
    by_tool = _by_tool(report)
    units: List[Dict[str, Any]] = []
    imports: List[str] = []
    residual = list(report.get("residual_unknowns") or []) if report.get("residual_unknowns") is not None else None
    extra_residual: List[str] = []
    edges: List[Dict[str, Any]] = []
    recoveries: List[str] = []
    language = dialect = None

    if family == "binary":
        language, dialect = "c", "ghidra_pseudo_c"
        units, failed = _native_units(by_tool, sha)
        extra_residual.extend(f"decompile_failed: {a}" for a in failed)
        recoveries = [RECOVERY_PSEUDOCODE] if units else []
        if not units:
            return _refusal(
                report,
                "unavailable",
                "no_pseudocode",
                "no batch_decompile / procedure_pseudo_code evidence: run rea_adapter with "
                "depth='xlate' and procedure=<entry>.",
            )
    elif family == "dotnet_assembly":
        language, dialect = "csharp", "cil"
        units, edges, imports = _dotnet_units(by_tool, sha)
        recoveries = [RECOVERY_CIL] if units else []
        if not units:
            return _refusal(
                report,
                "unavailable",
                "members_not_inspected",
                "no inspect_managed_members evidence: run rea_adapter with depth='xlate'.",
            )
    elif family == "js_bundle":
        language, dialect = "javascript", None
        from icdev.tools.mcp_client import target_auth

        try:
            current = target_auth.target_digest(report.get("target"))
        except Exception as exc:  # noqa: BLE001 -- unreadable: said so on the refusal
            return _refusal(report, "error", "source_unreadable", f"{type(exc).__name__}: {exc}")
        if current != sha:
            return _refusal(
                report,
                "error",
                "target_changed",
                "the target's bytes no longer match the sha256 REA analysed; nothing was read.",
            )
        units, imports, more, recoveries = _js_units(report, by_tool, sha)
        extra_residual.extend(more)
        if not units:
            return _refusal(
                report,
                "unavailable",
                "no_javascript_units",
                "no source map content and no extractable functions in the bundle.",
            )
    else:
        return _refusal(
            report,
            "unavailable",
            "family_not_translatable",
            f"family {family!r} has no translation path (binary, dotnet_assembly, js_bundle).",
        )

    names = {u["address"]: u["name"] for u in units if u.get("address")}
    graph = call_graph_from_records(report.get("body") or [], names)
    if edges:
        graph = graph or {"nodes": [], "edges": [], "failed_nodes": [], "limitations": []}
        known = {n["id"] for n in graph["nodes"]}
        for e in edges:
            for end in (e["source"], e["target"]):
                if end not in known:
                    graph["nodes"].append({"id": end, "address": None, "name": end})
                    known.add(end)
            graph["edges"].append(e)
    if graph is not None and family == "binary":
        decompiled = {u.get("address") for u in units}
        graph["undecompiled_procedures"] = sorted(
            n["address"] for n in graph["nodes"] if n.get("address") and n["address"] not in decompiled
        )
        for e in graph["edges"]:
            for unit in units:
                if unit["name"] == e["source"] and e["target"] not in unit.setdefault("calls", []):
                    unit["calls"].append(e["target"])
    _calls_from_source(units)

    if extra_residual:
        residual = (residual or []) + extra_residual
    se = _source_extractor()
    ordered = se.build_dependency_graph(units)
    status = report.get("status")
    if extra_residual and status == "ok":
        status = "partial"
    return {
        "basis": BASIS,
        "status": status,
        "reason": report.get("reason") if status == report.get("status") else "residual_unknowns",
        "reason_detail": report.get("reason_detail"),
        "language": language,
        "dialect": dialect,
        "family": family,
        "recovery": sorted(set(recoveries)),
        "source_path": report.get("target"),
        "file_count": None,
        "total_lines": sum(u.get("line_count") or 0 for u in units),
        "total_units": len(ordered),
        "units": ordered,
        "dependency_order": [u["name"] for u in ordered],
        "imports": sorted(imports),
        "call_graph": graph,
        "provenance": {
            **provenance,
            "evidence_ids": report.get("evidence_ids"),
            "analyzer": report.get("analyzer"),
            "rea_version": report.get("rea_version"),
        },
        "limitations": report.get("limitations"),
        "residual_unknowns": residual,
        "notice": (
            "Every unit is a reconstruction from a compiled artifact (basis: decompiled). "
            "It is not recovered source and must not be presented as such."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", help="a rea_adapter --json report file")
    ap.add_argument("--target", help="run rea_adapter (depth=xlate) on this artifact instead")
    ap.add_argument("--family", choices=["binary", "dotnet_assembly", "js_bundle"])
    ap.add_argument("--procedure", help="entry procedure for a native call graph")
    ap.add_argument("--engine", default=None)
    ap.add_argument("--authorization-basis", choices=AUTHORIZATION_BASES)
    ap.add_argument("--output-ir", help="write the IR JSON here (translate_unit --ir-file)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.report:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    elif args.target and args.family:
        from icdev.tools.analyzers import rea_adapter

        report = rea_adapter.analyze(
            args.target,
            family=args.family,
            engine=args.engine,
            procedure=args.procedure,
            depth="xlate",
            authorization_basis=args.authorization_basis,
        )
    else:
        ap.error("--report, or --target with --family, is required")
        return 2
    ir = extract_from_report(report)
    if args.output_ir:
        out = Path(args.output_ir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ir, indent=2, default=str), encoding="utf-8")
    if args.json:
        summary = dict(ir)
        if summary.get("units"):
            summary["units"] = [{k: v for k, v in u.items() if k != "source_code"} for u in summary["units"]]
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"{str(ir['status']).upper():<12} basis={ir['basis']} {ir.get('family')} units={ir['total_units']}")
        if ir.get("reason"):
            print(f"  reason: {ir['reason']} -- {ir.get('reason_detail')}")
    return 0 if ir["status"] in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
