# CUI // SP-CTI
"""Per-user profile memory for the SAG runtime (sag-mem-01).

Rather than build a parallel memory store, this layer adds a small, durable
*profile* on top of ICDEV's existing memory:

- The **``sag_user_profiles``** table (migration 287) holds two JSON blobs per
  ``(user_id, tenant_id)``: ``preferences`` (operator settings) and ``facts``
  (durable statements, each ``{text, confidence, source, updated_at}``).
- **Session-start injection** — :func:`build_profile_context` renders the profile
  facts *plus* the top hits from the existing hybrid memory index
  (``tools.memory.hybrid_search``) into a compact preamble the runtime prepends to
  the system prompt. The agent loop already injects hybrid-memory context on its
  own; this adds the durable profile facts on top.
- **Auto-capture** — :func:`consolidate_session_facts` extracts durable facts from
  a session transcript (dedup + confidence), for a post-session consolidation job.
- **``/memory``** — :func:`list_facts` / :func:`forget_fact` back the slash command.

DB access uses ``tools.db.storage.get_connection`` (RLS-aware; the table carries
``tenant_id`` + ``classification``). Every function degrades gracefully — a
missing table or unavailable DB yields empty results rather than raising, so the
runtime stays usable without the memory subsystem.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.profile_memory")

_TABLE = "sag_user_profiles"
_DEFAULT_USER = "default"
_MIN_CONFIDENCE = 0.5
_MAX_FACTS = 200

_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    user_id          TEXT NOT NULL,
    tenant_id        TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    preferences_json TEXT DEFAULT '{{}}',
    facts_json       TEXT DEFAULT '[]',
    profile          TEXT DEFAULT '',
    updated_at       TEXT,
    PRIMARY KEY (user_id, tenant_id)
)
"""

# Marker the SAG runtime uses to namespace a profile's tenant (see
# tools/agent_runtime/profiles.py::scoped_tenant). We derive the profile-tag
# column from it on write so no public API in this module changes (sag-prof-01).
_PROFILE_SEP = "::prof:"


def _profile_from_tenant(tenant_id: str) -> str:
    """Extract the profile name a namespaced tenant encodes ('' for default)."""
    if tenant_id and _PROFILE_SEP in tenant_id:
        return tenant_id.split(_PROFILE_SEP, 1)[1]
    return ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(conn) -> None:
    try:
        conn.execute(_DDL)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("profile_memory: ensure_schema failed: %s", exc)


def _norm(text: str) -> str:
    """Normalised key for dedup: lowercased, whitespace-collapsed."""
    return " ".join((text or "").lower().split())


# ---------------------------------------------------------------------------
# Load / persist
# ---------------------------------------------------------------------------
def load_profile(
    user_id: str = _DEFAULT_USER, tenant_id: str = ""
) -> dict[str, Any]:
    """Return ``{preferences: dict, facts: list}`` for a user (empty if none)."""
    empty = {"preferences": {}, "facts": []}
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        _ensure_schema(conn)
        cur = conn.execute(
            f"SELECT preferences_json, facts_json FROM {_TABLE} "
            f"WHERE user_id = %s AND tenant_id = %s",
            (user_id, tenant_id),
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.debug("profile_memory: load failed: %s", exc)
        return empty
    if not row:
        return empty
    try:
        prefs = json.loads(row[0]) if row[0] else {}
        facts = json.loads(row[1]) if row[1] else []
    except Exception:  # noqa: BLE001
        return empty
    return {"preferences": prefs or {}, "facts": facts or []}


def _save_profile(
    user_id: str, tenant_id: str, preferences: dict, facts: list, classification: str = "CUI"
) -> bool:
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        _ensure_schema(conn)
        now = _utcnow()
        pj = json.dumps(preferences or {})
        fj = json.dumps(facts or [])
        prof = _profile_from_tenant(tenant_id)
        # upsert (portable: check-then-write, mirrors notifications/preferences.py)
        cur = conn.execute(
            f"SELECT 1 FROM {_TABLE} WHERE user_id = %s AND tenant_id = %s",
            (user_id, tenant_id),
        )
        if cur.fetchone():
            conn.execute(
                f"UPDATE {_TABLE} SET preferences_json = %s, facts_json = %s, "
                f"profile = %s, updated_at = %s WHERE user_id = %s AND tenant_id = %s",
                (pj, fj, prof, now, user_id, tenant_id),
            )
        else:
            conn.execute(
                f"INSERT INTO {_TABLE} "
                f"(user_id, tenant_id, classification, preferences_json, facts_json, profile, updated_at) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (user_id, tenant_id, classification, pj, fj, prof, now),
            )
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile_memory: save failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
def list_facts(user_id: str = _DEFAULT_USER, tenant_id: str = "") -> list[dict[str, Any]]:
    """Return the durable facts for a user, highest confidence first."""
    facts = load_profile(user_id, tenant_id).get("facts", [])
    return sorted(facts, key=lambda f: f.get("confidence", 0), reverse=True)


def remember_facts(
    new_facts: list[dict[str, Any]],
    user_id: str = _DEFAULT_USER,
    tenant_id: str = "",
) -> int:
    """Merge ``new_facts`` into the profile with dedup by normalised text.

    Each fact is ``{text, confidence?, source?}``. On a duplicate the higher
    confidence and newest ``updated_at`` win. Returns the number of facts added
    or updated.
    """
    profile = load_profile(user_id, tenant_id)
    existing = {_norm(f.get("text", "")): f for f in profile.get("facts", [])}
    changed = 0
    for nf in new_facts:
        text = str(nf.get("text", "")).strip()
        if not text:
            continue
        conf = float(nf.get("confidence", 0.7) or 0.7)
        if conf < _MIN_CONFIDENCE:
            continue
        key = _norm(text)
        prior = existing.get(key)
        if prior is None:
            existing[key] = {
                "text": text, "confidence": conf,
                "source": nf.get("source", "manual"), "updated_at": _utcnow(),
            }
            changed += 1
        elif conf > prior.get("confidence", 0):
            prior.update({"confidence": conf, "source": nf.get("source", prior.get("source")),
                          "updated_at": _utcnow()})
            changed += 1
    if not changed:
        return 0
    facts = list(existing.values())[:_MAX_FACTS]
    _save_profile(user_id, tenant_id, profile.get("preferences", {}), facts)
    return changed


def forget_fact(
    text_or_index: str, user_id: str = _DEFAULT_USER, tenant_id: str = ""
) -> Optional[str]:
    """Forget a fact by 1-based index (as shown by ``/memory``) or by text match.

    Returns the removed fact's text, or None if nothing matched.
    """
    profile = load_profile(user_id, tenant_id)
    facts = sorted(profile.get("facts", []), key=lambda f: f.get("confidence", 0), reverse=True)
    if not facts:
        return None
    target_idx = None
    s = str(text_or_index).strip()
    if s.isdigit():
        idx = int(s) - 1
        if 0 <= idx < len(facts):
            target_idx = idx
    else:
        key = _norm(s)
        for i, f in enumerate(facts):
            if _norm(f.get("text", "")) == key or key in _norm(f.get("text", "")):
                target_idx = i
                break
    if target_idx is None:
        return None
    removed = facts.pop(target_idx)
    _save_profile(user_id, tenant_id, profile.get("preferences", {}), facts)
    return removed.get("text")


def set_preference(
    key: str, value: Any, user_id: str = _DEFAULT_USER, tenant_id: str = ""
) -> bool:
    """Set a single durable preference."""
    profile = load_profile(user_id, tenant_id)
    prefs = profile.get("preferences", {})
    prefs[key] = value
    return _save_profile(user_id, tenant_id, prefs, profile.get("facts", []))


# ---------------------------------------------------------------------------
# Session-start injection
# ---------------------------------------------------------------------------
def build_profile_context(
    user_id: str = _DEFAULT_USER,
    tenant_id: str = "",
    *,
    query: str = "",
    memory_hits: int = 5,
) -> str:
    """Render a compact preamble of profile facts + top hybrid-memory hits.

    Returns an empty string when there is nothing to inject (so the caller can
    skip prepending). The agent loop already injects hybrid memory for the
    current prompt; this adds durable profile facts and, when ``query`` is given,
    a few extra memory hits keyed to it.
    """
    lines: list[str] = []
    profile = load_profile(user_id, tenant_id)

    prefs = profile.get("preferences") or {}
    if prefs:
        pretty = ", ".join(f"{k}={v}" for k, v in prefs.items())
        lines.append(f"User preferences: {pretty}")

    facts = sorted(profile.get("facts", []), key=lambda f: f.get("confidence", 0), reverse=True)
    if facts:
        lines.append("Known facts about the user:")
        for f in facts[:10]:
            lines.append(f"  - {f.get('text', '')}")

    if query:
        try:
            from tools.memory.hybrid_search import search as _mem_search

            hits = _mem_search(query, limit=memory_hits, user_id=user_id, tenant_id=tenant_id or None)
            if hits:
                lines.append("Relevant remembered context:")
                for h in hits:
                    content = str(h.get("content", "")).strip().replace("\n", " ")
                    if content:
                        lines.append(f"  - {content[:200]}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("profile_memory: memory search failed: %s", exc)

    if not lines:
        return ""
    return "## Operator profile & memory\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Auto-capture: post-session fact consolidation
# ---------------------------------------------------------------------------
# Cheap deterministic markers of a durable user statement. The consolidation job
# may optionally call an LLM, but the deterministic extractor keeps it usable
# air-gapped and testable.
_FACT_MARKERS = (
    "i prefer", "i like", "i always", "i never", "i work", "i use", "my name is",
    "my team", "my role", "call me", "remember that", "note that", "i want",
    "please always", "please never", "from now on",
)


def extract_facts_from_text(text: str, *, source: str = "session") -> list[dict[str, Any]]:
    """Deterministically extract candidate durable facts from a user utterance."""
    out: list[dict[str, Any]] = []
    for raw in _split_sentences(text):
        low = raw.lower().strip()
        if not low:
            continue
        for marker in _FACT_MARKERS:
            if marker in low:
                # confidence: explicit imperatives ("always/never/remember") rank higher
                conf = 0.85 if any(k in low for k in ("always", "never", "remember", "from now on")) else 0.7
                out.append({"text": raw.strip()[:300], "confidence": conf, "source": source})
                break
    return out


def _split_sentences(text: str) -> list[str]:
    import re

    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def consolidate_session_facts(
    transcript: list[dict[str, Any]],
    user_id: str = _DEFAULT_USER,
    tenant_id: str = "",
) -> dict[str, Any]:
    """Extract durable facts from a session transcript and persist them (deduped).

    ``transcript`` is a list of ``{role, content}`` messages. Only ``user`` turns
    are mined. Returns ``{extracted, remembered}`` counts.
    """
    candidates: list[dict[str, Any]] = []
    for msg in transcript or []:
        if msg.get("role") != "user":
            continue
        candidates.extend(extract_facts_from_text(str(msg.get("content", "")), source="session"))
    if not candidates:
        return {"extracted": 0, "remembered": 0}
    remembered = remember_facts(candidates, user_id=user_id, tenant_id=tenant_id)
    return {"extracted": len(candidates), "remembered": remembered}
