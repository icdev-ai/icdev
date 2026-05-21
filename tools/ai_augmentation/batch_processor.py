# CUI // SP-CTI
"""AAC Batch Processor — Claude Batch API path for large repos (>10K files).

When total_files exceeds batch_threshold_files in aac_config.yaml, this module
submits source file content to the Anthropic Messages Batches API instead of
running Semgrep/AST locally.  Skipped when ICDEV_LLM_PROVIDER=ollama (air-gap).

Public API:
    is_batch_eligible(total_files: int) -> bool
    run_batch_scan(input_ref, scan_id, model, poll_interval) -> (patterns, batch_id | None)
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
from typing import Any

import yaml

_CONFIG_PATH = pathlib.Path(__file__).parent.parent.parent / "args" / "aac_config.yaml"
_BATCH_SIZE = 10_000        # Max requests per batch (Anthropic hard limit)
_DEFAULT_THRESHOLD = 10_000
_POLL_INTERVAL_SECS = 30
_MAX_TOKENS = 512

# Source file extensions to include in batch requests
_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}

_PROMPT_TEMPLATE = """\
Analyze the following source file and identify AI-augmentable code patterns.
Respond only with JSON using this schema: {{"patterns": [...]}}
Each pattern object: {{
  "pattern_type": one of [nested_conditionals, regex_user_input, \
string_template_rendering, scheduled_cron, hardcoded_threshold, \
db_render_notify_chain, keyword_list_search, large_rule_table],
  "function_name": "<enclosing function or '<unknown>'>",
  "line_start": <integer>,
  "line_end": <integer>,
  "pattern_detail": {{}}
}}
If no patterns are found respond with: {{"patterns": []}}

File: {file_path}
---
{file_content}"""


def _load_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _batch_threshold() -> int:
    return int(_load_config().get("batch_threshold_files", _DEFAULT_THRESHOLD))


def is_batch_eligible(total_files: int) -> bool:
    """Return True when the batch API path should be used.

    Conditions: file count exceeds threshold AND provider is not ollama
    (air-gap mode must use local Semgrep/AST fallback).
    """
    if os.environ.get("ICDEV_LLM_PROVIDER", "").lower() == "ollama":
        return False
    return total_files > _batch_threshold()


def _collect_source_files(root: str) -> list[pathlib.Path]:
    p = pathlib.Path(root)
    if p.is_file():
        return [p] if p.suffix.lower() in _EXT_LANGUAGE else []
    return sorted(
        f for f in p.rglob("*")
        if f.is_file() and f.suffix.lower() in _EXT_LANGUAGE
    )


def _file_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()


def _build_requests(
    files: list[pathlib.Path],
    scan_id: int,
    model: str,
) -> list[dict]:
    requests: list[dict] = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        h = _file_hash(f)
        prompt = _PROMPT_TEMPLATE.format(
            file_path=str(f),
            file_content=content[:8000],  # guard against extremely large files
        )
        requests.append({
            "custom_id": f"aac-{scan_id}-{h[:8]}",
            "params": {
                "model": model,
                "max_tokens": _MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            },
        })
    return requests


def _submit_batch(client: Any, requests: list[dict]) -> str:
    response = client.messages.batches.create(requests=requests)
    return response.id


def _poll_until_ended(
    client: Any,
    batch_id: str,
    poll_interval: int = _POLL_INTERVAL_SECS,
) -> None:
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return
        time.sleep(poll_interval)


def _parse_results(
    client: Any,
    batch_id: str,
    files: list[pathlib.Path],
    scan_id: int,
) -> list[dict]:
    """Stream batch results and convert LLM responses to AAC pattern dicts."""
    # Build lookup: last 8 hex chars of file hash → Path
    hash_to_file: dict[str, pathlib.Path] = {
        _file_hash(f)[:8]: f for f in files
    }

    patterns: list[dict] = []
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            continue
        message = result.result.message
        if not message.content:
            continue
        raw_text = getattr(message.content[0], "text", "")

        # custom_id format: "aac-{scan_id}-{hash8}"
        parts = result.custom_id.split("-")
        if len(parts) < 3:
            continue
        hash8 = parts[-1]
        source_file = hash_to_file.get(hash8)
        if source_file is None:
            continue

        try:
            json_text = raw_text
            if "```" in raw_text:
                start = raw_text.find("```")
                end = raw_text.rfind("```")
                if start != end:
                    json_text = raw_text[start:end].strip("`").strip()
                    if json_text.startswith("json"):
                        json_text = json_text[4:].strip()
            data = json.loads(json_text)
            file_patterns = data.get("patterns", [])
        except (json.JSONDecodeError, ValueError, AttributeError):
            continue

        language = _EXT_LANGUAGE.get(source_file.suffix.lower(), "unknown")
        for pat in file_patterns:
            pat_type = pat.get("pattern_type", "")
            if not pat_type:
                continue
            patterns.append({
                "pattern_type": pat_type,
                "module_path": str(source_file),
                "function_name": pat.get("function_name", "<unknown>"),
                "line_start": int(pat.get("line_start", 0)),
                "line_end": int(pat.get("line_end", 0)),
                "language": language,
                "pattern_detail": pat.get("pattern_detail", {}),
            })

    return patterns


def run_batch_scan(
    input_ref: str,
    scan_id: int,
    model: str = "claude-haiku-4-5-20251001",
    poll_interval: int = _POLL_INTERVAL_SECS,
) -> tuple[list[dict], str | None]:
    """Submit source files to the Claude Batch API and return parsed patterns.

    Args:
        input_ref:     Path to source file or directory.
        scan_id:       ID of the aac_scans row (used in custom_id for audit).
        model:         Claude model to use for pattern analysis.
        poll_interval: Seconds between status polls.

    Returns:
        (patterns, primary_batch_id) — batch_id is None when skipped or on error.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return [], None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return [], None

    files = _collect_source_files(input_ref)
    if not files:
        return [], None

    all_patterns: list[dict] = []
    batch_ids: list[str] = []

    for chunk_start in range(0, len(files), _BATCH_SIZE):
        chunk = files[chunk_start: chunk_start + _BATCH_SIZE]
        requests = _build_requests(chunk, scan_id, model)
        if not requests:
            continue
        batch_id = _submit_batch(client, requests)
        batch_ids.append(batch_id)
        _poll_until_ended(client, batch_id, poll_interval)
        all_patterns.extend(_parse_results(client, batch_id, chunk, scan_id))

    primary_batch_id = batch_ids[0] if batch_ids else None
    return all_patterns, primary_batch_id
