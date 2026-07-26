# CUI // SP-CTI
"""URL content analyzer for the /analyze slash command.

Fetches URL content (web pages or GitHub repos/files), truncates to a
token-safe window, then runs an LLM analysis pass shaped by canvas_type.

Public API
----------
analyze(url, canvas_type) -> dict
    Returns {reply, url, source_type, error}
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional

_MAX_CONTENT = 7000  # characters sent to LLM
_TIMEOUT = 12        # seconds per HTTP request

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_HEADERS = {"User-Agent": "ICDev-Analyzer/1.0", "Accept": "*/*"}


def _fetch(url: str, extra_headers: Optional[dict] = None) -> str:
    headers = {**_HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        raw = resp.read(_MAX_CONTENT * 5)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def _strip_html(html: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"[ \t]{4,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# GitHub URL parser + fetcher
# ---------------------------------------------------------------------------

_GITHUB_RE = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+)"
    r"(?:/(tree|blob)/([^/?#]+)(?:/([^?#]*))?)?",
)

_PRIORITY_BASES = {"main", "app", "index", "program", "server", "cli", "__init__", "mod", "lib"}
_PRIORITY_EXTS  = {".py", ".cs", ".ts", ".go", ".rs", ".java", ".js", ".kt", ".cpp", ".c"}


def _parse_github(url: str) -> Optional[dict]:
    m = _GITHUB_RE.match(url)
    if not m:
        return None
    owner, repo, kind, ref, path = m.groups()
    return {
        "owner": owner,
        "repo":  repo,
        "kind":  kind or "tree",
        "ref":   ref  or "main",
        "path":  (path or "").strip("/"),
    }


def _gh_api_headers() -> dict:
    return {"User-Agent": "ICDev-Analyzer/1.0", "Accept": "application/vnd.github+json"}


def _fetch_github(info: dict) -> str:
    owner, repo, ref, path = info["owner"], info["repo"], info["ref"], info["path"]
    parts: list[str] = [f"# GitHub — {owner}/{repo}  (ref: {ref}, path: /{path})\n"]

    # 1. Directory listing
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    entries: list[dict] = []
    try:
        raw = _fetch(api_url, _gh_api_headers())
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            entries = parsed
            dirs  = [e["name"] for e in entries if e.get("type") == "dir"]
            files = [e["name"] for e in entries if e.get("type") == "file"]
            if dirs:
                parts.append("**Subdirectories:** " + ", ".join(dirs[:20]))
            if files:
                parts.append("**Files:** " + ", ".join(files[:30]))
            parts.append("")
        elif isinstance(parsed, dict) and info["kind"] == "blob":
            # Single file — read content directly
            import base64
            content_b64 = parsed.get("content", "")
            if content_b64:
                decoded = base64.b64decode(content_b64.replace("\n", "")).decode("utf-8", errors="replace")
                parts.append(f"**File:** {parsed.get('name', path)}\n```\n{decoded[:3000]}\n```\n")
                return "\n".join(parts)
    except Exception:
        pass

    # 2. README
    readme_candidates = [f"{path}/README.md" if path else "README.md", "README.md"]
    for rp in readme_candidates:
        try:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rp}"
            content = _fetch(raw_url)
            parts.append(f"## README\n{content[:2500]}\n")
            break
        except Exception:
            continue

    # 3. Sample key source files
    def _sort_key(e: dict) -> tuple:
        name = e.get("name", "").lower()
        base, _, ext = name.rpartition(".")
        return (
            0 if base in _PRIORITY_BASES else 1,
            0 if ("." + ext) in _PRIORITY_EXTS else 1,
            e.get("size", 9999),
        )

    source_files = sorted(
        [e for e in entries if e.get("type") == "file"],
        key=_sort_key,
    )
    fetched = 0
    for entry in source_files[:10]:
        if fetched >= 3:
            break
        dl_url = entry.get("download_url") or (
            f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}/{entry['name']}"
        )
        try:
            snippet = _fetch(dl_url)[:1400]
            parts.append(f"## {entry['name']}\n```\n{snippet}\n```\n")
            fetched += 1
        except Exception:
            continue

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Generic URL fetcher
# ---------------------------------------------------------------------------

def _extract_html(raw: str, query: Optional[str]) -> str:
    """Two-pass fit_markdown extraction, falling back to the regex strip."""
    try:
        from tools.http.page_extract import extract

        result = extract(raw, query=query)
        if result["fit_markdown"].strip():
            return result["fit_markdown"]
    except Exception:  # noqa: BLE001 - never let extraction break a fetch
        pass
    return _strip_html(raw)


def fetch_content(url: str, query: Optional[str] = None) -> tuple[str, str]:
    """Return (content_text, source_type). source_type: 'github' | 'web' | 'error'.

    HTML pages go through the two-pass ``page_extract`` filter rather than a
    regex strip plus a positional ``[:_MAX_CONTENT]`` cut: pass 1 prunes site
    chrome, and when *query* is supplied pass 2 keeps the blocks that actually
    answer it — wherever they sit on the page.  The character cap survives only
    as a backstop.
    """
    info = _parse_github(url)
    if info:
        try:
            return _fetch_github(info)[:_MAX_CONTENT], "github"
        except Exception as exc:
            return f"[GitHub fetch error: {exc}]", "error"

    try:
        raw = _fetch(url)
        if re.search(r"<html", raw[:300], re.I) or "<!doctype" in raw[:300].lower():
            content = _extract_html(raw, query)
        else:
            content = raw
        return content[:_MAX_CONTENT], "web"
    except Exception as exc:
        return f"[Fetch error: {exc}]", "error"


# ---------------------------------------------------------------------------
# Canvas-aware LLM analysis
# ---------------------------------------------------------------------------

_CANVAS_LENS: dict[str, str] = {
    "cam": (
        "migration readiness: identify legacy technologies, EOL dependencies, "
        "refactoring complexity, modernization patterns, and cloud migration risks. "
        "Rate each technology on migration effort (Low/Medium/High)."
    ),
    "sdc": (
        "security posture: authentication mechanisms, authorization patterns, "
        "secrets management, encryption usage, attack surface, and OWASP Top-10 risks."
    ),
    "idc": (
        "infrastructure and deployment: containerization, IaC patterns, CI/CD maturity, "
        "scalability design, cloud-native adoption, and operational readiness."
    ),
    "ndc": (
        "network topology and exposure: protocols used, open endpoints, firewall/ACL design, "
        "network segmentation, and zero-trust readiness."
    ),
    "eda": (
        "data flows and integration: API contracts, event/message patterns, pipeline design, "
        "data formats, and integration coupling."
    ),
    "ddc": (
        "data models and schema: entity design, relationships, normalization level, "
        "indexing strategy, and query access patterns."
    ),
    "pdc": (
        "process flows and orchestration: workflows, state transitions, automation "
        "opportunities, BPMN alignment, and hand-off points."
    ),
    "bdc": (
        "business capabilities and value: feature set, business domain coverage, "
        "capability gaps, and value stream alignment."
    ),
    "odc": (
        "observability coverage: logging strategy, metrics instrumentation, distributed "
        "tracing, alerting design, and SLO/SLI definition."
    ),
}

_DEFAULT_LENS = (
    "general architecture and code quality: tech stack, design patterns, "
    "code organization, separation of concerns, and notable strengths or risks."
)


def analyze(url: str, canvas_type: str = "intake") -> dict:
    """Fetch *url* and return a structured LLM analysis dict.

    Returns
    -------
    {reply: str, url: str, source_type: str, error: str | None}
    """
    content, source_type = fetch_content(url)

    if source_type == "error":
        return {"reply": content, "url": url, "source_type": "error", "error": content}

    lens = _CANVAS_LENS.get(canvas_type.lower(), _DEFAULT_LENS)

    prompt = (
        "You are a senior technical architect performing a code and architecture review.\n\n"
        f"Resource URL: {url}\n"
        f"Analysis lens: {lens}\n\n"
        f"--- CONTENT START ---\n{content}\n--- CONTENT END ---\n\n"
        "Write a structured analysis with these exact sections (use markdown):\n"
        "### Overview\n"
        "What this is, its purpose, and target users (2–3 sentences).\n\n"
        "### Technology Stack\n"
        "Languages, frameworks, key libraries/dependencies found.\n\n"
        "### Key Findings\n"
        "5–7 specific observations relevant to the analysis lens above. "
        "Cite actual file names, class names, or patterns from the content.\n\n"
        "### Recommendations\n"
        "4–5 concrete, prioritized improvement actions.\n\n"
        "### Complexity Rating\n"
        "Low / Medium / High — one sentence justification.\n\n"
        "Be specific. Avoid generic advice. If content was truncated, note what was visible."
    )

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        req = LLMRequest(
            messages=[
                {"role": "system", "content": "You are a senior technical architect performing a code and architecture review. Be specific and cite actual file names, class names, and patterns from the provided content."},
                {"role": "user", "content": prompt},
            ],
        )
        resp = router.invoke("chat_response", req)
        reply = (resp.content or "").strip() if resp else ""
        if not reply:
            raise ValueError("empty LLM response")
    except Exception as exc:
        reply = (
            f"*LLM unavailable ({exc}) — showing raw content preview*\n\n"
            f"```\n{content[:2000]}\n```"
        )

    header = f"## Analysis — [{url}]({url})\n\n"
    return {
        "reply": header + reply,
        "url": url,
        "source_type": source_type,
        "error": None,
    }
