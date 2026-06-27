# CUI // SP-CTI
"""ACE Event Bus - lightweight in-process pub/sub for per-instance agent events.

One bus per process.  Callers subscribe to an instance_id and receive events
via a queue.Queue; events are dicts with at minimum {"type": str, ...}.

Public API
----------
publish(instance_id, event)         - broadcast to all subscribers for the instance
subscribe(instance_id, maxsize)     - returns a Queue; call unsubscribe when done
unsubscribe(instance_id, q)         - remove the queue from the subscriber list
"""
from __future__ import annotations

import queue
import threading
from typing import Any

_lock = threading.Lock()
_subscribers: dict[str, list] = {}

_DEFAULT_MAXSIZE = 256


def publish(instance_id: str, event: dict) -> None:
    with _lock:
        qs = list(_subscribers.get(instance_id, []))
    for q in qs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def subscribe(instance_id: str, maxsize: int = _DEFAULT_MAXSIZE) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=maxsize)
    with _lock:
        _subscribers.setdefault(instance_id, []).append(q)
    return q


def unsubscribe(instance_id: str, q: queue.Queue) -> None:
    with _lock:
        lst = _subscribers.get(instance_id)
        if lst is not None:
            try:
                lst.remove(q)
            except ValueError:
                pass
            if not lst:
                _subscribers.pop(instance_id, None)


def drain(instance_id: str, q: queue.Queue, timeout: float = 25.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def subscriber_count(instance_id: str) -> int:
    with _lock:
        return len(_subscribers.get(instance_id, []))
