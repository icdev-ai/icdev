# CUI // SP-CTI
"""The two Home monitor cards must not state things that are not true.

Both were rendering a confident number over an absence, which is the defect
class this codebase already named for the per-provider cache breakdown
(cch-obs-01): a zero has several causes and only one of them is "we measured
zero".

LLM PROMPT CACHE (cch-obs-03)
  * "Entries: 0" was the LIVE count while the card's own state_detail said
    "every stored entry is past its TTL". Measured on the live board:
    total_entries=0, stored_entries=11. The 11 were in the payload and rendered
    NOWHERE, so the sentence referred to a quantity the reader could not see.
  * `hit_rate_pct` was a hard 0.0 when hit_count == miss_count == 0. Nothing was
    asked, so there is no rate — and a bold "0.0% Hit Rate" says the cache
    failed every request it was given.
  * `unlogged` was `str(backend).startswith("postgres")` — "am I on PostgreSQL",
    not "is this table unlogged". Migration 20260816123233 set the table LOGGED,
    so from that day the field asserted the opposite of the truth.

CORTEX GOVERNANCE (ctx-obs-03)
  * "$0.0000 Spend" merged two different zeroes. Measured over 7 days: 142
    rows, all cost_usd=0.0 with 0 tokens — 11 `provider='deterministic'` (no
    model call happened at all, so free BY CONSTRUCTION and $0.00 is a real
    answer) and 131 with no provider and no tokens (never instrumented, so
    there is no spend to report).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cache_savings.savings import _cache_state, _table_is_unlogged  # noqa: E402
from tools.cortex import metrics as cortex_metrics  # noqa: E402
from tools.cortex.metrics import (  # noqa: E402
    COST_BILLED,
    COST_DETERMINISTIC,
    COST_UNCOSTED,
    classify_cost_basis,
)


# --------------------------------------------------------------------------- #
# 1. `unlogged` is measured, not inferred from the backend name
# --------------------------------------------------------------------------- #
class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_k):
        return None

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Cur(self._row)


class _RaisingConn:
    def cursor(self):
        raise RuntimeError("catalogue unreadable")


def test_a_logged_table_is_not_reported_unlogged():
    """The regression. `relpersistence='p'` is a LOGGED table, and the old
    `backend.startswith("postgres")` reported True for it anyway."""
    assert _table_is_unlogged(_Conn({"relpersistence": "p"}), "postgresql") is False


def test_an_unlogged_table_is_reported_unlogged():
    assert _table_is_unlogged(_Conn({"relpersistence": "u"}), "postgresql") is True


def test_a_tuple_row_is_read_the_same_as_a_dict_row():
    """psycopg2 may hand back either shape depending on the cursor factory."""
    assert _table_is_unlogged(_Conn(("u",)), "postgresql") is True
    assert _table_is_unlogged(_Conn(("p",)), "postgresql") is False


def test_a_non_postgres_backend_is_unknown_not_false():
    """SQLite has no such concept. False would be a claim that these rows
    survive a crash, and that claim has to be earned."""
    assert _table_is_unlogged(_Conn(None), "sqlite") is None


def test_an_unreadable_catalogue_is_unknown_not_false():
    assert _table_is_unlogged(_RaisingConn(), "postgresql") is None


def test_a_missing_table_is_unknown_not_logged():
    """No row in pg_class means the table is absent, which is not 'logged'."""
    assert _table_is_unlogged(_Conn(None), "postgresql") is None


# --------------------------------------------------------------------------- #
# 2. The expired state, and the count the message refers to
# --------------------------------------------------------------------------- #
def test_expired_means_rows_exist_but_none_are_live():
    """This is the state the card was contradicting itself in: the message
    speaks of stored entries while the rendered count is the LIVE one."""
    assert _cache_state(True, live_entries=0, stored_entries=11) == "expired"


def test_cold_and_expired_are_different_states():
    """Zero live entries has two causes and they need different fixes: nothing
    was ever written, versus everything written has aged out."""
    assert _cache_state(True, 0, 0) == "cold"
    assert _cache_state(True, 0, 11) == "expired"


def test_a_populated_cache_outranks_both():
    assert _cache_state(True, 3, 11) == "populated"


def test_disabled_beats_every_other_state():
    assert _cache_state(False, 0, 11) == "disabled"


# --------------------------------------------------------------------------- #
# 3. Governance spend: which zero is this?
# --------------------------------------------------------------------------- #
def _summ(rows):
    """Aggregate through the PRODUCTION classifier.

    An earlier draft of this file re-implemented the if/elif ladder here, which
    would have tested the copy rather than the code — the two could drift and
    every assertion below would still pass. `classify_cost_basis` is the one
    definition; this only counts what it returns.
    """
    summ = {COST_BILLED: 0, COST_DETERMINISTIC: 0, COST_UNCOSTED: 0, "cost_usd": 0.0}
    for gj in rows:
        cost = float(gj.get("cost_usd") or 0.0)
        summ["cost_usd"] += cost
        summ[classify_cost_basis(
            cost,
            gj.get("provider") or "",
            int(gj.get("input_tokens") or 0),
            int(gj.get("output_tokens") or 0),
        )] += 1
    # Mirrors the one line in `summarize` that derives the flag.
    summ["cost_usd_measurable"] = bool(
        summ[COST_BILLED] or summ[COST_DETERMINISTIC]
    )
    return summ


def test_a_deterministic_call_is_free_by_construction_not_uncosted():
    """TRUST rule 1: a pack decides without an LLM, so there is no bill. $0.00
    is a REAL answer here and must not be lumped in with 'never measured'."""
    s = _summ([{"provider": "deterministic", "cost_usd": 0.0}] * 11)
    assert s["deterministic_calls"] == 11
    assert s["uncosted_calls"] == 0
    assert s["cost_usd_measurable"] is True


def test_a_row_with_no_provider_and_no_tokens_is_uncosted():
    """The 131-row case. No spend was measured, so no spend may be reported."""
    s = _summ([{}] * 131)
    assert s["uncosted_calls"] == 131
    assert s["deterministic_calls"] == 0
    assert s["cost_usd_measurable"] is False, (
        "a window of nothing but un-instrumented rows has no spend to report; "
        "$0.0000 over it is a fabrication, not a measurement"
    )


def test_the_live_mix_is_reported_as_a_mix():
    """The measured 7-day shape: 11 deterministic + 131 uncosted, 0 billed."""
    s = _summ([{"provider": "deterministic"}] * 11 + [{}] * 131)
    assert (s["billed_calls"], s["deterministic_calls"], s["uncosted_calls"]) == (0, 11, 131)
    assert s["cost_usd_measurable"] is True
    assert s["cost_usd"] == 0.0


def test_tokens_without_a_price_still_count_as_measured():
    """A priced-at-zero call that recorded tokens WAS instrumented — the
    accounting ran and produced 0. That is a measurement, unlike a row nothing
    ever looked at."""
    s = _summ([{"input_tokens": 40, "output_tokens": 10, "cost_usd": 0.0}])
    assert s["billed_calls"] == 1
    assert s["uncosted_calls"] == 0
    assert s["cost_usd_measurable"] is True


def test_a_billed_call_is_billed():
    s = _summ([{"provider": "anthropic", "cost_usd": 0.02, "input_tokens": 100}])
    assert s["billed_calls"] == 1
    assert s["cost_usd_measurable"] is True


def test_the_three_counts_partition_the_scanned_rows():
    """They must never double-count: every scanned row lands in exactly one
    bucket, or a renderer summing them reports more calls than happened."""
    rows = ([{"provider": "deterministic"}] * 3 + [{}] * 5
            + [{"cost_usd": 0.01}] * 2 + [{"input_tokens": 7}] * 4)
    s = _summ(rows)
    assert s["billed_calls"] + s["deterministic_calls"] + s["uncosted_calls"] == len(rows)


def test_summarize_emits_the_new_fields():
    """The contract the tile route reads. A missing key would silently render
    the old merged figure again."""
    empty = cortex_metrics.summarize(window_hours=1, conn=_RaisingConn())
    for key in ("billed_calls", "deterministic_calls",
                "uncosted_calls", "cost_usd_measurable"):
        assert key in empty["summary"], key
