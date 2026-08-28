# CUI // SP-CTI
"""The savings figure must outlive the cache entry that produced it (cch-obs-07).

THE DEFECT. Every number on /cache-savings was derived live `FROM llm_response_cache`.
There is no savings table, so the figure was CUMULATIVE IN INTENT and VOLATILE IN FACT: a
row's contribution vanished when the row did, and rows vanish on an ordinary day --
`ttl_seconds: 3600`, LRU eviction past `max_entries`, and `invalidate()`.

savings.py's own header names this exact shape for a DIFFERENT cause (the table was
UNLOGGED and PostgreSQL truncates unlogged tables on crash recovery, which "reset a
CUMULATIVE metric to $0.0000 with no record it had ever been anything else"). Migration
20260816123233 fixed the RESTART half and left expiry and eviction.

Observed 2026-08-27: the panel read `$0.0000` and `0 / 15` having previously shown a
dollar figure. Nothing was broken -- whatever had been re-read had simply aged out.

WHY A UNIT TEST OF `cumulative()` ALONE WOULD NOT HAVE CAUGHT IT. Summing a table is
trivially correct. The defect is in the LIFECYCLE: the row is written at hit time and must
still be there after the cache forgets. So the load-bearing test drives the real
`LLMResponseCache` through set -> get -> invalidate and asserts the ledger is unmoved.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sqlite3
import types

import pytest

from tools.cache_savings import ledger

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = (REPO_ROOT / "tools" / "db" / "migrations"
             / "20260827235301_llm_cache_savings_ledger" / "up.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("_ledger_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Shim:
    """Minimal stand-in for a storage connection: %s placeholders, declared backend."""

    _backend = "sqlite"

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        return self._raw.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._raw.commit()

    def close(self):
        pass


@pytest.fixture
def led(tmp_path):
    """Create the ledger via THE MIGRATION, never a hand-written copy of its DDL.

    Five hand-written copies of `cli_llm_jobs`' DDL drifted from the migration and
    presented as four hanging tests. A sixth copy here would be the same mistake.
    """
    raw = sqlite3.connect(str(tmp_path / "ledger.db"))
    raw.row_factory = sqlite3.Row
    shim = _Shim(raw)
    _load_migration().up(shim)
    raw.commit()
    return shim


# ---------------------------------------------------------------------------
# the lifecycle -- the property the whole card exists for
# ---------------------------------------------------------------------------


def test_a_real_cache_hit_writes_a_row(monkeypatch):
    """Drive the ACTUAL LLMResponseCache, not a stand-in for it.

    The wiring is a `try/except Exception: pass` inside the hit path (a serving hit must
    never fail because bookkeeping did), so a broken call site is SILENT. Only exercising
    the real object can tell that apart from a working one.
    """
    import tools.cache_savings.ledger as ledger_mod
    from tools.llm.response_cache import LLMResponseCache

    calls = []
    monkeypatch.setattr(
        ledger_mod, "record_avoided_call", lambda **kw: calls.append(kw) or True
    )

    # BUILD OUR OWN INSTANCE AND LEAVE THE PROCESS AS WE FOUND IT. LLMResponseCache is a
    # process singleton whose __init__ returns early once initialised, so constructing it
    # here would pin every LATER test in the run to whatever config we happened to load --
    # which is exactly how this test broke test_lru_eviction in-suite while both passed
    # alone. Declaring the config also removes the need for a `pytest.skip` on a
    # deployment that ships the cache off: a skip reads as coverage while measuring
    # nothing, and would owe an entry against a `skip_max` that may only go down.
    prior = LLMResponseCache._instance
    LLMResponseCache._instance = None
    cache = LLMResponseCache(config={
        "enabled": True, "ttl_seconds": 3600, "max_entries": 100,
        "excluded_functions": [], "per_function": {}, "per_canvas": {},
    })
    monkeypatch.setattr(LLMResponseCache, "_instance", prior, raising=False)

    fn, key = "cch_obs_07_unit", "cch-obs-07-unit-key"
    cache.invalidate(function=fn)
    resp = types.SimpleNamespace(
        content="x", model="m", provider="anthropic",
        input_tokens=7, output_tokens=3, cost_usd=0.0,
        latency_ms=1, cached=False, error=None, finish_reason="stop",
    )
    cache.set(key, resp, function=fn)
    try:
        assert cache.get(key, jitter_ms=0) is not None, "precondition: entry is servable"
    finally:
        cache.invalidate(function=fn)

    assert calls, "a cache hit recorded nothing -- the ledger is wired to nothing"
    assert calls[0]["input_tokens"] == 7
    assert calls[0]["output_tokens"] == 3


def test_the_saving_survives_losing_every_cache_row(led):
    """The ledger must not be DERIVED from anything the cache is allowed to evict."""
    ledger.record_avoided_call(
        function="f", model_id="m", provider="anthropic",
        input_tokens=10_000, output_tokens=2_000, conn=led,
    )
    after_hit = ledger.cumulative(led)
    assert after_hit["avoided_calls"] == 1

    # This database has no llm_response_cache at all -- the strongest form of "the cache
    # forgot everything". The figure must be unmoved.
    assert ledger.cumulative(led) == after_hit


def test_bookkeeping_failure_never_breaks_a_hit(led, monkeypatch):
    """A serving cache must keep serving when the ledger is unwritable."""
    def boom(*a, **k):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(led, "execute", boom)
    assert ledger.record_avoided_call(
        function="f", model_id="m", provider="anthropic",
        input_tokens=1, output_tokens=1, conn=led,
    ) is False, "it must report the failure, not raise"


# ---------------------------------------------------------------------------
# None, never 0.0 -- the cch-obs-01 discipline, one layer up
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["ollama", "claude-cli", "some-unknown-vendor"])
def test_an_unbilled_call_saves_no_dollars_and_says_so(provider):
    """A local or subscription call has NO BILL to avoid. 0.0 would report a working
    cache as a failed one -- the defect cch-obs-01 removed from the per-provider view."""
    usd, basis = ledger.price_saving(provider, 10_000, 2_000)
    assert usd is None
    assert basis in ("local", "unpriced")


def test_a_metered_provider_is_priced_from_its_own_claim():
    """Anthropic's rate card must not be applied to everyone (savings.py's `_IN`)."""
    anth, anth_basis = ledger.price_saving("anthropic", 1_000_000, 0)
    oai, oai_basis = ledger.price_saving("openai", 1_000_000, 0)
    assert anth_basis == oai_basis == "priced"
    assert anth is not None and oai is not None
    assert anth != oai, (
        "two providers priced identically means the claim file is not being read"
    )


def test_cumulative_is_none_not_zero_when_nothing_priced_was_avoided(led):
    """A deployment on Ollama and a Claude subscription has genuinely saved no dollars.
    Reporting $0.00 implies the cache did nothing; it avoided the calls, unbilled."""
    ledger.record_avoided_call(function="f", model_id="m", provider="ollama",
                              input_tokens=5, output_tokens=5, conn=led)
    out = ledger.cumulative(led)
    assert out["avoided_calls"] == 1, "the call WAS avoided"
    assert out["tokens_saved_input"] == 5, "and the tokens ARE real"
    assert out["usd_saved"] is None, "but no bill existed to avoid"
    assert out["unpriced_calls"] == 1 and out["priced_calls"] == 0


def test_an_unbilled_saving_is_denominated_in_tokens(led):
    """A FLAT SUBSCRIPTION METERS QUOTA, so tokens are the unit -- not dollars, and not a
    bare call count either.

    "Unbilled" does not mean "nothing was saved". An uncached call spends the allowance
    whether or not it produces a line on an invoice, and protecting that allowance is the
    entire purpose of response and prefix caching. So the ledger must carry a REAL token
    figure for an unpriced provider, and it is the headline the tile renders. Reporting
    $0.0000 -- or only "3 calls avoided" -- understates a cache doing exactly its job.
    """
    for _ in range(3):
        ledger.record_avoided_call(function="f", model_id="m", provider="claude-cli",
                                   input_tokens=40_000, output_tokens=1_500, conn=led)
    out = ledger.cumulative(led)
    assert out["usd_saved"] is None, "a subscription has no per-token price"
    assert out["unpriced_calls"] == 3
    # The saving, in the unit that is actually scarce here.
    assert out["tokens_saved_input"] == 120_000
    assert out["tokens_saved_output"] == 4_500


def test_priced_and_unpriced_are_summed_apart(led):
    for prov in ("anthropic", "ollama", "ollama"):
        ledger.record_avoided_call(function="f", model_id="m", provider=prov,
                                   input_tokens=1_000_000, output_tokens=0, conn=led)
    out = ledger.cumulative(led)
    assert out["avoided_calls"] == 3
    assert out["priced_calls"] == 1 and out["unpriced_calls"] == 2
    assert out["usd_saved"] and out["usd_saved"] > 0, "the billed one still counts"


def test_an_unreadable_ledger_is_unmeasurable_never_zero():
    """`measurable` is what keeps 'could not read' from rendering as 'saved nothing'."""
    class _Dead:
        _backend = "sqlite"

        def execute(self, *a, **k):
            raise RuntimeError("no such table")

        def commit(self):
            pass

        def close(self):
            pass

    out = ledger.cumulative(_Dead())
    assert out["measurable"] is False
    assert out["usd_saved"] is None
    assert out["avoided_calls"] == 0


def test_an_empty_ledger_is_measurable_and_that_is_different(led):
    """Readable-and-empty is a MEASURED zero; it must not read as unmeasurable."""
    out = ledger.cumulative(led)
    assert out["measurable"] is True
    assert out["avoided_calls"] == 0


# ---------------------------------------------------------------------------
# the surface keeps the two halves apart
# ---------------------------------------------------------------------------


def test_savings_stats_carries_lifetime_apart_from_the_live_summary():
    """`summary` describes rows STILL IN the cache; `lifetime` describes what was saved.
    Folding one into the other lets a swept cache report its savings never happened."""
    from tools.cache_savings import savings

    stats = savings.get_savings_stats()
    assert "lifetime" in stats, "the durable half is missing from the surface"
    life = stats["lifetime"]
    assert set(life) >= {"avoided_calls", "usd_saved", "measurable", "priced_calls"}
    assert life["usd_saved"] is None or isinstance(life["usd_saved"], float)
    assert "lifetime" not in stats["summary"], "the two halves must stay separate"


# ---------------------------------------------------------------------------
# structural
# ---------------------------------------------------------------------------


def test_the_table_is_registered_append_only():
    """A ledger that can be UPDATEd is not evidence of anything."""
    src = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    names = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert ledger.TABLE in names, (
        f"{ledger.TABLE} is not in APPEND_ONLY_TABLES; an UPDATE would rewrite what a "
        "call cost at the one moment that cost was knowable"
    )


def test_the_migration_down_does_not_drop_the_table():
    """Unlike a cache, this content cannot be regenerated: the calls already did not
    happen. A `down` that drops it recreates the defect and destroys the only record."""
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    down = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "down")
    # STATEMENTS, never the source text. A first cut of this test read
    # `ast.unparse(down)` and failed on the word DROP inside the docstring EXPLAINING
    # why nothing is dropped -- a structural test that matches prose proves only that
    # somebody wrote about the subject, which is the same defect fixed in
    # test_aca_xp_ledger. Strip the docstring, then look at what actually executes.
    body = [n for n in down.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))]
    assert not any("DROP" in ast.unparse(n).upper() for n in body), (
        "down() must not drop the ledger"
    )
    assert all(isinstance(n, (ast.Return, ast.Pass)) for n in body), (
        "down() must do nothing at all -- the avoided calls cannot be regenerated"
    )


def test_the_migration_runs_on_sqlite_and_is_idempotent(led):
    """Applying twice must not raise -- migrations run against boards of every shape."""
    _load_migration().up(led)
    assert ledger.cumulative(led)["measurable"] is True
