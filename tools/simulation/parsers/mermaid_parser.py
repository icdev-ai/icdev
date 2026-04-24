# CUI // SP-CTI
"""
Parse Mermaid diagram source into a normalized graph_json.

Supported diagram types:
  flowchart LR/TD/RL/BT (also "graph LR" syntax)
  sequenceDiagram
  classDiagram
  erDiagram

Output schema:
  {
    "diagram_type": str,          # "flowchart" | "sequence" | "class" | "er"
    "direction": str | None,      # "LR" | "TD" | "RL" | "BT" | None
    "nodes": [
      {
        "id": str,
        "label": str,
        "shape": str,             # rectangle | rounded | circle | diamond |
                                  # stadium | subroutine | cylinder | hexagon |
                                  # parallelogram | actor | class | entity | participant
        "style": dict             # parsed fill/stroke/color from style directives
      }
    ],
    "edges": [
      {
        "id": int,
        "source": str,
        "target": str,
        "label": str,
        "type": str,              # arrow | line | dotted | thick | open
        "style": dict             # parsed stroke/color from linkStyle directives
      }
    ]
  }
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_mermaid(source: str) -> dict:
    """Parse a Mermaid diagram string into normalized graph_json."""
    source = source.strip()
    diagram_type, direction = _detect_type(source)

    if diagram_type == "flowchart":
        return _parse_flowchart(source, direction)
    if diagram_type == "sequence":
        return _parse_sequence(source)
    if diagram_type == "class":
        return _parse_class(source)
    if diagram_type == "er":
        return _parse_er(source)

    # Unknown — return empty skeleton
    return {"diagram_type": diagram_type, "direction": direction, "nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Type detection
# ---------------------------------------------------------------------------

_FLOWCHART_RE = re.compile(
    r"^(?:flowchart|graph)\s+(LR|TD|RL|BT|TB)", re.IGNORECASE
)
_SEQUENCE_RE = re.compile(r"^sequenceDiagram", re.IGNORECASE)
_CLASS_RE = re.compile(r"^classDiagram", re.IGNORECASE)
_ER_RE = re.compile(r"^erDiagram", re.IGNORECASE)


def _detect_type(source: str) -> tuple[str, str | None]:
    first_line = source.split("\n")[0].strip()
    m = _FLOWCHART_RE.match(first_line)
    if m:
        return "flowchart", m.group(1).upper()
    if _SEQUENCE_RE.match(first_line):
        return "sequence", None
    if _CLASS_RE.match(first_line):
        return "class", None
    if _ER_RE.match(first_line):
        return "er", None
    return "unknown", None


# ---------------------------------------------------------------------------
# Flowchart parser
# ---------------------------------------------------------------------------

# Node shape patterns: ordered from most-specific to least-specific to avoid
# early short-circuit on shapes with overlapping brackets.
_SHAPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("cylinder",       re.compile(r"^\[(\(.*?\))\]$")),        # [(text)]
    ("subroutine",     re.compile(r"^\[\[(.+?)\]\]$")),        # [[text]]
    ("stadium",        re.compile(r"^\(\[(.+?)\]\)$")),        # ([text])
    ("hexagon",        re.compile(r"^\{\{(.+?)\}\}$")),        # {{text}}
    ("diamond",        re.compile(r"^\{(.+?)\}$")),            # {text}
    ("circle",         re.compile(r"^\(\((.+?)\)\)$")),        # ((text))
    ("parallelogram",  re.compile(r"^\[/(.+?)/\]$")),          # [/text/]
    ("parallelogram",  re.compile(r"^\[\\(.+?)\\\]$")),        # [\text\]
    ("trapezoid",      re.compile(r"^\[/(.+?)\\\]$")),         # [/text\]
    ("trapezoid",      re.compile(r"^\[\\(.+?)/\]$")),         # [\text/]
    ("asymmetric",     re.compile(r"^>(.+?)\]$")),             # >text]
    ("rounded",        re.compile(r"^\((.+?)\)$")),            # (text)
    ("rectangle",      re.compile(r"^\[(.+?)\]$")),            # [text]
]

# Edge type detection from the connector string
_EDGE_TYPES: list[tuple[str, re.Pattern]] = [
    ("thick",  re.compile(r"==+>?")),
    ("dotted", re.compile(r"-\.+->")),
    ("dotted", re.compile(r"-\.->")),
    ("open",   re.compile(r"---")),
    ("line",   re.compile(r"^--$")),
    ("arrow",  re.compile(r"--+>")),
]

# Matches a full edge line, capturing source connector label target.
# Handles:  A --> B   A -->|label| B   A -- text --> B   A -.-> B   A ==> B
_EDGE_LINE_RE = re.compile(
    r"^(?P<src>[A-Za-z0-9_\-\"' ]+?)"
    r"\s*(?P<conn>(?:==+>?|--+>?|-\.+->?|---?))"
    r"(?:\|(?P<pipe_label>[^|]*)\|)?"
    r"(?:\s+(?P<mid_label>[^-=>|]+?)\s*(?P<conn2>-->?|==>?|--))?"
    r"\s*(?P<tgt>[A-Za-z0-9_\-\"' ]+?)\s*"
    r"(?:\[.*?\]|\(.*?\)|\{.*?\}|>.*?\])?$"
)

# Simpler two-pass approach: first extract raw tokens then classify
_EDGE_SPLIT_RE = re.compile(
    r"^(?P<rest>.+?)\s*(?P<conn>==+>?|--+>|-\.-+>?|---)\s*(?P<tgt_rest>.+)$"
)

_STYLE_LINE_RE = re.compile(
    r"^style\s+(\S+)\s+(.+)$", re.IGNORECASE
)
_LINKSTYLE_LINE_RE = re.compile(
    r"^linkStyle\s+(\S+)\s+(.+)$", re.IGNORECASE
)
_STYLE_PROP_RE = re.compile(r"([a-zA-Z\-]+):([^,;]+)")


def _parse_style_props(raw: str) -> dict:
    return {m.group(1).strip(): m.group(2).strip() for m in _STYLE_PROP_RE.finditer(raw)}


def _classify_edge_type(conn: str) -> str:
    for etype, pat in _EDGE_TYPES:
        if pat.search(conn):
            return etype
    return "arrow"


def _extract_node_id_label(token: str) -> tuple[str, str, str]:
    """Return (node_id, node_body_raw, shape) from a node token like A[Label]."""
    token = token.strip()
    # Quoted bare id like "A B"
    if token.startswith('"') and token.endswith('"'):
        inner = token[1:-1]
        return inner, inner, "rectangle"

    # Find the first bracket-like character that begins the shape suffix
    # Node ID is everything up to that character.
    shape_start = len(token)
    for ch in ("[(", "[[", "([", "{{", "{", "((", "[/", "[\\", "[", "(", ">"):
        idx = token.find(ch)
        if idx != -1 and idx < shape_start:
            shape_start = idx

    node_id = token[:shape_start].strip().strip('"')
    body_raw = token[shape_start:].strip() if shape_start < len(token) else ""

    if not body_raw:
        return node_id, node_id, "rectangle"

    for shape, pat in _SHAPE_PATTERNS:
        m = pat.match(body_raw)
        if m:
            return node_id, m.group(1).strip(), shape

    return node_id, body_raw.strip("[](){}"), "rectangle"


def _parse_flowchart_edge_line(line: str) -> dict | None:
    """
    Parse one flowchart edge line.  Returns edge dict or None if not an edge.

    Strategy: split on the connector token (longest match first), then parse
    the pipe-label  -->|label|  or mid-label  -- text -->  variants.
    """
    # Drop subgraph lines
    if re.match(r"^(sub)?graph\b|^end\b|^style\b|^linkStyle\b|^classDef\b|^class\b",
                line, re.IGNORECASE):
        return None

    # Try connector split
    m = _EDGE_SPLIT_RE.match(line)
    if not m:
        return None

    src_raw = m.group("rest").strip()
    conn = m.group("conn").strip()
    tgt_raw = m.group("tgt_rest").strip()

    label = ""

    # Pipe-label on right side:  -->|some label| Target[...]
    pipe_m = re.match(r"^\|([^|]*)\|\s*(.+)$", tgt_raw)
    if pipe_m:
        label = pipe_m.group(1).strip()
        tgt_raw = pipe_m.group(2).strip()

    # Mid-label on left side:  Source -- some text --> Target
    # In this case src_raw ends with the label after "--"
    mid_m = re.match(r"^(.+?)\s*--\s+(.+)$", src_raw)
    if mid_m and not label:
        src_raw = mid_m.group(1).strip()
        label = mid_m.group(2).strip()

    src_id, _, _ = _extract_node_id_label(src_raw)
    tgt_id, tgt_label, tgt_shape = _extract_node_id_label(tgt_raw)

    return {
        "conn": conn,
        "src_id": src_id,
        "tgt_id": tgt_id,
        "tgt_label": tgt_label,
        "tgt_shape": tgt_shape,
        "label": label,
        "type": _classify_edge_type(conn),
    }


def _parse_flowchart(source: str, direction: str | None) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    node_styles: dict[str, dict] = {}
    link_styles: dict[str, dict] = {}

    lines = source.split("\n")

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue

        # style directive
        sm = _STYLE_LINE_RE.match(line)
        if sm:
            node_styles[sm.group(1)] = _parse_style_props(sm.group(2))
            continue

        # linkStyle directive
        lm = _LINKSTYLE_LINE_RE.match(line)
        if lm:
            link_styles[lm.group(1)] = _parse_style_props(lm.group(2))
            continue

        # Skip non-node/edge lines
        if re.match(r"^(?:flowchart|graph|sequenceDiagram|classDiagram|erDiagram"
                    r"|subgraph|end|classDef|class\s)\b", line, re.IGNORECASE):
            continue

        # Try to parse as edge
        edge_info = _parse_flowchart_edge_line(line)
        if edge_info:
            edge_idx = len(edges)
            edges.append({
                "id": edge_idx,
                "source": edge_info["src_id"],
                "target": edge_info["tgt_id"],
                "label": edge_info["label"],
                "type": edge_info["type"],
                "style": {},
            })
            # Register target node if it carries a label/shape inline
            tgt_id = edge_info["tgt_id"]
            if tgt_id not in nodes:
                nodes[tgt_id] = {
                    "id": tgt_id,
                    "label": edge_info["tgt_label"] or tgt_id,
                    "shape": edge_info["tgt_shape"],
                    "style": {},
                }
            # Register source node (bare id — label resolved later or stays as id)
            src_id = edge_info["src_id"]
            if src_id not in nodes:
                nodes[src_id] = {
                    "id": src_id,
                    "label": src_id,
                    "shape": "rectangle",
                    "style": {},
                }
            continue

        # Standalone node definition (no edge connector present)
        node_id, label, shape = _extract_node_id_label(line)
        if node_id and node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "label": label,
                "shape": shape,
                "style": {},
            }

    # Apply style annotations
    for nid, style in node_styles.items():
        if nid in nodes:
            nodes[nid]["style"].update(style)

    for edge_idx_str, style in link_styles.items():
        try:
            idx = int(edge_idx_str)
            if 0 <= idx < len(edges):
                edges[idx]["style"].update(style)
        except ValueError:
            # "default" or other keywords
            for e in edges:
                e["style"].update(style)

    return {
        "diagram_type": "flowchart",
        "direction": direction,
        "nodes": list(nodes.values()),
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# sequenceDiagram parser
# ---------------------------------------------------------------------------

_SEQ_PARTICIPANT_RE = re.compile(
    r"^(?:participant|actor)\s+(\S+)(?:\s+as\s+(.+))?$", re.IGNORECASE
)
_SEQ_MSG_RE = re.compile(
    r"^(\w+)\s*(-->>|->>|--x|-x|-->|->|--\)|--|->)\s*(\w+)\s*:\s*(.*)$"
)
_SEQ_NOTE_RE = re.compile(
    r"^[Nn]ote\s+(?:over|(?:left|right)\s+of)\s+(\S+(?:\s*,\s*\S+)?)\s*:\s*(.*)$"
)
_SEQ_BLOCK_RE = re.compile(
    r"^(loop|alt|else|opt|par|critical|break|rect|activate|deactivate)\b",
    re.IGNORECASE
)


def _seq_msg_type(connector: str) -> str:
    if "-->>" in connector or "-->" in connector:
        return "dotted"
    return "arrow"


def _parse_sequence(source: str) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def _ensure_node(pid: str, label: str | None = None) -> None:
        if pid not in nodes:
            nodes[pid] = {
                "id": pid,
                "label": label or pid,
                "shape": "participant",
                "style": {},
            }

    lines = source.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue

        pm = _SEQ_PARTICIPANT_RE.match(line)
        if pm:
            pid = pm.group(1)
            label = (pm.group(2) or pid).strip()
            _ensure_node(pid, label)
            # Mark actors visually distinct
            if line.lower().startswith("actor"):
                nodes[pid]["shape"] = "actor"
            continue

        mm = _SEQ_MSG_RE.match(line)
        if mm:
            src, conn, tgt, msg = mm.group(1), mm.group(2), mm.group(3), mm.group(4)
            _ensure_node(src)
            _ensure_node(tgt)
            edges.append({
                "id": len(edges),
                "source": src,
                "target": tgt,
                "label": msg.strip(),
                "type": _seq_msg_type(conn),
                "style": {},
            })
            continue

    return {
        "diagram_type": "sequence",
        "direction": None,
        "nodes": list(nodes.values()),
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# classDiagram parser
# ---------------------------------------------------------------------------

_CLASS_DEF_RE = re.compile(r"^class\s+(\S+)\s*(?:\{|$)")
_CLASS_REL_RE = re.compile(
    r"^(\S+)\s*"
    r"(<\|--|<\|\.\.|\.\.\||--\||\.\.>|-->|\.\.|--|<-->|\*--|o--)\s*"
    r"(?:\"([^\"]*)\"\s+)?(\S+)"
    r"(?:\s*:\s*(.+))?$"
)
_CLASS_ANNOTATION_RE = re.compile(r"^<<(.+)>>$")

_CLASS_REL_TYPES: dict[str, str] = {
    "<|--": "inheritance",
    "<|..": "realization",
    "..|>": "realization",
    "--|>": "inheritance",
    "..>":  "dependency",
    "-->":  "association",
    "..":   "dependency",
    "--":   "association",
    "<-->": "association",
    "*--":  "composition",
    "o--":  "aggregation",
}


def _classify_class_rel(conn: str) -> str:
    for k, v in _CLASS_REL_TYPES.items():
        if conn.startswith(k) or conn.endswith(k[::-1]):
            return v
    return "association"


def _parse_class(source: str) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    inside_class: str | None = None
    brace_depth = 0

    def _ensure_class(name: str) -> None:
        if name not in nodes:
            nodes[name] = {
                "id": name,
                "label": name,
                "shape": "class",
                "style": {},
                "members": [],
                "annotation": None,
            }

    lines = source.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue

        if line.startswith("classDiagram"):
            continue

        # Track brace depth for class body
        open_b = line.count("{")
        close_b = line.count("}")
        was_inside = inside_class is not None

        # Class definition header
        m = _CLASS_DEF_RE.match(line)
        if m and not was_inside:
            inside_class = m.group(1)
            _ensure_class(inside_class)
            brace_depth += open_b - close_b
            if open_b > close_b:
                continue
            else:
                inside_class = None
            continue

        if inside_class:
            brace_depth += open_b - close_b
            if brace_depth <= 0:
                inside_class = None
                brace_depth = 0
                continue
            # Annotation line like <<interface>>
            ann_m = _CLASS_ANNOTATION_RE.match(line)
            if ann_m:
                nodes[inside_class]["annotation"] = ann_m.group(1).strip()
            else:
                nodes[inside_class]["members"].append(line)
            continue

        # Relationship line
        rel_m = _CLASS_REL_RE.match(line)
        if rel_m:
            src, conn, cardinality, tgt, label = (
                rel_m.group(1), rel_m.group(2),
                rel_m.group(3), rel_m.group(4), rel_m.group(5)
            )
            _ensure_class(src)
            _ensure_class(tgt)
            edges.append({
                "id": len(edges),
                "source": src,
                "target": tgt,
                "label": (label or "").strip(),
                "cardinality": (cardinality or "").strip(),
                "type": _classify_class_rel(conn),
                "style": {},
            })
            continue

        # Bare class name without body
        if re.match(r"^[A-Za-z_]\w*$", line):
            _ensure_class(line)

    return {
        "diagram_type": "class",
        "direction": None,
        "nodes": list(nodes.values()),
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# erDiagram parser
# ---------------------------------------------------------------------------

_ER_REL_RE = re.compile(
    r"^(\S+)\s+"
    r"(\|o|o\||o\{|\{o|\|\||\}\||\|\}|\}o|o\})\s*"
    r"(--|\.\.)?"
    r"\s*(\|o|o\||o\{|\{o|\|\||\}\||\|\}|\}o|o\})\s+"
    r"(\S+)\s*(?::\s*(.+))?$"
)
_ER_ENTITY_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*(?:\{|$)", re.IGNORECASE)
_ER_ATTR_RE = re.compile(r"^\s*(\S+)\s+(\S+)(?:\s+PK|FK)?(?:\s+\"[^\"]*\")?$")

_ER_CARDINALITY: dict[str, str] = {
    "||": "exactly-one",
    "|o": "zero-or-one",
    "o|": "zero-or-one",
    "}|": "one-or-more",
    "|{": "one-or-more",
    "}o": "zero-or-more",
    "o{": "zero-or-more",
    "{o": "zero-or-more",
    "o}": "zero-or-more",
}


def _er_card(token: str) -> str:
    return _ER_CARDINALITY.get(token, token)


def _parse_er(source: str) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    inside_entity: str | None = None
    brace_depth = 0

    def _ensure_entity(name: str) -> None:
        if name not in nodes:
            nodes[name] = {
                "id": name,
                "label": name,
                "shape": "entity",
                "style": {},
                "attributes": [],
            }

    lines = source.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue
        if line.startswith("erDiagram"):
            continue

        open_b = line.count("{")
        close_b = line.count("}")

        # Relationship line
        rel_m = _ER_REL_RE.match(line)
        if rel_m and not inside_entity:
            src = rel_m.group(1)
            left_card = rel_m.group(2)
            right_card = rel_m.group(4)
            tgt = rel_m.group(5)
            label = (rel_m.group(6) or "").strip()
            _ensure_entity(src)
            _ensure_entity(tgt)
            edges.append({
                "id": len(edges),
                "source": src,
                "target": tgt,
                "label": label,
                "cardinality_source": _er_card(left_card),
                "cardinality_target": _er_card(right_card),
                "type": "relationship",
                "style": {},
            })
            continue

        # Entity body
        entity_m = _ER_ENTITY_RE.match(line)
        if entity_m and not inside_entity:
            inside_entity = entity_m.group(1)
            _ensure_entity(inside_entity)
            brace_depth += open_b - close_b
            if open_b == 0 or open_b <= close_b:
                inside_entity = None
                brace_depth = 0
            continue

        if inside_entity:
            brace_depth += open_b - close_b
            if brace_depth <= 0:
                inside_entity = None
                brace_depth = 0
                continue
            attr_m = _ER_ATTR_RE.match(line)
            if attr_m and line != "}":
                nodes[inside_entity]["attributes"].append({
                    "type": attr_m.group(1),
                    "name": attr_m.group(2),
                })

    return {
        "diagram_type": "er",
        "direction": None,
        "nodes": list(nodes.values()),
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            src = fh.read()
    else:
        src = sys.stdin.read()

    print(json.dumps(parse_mermaid(src), indent=2))
