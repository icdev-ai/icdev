# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""SysML v1.6 XMI Parser for Cameo Systems Modeler exports.

Parses MagicDraw/Cameo XMI files using Python stdlib xml.etree.ElementTree
(no lxml — air-gapped environment).  Extracts SysML blocks, interface blocks,
activities, requirements, state machines, use cases, and all relationship types
(structural + SysML dependency stereotypes).

Stores parsed elements into the ICDEV SQLite database (sysml_elements,
sysml_relationships, model_imports tables) and records an immutable audit
trail entry.

CLI usage:
    python tools/mbse/xmi_parser.py --project-id proj-123 --file model.xmi
    python tools/mbse/xmi_parser.py --project-id proj-123 --file model.xmi --validate-only
    python tools/mbse/xmi_parser.py --project-id proj-123 --file model.xmi --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Audit logger — graceful fallback for standalone execution
# ---------------------------------------------------------------------------
try:
    from tools.audit.audit_logger import log_event  # type: ignore
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def log_event(**kwargs) -> int:  # noqa: D103 – stub
        return -1

# ---------------------------------------------------------------------------
# Well-known XMI / UML / SysML / MagicDraw namespace URIs
# ---------------------------------------------------------------------------
KNOWN_NAMESPACES: Dict[str, str] = {
    "xmi": "http://www.omg.org/spec/XMI/20131001",
    "uml": "http://www.omg.org/spec/UML/20131001",
    "sysml": "http://www.omg.org/spec/SysML/20181001",
    "md": "http://www.nomagic.com/magicdraw/UML/2.5.1",
}

# Older namespace variants that Cameo may emit
NAMESPACE_ALTERNATIVES: Dict[str, List[str]] = {
    "xmi": [
        "http://www.omg.org/spec/XMI/20131001",
        "http://www.omg.org/spec/XMI/20110701",
        "http://www.omg.org/spec/XMI/2.5.1",
        "http://www.omg.org/XMI",
    ],
    "uml": [
        "http://www.omg.org/spec/UML/20131001",
        "http://www.omg.org/spec/UML/20110701",
        "http://www.eclipse.org/uml2/5.0.0/UML",
        "http://schema.omg.org/spec/UML/2.1",
    ],
    "sysml": [
        "http://www.omg.org/spec/SysML/20181001",
        "http://www.omg.org/spec/SysML/20150709",
        "http://www.omg.org/spec/SysML/20120401",
    ],
    "md": [
        "http://www.nomagic.com/magicdraw/UML/2.5.1",
        "http://www.nomagic.com/magicdraw/UML/2.5",
        "http://www.nomagic.com/magicdraw/UML/2.4.1",
    ],
}

# SysML relationship stereotype keywords (lower-cased for matching)
SYSML_REL_STEREOTYPES = {
    "satisfy", "derive", "verify", "refine", "trace", "allocate", "copy",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    """Generate a prefixed UUID for a SysML element."""
    return f"sysml-{uuid.uuid4()}"


def _file_hash(file_path: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ts() -> str:
    """Current ISO-8601 timestamp."""
    return datetime.now().isoformat()


def _attr(element: ET.Element, local_name: str, ns: Dict[str, str]) -> Optional[str]:
    """Retrieve an attribute by namespace-qualified or plain local name.

    Tries ``{xmi_ns}local_name``, ``xmi:local_name``, and bare ``local_name``.
    """
    xmi_ns = ns.get("xmi", "")
    # Fully qualified
    val = element.get(f"{{{xmi_ns}}}{local_name}") if xmi_ns else None
    if val is not None:
        return val
    # Prefixed (sometimes kept literally in attribute names)
    val = element.get(f"xmi:{local_name}")
    if val is not None:
        return val
    # Bare
    return element.get(local_name)


def _xmi_id(element: ET.Element, ns: Dict[str, str]) -> Optional[str]:
    """Extract the ``xmi:id`` attribute from *element*."""
    return _attr(element, "id", ns)


def _xmi_type(element: ET.Element, ns: Dict[str, str]) -> Optional[str]:
    """Extract the ``xmi:type`` attribute from *element*."""
    return _attr(element, "type", ns)


def _qualified_name(element: ET.Element, root: ET.Element) -> str:
    """Build a colon-separated qualified name by walking up parent map."""
    # ElementTree doesn't maintain parent references natively, so we build
    # one if not yet cached on the root element.
    parent_map = getattr(root, "_parent_map", None)
    if parent_map is None:
        parent_map = {c: p for p in root.iter() for c in p}
        root._parent_map = parent_map  # type: ignore[attr-defined]
    parts: List[str] = []
    cur = element
    while cur is not None:
        name = cur.get("name")
        if name:
            parts.append(name)
        cur = parent_map.get(cur)
    parts.reverse()
    return "::".join(parts) if parts else ""


def _find_all_ns(root: ET.Element, tag_local: str, ns: Dict[str, str],
                 ns_key: str = "uml") -> List[ET.Element]:
    """Find all descendant elements matching *tag_local* under any known
    namespace variant for *ns_key*.
    """
    results: List[ET.Element] = []
    uri = ns.get(ns_key, "")
    if uri:
        results.extend(root.iter(f"{{{uri}}}{tag_local}"))
    # Also try without namespace (some exports omit it)
    results.extend(e for e in root.iter(tag_local) if e not in results)
    return results


def _get_description(element: ET.Element, ns: Dict[str, str]) -> str:
    """Extract the description / body / documentation from an element.

    Cameo stores documentation in ``ownedComment`` children with a ``body``
    sub-element, or as a ``body`` attribute directly.
    """
    # ownedComment → body
    for comment in element.iter("ownedComment"):
        body_el = comment.find("body")
        if body_el is not None and body_el.text:
            return body_el.text.strip()
        body_attr = comment.get("body")
        if body_attr:
            return body_attr.strip()
    # Direct body attribute (rare)
    body = element.get("body")
    if body:
        return body.strip()
    return ""


# ---------------------------------------------------------------------------
# Namespace detection
# ---------------------------------------------------------------------------

def _detect_namespaces(root: ET.Element) -> Dict[str, str]:
    """Detect XMI/UML/SysML/MagicDraw namespaces from the root element.

    Inspects the root tag, registered namespace map, and tag prefixes across
    all children.  Returns a dict mapping short keys ('xmi', 'uml', 'sysml',
    'md') to their detected namespace URIs.
    """
    detected: Dict[str, str] = {}

    # 1. Collect all namespace URIs declared in the document.
    #    ElementTree exposes them through iterparse or the tag itself.
    declared_uris: set = set()

    # Root tag may carry a namespace: {uri}LocalName
    root_tag = root.tag
    if root_tag.startswith("{"):
        uri = root_tag[1:root_tag.index("}")]
        declared_uris.add(uri)

    # Walk all elements for additional namespace URIs
    for elem in root.iter():
        tag = elem.tag
        if tag.startswith("{"):
            declared_uris.add(tag[1:tag.index("}")])
        # Attributes may also carry namespace URIs
        for attr_name in elem.attrib:
            if attr_name.startswith("{"):
                declared_uris.add(attr_name[1:attr_name.index("}")])

    # 2. Match declared URIs to known namespace keys
    for key, alternatives in NAMESPACE_ALTERNATIVES.items():
        for alt_uri in alternatives:
            if alt_uri in declared_uris:
                detected[key] = alt_uri
                break

    # 3. Fallback: use well-known defaults for anything not detected
    for key, default_uri in KNOWN_NAMESPACES.items():
        if key not in detected:
            detected[key] = default_uri

    return detected


# ---------------------------------------------------------------------------
# Stereotype resolution
# ---------------------------------------------------------------------------

def _build_stereotype_map(root: ET.Element, ns: Dict[str, str]) -> Dict[str, str]:
    """Build a mapping from xmi:id → stereotype name.

    Cameo XMI exports stereotype applications as top-level elements whose tag
    contains the stereotype name and whose ``base_Class`` (or similar
    ``base_*``) attribute references the model element xmi:id.

    Returns mapping ``{element_xmi_id: stereotype_name}``.
    """
    stereo_map: Dict[str, str] = {}

    ns.get("sysml", "")
    ns.get("md", "")

    # Iterate top-level children of root looking for stereotype applications
    for child in root:
        tag = child.tag
        # Strip namespace to get local name
        if "}" in tag:
            local = tag.split("}", 1)[1]
        else:
            local = tag

        # Common SysML stereotypes in Cameo exports
        local.lower()
        target_xmi_id: Optional[str] = None

        # Look for base_Class, base_NamedElement, base_Abstraction, etc.
        for attr_name, attr_val in child.attrib.items():
            clean_attr = attr_name
            if "}" in clean_attr:
                clean_attr = clean_attr.split("}", 1)[1]
            if clean_attr.startswith("base_"):
                target_xmi_id = attr_val
                break

        if target_xmi_id:
            stereo_map[target_xmi_id] = local

    return stereo_map


# ---------------------------------------------------------------------------
# Element extraction functions
# ---------------------------------------------------------------------------

def _extract_blocks(root: ET.Element, ns: Dict[str, str]) -> List[Dict[str, Any]]:
    """Extract <<Block>> stereotyped classes from XMI.

    Looks for ``packagedElement`` nodes with ``xmi:type="uml:Class"`` and
    matches them against the stereotype map for 'Block'.
    """
    stereo_map = _build_stereotype_map(root, ns)
    blocks: List[Dict[str, Any]] = []

    for elem in root.iter("packagedElement"):
        xmi_t = _xmi_type(elem, ns)
        if xmi_t not in ("uml:Class", "Class"):
            continue
        xid = _xmi_id(elem, ns)
        if not xid:
            continue

        stereo = stereo_map.get(xid, "")
        if stereo.lower() not in ("block", "sysml::block", "sysml::blocks::block"):
            # Also accept if no explicit stereotype but tag hints at block
            if stereo:
                continue  # Has a different stereotype

        name = elem.get("name", "")
        if not name:
            continue

        # Collect owned attributes (properties / value properties)
        properties: List[Dict[str, str]] = []
        for attr in elem.iter("ownedAttribute"):
            prop_name = attr.get("name", "")
            prop_type = _xmi_type(attr, ns) or ""
            prop_id = _xmi_id(attr, ns) or ""
            if prop_name:
                properties.append({
                    "name": prop_name,
                    "type": prop_type,
                    "xmi_id": prop_id,
                    "visibility": attr.get("visibility", "public"),
                })

        # Collect ports
        ports: List[Dict[str, str]] = []
        for port in elem.iter("ownedPort"):
            port_name = port.get("name", "")
            port_id = _xmi_id(port, ns) or ""
            if port_name or port_id:
                ports.append({
                    "name": port_name,
                    "xmi_id": port_id,
                    "type": _xmi_type(port, ns) or "port",
                })

        blocks.append({
            "id": _new_id(),
            "xmi_id": xid,
            "element_type": "block",
            "name": name,
            "qualified_name": _qualified_name(elem, root),
            "stereotype": stereo or "Block",
            "description": _get_description(elem, ns),
            "properties": json.dumps({
                "attributes": properties,
                "ports": ports,
            }),
            "diagram_type": "bdd",
        })

    return blocks


def _extract_interface_blocks(root: ET.Element, ns: Dict[str, str]) -> List[Dict[str, Any]]:
    """Extract <<InterfaceBlock>> stereotyped classes from XMI."""
    stereo_map = _build_stereotype_map(root, ns)
    iblocks: List[Dict[str, Any]] = []

    for elem in root.iter("packagedElement"):
        xmi_t = _xmi_type(elem, ns)
        if xmi_t not in ("uml:Class", "Class", "uml:Interface", "Interface"):
            continue
        xid = _xmi_id(elem, ns)
        if not xid:
            continue

        stereo = stereo_map.get(xid, "")
        if "interfaceblock" not in stereo.lower() and "interface_block" not in stereo.lower():
            continue

        name = elem.get("name", "")
        if not name:
            continue

        # Collect flow properties
        flow_props: List[Dict[str, str]] = []
        for attr in elem.iter("ownedAttribute"):
            prop_name = attr.get("name", "")
            if prop_name:
                flow_props.append({
                    "name": prop_name,
                    "type": _xmi_type(attr, ns) or "",
                    "xmi_id": _xmi_id(attr, ns) or "",
                    "direction": attr.get("direction", "inout"),
                })

        iblocks.append({
            "id": _new_id(),
            "xmi_id": xid,
            "element_type": "interface_block",
            "name": name,
            "qualified_name": _qualified_name(elem, root),
            "stereotype": stereo or "InterfaceBlock",
            "description": _get_description(elem, ns),
            "properties": json.dumps({"flow_properties": flow_props}),
            "diagram_type": "bdd",
        })

    return iblocks


def _extract_activities(root: ET.Element, ns: Dict[str, str]) -> List[Dict[str, Any]]:
    """Extract Activity elements with actions, control flows, and object flows."""
    activities: List[Dict[str, Any]] = []

    for elem in root.iter("packagedElement"):
        xmi_t = _xmi_type(elem, ns)
        if xmi_t not in ("uml:Activity", "Activity"):
            continue
        xid = _xmi_id(elem, ns)
        if not xid:
            continue

        name = elem.get("name", "")
        if not name:
            name = f"Activity_{xid[:8]}"

        # Collect actions
        actions: List[Dict[str, str]] = []
        for node_tag in ("node", "ownedNode", "group"):
            for node in elem.iter(node_tag):
                node_type = _xmi_type(node, ns) or ""
                node_name = node.get("name", "")
                node_id = _xmi_id(node, ns) or ""
                if node_name or node_id:
                    actions.append({
                        "name": node_name,
                        "type": node_type,
                        "xmi_id": node_id,
                    })

        # Also look for OpaqueAction, CallBehaviorAction, etc.
        for action_tag in ("ownedAction",):
            for act in elem.iter(action_tag):
                actions.append({
                    "name": act.get("name", ""),
                    "type": _xmi_type(act, ns) or "action",
                    "xmi_id": _xmi_id(act, ns) or "",
                })

        # Collect edges (control flow / object flow)
        control_flows: List[Dict[str, str]] = []
        object_flows: List[Dict[str, str]] = []
        for edge_tag in ("edge", "ownedEdge"):
            for edge in elem.iter(edge_tag):
                edge_type = _xmi_type(edge, ns) or ""
                edge_data = {
                    "name": edge.get("name", ""),
                    "xmi_id": _xmi_id(edge, ns) or "",
                    "source": edge.get("source", ""),
                    "target": edge.get("target", ""),
                }
                if "ObjectFlow" in edge_type:
                    object_flows.append(edge_data)
                else:
                    control_flows.append(edge_data)

        activities.append({
            "id": _new_id(),
            "xmi_id": xid,
            "element_type": "activity",
            "name": name,
            "qualified_name": _qualified_name(elem, root),
            "stereotype": "",
            "description": _get_description(elem, ns),
            "properties": json.dumps({
                "actions": actions,
                "control_flows": control_flows,
                "object_flows": object_flows,
            }),
            "diagram_type": "act",
        })

    return activities


def _extract_requirements(root: ET.Element, ns: Dict[str, str]) -> List[Dict[str, Any]]:
    """Extract <<Requirement>> stereotyped elements with text and ID.

    SysML requirements may appear as stereotyped uml:Class elements or as
    dedicated ``Requirement`` elements under the SysML namespace.
    """
    stereo_map = _build_stereotype_map(root, ns)
    requirements: List[Dict[str, Any]] = []

    # Strategy 1: stereotyped classes
    for elem in root.iter("packagedElement"):
        xmi_t = _xmi_type(elem, ns)
        if xmi_t not in ("uml:Class", "Class"):
            continue
        xid = _xmi_id(elem, ns)
        if not xid:
            continue

        stereo = stereo_map.get(xid, "")
        if "requirement" not in stereo.lower():
            continue

        name = elem.get("name", "")
        req_id = ""
        req_text = ""

        # Requirement ID and text may be in the stereotype application
        for child in root:
            tag_local = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
            if "requirement" not in tag_local.lower():
                continue
            for attr_name, attr_val in child.attrib.items():
                clean = attr_name.split("}", 1)[-1] if "}" in attr_name else attr_name
                if clean.startswith("base_") and attr_val == xid:
                    req_id = child.get("id", child.get("Id", ""))
                    req_text = child.get("text", child.get("Text", ""))
                    break

        if not req_text:
            req_text = _get_description(elem, ns)

        requirements.append({
            "id": _new_id(),
            "xmi_id": xid,
            "element_type": "requirement",
            "name": name or f"REQ-{xid[:8]}",
            "qualified_name": _qualified_name(elem, root),
            "stereotype": stereo or "Requirement",
            "description": req_text,
            "properties": json.dumps({
                "requirement_id": req_id,
                "text": req_text,
            }),
            "diagram_type": "req",
        })

    # Strategy 2: SysML namespace Requirement elements
    sysml_uri = ns.get("sysml", "")
    if sysml_uri:
        for elem in root.iter(f"{{{sysml_uri}}}Requirement"):
            xid = _xmi_id(elem, ns) or elem.get("base_Class", "")
            if not xid:
                continue
            # Avoid duplicates
            if any(r["xmi_id"] == xid for r in requirements):
                continue
            name = elem.get("name", "")
            req_id = elem.get("id", elem.get("Id", ""))
            req_text = elem.get("text", elem.get("Text", ""))
            requirements.append({
                "id": _new_id(),
                "xmi_id": xid,
                "element_type": "requirement",
                "name": name or f"REQ-{xid[:8]}",
                "qualified_name": "",
                "stereotype": "Requirement",
                "description": req_text or "",
                "properties": json.dumps({
                    "requirement_id": req_id,
                    "text": req_text or "",
                }),
                "diagram_type": "req",
            })

    return requirements


def _extract_state_machines(root: ET.Element, ns: Dict[str, str]) -> List[Dict[str, Any]]:
    """Extract StateMachine elements with states and transitions."""
    machines: List[Dict[str, Any]] = []

    for elem in root.iter("packagedElement"):
        xmi_t = _xmi_type(elem, ns)
        if xmi_t not in ("uml:StateMachine", "StateMachine"):
            continue
        xid = _xmi_id(elem, ns)
        if not xid:
            continue

        name = elem.get("name", "")
        if not name:
            name = f"StateMachine_{xid[:8]}"

        # Collect states
        states: List[Dict[str, str]] = []
        for region in elem.iter("region"):
            for subvertex_tag in ("subvertex", "ownedState"):
                for state in region.iter(subvertex_tag):
                    state_type = _xmi_type(state, ns) or ""
                    state_name = state.get("name", "")
                    state_id = _xmi_id(state, ns) or ""
                    kind = state.get("kind", "")
                    if state_name or state_id:
                        states.append({
                            "name": state_name,
                            "type": state_type,
                            "xmi_id": state_id,
                            "kind": kind,
                        })
        # Fallback: look for State elements directly under StateMachine
        if not states:
            for state in elem.iter("subvertex"):
                states.append({
                    "name": state.get("name", ""),
                    "type": _xmi_type(state, ns) or "",
                    "xmi_id": _xmi_id(state, ns) or "",
                    "kind": state.get("kind", ""),
                })

        # Collect transitions
        transitions: List[Dict[str, str]] = []
        for region in elem.iter("region"):
            for trans in region.iter("transition"):
                transitions.append({
                    "name": trans.get("name", ""),
                    "xmi_id": _xmi_id(trans, ns) or "",
                    "source": trans.get("source", ""),
                    "target": trans.get("target", ""),
                    "guard": _get_guard_text(trans),
                    "trigger": _get_trigger_name(trans),
                })
        # Fallback
        if not transitions:
            for trans in elem.iter("transition"):
                transitions.append({
                    "name": trans.get("name", ""),
                    "xmi_id": _xmi_id(trans, ns) or "",
                    "source": trans.get("source", ""),
                    "target": trans.get("target", ""),
                    "guard": _get_guard_text(trans),
                    "trigger": _get_trigger_name(trans),
                })

        machines.append({
            "id": _new_id(),
            "xmi_id": xid,
            "element_type": "state_machine",
            "name": name,
            "qualified_name": _qualified_name(elem, root),
            "stereotype": "",
            "description": _get_description(elem, ns),
            "properties": json.dumps({
                "states": states,
                "transitions": transitions,
            }),
            "diagram_type": "stm",
        })

    return machines


def _get_guard_text(transition: ET.Element) -> str:
    """Extract guard condition text from a transition element."""
    guard = transition.find("guard")
    if guard is not None:
        spec = guard.find("specification")
        if spec is not None:
            body = spec.get("body") or spec.get("value") or ""
            if body:
                return body
            if spec.text:
                return spec.text.strip()
        body_attr = guard.get("body")
        if body_attr:
            return body_attr
    return ""


def _get_trigger_name(transition: ET.Element) -> str:
    """Extract trigger name from a transition element."""
    for trigger in transition.iter("trigger"):
        name = trigger.get("name", "")
        if name:
            return name
        event = trigger.get("event", "")
        if event:
            return event
    return ""


def _extract_use_cases(root: ET.Element, ns: Dict[str, str]) -> List[Dict[str, Any]]:
    """Extract UseCase and Actor elements from XMI."""
    use_cases: List[Dict[str, Any]] = []

    for elem in root.iter("packagedElement"):
        xmi_t = _xmi_type(elem, ns)
        if xmi_t not in ("uml:UseCase", "UseCase", "uml:Actor", "Actor"):
            continue
        xid = _xmi_id(elem, ns)
        if not xid:
            continue

        name = elem.get("name", "")
        if not name:
            continue

        is_actor = "Actor" in (xmi_t or "")
        element_type = "actor" if is_actor else "use_case"

        # Collect extension points for use cases
        ext_points: List[Dict[str, str]] = []
        if not is_actor:
            for ep in elem.iter("extensionPoint"):
                ext_points.append({
                    "name": ep.get("name", ""),
                    "xmi_id": _xmi_id(ep, ns) or "",
                })

        # Collect included use cases
        includes: List[str] = []
        for inc in elem.iter("include"):
            addition = inc.get("addition", "")
            if addition:
                includes.append(addition)

        use_cases.append({
            "id": _new_id(),
            "xmi_id": xid,
            "element_type": element_type,
            "name": name,
            "qualified_name": _qualified_name(elem, root),
            "stereotype": "Actor" if is_actor else "",
            "description": _get_description(elem, ns),
            "properties": json.dumps({
                "extension_points": ext_points,
                "includes": includes,
            }) if not is_actor else json.dumps({}),
            "diagram_type": "uc",
        })

    return use_cases


# ---------------------------------------------------------------------------
# Relationship extraction
# ---------------------------------------------------------------------------

def _extract_relationships(root: ET.Element, ns: Dict[str, str]) -> List[Dict[str, Any]]:
    """Extract all relationships from XMI.

    Covers:
    - Associations (including aggregation/composition)
    - Generalizations
    - Dependencies
    - Realizations
    - Usages
    - SysML stereotype links: satisfy, derive, verify, refine, trace, allocate
    """
    relationships: List[Dict[str, Any]] = []

    # --- Structural relationships inside packagedElements ---
    for elem in root.iter("packagedElement"):
        xmi_t = _xmi_type(elem, ns)

        # Associations
        if xmi_t in ("uml:Association", "Association"):
            _parse_association(elem, ns, relationships)

        # Dependencies
        elif xmi_t in ("uml:Dependency", "Dependency"):
            _parse_simple_rel(elem, ns, "dependency", relationships)

        # Realizations
        elif xmi_t in ("uml:Realization", "Realization",
                        "uml:InterfaceRealization", "InterfaceRealization"):
            _parse_simple_rel(elem, ns, "realization", relationships)

        # Usages
        elif xmi_t in ("uml:Usage", "Usage"):
            _parse_simple_rel(elem, ns, "usage", relationships)

        # Abstractions (may carry SysML stereotypes like «satisfy»)
        elif xmi_t in ("uml:Abstraction", "Abstraction"):
            _parse_abstraction(elem, ns, root, relationships)

    # --- Generalizations (nested inside classes) ---
    for gen in root.iter("generalization"):
        general = gen.get("general", "")
        if not general:
            # May be a child element
            gen_ref = gen.find("general")
            if gen_ref is not None:
                general = gen_ref.get("href", "") or _xmi_id(gen_ref, ns) or ""
        # Parent class xmi:id
        parent = gen.getparent() if hasattr(gen, "getparent") else None
        source_id = ""
        if parent is not None:
            source_id = _xmi_id(parent, ns) or ""
        if not source_id:
            # Walk up via parent map
            parent_map = getattr(root, "_parent_map", None)
            if parent_map is None:
                parent_map = {c: p for p in root.iter() for c in p}
                root._parent_map = parent_map  # type: ignore[attr-defined]
            p = parent_map.get(gen)
            if p is not None:
                source_id = _xmi_id(p, ns) or ""

        if source_id and general:
            relationships.append({
                "source_xmi_id": source_id,
                "target_xmi_id": general,
                "relationship_type": "generalization",
                "name": gen.get("name", ""),
                "properties": json.dumps({}),
            })

    # --- SysML stereotype relationships (top-level in profile application) ---
    _parse_sysml_stereo_rels(root, ns, relationships)

    return relationships


def _parse_association(elem: ET.Element, ns: Dict[str, str],
                       rels: List[Dict[str, Any]]) -> None:
    """Parse a UML Association into one or more relationship records."""
    name = elem.get("name", "")
    member_ends: List[str] = []
    aggregation = ""

    # memberEnd attribute (space-separated xmi:idrefs)
    member_end_attr = elem.get("memberEnd", "")
    if member_end_attr:
        member_ends = member_end_attr.split()

    # ownedEnd elements
    owned_ends: List[ET.Element] = list(elem.iter("ownedEnd"))
    for oe in owned_ends:
        agg = oe.get("aggregation", "none")
        if agg in ("composite", "shared"):
            aggregation = agg

    # Determine source and target from memberEnd / ownedEnd
    source_id = ""
    target_id = ""

    if len(owned_ends) >= 2:
        source_id = owned_ends[0].get("type", "") or _xmi_id(owned_ends[0], ns) or ""
        target_id = owned_ends[1].get("type", "") or _xmi_id(owned_ends[1], ns) or ""
    elif len(member_ends) >= 2:
        source_id = member_ends[0]
        target_id = member_ends[1]
    elif len(owned_ends) == 1:
        target_id = owned_ends[0].get("type", "") or _xmi_id(owned_ends[0], ns) or ""
        if member_ends:
            source_id = member_ends[0] if member_ends[0] != target_id else (
                member_ends[1] if len(member_ends) > 1 else ""
            )

    if not source_id or not target_id:
        return

    if aggregation == "composite":
        rel_type = "composition"
    elif aggregation == "shared":
        rel_type = "aggregation"
    else:
        rel_type = "association"

    rels.append({
        "source_xmi_id": source_id,
        "target_xmi_id": target_id,
        "relationship_type": rel_type,
        "name": name,
        "properties": json.dumps({"aggregation": aggregation}),
    })


def _parse_simple_rel(elem: ET.Element, ns: Dict[str, str],
                       rel_type: str, rels: List[Dict[str, Any]]) -> None:
    """Parse a simple directed relationship (Dependency, Realization, Usage)."""
    name = elem.get("name", "")

    # client → source, supplier → target
    client = elem.get("client", "")
    supplier = elem.get("supplier", "")

    # May also be nested elements
    if not client:
        client_el = elem.find("client")
        if client_el is not None:
            client = client_el.get("href", "") or _xmi_id(client_el, ns) or ""
    if not supplier:
        supplier_el = elem.find("supplier")
        if supplier_el is not None:
            supplier = supplier_el.get("href", "") or _xmi_id(supplier_el, ns) or ""

    if client and supplier:
        rels.append({
            "source_xmi_id": client,
            "target_xmi_id": supplier,
            "relationship_type": rel_type,
            "name": name,
            "properties": json.dumps({}),
        })


def _parse_abstraction(elem: ET.Element, ns: Dict[str, str],
                        root: ET.Element, rels: List[Dict[str, Any]]) -> None:
    """Parse a UML Abstraction, checking for SysML stereotype overlay."""
    name = elem.get("name", "")
    xid = _xmi_id(elem, ns)

    client = elem.get("client", "")
    supplier = elem.get("supplier", "")
    if not client:
        c = elem.find("client")
        if c is not None:
            client = c.get("href", "") or _xmi_id(c, ns) or ""
    if not supplier:
        s = elem.find("supplier")
        if s is not None:
            supplier = s.get("href", "") or _xmi_id(s, ns) or ""

    if not client or not supplier:
        return

    # Check if a SysML stereotype (satisfy, derive, etc.) applies to this Abstraction
    rel_type = "dependency"
    if xid:
        stereo_map = _build_stereotype_map(root, ns)
        stereo = stereo_map.get(xid, "").lower()
        for kw in SYSML_REL_STEREOTYPES:
            if kw in stereo:
                rel_type = kw
                break

    rels.append({
        "source_xmi_id": client,
        "target_xmi_id": supplier,
        "relationship_type": rel_type,
        "name": name,
        "properties": json.dumps({}),
    })


def _parse_sysml_stereo_rels(root: ET.Element, ns: Dict[str, str],
                              rels: List[Dict[str, Any]]) -> None:
    """Parse SysML stereotype relationship applications at the top level.

    Cameo exports «satisfy», «derive», «verify», «refine», «trace», «allocate»
    as top-level elements under the SysML profile namespace.  Each carries
    ``base_Abstraction`` (or ``base_Dependency``) plus ``client``/``supplier``
    attributes or nested sub-elements referencing the related model elements.
    """
    ns.get("sysml", "")

    for child in root:
        tag = child.tag
        if "}" in tag:
            local = tag.split("}", 1)[1]
        else:
            local = tag

        local_lower = local.lower()
        matched_type: Optional[str] = None
        for kw in SYSML_REL_STEREOTYPES:
            if kw == local_lower or local_lower.endswith(kw):
                matched_type = kw
                break

        if not matched_type:
            continue

        # Resolve the Abstraction/Dependency this stereotype applies to
        base_ref = ""
        for attr_name, attr_val in child.attrib.items():
            clean = attr_name.split("}", 1)[-1] if "}" in attr_name else attr_name
            if clean.startswith("base_"):
                base_ref = attr_val
                break

        # The actual source/target must come from the referenced Abstraction
        # element, which we've already parsed.  Mark this relationship type
        # override so we can merge later.
        if base_ref:
            # Try to find matching dependency/abstraction already in rels
            found = False
            for r in rels:
                if r.get("_base_xmi_id") == base_ref or r.get("source_xmi_id") == base_ref:
                    r["relationship_type"] = matched_type
                    found = True
                    break
            if not found:
                # Store as unresolved — will attempt resolution in _resolve step
                rels.append({
                    "source_xmi_id": base_ref,
                    "target_xmi_id": "",
                    "relationship_type": matched_type,
                    "name": child.get("name", ""),
                    "properties": json.dumps({"base_ref": base_ref}),
                    "_unresolved": True,
                })


# ---------------------------------------------------------------------------
# Cross-reference resolution
# ---------------------------------------------------------------------------

def _resolve_xmi_refs(elements: List[Dict[str, Any]],
                      relationships: List[Dict[str, Any]]) -> Tuple[
                          List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve xmi:idref cross-references between elements.

    Builds a lookup from xmi_id → generated id so that relationship
    source_element_id / target_element_id can be set correctly for DB
    insertion.  Also resolves parent_id for nested elements.

    Drops relationships whose source or target could not be resolved.
    """
    # Build xmi_id → element dict
    xmi_lookup: Dict[str, Dict[str, Any]] = {}
    for el in elements:
        xmi_lookup[el["xmi_id"]] = el

    # Resolve parent_id (based on qualified_name nesting if present)
    for el in elements:
        qn = el.get("qualified_name", "")
        if "::" in qn:
            parent_qn = "::".join(qn.split("::")[:-1])
            for candidate in elements:
                if candidate.get("qualified_name", "") == parent_qn and candidate["id"] != el["id"]:
                    el["parent_id"] = candidate["id"]
                    break

    # Resolve relationships
    resolved_rels: List[Dict[str, Any]] = []
    for rel in relationships:
        # Skip unresolved SysML stereo links with empty targets
        if rel.get("_unresolved") and not rel.get("target_xmi_id"):
            continue

        src_xmi = rel.get("source_xmi_id", "")
        tgt_xmi = rel.get("target_xmi_id", "")

        src_el = xmi_lookup.get(src_xmi)
        tgt_el = xmi_lookup.get(tgt_xmi)

        if src_el and tgt_el:
            resolved_rels.append({
                "source_element_id": src_el["id"],
                "target_element_id": tgt_el["id"],
                "relationship_type": rel["relationship_type"],
                "name": rel.get("name", ""),
                "properties": rel.get("properties", "{}"),
            })
        # If only one side resolved, still keep with xmi_id as fallback
        elif src_el or tgt_el:
            resolved_rels.append({
                "source_element_id": src_el["id"] if src_el else src_xmi,
                "target_element_id": tgt_el["id"] if tgt_el else tgt_xmi,
                "relationship_type": rel["relationship_type"],
                "name": rel.get("name", ""),
                "properties": rel.get("properties", "{}"),
            })

    # Clean internal keys from relationships
    for rel in resolved_rels:
        rel.pop("_unresolved", None)
        rel.pop("_base_xmi_id", None)

    return elements, resolved_rels


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_xmi(file_path: str) -> Dict[str, Any]:
    """Validate XMI structure before import.

    Returns::

        {
            "valid": bool,
            "errors": [...],
            "namespaces": {...},
            "element_count": int,
        }
    """
    errors: List[str] = []
    namespaces: Dict[str, str] = {}
    element_count = 0

    fpath = Path(file_path)
    if not fpath.exists():
        return {
            "valid": False,
            "errors": [f"File not found: {file_path}"],
            "namespaces": {},
            "element_count": 0,
        }

    if fpath.suffix.lower() not in (".xmi", ".xml", ".uml"):
        errors.append(f"Unexpected file extension: {fpath.suffix} (expected .xmi, .xml, or .uml)")

    # Attempt parse
    try:
        tree = ET.parse(str(fpath))
        root = tree.getroot()
    except ET.ParseError as exc:
        return {
            "valid": False,
            "errors": [f"XML parse error: {exc}"],
            "namespaces": {},
            "element_count": 0,
        }

    # Detect namespaces
    namespaces = _detect_namespaces(root)

    # Verify root element is XMI
    root_local = root.tag.split("}", 1)[-1] if "}" in root.tag else root.tag
    if root_local.upper() != "XMI" and root_local != "Model":
        errors.append(
            f"Root element is <{root_local}>, expected <xmi:XMI> or <XMI>. "
            "File may not be a valid XMI export."
        )

    # Check for XMI version attribute
    xmi_version = None
    xmi_ns = namespaces.get("xmi", "")
    for attr_name in (f"{{{xmi_ns}}}version", "xmi:version", "version"):
        val = root.get(attr_name)
        if val:
            xmi_version = val
            break
    if not xmi_version:
        errors.append("Missing xmi:version attribute on root element.")

    # Count packagedElements
    element_count = sum(1 for _ in root.iter("packagedElement"))
    if element_count == 0:
        errors.append("No <packagedElement> nodes found. File may be empty or use non-standard structure.")

    # Check for at least one recognized UML type
    found_uml = False
    for pe in root.iter("packagedElement"):
        xmi_t = _xmi_type(pe, namespaces)
        if xmi_t and ("uml:" in str(xmi_t) or xmi_t in (
            "Class", "Activity", "StateMachine", "UseCase", "Actor",
            "Association", "Package", "Interface"
        )):
            found_uml = True
            break
    if not found_uml and element_count > 0:
        errors.append("No recognized UML-typed packagedElements found.")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "namespaces": namespaces,
        "element_count": element_count,
    }


# ---------------------------------------------------------------------------
# Full parse
# ---------------------------------------------------------------------------

def parse_xmi(file_path: str) -> Dict[str, Any]:
    """Parse an XMI file and return structured data.

    Returns::

        {
            "elements": [...],
            "relationships": [...],
            "metadata": {
                "file": str,
                "file_hash": str,
                "namespaces": {...},
                "element_count": int,
                "relationship_count": int,
                "parsed_at": str,
            },
        }
    """
    fpath = Path(file_path)
    if not fpath.exists():
        raise FileNotFoundError(f"XMI file not found: {file_path}")

    tree = ET.parse(str(fpath))
    root = tree.getroot()
    ns = _detect_namespaces(root)
    source_hash = _file_hash(file_path)

    # Extract all element types
    elements: List[Dict[str, Any]] = []
    elements.extend(_extract_blocks(root, ns))
    elements.extend(_extract_interface_blocks(root, ns))
    elements.extend(_extract_activities(root, ns))
    elements.extend(_extract_requirements(root, ns))
    elements.extend(_extract_state_machines(root, ns))
    elements.extend(_extract_use_cases(root, ns))

    # Tag every element with source info
    for el in elements:
        el["source_file"] = str(fpath.name)
        el["source_hash"] = source_hash
        el.setdefault("parent_id", None)

    # Extract relationships
    relationships = _extract_relationships(root, ns)

    # Tag every relationship with source info
    for rel in relationships:
        rel["source_file"] = str(fpath.name)

    # Resolve cross-references
    elements, relationships = _resolve_xmi_refs(elements, relationships)

    return {
        "elements": elements,
        "relationships": relationships,
        "metadata": {
            "file": str(fpath),
            "file_hash": source_hash,
            "namespaces": ns,
            "element_count": len(elements),
            "relationship_count": len(relationships),
            "parsed_at": _ts(),
        },
    }


# ---------------------------------------------------------------------------
# Database import
# ---------------------------------------------------------------------------

def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection to the ICDEV database."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def import_xmi(project_id: str, file_path: str,
               db_path: str = None) -> Dict[str, Any]:
    """Full import pipeline: validate -> parse -> store in DB -> audit trail.

    Steps:
        1. Validate XMI structure
        2. Compute file hash (SHA-256)
        3. Parse all elements and relationships
        4. Insert into sysml_elements and sysml_relationships tables
        5. Record in model_imports table
        6. Log audit trail event (xmi_imported)
        7. Return summary

    Returns::

        {
            "import_id": int,
            "elements_imported": int,
            "relationships_imported": int,
            "errors": int,
            "status": str,
        }
    """
    timestamp = _ts()
    error_details: List[str] = []

    # Step 1 — Validate
    validation = validate_xmi(file_path)
    if not validation["valid"]:
        # Record failed import
        try:
            conn = _get_connection(db_path)
            c = conn.cursor()
            c.execute(
                """INSERT INTO model_imports
                   (project_id, import_type, source_file, source_hash,
                    elements_imported, relationships_imported, errors,
                    error_details, status, imported_by, imported_at)
                   VALUES (?, ?, ?, ?, 0, 0, ?, ?, 'failed', 'icdev-mbse-engine', ?)""",
                (
                    project_id,
                    "xmi",
                    str(Path(file_path).name),
                    "",
                    len(validation["errors"]),
                    json.dumps(validation["errors"]),
                    timestamp,
                ),
            )
            conn.commit()
            import_id = c.lastrowid
            conn.close()
        except Exception:
            import_id = -1

        return {
            "import_id": import_id,
            "elements_imported": 0,
            "relationships_imported": 0,
            "errors": len(validation["errors"]),
            "error_details": validation["errors"],
            "status": "failed",
        }

    # Step 2–3 — Parse
    try:
        parsed = parse_xmi(file_path)
    except Exception as exc:
        return {
            "import_id": -1,
            "elements_imported": 0,
            "relationships_imported": 0,
            "errors": 1,
            "error_details": [f"Parse error: {exc}"],
            "status": "failed",
        }

    elements = parsed["elements"]
    relationships = parsed["relationships"]
    source_hash = parsed["metadata"]["file_hash"]
    parsed["metadata"]["file"]

    # Step 4 — Store in DB
    conn = _get_connection(db_path)
    cursor = conn.cursor()

    elements_inserted = 0
    rels_inserted = 0

    # Insert elements
    for el in elements:
        try:
            cursor.execute(
                """INSERT OR REPLACE INTO sysml_elements
                   (id, project_id, xmi_id, element_type, name, qualified_name,
                    parent_id, stereotype, description, properties,
                    diagram_type, source_file, source_hash, imported_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    el["id"],
                    project_id,
                    el["xmi_id"],
                    el["element_type"],
                    el["name"],
                    el.get("qualified_name", ""),
                    el.get("parent_id"),
                    el.get("stereotype", ""),
                    el.get("description", ""),
                    el.get("properties", "{}"),
                    el.get("diagram_type"),
                    el.get("source_file", ""),
                    el.get("source_hash", source_hash),
                    timestamp,
                    timestamp,
                ),
            )
            elements_inserted += 1
        except sqlite3.Error as exc:
            error_details.append(f"Element '{el.get('name', '')}': {exc}")

    # Insert relationships
    for rel in relationships:
        try:
            cursor.execute(
                """INSERT OR REPLACE INTO sysml_relationships
                   (project_id, source_element_id, target_element_id,
                    relationship_type, name, properties, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    rel["source_element_id"],
                    rel["target_element_id"],
                    rel["relationship_type"],
                    rel.get("name", ""),
                    rel.get("properties", "{}"),
                    rel.get("source_file", str(Path(file_path).name)),
                ),
            )
            rels_inserted += 1
        except sqlite3.Error as exc:
            error_details.append(f"Relationship '{rel.get('name', '')}': {exc}")

    # Step 5 — Record import
    status = "completed" if not error_details else "partial"
    cursor.execute(
        """INSERT INTO model_imports
           (project_id, import_type, source_file, source_hash,
            elements_imported, relationships_imported, errors,
            error_details, status, imported_by, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'icdev-mbse-engine', ?)""",
        (
            project_id,
            "xmi",
            str(Path(file_path).name),
            source_hash,
            elements_inserted,
            rels_inserted,
            len(error_details),
            json.dumps(error_details) if error_details else None,
            status,
            timestamp,
        ),
    )
    import_id = cursor.lastrowid

    conn.commit()
    conn.close()

    # Step 6 — Audit trail
    if _HAS_AUDIT:
        try:
            log_event(
                event_type="xmi_imported",
                actor="icdev-mbse-engine",
                action=f"Imported XMI file '{Path(file_path).name}' for project {project_id}",
                project_id=project_id,
                details={
                    "import_id": import_id,
                    "elements_imported": elements_inserted,
                    "relationships_imported": rels_inserted,
                    "errors": len(error_details),
                    "source_hash": source_hash,
                    "status": status,
                },
                affected_files=[str(file_path)],
                classification="CUI",
                db_path=Path(db_path) if db_path else None,
            )
        except Exception:
            pass  # Audit failure should not block import

    # Step 7 — Return summary
    return {
        "import_id": import_id,
        "elements_imported": elements_inserted,
        "relationships_imported": rels_inserted,
        "errors": len(error_details),
        "error_details": error_details if error_details else [],
        "status": status,
    }


# ---------------------------------------------------------------------------
# Import summary
# ---------------------------------------------------------------------------

def get_import_summary(import_id: int, db_path: str = None) -> Dict[str, Any]:
    """Return import details from the model_imports table.

    Returns the full row as a dict, or an error dict if not found.
    """
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM model_imports WHERE id = ?", (import_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"error": f"Import #{import_id} not found.", "import_id": import_id}

    result = dict(row)
    # Parse JSON fields for convenience
    if result.get("error_details"):
        try:
            result["error_details"] = json.loads(result["error_details"])
        except (json.JSONDecodeError, TypeError):
            pass

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Command-line interface for XMI parsing and import."""
    parser = argparse.ArgumentParser(
        description="Import SysML XMI from Cameo Systems Modeler"
    )
    parser.add_argument(
        "--project-id", required=True,
        help="ICDEV project identifier (e.g. proj-123)",
    )
    parser.add_argument(
        "--file", required=True,
        help="Path to XMI file exported from Cameo/MagicDraw",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Validate XMI structure without importing",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override database path (default: data/icdev.db)",
    )
    args = parser.parse_args()

    file_path = str(Path(args.file).resolve())

    # ---- Validate-only mode ----
    if args.validate_only:
        result = validate_xmi(file_path)
        if args.json_output:
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2))
            print("CUI // SP-CTI")
        else:
            print("CUI // SP-CTI")
            print(f"XMI Validation: {'PASS' if result['valid'] else 'FAIL'}")
            print(f"  File:        {file_path}")
            print(f"  Elements:    {result['element_count']}")
            print(f"  Namespaces:  {len(result['namespaces'])}")
            for key, uri in result["namespaces"].items():
                print(f"    {key}: {uri}")
            if result["errors"]:
                print(f"  Errors ({len(result['errors'])}):")
                for err in result["errors"]:
                    print(f"    - {err}")
            print("CUI // SP-CTI")
        sys.exit(0 if result["valid"] else 1)

    # ---- Full import mode ----
    db_path_str = str(args.db_path) if args.db_path else None

    result = import_xmi(
        project_id=args.project_id,
        file_path=file_path,
        db_path=db_path_str,
    )

    if args.json_output:
        print("CUI // SP-CTI")
        print(json.dumps(result, indent=2))
        print("CUI // SP-CTI")
    else:
        print("CUI // SP-CTI")
        print(f"XMI Import {'Complete' if result['status'] in ('completed', 'partial') else 'Failed'}")
        print(f"  Project:       {args.project_id}")
        print(f"  File:          {Path(file_path).name}")
        print(f"  Import ID:     {result['import_id']}")
        print(f"  Elements:      {result['elements_imported']}")
        print(f"  Relationships: {result['relationships_imported']}")
        print(f"  Errors:        {result['errors']}")
        print(f"  Status:        {result['status']}")
        if result.get("error_details"):
            print("  Error Details:")
            for err in result["error_details"]:
                print(f"    - {err}")
        print("CUI // SP-CTI")

    sys.exit(0 if result["status"] in ("completed", "partial") else 1)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
