#!/usr/bin/env python3
# CUI // SP-CTI
"""Guards for the config-driven external-repo seeder (xbm-list-02).

``tools/innovation/seed_external_repos.py`` used to carry its targets as a
Python literal. They now live in ``context/genesis/competitors.yaml`` beside
the Genesis scout's watchlist, so one file drives both.

Two things this pins:

1. **Adding a repo needs no code change.** A watchlist entry carrying an
   ``adaptation`` block becomes a signal; the seeder is never edited. A test
   that only read the shipped file would pass even if the loader hardcoded
   its results, so the additive test builds a watchlist of its own.

2. **Previously written signals stay valid.** Dedup is keyed on
   ``sha256(title + description)[:16]``. The 14 rows written before this
   change are matched only if the YAML reproduces both strings byte for byte
   — a reflowed block scalar or a smart-quote substitution silently registers
   a duplicate instead. The hashes below are the frozen baseline, taken from
   the rows already in ``innovation_signals``.

Offline and deterministic: YAML parsing and pure hashing, no DB, no network.
"""

import hashlib
import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import seed_external_repos as seeder

WATCHLIST = BASE_DIR / "context" / "genesis" / "competitors.yaml"

# content_hash of every signal written by the pre-config seeder, read back from
# innovation_signals. Changing a title or its `notes` breaks one of these, and
# the break is the point: it means the next --register-all writes a second row.
FROZEN_HASHES = {
    "headroom": "3152ef67af7de952",
    "last30days-skill": "87d1027db0a31d64",
    "agent-reach": "377d5f00f8270167",
    "markitdown": "dd4b5d43ac435070",
    "iii-hq-iii": "7c1db912430b85b3",
    "hermes-agent": "0fe43ef408180ceb",
    "tolaria": "b8cef2a0d5b4e8f0",
    "taste-skill": "26f8f4e1b2bc4b28",
    "ruview": "848a6c513925cac9",
    "sideshow": "9fe913418f4c99cf",
    "httpinspect": "4655b1bbec080e9e",
    "loopy": "3044823239e2d6b3",
    "agentcn": "4df131f5a9a769bf",
    "trilium": "004f8e3f36cd485a",
}


@pytest.fixture(scope="module")
def repos():
    return seeder.load_repos(WATCHLIST)


def _write_watchlist(tmp_path: Path, targets: list) -> Path:
    path = tmp_path / "competitors.yaml"
    path.write_text(yaml.safe_dump({"targets": targets}), encoding="utf-8")
    return path


# ── previously written signals remain valid ──────────────────────────────


def test_content_hashes_match_the_rows_already_written(repos):
    """The exact dedup key of all 14 pre-existing signals is reproduced."""
    got = {
        r["source"]: hashlib.sha256((r["title"] + r["description"]).encode()).hexdigest()[:16]
        for r in repos
    }
    drifted = {s: (got.get(s), h) for s, h in FROZEN_HASHES.items() if got.get(s) != h}
    assert not drifted, f"content hash drifted (got, expected): {drifted}"


def test_every_frozen_source_is_still_in_the_watchlist(repos):
    """A dropped entry stops being re-registered and silently rots."""
    missing = set(FROZEN_HASHES) - {r["source"] for r in repos}
    assert not missing, f"sources no longer in the watchlist: {sorted(missing)}"


def test_scoring_weights_are_unchanged(repos):
    """The composite score is comparable across old and new rows only if the
    weights are. These mirror args/innovation_config.yaml."""
    assert seeder.SCORING_WEIGHTS == {
        "novelty": 0.20,
        "feasibility": 0.25,
        "compliance_alignment": 0.25,
        "user_impact": 0.20,
        "effort": 0.10,
    }
    assert round(sum(seeder.SCORING_WEIGHTS.values()), 6) == 1.0


def test_every_candidate_scores_on_all_five_dimensions(repos):
    for repo in repos:
        assert set(repo["scoring_hints"]) == set(seeder.SCORING_WEIGHTS), repo["source"]


# ── adding a repo takes no code change ───────────────────────────────────


def test_a_new_watchlist_entry_becomes_a_candidate(tmp_path):
    """The acceptance criterion: config-only, and the seeder is untouched."""
    path = _write_watchlist(
        tmp_path,
        [
            {
                "repo": "someowner/brand-new-tool",
                "subsystem": "evaluation",
                "category": "ai_tooling",
                "watch": ["stars"],
                "notes": "A tool that did not exist when the seeder was written.",
                "adaptation": {
                    "title": "Brand New Tool",
                    "gotcha_layer": "tool",
                    "target": "tools/somewhere/ — adapt it",
                    "status": "pending",
                    "scoring": {
                        "novelty": 0.9,
                        "feasibility": 0.8,
                        "compliance_alignment": 0.7,
                        "user_impact": 0.6,
                        "effort": 0.5,
                    },
                },
            }
        ],
    )

    loaded = seeder.load_repos(path)

    assert len(loaded) == 1
    entry = loaded[0]
    assert entry["source"] == "brand-new-tool"  # derived from the slug
    assert entry["title"] == "Brand New Tool"
    assert entry["description"] == "A tool that did not exist when the seeder was written."
    assert entry["url"] == "https://github.com/someowner/brand-new-tool"
    assert entry["category"] == "ai_tooling"
    assert entry["adaptation_target"] == "tools/somewhere/ — adapt it"

    scored = seeder.score_repos(loaded)["repos"]
    assert scored[0]["source"] == "brand-new-tool"
    assert scored[0]["score"] == pytest.approx(0.9 * 0.20 + 0.8 * 0.25 + 0.7 * 0.25 + 0.6 * 0.20 + 0.5 * 0.10)


def test_a_minimal_adaptation_block_is_enough(tmp_path):
    """Only `adaptation:` is required; everything else defaults."""
    path = _write_watchlist(
        tmp_path,
        [
            {
                "repo": "owner/minimal",
                "subsystem": "iac",
                "watch": ["stars"],
                "notes": "Rationale doubling as the description.",
                "adaptation": {},
            }
        ],
    )

    entry = seeder.load_repos(path)[0]

    assert entry["source"] == "minimal"
    assert entry["title"] == "owner/minimal"
    assert entry["description"] == "Rationale doubling as the description."
    assert entry["gotcha_layer"] == "tool"
    assert entry["implementation_status"] == "pending"
    # Neutral 0.5 on every dimension rather than a crash or a missing key.
    assert entry["scoring_hints"] == {d: 0.5 for d in seeder.SCORING_WEIGHTS}


def test_an_explicit_source_overrides_the_derived_one(tmp_path):
    """`iii-hq/iii` was registered as `iii-hq-iii`; the override keeps it stable."""
    path = _write_watchlist(
        tmp_path,
        [
            {
                "repo": "iii-hq/iii",
                "subsystem": "agent_runtime",
                "watch": ["stars"],
                "notes": "n",
                "adaptation": {"source": "iii-hq-iii"},
            }
        ],
    )
    assert seeder.load_repos(path)[0]["source"] == "iii-hq-iii"


# ── watch-only entries are not candidates ────────────────────────────────


def test_entries_without_an_adaptation_block_are_skipped(tmp_path):
    """Most of the watchlist is competitors to monitor, not code to adapt."""
    path = _write_watchlist(
        tmp_path,
        [
            {"repo": "a/watched-only", "subsystem": "iac", "watch": ["releases"], "notes": "n"},
            {"repo": "b/candidate", "subsystem": "iac", "watch": ["stars"], "notes": "n", "adaptation": {}},
        ],
    )
    assert [r["source"] for r in seeder.load_repos(path)] == ["candidate"]


def test_the_shipped_watchlist_still_has_watch_only_entries():
    """If every entry became a candidate, the two lists would have merged by
    accident rather than by design."""
    targets = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8"))["targets"]
    watch_only = [t for t in targets if t.get("adaptation") is None]
    assert watch_only, "expected competitor entries that are watched but not adapted"
    assert len(watch_only) > len(FROZEN_HASHES)


# ── failure is loud ──────────────────────────────────────────────────────


def test_a_missing_watchlist_raises_rather_than_seeding_nothing(tmp_path):
    """Registering zero repos and succeeding is indistinguishable from a no-op."""
    with pytest.raises(seeder.WatchlistError):
        seeder.watchlist_path((tmp_path / "nope.yaml",))


def test_malformed_yaml_raises(tmp_path):
    path = tmp_path / "competitors.yaml"
    path.write_text("targets: [\n  - broken: {{{\n", encoding="utf-8")
    with pytest.raises(seeder.WatchlistError):
        seeder.load_repos(path)


def test_the_shipped_watchlist_resolves_without_an_argument():
    """The default path must find the checked-in file, not just a fixture."""
    assert seeder.watchlist_path() == WATCHLIST
