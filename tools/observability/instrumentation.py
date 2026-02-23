#!/usr/bin/env python3
# CUI // SP-CTI
"""Auto-instrumentation decorator for ICDEV observability (D280).

Provides @traced() decorator that wraps functions in trace spans automatically.
Captures function name, args hash, result status, duration, and errors.

Usage:
    from tools.observability.instrumentation import traced

    @traced(name="my.operation", kind="CLIENT")
    def my_function(arg1, arg2):
        return result

    # With attribute extraction
    @traced(name="mcp.tool_call", attributes={"mcp.server.name": "builder"})
    def handle_tool_call(tool_name, arguments):
        ...

    # Auto-names from function: "module.function_name"
    @traced()
    def process_data():
        ...
"""

import functools
import hashlib
import json
import logging
from typing import Any, Callable, Dict, Optional

from tools.observability.tracer import Span, set_content_tag

logger = logging.getLogger("icdev.observability.instrumentation")


def traced(
    name: Optional[str] = None,
    kind: str = "INTERNAL",
    attributes: Optional[Dict[str, Any]] = None,
    record_args: bool = False,
    record_result: bool = False,
) -> Callable:
    """Decorator that wraps a function call in a trace span.

    Args:
        name: Span name. Defaults to "module.function_name".
        kind: Span kind (INTERNAL, CLIENT, SERVER, PRODUCER, CONSUMER).
        attributes: Static attributes to set on every span.
        record_args: If True, hash function args into span attributes.
        record_result: If True, hash function result into span attributes.

    Returns:
        Decorated function.
    """
    def decorator(func: Callable) -> Callable:
        span_name = name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Import here to avoid circular imports
            from tools.observability import get_tracer

            tracer = get_tracer()
            span_attrs = dict(attributes) if attributes else {}
            span_attrs["code.function"] = func.__qualname__
            span_attrs["code.module"] = func.__module__

            if record_args:
                try:
                    args_str = json.dumps({"args": str(args), "kwargs": str(kwargs)}, default=str)
                    span_attrs["code.args_hash"] = hashlib.sha256(
                        args_str.encode("utf-8")
                    ).hexdigest()[:16]
                except Exception:
                    pass

            with tracer.start_span(span_name, kind=kind, attributes=span_attrs) as span:
                try:
                    result = func(*args, **kwargs)
                    if record_result and result is not None:
                        try:
                            result_str = json.dumps(result, default=str)
                            span.set_attribute(
                                "code.result_hash",
                                hashlib.sha256(result_str.encode("utf-8")).hexdigest()[:16],
                            )
                        except Exception:
                            pass
                    return result
                except Exception as e:
                    span.set_status("ERROR", str(e))
                    span.add_event("exception", {
                        "exception.type": type(e).__name__,
                        "exception.message": str(e),
                    })
                    raise

        return wrapper
    return decorator


def traced_generator(
    name: Optional[str] = None,
    kind: str = "INTERNAL",
    attributes: Optional[Dict[str, Any]] = None,
) -> Callable:
    """Decorator for generator/streaming functions.

    Creates a span that covers the entire generator lifecycle,
    from first next() to StopIteration.
    """
    def decorator(func: Callable) -> Callable:
        span_name = name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from tools.observability import get_tracer

            tracer = get_tracer()
            span_attrs = dict(attributes) if attributes else {}
            span_attrs["code.function"] = func.__qualname__

            span = tracer.start_span(span_name, kind=kind, attributes=span_attrs)
            try:
                gen = func(*args, **kwargs)
                item_count = 0
                for item in gen:
                    item_count += 1
                    yield item
                span.set_attribute("gen.item_count", item_count)
                span.set_status("OK")
            except Exception as e:
                span.set_status("ERROR", str(e))
                raise
            finally:
                span.end()

        return wrapper
    return decorator
