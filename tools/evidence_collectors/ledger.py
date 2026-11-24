# CUI // SP-CTI
"""The append-only, hash-chained ledger of objective-evidence reports (icx-ent-02).

One table, ``ec_events`` (migration 20261119040000), one hash chain per system (stream ``system:<id>``):
``prepared`` (someone, or an agent, collected and recorded the system's report at its content digest, with the
observations it rests on), then ``published`` or ``refused`` (a person who did not prepare it took the declared step
decl-wft-ec-evidence-publish, bound to that digest). Each row carries the hash of its stream's previous row;
``UNIQUE(stream, prev_hash)`` stops a fork, and SQLite/PostgreSQL triggers refuse UPDATE and DELETE. The chain walk
is the shared ``tools.common.stream_chain.order_streams`` (no copy of it here); the row shape is the conformance and
vulnerability-research ledgers'.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from tools.common import stream_chain
from tools.evidence_collectors.constants import ACTOR_KINDS, EVENTS, GENESIS, TABLE
from tools.idp.dora import canonical, digest

COLS = ("event_id", "stream", "event", "system_id", "actor", "actor_kind", "actor_role", "step_id", "reason",
        "content_digest", "payload_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in COLS if c != "row_hash")


class LedgerRefused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def connection():
    """The platform database without the row-security predicate: visibility is decided per system by its org and
    marking in ``service.py`` before anything is shown."""
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


def payload(row: dict) -> dict:
    try:
        return json.loads(row.get("payload_json") or "{}")
    except ValueError:
        return {}


def read(conn=None, *, stream: str | None = None) -> list[dict]:
    own = conn is None
    conn = conn or connection()
    try:
        sql = f"SELECT {', '.join(COLS)} FROM {TABLE}"  # nosec B608 — fixed table and columns
        params: tuple = ()
        if stream:
            sql, params = sql + " WHERE stream = %s", (stream,)
        rows = conn.execute(sql + " ORDER BY created_at, event_id", params).fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(COLS, tuple(r))) for r in rows]


def chain(rows: Iterable[dict]) -> dict[str, Any]:
    """Each stream's rows in chain order, plus ``_problems``. A prepared row whose recorded report is not its digest
    is a problem (and never walked through)."""
    from tools.evidence_collectors.bundle import content_digest

    def check(r: dict) -> str | None:
        if r["event"] == "prepared":
            rep = payload(r).get("content")
            if not isinstance(rep, dict) or content_digest(rep) != r["content_digest"] \
                    or rep.get("content_digest") != r["content_digest"]:
                return "the recorded report does not match its digest"
        return None

    out = stream_chain.order_streams(rows, key=lambda r: r["stream"], row_hash=_row_hash,
                                     stored=lambda r: r["row_hash"], prev=lambda r: r["prev_hash"],
                                     ident=lambda r: r["event_id"], check=check, genesis=GENESIS)
    out["_problems"] = [{"event_id": p["id"], "stream": p["stream"], "problem": p["problem"]}
                        for p in out["_problems"]]
    return out


def stream_rows(stream: str, conn) -> tuple[list[dict], list[dict]]:
    ch = chain(read(conn, stream=stream))
    return ch.get(stream, []), ch["_problems"]


def head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def append(conn, *, stream: str, event: str, system_id: str, actor: str, actor_kind: str, prev_hash: str,
           content_digest: str = "", content: Any = None, detail: dict | None = None, actor_role: str = "",
           step_id: str = "", reason: str = "") -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    body: dict = {"detail": detail or {}}
    if content is not None:
        body["content"] = content
    row = {"event_id": f"ece-{uuid.uuid4().hex[:16]}", "stream": stream, "event": event, "system_id": system_id,
           "actor": actor, "actor_kind": actor_kind, "actor_role": actor_role or "", "step_id": step_id or "",
           "reason": reason or "", "content_digest": content_digest, "payload_json": canonical(body),
           "prev_hash": prev_hash, "created_at": now()}
    row["row_hash"] = _row_hash(row)
    try:
        conn.execute(f"INSERT INTO {TABLE} ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})",
                     tuple(row[c] for c in COLS))
        conn.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            conn.rollback()     # PostgreSQL: the failed statement aborted the transaction
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            raise LedgerRefused(409, "concurrent", "this system's evidence changed while you were deciding: reload "
                                                   "it") from exc
        raise LedgerRefused(503, "store_unavailable",
                            f"the evidence ledger could not be written, so nothing changed ({type(exc).__name__})") from exc
    return row
