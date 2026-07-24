# CUI // SP-CTI
"""Document Modernization Engine — egress-safe URL link-rot detection.

Cited URLs decay: pages 404, move permanently, or their content silently
changes. This module extracts the URLs a DIC document cites, checks each
one's health, and records deterministic findings (broken / moved / changed)
that flow through the same append-only supersede discipline and DocDrift dedup
as every other docmod finding — no new sink, no LLM in the verdict.

SECURITY — outbound egress guard (this is the load-bearing part)
----------------------------------------------------------------
The URLs come from document content, so treating them as trusted request
targets would be a server-side request forgery surface. Every URL passes an
egress policy BEFORE any socket is opened:

  * https only. Any other scheme (http, ftp, file, gopher, ...) is refused.
  * The hostname is resolved to its IP address(es) FIRST, then EVERY resolved
    address is checked against the denied set below. If ANY resolved address is
    non-public the whole URL is refused. Checking after resolution — rather than
    string-matching the hostname — is what prevents a public-looking name from
    steering the request at internal infrastructure.
  * Denied address space: loopback (127.0.0.0/8, ::1), private/RFC1918 and
    IPv6 unique-local (fc00::/7), link-local (169.254.0.0/16 — which covers the
    cloud instance-metadata address — and fe80::/10), multicast, unspecified,
    and other reserved ranges. Public, globally routable addresses pass.
  * An operator allowlist / denylist (args/docmod/docmod_config.yaml) is
    honored; denylist wins over allowlist.
  * Redirects are never auto-followed. Each hop's target is re-resolved and
    re-checked through the same guard, and the chain depth is capped.
  * The number of URLs checked per sweep is capped (blast-radius + politeness).

Air-gap: when there is no egress (offline config flag or air-gap runtime) the
sweep skips cleanly and every URL is reported ``not checked (no egress)`` — a
missing network is NEVER reported as a rotted link.

TRUST: link status is deterministic given a response; the LLM is not involved.
HITL: findings are queued for triage (state 'open'); nothing is auto-edited.
"""
from __future__ import annotations

import functools
import hashlib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Findings from this module all share one synthetic pack id and finding type,
# so the card/drift bridges and get_findings() treat them like any other pack.
PACK_ID = "link_health"
FINDING_TYPE = "link_rot"          # constants.FINDING_TYPES (plain TEXT column, no CHECK)
ENTITY_TYPE = "evidence_anchor"    # constants.KG_ENTITY_TYPES — a URL is a citation link

# Mutable per-URL bookkeeping (NOT append-only — like docmod_doc_scan_state).
# Holds the last-known content hash so head-content drift can be detected across
# sweeps. Carries tenant_id/classification for RLS parity with the findings it
# feeds.
_LINK_STATE_DDL = """
CREATE TABLE IF NOT EXISTS docmod_link_checks (
    check_key            TEXT PRIMARY KEY,
    doc_id               TEXT NOT NULL,
    url                  TEXT NOT NULL,
    url_host             TEXT,
    last_status          TEXT,
    last_http_status     INTEGER,
    last_head_hash       TEXT,
    last_redirect_target TEXT,
    last_checked_at      TEXT,
    tenant_id            TEXT,
    classification       TEXT DEFAULT 'CUI'
)
"""

# Deterministic verdicts, keyed by the checker's status. ok / blocked /
# not_checked / error do NOT map to a finding (a blocked internal URL is a
# data-quality signal, but not "link rot", and an unreachable-because-air-gap
# URL must never be called rotted).
_STATUS_VERDICT: dict[str, dict] = {
    "broken": {
        "currency_verdict": "retired",
        "severity": "high",
        "rationale": "Cited URL is unreachable (HTTP 404/410 or a dead redirect chain).",
    },
    "moved": {
        "currency_verdict": "deprecated",
        "severity": "low",
        "rationale": "Cited URL permanently redirects; the citation should point at the new location.",
    },
    "changed": {
        "currency_verdict": "divergent",
        "severity": "medium",
        "rationale": "Cited URL is reachable but its head content changed since the last check.",
    },
}

_EVIDENCE_DETAIL: dict[str, str] = {
    "broken": "Origin returned an unreachable status for the cited URL.",
    "moved": "Origin reported a permanent redirect (HTTP 301/308) for the cited URL.",
    "changed": "Head content hash of the cited URL differs from the previously recorded value.",
}

# URL matcher: an http(s) scheme run of non-space, non-delimiter characters.
# http is matched too so the guard can explicitly refuse it (surfacing a
# 'blocked' status) rather than silently dropping non-https citations.
_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]}]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>\"'"

_HEAD_HASH_BYTES_DEFAULT = 4096
_TIMEOUT_DEFAULT = 5.0
_MAX_REDIRECTS_DEFAULT = 3
_MAX_URLS_DEFAULT = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _evidence_connect():
    """RLS-free canvas connection for chunk/section reads (see scanner._evidence_connect)."""
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection()


# ── configuration ─────────────────────────────────────────────────────────────

def _load_full_cfg() -> dict:
    from tools.doc_modernization.pack_loader import load_config
    return load_config() or {}


def link_config() -> dict:
    """The ``link_rot:`` config block, plus the top-level ``offline`` flag folded
    in as ``_offline`` so air-gap detection has a single input."""
    full = _load_full_cfg()
    cfg = dict(full.get("link_rot") or {})
    cfg["_offline"] = bool(full.get("offline"))
    return cfg


def _is_airgap(cfg: dict) -> bool:
    if cfg.get("_offline"):
        return True
    try:  # pragma: no cover - optional runtime shim
        from tools.airgap import is_airgap as _ia
        return bool(_ia())
    except Exception:
        return False


# ── URL extraction ────────────────────────────────────────────────────────────

def extract_urls(text: str) -> list[str]:
    """Deterministically pull citation URLs out of one chunk of text.

    Trailing sentence punctuation and an unbalanced closing paren that the
    greedy match swept up are trimmed. Order-preserving, de-duplicated.
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _URL_RE.finditer(text):
        raw = (m.group(0) or "").rstrip(_TRAILING)
        # A trailing ')' is only part of the URL if it balances an earlier '('.
        while raw.endswith(")") and raw.count(")") > raw.count("("):
            raw = raw[:-1].rstrip(_TRAILING)
        if raw and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


# ── egress guard ──────────────────────────────────────────────────────────────

def _ip_is_denied(ip: ipaddress._BaseAddress) -> bool:
    """True for any address that is not globally routable public space.

    The union below covers loopback, private/unique-local, link-local (incl. the
    instance-metadata address), multicast, unspecified and reserved ranges for
    both IPv4 and IPv6.
    """
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def egress_guard(url: str, cfg: dict, resolver=None) -> tuple[bool, str, list[str]]:
    """Decide whether ``url`` may be contacted. Returns (allowed, reason, ips).

    Enforced before any outbound connection. ``resolver`` defaults to
    ``socket.getaddrinfo`` and is injectable for testing.
    """
    resolver = resolver or socket.getaddrinfo
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return (False, "malformed", [])

    if (parts.scheme or "").lower() != "https":
        return (False, "scheme_not_https", [])
    host = parts.hostname
    if not host:
        return (False, "no_host", [])
    host_l = host.lower()

    # Denylist beats allowlist. Suffix match so "example.com" covers subdomains.
    denylist = [str(h).lower() for h in (cfg.get("denylist") or [])]
    if any(host_l == d or host_l.endswith("." + d) for d in denylist):
        return (False, "denylisted", [])
    allowlist = [str(h).lower() for h in (cfg.get("allowlist") or [])]
    if allowlist and not any(host_l == a or host_l.endswith("." + a) for a in allowlist):
        return (False, "not_allowlisted", [])

    # A literal-IP host skips DNS but is still range-checked.
    try:
        lit = ipaddress.ip_address(host)
    except ValueError:
        lit = None
    if lit is not None:
        if _ip_is_denied(lit):
            return (False, "denied_ip_range", [str(lit)])
        return (True, "ok", [str(lit)])

    # Resolve, then check EVERY answer. One non-public address anywhere in the
    # answer set fails the whole URL — the resolve-then-check step.
    port = parts.port or 443
    try:
        infos = resolver(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Unresolvable: a dead domain and a missing network are indistinguishable
        # here, so we refuse to call it rotted — mapped to 'not_checked' upstream.
        return (False, "unresolved", [])
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("link_check: resolver error for %s: %s", host, exc)
        return (False, "unresolved", [])

    ips: list[str] = []
    for info in infos:
        try:
            ip_str = info[4][0]
        except (IndexError, TypeError):
            continue
        ips.append(ip_str)
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return (False, "denied_ip_range", ips)
        if _ip_is_denied(ip_obj):
            return (False, "denied_ip_range", ips)
    if not ips:
        return (False, "unresolved", [])
    return (True, "ok", ips)


# ── HTTP probe ────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never auto-follow: the caller re-validates each redirect target itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _default_probe(url: str, method: str, timeout: float,
                   head_hash_bytes: int = _HEAD_HASH_BYTES_DEFAULT):
    """One HTTP request, no redirect following, tight timeout.

    Returns (code, headers_lower_dict, body_bytes_or_None). A GET requests only
    the first ``head_hash_bytes`` via Range so a large body is never pulled.
    Non-2xx responses come back as codes (via HTTPError), not exceptions.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", "ICDEV-DocMod-LinkCheck/1.0")
    if method == "GET":
        req.add_header("Range", "bytes=0-%d" % (max(0, head_hash_bytes - 1)))
    try:
        # Safe: the scheme is validated to https by egress_guard before any URL
        # reaches this function, redirects are disabled, and the timeout is short
        # and mandatory. (B310 acknowledged on the call below.)
        with opener.open(req, timeout=timeout) as resp:  # nosec B310
            code = int(getattr(resp, "status", None) or resp.getcode())
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read(head_hash_bytes) if method == "GET" else None
            return (code, headers, body)
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        body = None
        if method == "GET":
            try:
                body = e.read(head_hash_bytes)
            except Exception:
                body = None
        return (int(e.code), headers, body)


def _compute_hash(body) -> str | None:
    if not body:
        return None
    return hashlib.sha256(body).hexdigest()


def check_url(url: str, cfg: dict, previous_hash: str | None = None,
              resolver=None, probe=None) -> dict:
    """Egress-guarded health check for a single URL.

    ``status`` is one of: ok, broken, moved, changed, blocked, not_checked, error.
    ``resolver`` / ``probe`` are injectable so tests never touch the network.
    """
    timeout = float(cfg.get("timeout_seconds", _TIMEOUT_DEFAULT) or _TIMEOUT_DEFAULT)
    max_redirects = int(cfg.get("max_redirects", _MAX_REDIRECTS_DEFAULT) or _MAX_REDIRECTS_DEFAULT)
    head_bytes = int(cfg.get("head_hash_bytes", _HEAD_HASH_BYTES_DEFAULT) or _HEAD_HASH_BYTES_DEFAULT)
    if probe is None:
        probe = functools.partial(_default_probe, head_hash_bytes=head_bytes)
    resolver = resolver or socket.getaddrinfo

    result = {
        "url": url, "status": None, "http_status": None,
        "redirect_target": None, "head_hash": None, "reason": None,
        "checked_at": _now(),
    }

    current = url
    hops = 0
    first_permanent_target: str | None = None

    while True:
        allowed, reason, _ips = egress_guard(current, cfg, resolver=resolver)
        if not allowed:
            if reason == "unresolved":
                result.update(status="not_checked", reason="unresolved")
            else:
                result.update(status="blocked", reason=reason)
            return result

        # HEAD first; fall back to a ranged GET when HEAD is unsupported/blocked.
        try:
            code, headers, body = probe(current, "HEAD", timeout)
        except Exception as exc:
            logger.debug("link_check: HEAD failed for %s: %s", current, exc)
            code, headers, body = None, {}, None
        if code in (None, 403, 405, 501):
            try:
                code, headers, body = probe(current, "GET", timeout)
            except Exception as exc:
                logger.debug("link_check: GET failed for %s: %s", current, exc)
                result.update(status="not_checked", reason="transport_error")
                return result

        result["http_status"] = code
        headers = headers or {}

        if code is not None and 200 <= code < 300:
            head_hash = _compute_hash(body)
            if head_hash is None:
                # HEAD confirmed 2xx but gave no body — one ranged GET for the hash.
                try:
                    _c, _h, gbody = probe(current, "GET", timeout)
                    head_hash = _compute_hash(gbody)
                except Exception:
                    head_hash = None
            result["head_hash"] = head_hash
            if first_permanent_target:
                result.update(status="moved", redirect_target=first_permanent_target,
                              reason="permanent_redirect")
            elif previous_hash and head_hash and head_hash != previous_hash:
                result.update(status="changed", reason="head_hash_drift")
            else:
                result.update(status="ok", reason="reachable")
            return result

        if code in (301, 308):
            target = headers.get("location")
            if not target:
                result.update(status="broken", reason="redirect_no_location")
                return result
            nxt = urllib.parse.urljoin(current, target)
            if first_permanent_target is None:
                first_permanent_target = nxt
            current = nxt
            hops += 1
            if hops > max_redirects:
                result.update(status="moved", redirect_target=first_permanent_target,
                              reason="permanent_redirect_truncated")
                return result
            continue

        if code in (302, 303, 307):
            target = headers.get("location")
            if not target:
                result.update(status="ok", reason="transient_redirect")
                return result
            current = urllib.parse.urljoin(current, target)
            hops += 1
            if hops > max_redirects:
                result.update(status="broken", reason="redirect_loop")
                return result
            continue

        if code in (404, 410):
            result.update(status="broken", reason="http_%s" % code)
            return result

        # 401/403/5xx and anything else: not a confident rot signal.
        result.update(status="error", reason="http_%s" % code)
        return result


# ── persistence ───────────────────────────────────────────────────────────────

def _check_key(doc_id: str, url: str) -> str:
    return hashlib.sha256(f"{doc_id}|{url}".encode("utf-8")).hexdigest()


def dedupe_key(doc_id: str, url: str) -> str:
    """Stable finding dedupe key — mirrors scanner.dedupe_key's contract
    (doc | pack | entity | finding_type) so the drift/card bridges dedup it."""
    raw = f"{doc_id}|{PACK_ID}|{url.lower().strip()}|{FINDING_TYPE}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_link_state_table(conn) -> None:
    try:
        conn.execute(_LINK_STATE_DDL)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("link_check: ensure state table failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _doc_meta(conn, doc_id: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT tenant_id, classification FROM dic_documents WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    if not row:
        return (None, None)
    d = dict(row)
    return (d.get("tenant_id"), d.get("classification"))


def _link_state_get(conn, doc_id: str, url: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT last_head_hash, last_status FROM docmod_link_checks WHERE check_key=%s",
            (_check_key(doc_id, url),),
        ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def _link_state_upsert(conn, doc_id, url, res, keep_hash, tenant_id, classification) -> None:
    key = _check_key(doc_id, url)
    host = urllib.parse.urlsplit(url).hostname
    conn.execute("DELETE FROM docmod_link_checks WHERE check_key=%s", (key,))
    conn.execute(
        """INSERT INTO docmod_link_checks
           (check_key, doc_id, url, url_host, last_status, last_http_status,
            last_head_hash, last_redirect_target, last_checked_at,
            tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (key, doc_id, url, host, res["status"], res["http_status"], keep_hash,
         res["redirect_target"], res["checked_at"], tenant_id, classification),
    )


def _open_link_findings(conn, doc_id: str) -> dict[str, dict]:
    """Latest-state OPEN link_rot findings for a doc, keyed by dedupe_key."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_findings WHERE doc_id=%s AND pack_id=%s AND finding_type=%s "
        "ORDER BY created_at",
        (doc_id, PACK_ID, FINDING_TYPE),
    ).fetchall()]
    latest: dict[str, dict] = {}
    for r in rows:
        if r.get("dedupe_key"):
            latest[r["dedupe_key"]] = r
    return {k: v for k, v in latest.items() if v.get("state") == "open"}


def _insert_link_finding(conn, run_id, doc_id, version_id, url, ref, res, verdict,
                         key, tenant_id, classification, supersedes_id=None) -> str:
    fid = f"fnd-{uuid.uuid4().hex[:12]}"
    evidence = [{
        "source": url,
        "detail": _EVIDENCE_DETAIL.get(res["status"], ""),
        "date": res["checked_at"],
        "http_status": res["http_status"],
        "redirect_target": res["redirect_target"],
    }]
    replacement = res["redirect_target"] if res["status"] == "moved" else None
    repl_ev = None
    if replacement:
        repl_ev = [{
            "source": replacement,
            "detail": "Permanent-redirect target reported by the origin server (Location header).",
            "date": res["checked_at"],
        }]
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, chunk_link_id, section_heading,
            page, pack_id, entity_label, entity_type, finding_type, currency_verdict,
            severity, rationale, evidence_json, recommended_replacement,
            replacement_evidence_json, confidence, state, supersedes_id,
            dedupe_key, created_at, tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            fid, run_id, doc_id, version_id,
            ref.get("chunk_link_id"), ref.get("section"), ref.get("page"),
            PACK_ID, url, ENTITY_TYPE, FINDING_TYPE, verdict["currency_verdict"],
            verdict["severity"], verdict["rationale"],
            json.dumps(evidence, default=str), replacement,
            json.dumps(repl_ev, default=str) if repl_ev else None,
            1.0, "open", supersedes_id, key, _now(), tenant_id, classification,
        ),
    )
    return fid


def _supersede_link_finding(conn, run_id, row, version_id, tenant_id, classification) -> None:
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, pack_id, entity_label,
            entity_type, finding_type, currency_verdict, severity, rationale,
            confidence, state, supersedes_id, dedupe_key, created_at,
            tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'current',%s,%s,%s,'superseded',%s,%s,%s,%s,%s)""",
        (
            f"fnd-{uuid.uuid4().hex[:12]}", run_id, row["doc_id"], version_id,
            PACK_ID, row["entity_label"], row.get("entity_type") or ENTITY_TYPE,
            FINDING_TYPE, row.get("severity") or "low",
            "URL reachable again or removed from the document.",
            row.get("confidence") or 1.0, row["finding_id"], row["dedupe_key"],
            _now(), tenant_id, classification,
        ),
    )


# ── per-document + corpus drivers ─────────────────────────────────────────────

def check_document_links(doc_id: str, conn, ev_conn, cfg: dict, run_id: str,
                         cap: int, resolver=None, probe=None) -> dict:
    """Check every URL in one document (up to ``cap``); emit/transition findings.

    Findings write on ``conn``; chunk/section reads use the RLS-free ``ev_conn``
    so an evidence-read failure can never poison the finding-write transaction.
    """
    from .scanner import _doc_chunks, _latest_approved_version

    version_id = _latest_approved_version(conn, doc_id)
    if not version_id:
        return {"doc_id": doc_id, "scanned": False, "reason": "no approved version"}
    tenant_id, classification = _doc_meta(conn, doc_id)

    # Collect URLs with their first-seen chunk location.
    urls: dict[str, dict] = {}
    for text, chunk_ref in _doc_chunks(ev_conn, doc_id, version_id):
        for u in extract_urls(text):
            urls.setdefault(u, {
                "chunk_link_id": chunk_ref.chunk_link_id,
                "section": chunk_ref.section,
                "page": chunk_ref.page,
            })

    existing = _open_link_findings(conn, doc_id)
    results: dict[str, dict] = {}
    checked = 0
    capped = False
    for url, ref in urls.items():
        if checked >= cap:
            capped = True
            break
        prev = _link_state_get(conn, doc_id, url)
        prev_hash = (prev or {}).get("last_head_hash")
        res = check_url(url, cfg, previous_hash=prev_hash, resolver=resolver, probe=probe)
        results[url] = res
        checked += 1
        if res["status"] != "not_checked":
            keep_hash = res["head_hash"] or prev_hash
            _link_state_upsert(conn, doc_id, url, res, keep_hash, tenant_id, classification)

    # Emit / transition findings for problem URLs.
    seen_keys: set[str] = set()
    new = 0
    for url, res in results.items():
        verdict = _STATUS_VERDICT.get(res["status"])
        if verdict is None:
            continue
        key = dedupe_key(doc_id, url)
        seen_keys.add(key)
        cur = existing.get(key)
        if cur and cur.get("currency_verdict") == verdict["currency_verdict"]:
            continue  # identical finding already open — no duplicate row
        _insert_link_finding(
            conn, run_id, doc_id, version_id, url, urls[url], res, verdict, key,
            tenant_id, classification,
            supersedes_id=cur.get("finding_id") if cur else None,
        )
        new += 1

    # Resolve open findings whose URL is now healthy or gone — but only when we
    # actually re-checked it this sweep (never resolve on a cap-skip).
    resolved = 0
    for key, row in existing.items():
        if key in seen_keys:
            continue
        url = row.get("entity_label")
        removed = url not in urls
        healthy = url in results and results[url]["status"] == "ok"
        if removed or healthy:
            _supersede_link_finding(conn, run_id, row, version_id, tenant_id, classification)
            resolved += 1

    return {
        "doc_id": doc_id, "scanned": True, "urls_found": len(urls),
        "urls_checked": checked, "findings_new": new,
        "findings_resolved": resolved, "capped": capped,
    }


def check_corpus_links(collection_id: str | None = None, conn=None,
                       trigger: str = "manual", cap: int | None = None,
                       resolver=None, probe=None) -> dict:
    """Check cited URLs across a collection (or the whole corpus when None).

    Self-skips when the feature is disabled or when there is no egress (air-gap).
    """
    cfg = link_config()
    if not cfg.get("enabled", False):
        return {"enabled": False, "reason": "link_rot disabled in docmod_config.yaml"}

    own = conn is None
    if own:
        conn = _connect()
    try:
        _ensure_link_state_table(conn)
        if _is_airgap(cfg):
            conn.commit()
            return {"enabled": True, "skipped": True,
                    "status": "not checked (no egress)"}

        cap = cap if cap is not None else int(cfg.get("max_urls_per_sweep", _MAX_URLS_DEFAULT) or _MAX_URLS_DEFAULT)
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        scope_type = "collection" if collection_id else "all"
        conn.execute(
            """INSERT INTO docmod_scan_runs
               (run_id, scope_type, scope_id, pack_ids, triggered_by, started_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (run_id, scope_type, collection_id, json.dumps([PACK_ID]), trigger, _now()),
        )

        if collection_id:
            rows = conn.execute(
                "SELECT doc_id FROM dic_documents WHERE collection_id=%s", (collection_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT doc_id FROM dic_documents").fetchall()
        doc_ids = [dict(r)["doc_id"] for r in rows]

        ev_conn = _evidence_connect()
        docs_scanned = new = resolved = urls_checked = 0
        remaining = cap
        try:
            for doc_id in doc_ids:
                if remaining <= 0:
                    break
                r = check_document_links(doc_id, conn, ev_conn, cfg, run_id,
                                         remaining, resolver=resolver, probe=probe)
                if not r.get("scanned"):
                    continue
                docs_scanned += 1
                new += r.get("findings_new", 0)
                resolved += r.get("findings_resolved", 0)
                uc = r.get("urls_checked", 0)
                urls_checked += uc
                remaining -= uc
        finally:
            try:
                ev_conn.close()
            except Exception:
                pass

        conn.execute(
            "UPDATE docmod_scan_runs SET docs_scanned=%s, findings_new=%s, "
            "findings_resolved=%s, finished_at=%s WHERE run_id=%s",
            (docs_scanned, new, resolved, _now(), run_id),
        )
        conn.commit()
        return {
            "enabled": True, "run_id": run_id, "docs_scanned": docs_scanned,
            "urls_checked": urls_checked, "findings_new": new,
            "findings_resolved": resolved, "cap": cap,
        }
    finally:
        if own:
            conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Egress-safe URL link-rot check for DIC documents (docmod)."
    )
    ap.add_argument("--collection", default=None, help="restrict to one collection id")
    ap.add_argument("--cap", type=int, default=None, help="override max URLs this run")
    ap.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    out = check_corpus_links(collection_id=args.collection, trigger="manual", cap=args.cap)
    if args.json_out:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
