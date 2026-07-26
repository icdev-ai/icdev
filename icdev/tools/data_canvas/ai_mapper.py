# CUI // SP-CTI
"""AI Data Mapping Engine.

Pure-function schema parsing, field matching, and transformation generation.
No Flask imports — usable in tests and background jobs.

Air-gap safe:
  - Schema parsing: deterministic Python regex/JSON/CSV — no LLM.
  - Field matching: name similarity + type compatibility — no LLM.
  - Semantic similarity: LLMRouter embeddings — gracefully degrades to 0.0
    matrix if LLM unavailable (air-gap / no embedding provider).
  - Transform generation: SQL always deterministic; Python/dbt fall back to
    template if LLM unavailable.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from tools.data_canvas.constants import (
    MAPPING_ARTIFACT_TYPES,
    MAPPING_CONF_AUTO_CONFIRM,
    MAPPING_CONF_SUGGEST,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_MAX_SCHEMA_BYTES = 512_000  # 500 KB guard

# ── Type groups for compatibility scoring ─────────────────────────────────────
_INT_TYPES   = {"int", "integer", "bigint", "smallint", "tinyint", "number"}
_STR_TYPES   = {"text", "string", "varchar", "char", "nvarchar", "nchar", "clob"}
_FLOAT_TYPES = {"real", "float", "double", "decimal", "numeric", "money"}
_BOOL_TYPES  = {"bool", "boolean", "bit"}
_DATE_TYPES  = {"date", "datetime", "timestamp", "timestamptz", "time"}
_BLOB_TYPES  = {"blob", "bytea", "binary", "varbinary"}


# ── Schema parsers ────────────────────────────────────────────────────────────

def _parse_json_schema(raw: str) -> list[dict]:
    """Recursively flatten JSON Schema properties into field descriptors."""
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("ai_mapper: invalid JSON Schema")
        return []

    def _walk(node: dict, path: str) -> list[dict]:
        fields = []
        props = node.get("properties", {})
        for name, spec in props.items():
            fpath = f"{path}.{name}" if path else name
            js_type = spec.get("type", "string")
            if isinstance(js_type, list):
                js_type = js_type[0]
            fields.append({
                "name": name,
                "type": _normalise_type(js_type),
                "path": fpath,
                "nullable": name not in (node.get("required") or []),
                "description": spec.get("description", ""),
            })
            if "properties" in spec:
                fields.extend(_walk(spec, fpath))
        return fields

    return _walk(data, "")


def _parse_csv_headers(raw: str) -> list[dict]:
    """Parse comma or newline-separated column headers."""
    sep = "," if "," in raw.split("\n")[0] else "\n"
    headers = [h.strip() for h in raw.split(sep) if h.strip()]
    return [
        {"name": h, "type": "string", "path": h, "nullable": True, "description": ""}
        for h in headers
    ]


_DDL_COL_RE = re.compile(
    r"^\s*[`\"]?(\w+)[`\"]?\s+"
    r"(INTEGER|INT|BIGINT|SMALLINT|TINYINT|SERIAL|BIGSERIAL"
    r"|TEXT|VARCHAR|CHAR|NVARCHAR|NCHAR|CLOB|STRING"
    r"|REAL|FLOAT|DOUBLE|DECIMAL|NUMERIC|MONEY"
    r"|BOOLEAN|BOOL|BIT"
    r"|TIMESTAMP(?:TZ)?|DATE|DATETIME|TIME"
    r"|BLOB|BYTEA|BINARY|VARBINARY|JSON|JSONB|UUID|ARRAY)"
    r"(?:\s*\([^)]*\))?",
    re.IGNORECASE,
)


_DDL_SKIP = {"CREATE", "TABLE", "PRIMARY", "FOREIGN", "UNIQUE",
             "INDEX", "CONSTRAINT", "CHECK", "KEY", "REFERENCES",
             "ALTER", "DROP", "ADD", "MODIFY", "CHANGE"}


def _parse_sql_ddl(raw: str) -> list[dict]:
    """Parse column definitions from SQL DDL CREATE TABLE statement(s).

    Handles both multi-line DDL (one column per line) and single-line DDL
    where columns are comma-separated inside parentheses.
    """
    fields = []
    _seen: set[str] = set()

    def _process_chunk(chunk: str) -> None:
        chunk = chunk.strip().rstrip(",").strip()
        m = _DDL_COL_RE.match(chunk)
        if not m:
            return
        col_name = m.group(1)
        col_type = m.group(2).upper()
        if col_name.upper() in _DDL_SKIP:
            return
        if col_name in _seen:
            return
        _seen.add(col_name)
        nullable = "NOT NULL" not in chunk.upper()
        fields.append({
            "name": col_name,
            "type": _normalise_type(col_type),
            "path": col_name,
            "nullable": nullable,
            "description": "",
        })

    # First try multi-line (standard formatted DDL)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) > 2:
        for line in lines:
            _process_chunk(line)
    else:
        # Single-line DDL: extract columns between ( and )
        m_paren = re.search(r"\((.+)\)", raw, re.DOTALL)
        if m_paren:
            inner = m_paren.group(1)
            # Split on commas not inside nested parens
            depth = 0
            buf = []
            for ch in inner:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    _process_chunk("".join(buf))
                    buf = []
                else:
                    buf.append(ch)
            if buf:
                _process_chunk("".join(buf))
        else:
            # Fallback: try each line anyway
            for line in lines:
                _process_chunk(line)
    return fields


def _parse_openapi3(raw: str) -> list[dict]:
    """Flatten OpenAPI 3.0 requestBody/component schemas into field descriptors."""
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("ai_mapper: invalid OpenAPI JSON")
        return []

    fields = []
    schemas = data.get("components", {}).get("schemas", {})

    def _walk_schema(schema_name: str, schema: dict) -> None:
        for name, spec in schema.get("properties", {}).items():
            js_type = spec.get("type", "string")
            if isinstance(js_type, list):
                js_type = js_type[0]
            fields.append({
                "name": name,
                "type": _normalise_type(js_type),
                "path": f"{schema_name}.{name}",
                "nullable": name not in (schema.get("required") or []),
                "description": spec.get("description", ""),
            })

    for sname, sdef in schemas.items():
        _walk_schema(sname, sdef)

    return fields


def _normalise_type(raw_type: str) -> str:
    """Normalise SQL/JSON/OpenAPI type names to a canonical lower-case form."""
    t = raw_type.lower().split("(")[0].strip()
    if t in _INT_TYPES or t in {"serial", "bigserial"}:
        return "integer"
    if t in _STR_TYPES:
        return "string"
    if t in _FLOAT_TYPES:
        return "float"
    if t in _BOOL_TYPES:
        return "boolean"
    if t in _DATE_TYPES:
        return "datetime"
    if t in _BLOB_TYPES:
        return "bytes"
    return t or "string"


# ── Public: parse_schema ──────────────────────────────────────────────────────

def parse_schema(raw: str, fmt: str) -> list[dict]:
    """Parse a raw schema string into a list of field descriptors.

    Each descriptor: {name, type, path, nullable, description}
    Never raises — returns [] on parse failure.
    """
    if not raw or not isinstance(raw, str):
        return []
    if len(raw.encode()) > _MAX_SCHEMA_BYTES:
        logger.warning("ai_mapper: schema too large (> %d bytes), truncating", _MAX_SCHEMA_BYTES)
        raw = raw[:_MAX_SCHEMA_BYTES]

    try:
        if fmt == "json_schema":
            return _parse_json_schema(raw)
        if fmt == "csv_headers":
            return _parse_csv_headers(raw)
        if fmt == "sql_ddl":
            return _parse_sql_ddl(raw)
        if fmt == "openapi3":
            return _parse_openapi3(raw)
        logger.warning("ai_mapper: unknown format %r, trying JSON Schema", fmt)
        return _parse_json_schema(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_mapper: parse_schema failed for fmt=%r: %s", fmt, exc)
        return []


# ── Similarity helpers ────────────────────────────────────────────────────────

def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a):
        curr = [i + 1] + [0] * lb
        for j, cb in enumerate(b):
            curr[j + 1] = min(
                prev[j + 1] + 1,
                curr[j] + 1,
                prev[j] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[lb]


def _tokenise(name: str) -> set[str]:
    """Split identifier on _, ., camelCase, and spaces into lower-case tokens."""
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    return {t.lower() for t in re.split(r"[_.\s]+", name) if t}


def _name_similarity(a: str, b: str) -> float:
    """Name similarity: 0.6 × edit-distance score + 0.4 × Jaccard token overlap."""
    if not a or not b:
        return 0.0
    a_low, b_low = a.lower(), b.lower()
    if a_low == b_low:
        return 1.0
    max_len = max(len(a_low), len(b_low))
    edit_score = 1.0 - _levenshtein(a_low, b_low) / max_len
    tok_a, tok_b = _tokenise(a), _tokenise(b)
    jaccard = len(tok_a & tok_b) / len(tok_a | tok_b) if (tok_a | tok_b) else 0.0
    return 0.6 * edit_score + 0.4 * jaccard


def _type_compat(ta: str, tb: str) -> float:
    """Type compatibility: 1.0 identical, 0.7 same group, 0.0 incompatible."""
    if ta == tb:
        return 1.0
    groups = [_INT_TYPES, _STR_TYPES, _FLOAT_TYPES, _BOOL_TYPES,
              _DATE_TYPES, _BLOB_TYPES]
    for g in groups:
        canon_ta = _normalise_type(ta)
        canon_tb = _normalise_type(tb)
        if canon_ta in g and canon_tb in g:
            return 0.7
    return 0.0


def _semantic_similarity(names_a: list[str], names_b: list[str]) -> list[list[float]]:
    """Return cosine-similarity matrix [n_a][n_b] using LLMRouter embeddings.

    Returns zero matrix on any error — caller falls back to name+type only.
    """
    n_a, n_b = len(names_a), len(names_b)
    zero = [[0.0] * n_b for _ in range(n_a)]
    if not names_a or not names_b:
        return zero
    try:
        from tools.llm.router import LLMRouter  # noqa: PLC0415
        router = LLMRouter()
        # get_embedding_provider() returns a live provider or raises
        # LLMUnavailableError (no-LLM / air-gap) — the broad except below then
        # degrades to the zero matrix so callers fall back to name+type only.
        provider = getattr(router, "get_embedding_provider", lambda: None)()
        if provider is None:
            return zero
        vecs_a = provider.embed_batch(names_a)
        vecs_b = provider.embed_batch(names_b)
        if not vecs_a or not vecs_b or vecs_a[0] is None:
            return zero

        def _dot(u: list[float], v: list[float]) -> float:
            return sum(x * y for x, y in zip(u, v))

        def _norm(u: list[float]) -> float:
            return sum(x * x for x in u) ** 0.5

        matrix = []
        for va in vecs_a:
            if va is None:
                matrix.append([0.0] * n_b)
                continue
            row = []
            na = _norm(va)
            for vb in vecs_b:
                if vb is None:
                    row.append(0.0)
                    continue
                nb = _norm(vb)
                sim = _dot(va, vb) / (na * nb) if na and nb else 0.0
                row.append(max(0.0, min(1.0, sim)))
            matrix.append(row)
        return matrix
    except Exception as exc:  # noqa: BLE001
        logger.debug("ai_mapper: semantic similarity unavailable: %s", exc)
        return zero


# ── Public: score_field_pairs ─────────────────────────────────────────────────

def score_field_pairs(
    src_fields: list[dict],
    tgt_fields: list[dict],
) -> list[dict]:
    """Score source→target field pairs, one best-match result per source field.

    Confidence formula (LLM available):
        0.40 × name_score + 0.35 × semantic_score + 0.25 × type_score
    Confidence formula (air-gap / no LLM):
        0.65 × name_score + 0.35 × type_score

    Returns list of match dicts:
        {src, tgt, confidence, match_method, name_score, sem_score, type_score}
    """
    if not src_fields or not tgt_fields:
        return []

    src_names = [f["name"] for f in src_fields]
    tgt_names = [f["name"] for f in tgt_fields]

    sem_matrix = _semantic_similarity(src_names, tgt_names)
    has_semantic = any(any(v > 0 for v in row) for row in sem_matrix)

    results = []
    for i, src in enumerate(src_fields):
        best_j, best_conf = 0, -1.0
        best_scores: dict[str, float] = {}
        for j, tgt in enumerate(tgt_fields):
            ns = _name_similarity(src["name"], tgt["name"])
            ts = _type_compat(src.get("type", ""), tgt.get("type", ""))
            ss = sem_matrix[i][j]
            if has_semantic:
                conf = 0.40 * ns + 0.35 * ss + 0.25 * ts
                method = "combined"
            else:
                conf = 0.65 * ns + 0.35 * ts
                method = "name"
            if conf > best_conf:
                best_j, best_conf = j, conf
                best_scores = {"name_score": ns, "sem_score": ss,
                               "type_score": ts, "match_method": method}

        tgt = tgt_fields[best_j]
        results.append({
            "id": f"fm-{uuid.uuid4().hex[:12]}",
            "src": src,
            "tgt": tgt,
            "confidence": round(best_conf, 4),
            "match_method": best_scores.get("match_method", "name"),
            "name_score": best_scores.get("name_score", 0.0),
            "sem_score": best_scores.get("sem_score", 0.0),
            "type_score": best_scores.get("type_score", 0.0),
        })
    return results


# ── Public: assign_status ─────────────────────────────────────────────────────

def assign_status(confidence: float) -> str:
    """Map a confidence score to a field mapping status string."""
    if confidence >= MAPPING_CONF_AUTO_CONFIRM:
        return "confirmed"
    if confidence >= MAPPING_CONF_SUGGEST:
        return "pending"
    return "needs_review"


# ── Public: generate_transforms ───────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_sql(session_id: str, pairs: list[dict], classification: str) -> str:
    """Deterministic SQL SELECT … AS transformation template."""
    lines = [
        f"-- AI Data Mapping — session: {session_id}",
        f"-- Generated: {_now_utc()}   Classification: {classification}",
        "-- Review all CAST expressions before running in production.",
        "--",
        "SELECT",
    ]
    for idx, p in enumerate(pairs):
        src = p.get("source_field", "")
        tgt = p.get("target_field", "")
        src_t = p.get("source_type", "string")
        tgt_t = p.get("target_type", "string")
        comma = "," if idx < len(pairs) - 1 else ""
        if src_t == tgt_t or not tgt_t:
            lines.append(f"    {src} AS {tgt}{comma}")
        else:
            lines.append(f"    CAST({src} AS {tgt_t.upper()}) AS {tgt}{comma}  -- {src_t} → {tgt_t}")
    lines += ["FROM source_table;", ""]
    return "\n".join(lines)


def _gen_python(pairs: list[dict]) -> str:
    """Python dict-comprehension fallback template."""
    mapping_lines = []
    for p in pairs:
        src = p.get("source_field", "")
        tgt = p.get("target_field", "")
        expr = p.get("transform_expr") or f'source.get("{src}")'
        mapping_lines.append(f'    "{tgt}": {expr},')
    body = "\n".join(mapping_lines)
    return (
        "def transform(source: dict) -> dict:\n"
        '    """AI-generated field mapping transform. Review before production use."""\n'
        "    return {\n"
        f"{body}\n"
        "    }\n"
    )


def _gen_dbt(session_id: str, pairs: list[dict]) -> str:
    """dbt Jinja2 SELECT model template."""
    lines = [
        f"{{# AI Data Mapping — session: {session_id} #}}",
        "SELECT",
    ]
    for idx, p in enumerate(pairs):
        src = p.get("source_field", "")
        tgt = p.get("target_field", "")
        comma = "," if idx < len(pairs) - 1 else ""
        lines.append(f"    {{{{ dbt_utils.safe_cast('{src}', api.Column.translate_type('string')) }}}} AS {tgt}{comma}")
    lines += ["FROM {{ source('source_system', 'source_table') }}", ""]
    return "\n".join(lines)


def generate_transforms(
    session_id: str,
    confirmed_pairs: list[dict],
    artifact_type: str,
    classification: str = "CUI",
) -> tuple[str, str]:
    """Generate transformation artifact text.

    Returns (artifact_text, model_used). SQL is always deterministic.
    Python/dbt attempt LLM then fall back to templates.
    """
    if artifact_type not in MAPPING_ARTIFACT_TYPES:
        artifact_type = "sql"

    if artifact_type == "sql":
        return _gen_sql(session_id, confirmed_pairs, classification), "template"

    if artifact_type in ("python", "dbt"):
        try:
            from tools.llm.router import LLMRouter  # noqa: PLC0415
            from tools.llm.provider import LLMRequest  # noqa: PLC0415
            router = LLMRouter()
            provider, model_id, _ = router.get_provider_for_function("code_generation")
            if provider and model_id:
                pairs_json = json.dumps(confirmed_pairs[:20], indent=2)
                fmt = "Python dict transform function" if artifact_type == "python" else "dbt Jinja2 SELECT model"
                prompt = (
                    f"Generate a {fmt} that maps source fields to target fields.\n"
                    f"Field pairs (source_field → target_field):\n{pairs_json}\n"
                    "Return only the code, no explanation."
                )
                req = LLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt=(
                        "You are a data engineer. Generate a correct, minimal field-mapping "
                        "transform. Return only code, no prose and no markdown fences."
                    ),
                    max_tokens=1024,
                    temperature=0.1,
                    classification=classification,
                    skip_injection_scan=True,
                )
                resp = router.invoke("code_generation", req)
                text = getattr(resp, "content", "") or getattr(resp, "text", "")
                if text:
                    return text.strip(), model_id
        except Exception as exc:  # noqa: BLE001
            # Do not silently swallow — a dead LLM path here previously looked
            # identical to a working one. Log at WARNING so the fallback is visible.
            logger.warning("ai_mapper: LLM transform generation failed, using template: %s", exc)

        # Template fallback
        if artifact_type == "python":
            return _gen_python(confirmed_pairs), "template"
        return _gen_dbt(session_id, confirmed_pairs), "template"

    if artifact_type == "xslt":
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<!-- AI Data Mapping — session: {session_id}  Classification: {classification} -->',
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">',
            '  <xsl:output method="xml" indent="yes"/>',
            '  <xsl:template match="/">',
            '    <target>',
        ]
        for p in confirmed_pairs:
            src = p.get("source_field", "")
            tgt = p.get("target_field", "")
            lines.append(f'      <{tgt}><xsl:value-of select="//{src}"/></{tgt}>')
        lines += ["    </target>", "  </xsl:template>", "</xsl:stylesheet>", ""]
        return "\n".join(lines), "template"

    return _gen_sql(session_id, confirmed_pairs, classification), "template"
