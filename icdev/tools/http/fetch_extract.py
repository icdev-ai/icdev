# CUI // SP-CTI
"""One hardened path from an untrusted URL to model-safe text.

Three things have to happen to every third-party page before a model sees it,
and before this module they happened in different places, differently, or not
at all:

1. **Fetch through the central client** (:mod:`tools.http.client`) so mTLS,
   the CA bundle, egress proxy, retry and backoff from ``args/http_client.yaml``
   apply.  Raw ``urllib.request``/``requests`` call sites bypass all of it.
2. **Extract with the two-pass filter** (:mod:`tools.http.page_extract`) rather
   than a regex tag-strip plus a positional ``[:N]`` cut.  Positional truncation
   keeps whatever happens to sit at the top of the file — nav bars, cookie
   banners — and drops the answer.
3. **Scan for prompt injection** (:func:`tools.security.injection_scanner.scan_text`)
   *after* extraction, so instructions hidden in markup the strip would have
   dropped are still seen, and critical hits are dropped rather than forwarded.

Callers get :class:`FetchedPage` and never an exception: a hostile or dead URL
must not take down the pipeline that touched it.

Public API
----------
``fetch_page(url, query=...)``  fetch + extract + scan (the usual entry point)
``extract_page(html, ...)``     extract + scan for HTML you already hold
``fetch_raw(url, ...)``         byte-capped fetch only, for non-HTML payloads
``scan_or_drop(text, ...)``     scan text from any origin, drop on critical

Usage::

    from tools.http.fetch_extract import fetch_page

    page = fetch_page("https://example.gov/spec", query="key rotation")
    if page.ok:
        prompt = page.text          # fit_markdown, already injection-scanned
    page.blocked                    # True when critical injection was dropped

CLI::

    python tools/http/fetch_extract.py --url https://example.gov/spec \
        --query "key rotation" --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "FetchedPage",
    "fetch_page",
    "extract_page",
    "fetch_raw",
    "scan_or_drop",
    "max_bytes",
    "user_agent",
]


# ── Config (args/http_client.yaml, via the central client's loader) ───────────
def _fetch_cfg() -> dict:
    try:
        from tools.http.client import _load_config

        section = _load_config().get("fetch", {})
        return section if isinstance(section, dict) else {}
    except Exception:  # noqa: BLE001 - config must never break a fetch
        return {}


def max_bytes() -> int:
    """Transport-level read cap per response body, from ``args/http_client.yaml``."""
    try:
        return int(_fetch_cfg().get("max_bytes", 2 * 1024 * 1024))
    except (TypeError, ValueError):
        return 2 * 1024 * 1024


def user_agent() -> str:
    """Default User-Agent for outbound page fetches."""
    return str(_fetch_cfg().get("user_agent") or "ICDEV-Fetch/1.0 (+https://icdev.local)")


def _block_on_critical_default() -> bool:
    value = _fetch_cfg().get("block_on_critical_injection", True)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# ── Result ────────────────────────────────────────────────────────────────────
@dataclass
class FetchedPage:
    """Outcome of a fetch/extract/scan cycle. ``text`` is what a model may read."""

    url: str = ""
    ok: bool = False
    status_code: Optional[int] = None
    content_type: str = ""
    title: str = ""
    text: str = ""
    raw_text: str = ""
    links: list = field(default_factory=list)
    injection_findings: list = field(default_factory=list)
    blocked: bool = False
    truncated: bool = False
    is_html: bool = False
    reason: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "ok": self.ok,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "title": self.title,
            "text": self.text,
            "raw_text": self.raw_text,
            "links": self.links,
            "injection_findings": self.injection_findings,
            "blocked": self.blocked,
            "truncated": self.truncated,
            "is_html": self.is_html,
            "reason": self.reason,
            "error": self.error,
        }


# ── Injection scanning ────────────────────────────────────────────────────────
def scan_or_drop(
    text: str,
    *,
    source: str = "",
    block_on_critical: Optional[bool] = None,
) -> tuple[str, list, bool]:
    """Scan *text* for prompt injection.  Returns ``(text, findings, blocked)``.

    On a *critical* finding the text is replaced with the empty string so a
    caller that ignores ``blocked`` still cannot forward the payload to a model.
    Lower-severity findings are reported but do not drop the content — that
    matches the existing genesis reflex contract (block critical, log the rest).
    """
    if not text:
        return text, [], False
    try:
        from tools.security.injection_scanner import scan_text
    except Exception:  # noqa: BLE001 - scanner import failure must fail closed
        return "", [], True

    try:
        findings = scan_text(text, source=source) or []
    except Exception as exc:  # noqa: BLE001 - a scanner crash must fail closed
        return "", [{"category": "scanner_error", "severity": "critical", "snippet": str(exc)[:200]}], True

    if block_on_critical is None:
        block_on_critical = _block_on_critical_default()
    critical = [f for f in findings if str(f.get("severity", "")).lower() == "critical"]
    if critical and block_on_critical:
        return "", findings, True
    return text, findings, False


# ── Fetch ─────────────────────────────────────────────────────────────────────
def fetch_raw(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: Optional[Any] = None,
    limit: Optional[int] = None,
    session: Any = None,
) -> FetchedPage:
    """Fetch *url* through the central client, reading at most ``limit`` bytes.

    No extraction and no scanning — use :func:`fetch_page` for HTML.  The body is
    streamed and cut at the byte cap so a multi-gigabyte response cannot exhaust
    memory before the cap is checked.
    """
    page = FetchedPage(url=url)
    cap = int(limit if limit is not None else max_bytes())

    try:
        from tools.http.client import default_timeout, get_session, iter_chunk_size
    except Exception as exc:  # noqa: BLE001 - requests missing / air-gap
        page.error = f"http client unavailable: {exc}"
        return page

    request_headers = {"User-Agent": user_agent(), "Accept": "*/*"}
    request_headers.update(headers or {})

    owned = session is None
    try:
        sess = session or get_session()
        try:
            resp = sess.get(
                url,
                headers=request_headers,
                timeout=timeout if timeout is not None else default_timeout(),
                stream=True,
            )
            page.status_code = resp.status_code
            page.content_type = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip()
            resp.raise_for_status()

            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=iter_chunk_size()):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= cap:
                    page.truncated = True
                    break
            body = b"".join(chunks)[:cap]
            encoding = resp.encoding or resp.apparent_encoding or "utf-8"
            page.raw_text = body.decode(encoding, errors="replace")
            page.ok = True
            resp.close()
        finally:
            if owned:
                sess.close()
    except Exception as exc:  # noqa: BLE001 - a dead URL is data, not a crash
        page.error = f"{type(exc).__name__}: {exc}"
        page.ok = False
    return page


def _looks_like_html(text: str, content_type: str) -> bool:
    if content_type and ("html" in content_type or "xhtml" in content_type):
        return True
    if content_type and content_type not in ("", "application/octet-stream", "text/plain"):
        return False
    head = text[:512].lstrip().lower()
    return head.startswith("<!doctype html") or "<html" in head or "<body" in head


# ── Extract ───────────────────────────────────────────────────────────────────
def extract_page(
    html: str,
    *,
    url: str = "",
    query: Optional[str] = None,
    content_type: str = "text/html",
    scan: bool = True,
    block_on_critical: Optional[bool] = None,
) -> FetchedPage:
    """Run the two-pass filter (and the injection scan) over HTML already in hand.

    Falls back through ``fit_markdown`` → ``raw_text`` → parser-based
    :func:`~tools.http.page_extract.to_text`, so a page the relevance pass empties
    out still yields its readable text instead of nothing.
    """
    page = FetchedPage(url=url, ok=True, content_type=content_type, raw_text=html)
    page.is_html = _looks_like_html(html, content_type)

    if not page.is_html:
        page.text = html
        page.reason = "not html — passed through unextracted"
    else:
        from tools.http import page_extract

        try:
            result = page_extract.extract(html, query=query)
            page.title = result["title"]
            page.raw_text = result["raw_text"]
            page.links = result["links"]
            page.reason = result["reason"]
            page.text = result["fit_markdown"].strip() or result["raw_text"]
            if not result["fit_markdown"].strip():
                page.reason += "; fit_markdown empty — fell back to raw_text"
        except Exception as exc:  # noqa: BLE001 - extraction must not break a fetch
            page.raw_text = page_extract.to_text(html)
            page.text = page.raw_text
            page.reason = f"page_extract failed ({type(exc).__name__}: {exc}) — used to_text()"

    if scan:
        page.text, page.injection_findings, page.blocked = scan_or_drop(
            page.text, source=url or "fetch_extract", block_on_critical=block_on_critical
        )
        if page.blocked:
            page.reason = (page.reason + "; " if page.reason else "") + (
                "critical prompt-injection finding — content dropped"
            )
    return page


def fetch_page(
    url: str,
    *,
    query: Optional[str] = None,
    headers: Optional[dict] = None,
    timeout: Optional[Any] = None,
    limit: Optional[int] = None,
    session: Any = None,
    scan: bool = True,
    block_on_critical: Optional[bool] = None,
) -> FetchedPage:
    """Fetch *url*, extract relevant text, injection-scan it.  Never raises.

    Args:
        url: the target URL (untrusted third-party content).
        query: what the caller is looking for.  Drives the BM25 pass — supply it
            whenever you have one, or relevance selection degrades to pruning only.
        headers: extra request headers merged over the defaults.
        timeout: requests timeout; defaults to ``args/http_client.yaml``.
        limit: byte cap override; defaults to ``fetch.max_bytes``.
        session: reuse an existing ``requests.Session`` across many fetches.
        scan: set False only for forensic callers that must see raw payloads.
        block_on_critical: override the ``fetch.block_on_critical_injection`` arg.

    Returns:
        :class:`FetchedPage`.  Check ``ok``; ``error`` explains a failure.
    """
    page = fetch_raw(url, headers=headers, timeout=timeout, limit=limit, session=session)
    if not page.ok:
        return page

    extracted = extract_page(
        page.raw_text,
        url=url,
        query=query,
        content_type=page.content_type or "text/html",
        scan=scan,
        block_on_critical=block_on_critical,
    )
    extracted.status_code = page.status_code
    extracted.truncated = page.truncated
    if page.truncated:
        extracted.reason = (extracted.reason + "; " if extracted.reason else "") + (
            f"response body cut at fetch.max_bytes ({max_bytes()} bytes)"
        )
    return extracted


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a URL through the central client, extract fit_markdown, scan for injection."
    )
    parser.add_argument("--url", required=True, help="URL to fetch")
    parser.add_argument("--query", default=None, help="query for the BM25 relevance pass")
    parser.add_argument("--json", action="store_true", help="emit the full payload")
    args = parser.parse_args(argv)

    page = fetch_page(args.url, query=args.query)
    if args.json:
        print(json.dumps(page.to_dict(), indent=2, ensure_ascii=False))
    else:
        if page.error:
            print(f"error: {page.error}", file=sys.stderr)
            return 1
        print(page.text)
    return 0 if page.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
