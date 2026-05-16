# [TEMPLATE: CUI // SP-CTI]
"""Proxy: include tools/llm/response_cache_test.py in the main pytest suite.

The canonical unit tests live alongside the implementation at
tools/llm/response_cache_test.py so they can be run standalone.
This proxy re-exports them so `pytest tests/` picks them up too.
"""

from tools.llm.response_cache_test import (  # noqa: F401
    DummyRequest,
    cache,
    test_canonical_key_determinism,
    test_canonical_key_order_independence,
    test_cache_hit_miss,
    test_cache_ttl_expiry,
    test_excluded_functions_not_cached,
    test_cache_jitter,
    test_lru_eviction,
    test_invalidate_by_function,
    test_stats,
    test_response_to_row_roundtrip,
    test_row_to_response_dict_fallback,
)
