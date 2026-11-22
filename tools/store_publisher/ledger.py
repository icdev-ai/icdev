# CUI // SP-CTI
"""The append-only, hash-chained ledger of store packages (icx-ent-04).

One table, ``sp_events`` (migration 20261122112221), one hash chain per package (stream ``package:<id>``): ``prepared``
(a person built the package; the publisher's key signed its card; the whole package is recorded at its content digest
with the build record), then ``published`` or ``refused`` (a person who did not prepare it took the declared step
icos-store-publish, bound to that digest). Each row carries the hash of its stream's previous row;
``UNIQUE(stream, prev_hash)`` stops a fork, and SQLite/PostgreSQL triggers refuse UPDATE and DELETE. The chain walk is
the shared ``tools.common.stream_chain.order_streams``.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from tools.common import stream_chain
from tools.idp.dora import canonical, digest
from tools.store_publisher.constants import ACTOR_KINDS, EVENTS, GENESIS, TABLE

COLS = ("event_id", "stream", "event", "package_id", "org", "actor", "actor_kind", "actor_role", "step_id", "reason",
        "content_digest", "payload_json", "prev_hash", "row_hash", "created_at")
_HASHED = tuple(c for c in COLS if c != "row_hash")
EVENT_ID = r"spe-[0-9a-f]{16}"


class Refused(Exception):
    """The request is refused. ``status`` is the HTTP status a route returns."""

    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "refused": True}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def connection():
    """The platform database without the row-security predicate: visibility is decided per package by its org and
    marking in ``service.py`` before anything is shown."""
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _row_hash(row: dict) -> str:
    return digest({k: row.get(k, "") for k in _HASHED})


def payload(row: dict) -> dict:
    try:
        out = json.loads(row.get("payload_json") or "{}")
    except (ValueError, RecursionError):
        return {}
    return out if isinstance(out, dict) else {}


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
    """Each stream's rows in chain order, plus ``_problems``. A prepared row whose recorded package is not its digest
    is a problem, never walked through."""
    from tools.store_publisher.package import content_digest

    def check(r: dict) -> str | None:
        if r["event"] == "prepared":
            pkg = payload(r).get("package")
            if not isinstance(pkg, dict) or pkg.get("content_digest") != r["content_digest"] \
                    or content_digest(pkg) != r["content_digest"]:
                return "the recorded package does not match its digest"
        return None

    out = stream_chain.order_streams(rows, key=lambda r: r["stream"], row_hash=_row_hash,
                                     stored=lambda r: r["row_hash"], prev=lambda r: r["prev_hash"],
                                     ident=lambda r: r["event_id"], check=check, genesis=GENESIS)
    out["_problems"] = [{"event_id": p["id"], "stream": p["stream"], "problem": p["problem"]}
                        for p in out["_problems"]]
    return out


def head(ordered: list[dict]) -> str:
    return ordered[-1]["row_hash"] if ordered else GENESIS


def append(conn, *, stream: str, event: str, package_id: str, org: str, actor: str, actor_kind: str,
           prev_hash: str, content_digest: str = "", body: dict | None = None, actor_role: str = "",
           step_id: str = "", reason: str = "") -> dict:
    if event not in EVENTS or actor_kind not in ACTOR_KINDS:
        raise ValueError(f"bad event {event!r} or actor kind {actor_kind!r}")
    row = {"event_id": f"spe-{uuid.uuid4().hex[:16]}", "stream": stream, "event": event, "package_id": package_id,
           "org": org, "actor": actor, "actor_kind": actor_kind, "actor_role": actor_role or "",
           "step_id": step_id or "", "reason": reason or "", "content_digest": content_digest,
           "payload_json": canonical(body or {}), "prev_hash": prev_hash, "created_at": now()}
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
            raise Refused(409, "concurrent", "this package changed while you were deciding: reload it") from exc
        raise Refused(503, "store_unavailable",
                      f"the store publisher ledger could not be written, so nothing changed ({type(exc).__name__})") from exc
    return row


__all__ = ["COLS", "EVENT_ID", "Refused", "append", "chain", "head", "payload", "read"]
