# CUI // SP-CTI
"""One per-stream hash chain reader for ICDEV's append-only, hash-chained ledgers (icx-ent-01).

Several ledgers keep the same shape: each row carries the hash of the row before it IN ITS STREAM (a repo, an
ADR, a standard, a golden path, a vulnerability-research stream), the first row of a stream points at GENESIS,
and ``UNIQUE(stream, prev_hash)`` stops a fork. Each had its own copy of the reader
(``tools/idp/conformance.py``, ``golden_paths.py``, ``adrs.py``, ``standards.py``); this is the one shared form.
``tools/vulnresearch/ledger.py`` uses it; the CoE ledgers move onto it in the harmonisation pass (osx-ent-h1).

:func:`order_streams` is pure: given the rows and how to read them, it returns each stream's rows in chain order
from GENESIS and ``_problems``: a row whose stored hash is not its content's hash, a row whose content check
fails, and a row that is not on its stream's chain. A row that does not verify is never walked through.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

GENESIS = "0" * 64

__all__ = ["GENESIS", "order_streams"]


def order_streams(rows: Iterable[dict], *, key: Callable[[dict], str], row_hash: Callable[[dict], str],
                  stored: Callable[[dict], str], prev: Callable[[dict], str], ident: Callable[[dict], Any],
                  check: Optional[Callable[[dict], Optional[str]]] = None,
                  genesis: str = GENESIS) -> dict[str, Any]:
    """``{stream: [rows in chain order], "_problems": [{id, stream, problem}]}``."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(key(r)), []).append(r)
    out: dict[str, Any] = {"_problems": []}
    for stream, items in sorted(by.items()):
        good = []
        for r in items:
            if row_hash(r) != stored(r):
                out["_problems"].append({"id": ident(r), "stream": stream,
                                         "problem": "row hash does not match its content"})
                continue
            why = check(r) if check else None
            if why:
                out["_problems"].append({"id": ident(r), "stream": stream, "problem": why})
                continue
            good.append(r)
        nxt: dict[str, dict] = {}
        for r in good:
            nxt.setdefault(str(prev(r)), r)
        ordered, at = [], genesis
        while at in nxt:
            r = nxt.pop(at)
            ordered.append(r)
            at = str(stored(r))
        walked = {id(r) for r in ordered}
        for r in good:
            if id(r) not in walked:
                out["_problems"].append({"id": ident(r), "stream": stream,
                                         "problem": "not on the stream's hash chain"})
        out[stream] = ordered
    return out
