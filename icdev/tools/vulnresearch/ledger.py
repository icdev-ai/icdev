# CUI // SP-CTI
"""The append-only, hash-chained ledger of AI-assisted vulnerability discovery (icx-ent-01).

One table, ``vr_events`` (migration 20261118180000), one hash chain per STREAM:

* ``target:<id>``   registered, then a person authorises or refuses it, and a person may revoke it (a stop);
* ``run:<id>``      one discovery run's whole result, at its digest (``run_recorded``), or one measurement on the
                    labelled benchmark (``measured``);
* ``patch:<fid>``   a patch proposal for one finding, then a person accepts or rejects it;
* ``bundle:<id>``   a target's ``icos-findings/v1`` bundle prepared, then a person publishes or refuses it.

Each row carries the hash of its stream's previous row; ``UNIQUE(stream, prev_hash)`` stops a stream forking, and
SQLite/PostgreSQL triggers refuse UPDATE and DELETE. Nothing here decides anything: the modules that call
:func:`append` take the declared steps first. The shape is the conformance and Academy ledgers'
(``tools/idp/conformance.py``), one table instead of one per kind.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from tools.idp.dora import canonical, digest
from tools.vulnresearch.constants import ACTOR_KINDS, EVENTS, GENESIS, TABLE

COLS = ("event_id", "stream", "event", "subject", "actor", "actor_kind", "actor_role", "step_id", "reason",
        "content_digest", "payload_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in COLS if c != "row_hash")

__all__ = ["COLS", "LedgerRefused", "append", "chain", "connection", "head", "read", "stream_rows", "payload",
           "canonical", "digest", "actor_kind", "now"]


class LedgerRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def connection():
    """The platform database without the row-security predicate (the ledger is the platform's own security
    research record, as coe_conformance_events is)."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def actor_kind(actor: dict) -> str:
    aid = str(actor.get("id") or "")
    if aid.startswith("bootstrap:"):
        return "bootstrap"
    if actor.get("role") == "service" or aid.startswith("agent:"):
        return "agent"
    return "person"


def read(conn=None, *, stream: Optional[str] = None, prefix: Optional[str] = None) -> list[dict]:
    own = conn is None
    conn = conn or connection()
    try:
        sql = f"SELECT {', '.join(COLS)} FROM {TABLE}"  # nosec B608 — fixed table and columns
        params: tuple = ()
        if stream:
            sql, params = sql + " WHERE stream = %s", (stream,)
        elif prefix:
            sql, params = sql + " WHERE stream LIKE %s", (prefix + "%",)
        rows = conn.execute(sql + " ORDER BY created_at, event_id", params).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Each stream's rows in hash-chain order, plus ``_problems`` (a row that does not verify or is off its chain)."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["stream"]), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for stream, items in sorted(by.items()):
        nxt = {str(r["prev_hash"]): r for r in items}
        ordered, prev = [], GENESIS
        while prev in nxt:
            r = nxt.pop(prev)
            ordered.append(r)
            prev = str(r["row_hash"])
        for r in items:
            if _row_hash(r) != r["row_hash"]:
                out["_problems"].append({"event_id": r["event_id"], "stream": stream,
                                         "problem": "row hash does not match its content"})
            elif r["content_digest"] and r["event"] in ("run_recorded", "measured", "prepared", "patch_proposed"):
                if content_digest_of(r["event"], payload(r).get("content")) != r["content_digest"]:
                    out["_problems"].append({"event_id": r["event_id"], "stream": stream,
                                             "problem": "the recorded content does not match its digest"})
        for r in nxt.values():
            out["_problems"].append({"event_id": r["event_id"], "stream": stream,
                                     "problem": "not on the stream's hash chain"})
        out[stream] = ordered
    return out


def content_digest_of(event: str, content: Any) -> str:
    """The digest a row records for its content. A prepared bundle records ITS OWN ``content_digest`` (over the
    bundle without ``content_digest`` and ``produced_at``), so the person's publication binds what ICDEV[OS]
    recomputes; every other content is digested whole."""
    if event == "prepared" and isinstance(content, dict):
        return digest({k: v for k, v in content.items() if k not in ("content_digest", "produced_at")})
    return digest(content)


def stream_rows(stream: str, conn) -> tuple[list[dict], list[dict]]:
    """``(ordered rows, problems)`` of one stream."""
    ch = chain(read(conn, stream=stream))
    return ch.get(stream, []), ch["_problems"]


def head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def payload(row: dict) -> dict:
    try:
        return json.loads(row.get("payload_json") or "{}")
    except ValueError:
        return {}


def append(conn, *, stream: str, event: str, subject: str, actor: str, actor_kind: str, prev_hash: str,
           content: Any = None, detail: Optional[dict] = None, actor_role: str = "", step_id: str = "",
           reason: str = "") -> dict:
    """Append one row. ``content`` (when given) is recorded whole and digested; ``detail`` rides beside it."""
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    body: dict = {"detail": detail or {}}
    if content is not None:
        body["content"] = content
    row = {"event_id": f"vre-{uuid.uuid4().hex[:16]}", "stream": stream, "event": event, "subject": subject,
           "actor": actor, "actor_kind": actor_kind, "actor_role": actor_role or "", "step_id": step_id or "",
           "reason": reason or "", "content_digest": content_digest_of(event, content) if content is not None else "",
           "payload_json": canonical(body), "prev_hash": prev_hash, "created_at": now()}
    row["row_hash"] = _row_hash(row)
    try:
        conn.execute(f"INSERT INTO {TABLE} ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})",
                     tuple(row[c] for c in COLS))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        except Exception:  # noqa: BLE001
            pass
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise LedgerRefused(409, "concurrent", f"{stream} changed while you were deciding: reload it") from exc
        raise LedgerRefused(503, "store_unavailable",
                            f"the vulnerability research ledger could not be written, so nothing changed ({exc})") from exc
    return row
