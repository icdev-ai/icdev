#!/usr/bin/env python3
# CUI // SP-CTI
"""Guards for the Genesis Scout watchlist (context/genesis/competitors.yaml).

The watchlist is the routing table between an external finding and the ICDEV
subsystem that finding is about. Two things can silently break that:

1. An entry with no ``subsystem`` — the scout still polls it, the brief still
   prints it, and nobody can tell whose problem it is.
2. A benchmark-map subsystem with no target at all — the map names ten
   subsystems (docs/research/external-benchmark-map.md §1-§10) and a gap here
   means that subsystem is unwatched without anyone noticing.

Plus the manual-review entries: Cortex.io, Port and OpsLevel are commercial
products with no public repo. ``run()`` skips any entry without ``repo``, so
without the manual-review path they would be present in the file and absent
from every brief — the worst of both worlds. These tests pin that they reach
the brief.

Offline and deterministic: no GitHub calls, only YAML parsing and the pure
``_generate_brief`` renderer.
"""

import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes import scout

WATCHLIST = BASE_DIR / "context" / "genesis" / "competitors.yaml"

# The ten subsystems named in docs/research/external-benchmark-map.md §1-§10.
# Every one of them must be represented in the watchlist by at least one
# trackable repo or an explicit not-trackable entry.
BENCHMARK_MAP_SUBSYSTEMS = {
    "developer_portal",
    "observability",
    "agent_runtime",
    "security_ops",
    "rag_knowledge_graph",
    "compliance_ato",
    "delivery_pipeline",
    "data_lineage",
    "evaluation",
    "iac",
}


@pytest.fixture(scope="module")
def targets():
    data = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or {}
    entries = data.get("targets", [])
    assert entries, "watchlist parsed to zero targets"
    return entries


def test_every_entry_declares_a_subsystem(targets):
    """A finding is only routable if the entry says what it benchmarks."""
    missing = [t.get("repo") or t.get("name") or repr(t) for t in targets if not t.get("subsystem")]
    assert not missing, f"entries with no subsystem: {missing}"


def test_every_benchmark_map_subsystem_is_covered(targets):
    """Each of the map's ten subsystems has at least one target."""
    covered = {t["subsystem"] for t in targets if t.get("subsystem")}
    uncovered = BENCHMARK_MAP_SUBSYSTEMS - covered
    assert not uncovered, f"benchmark-map subsystems with no target: {sorted(uncovered)}"


def test_untrackable_entries_are_explicit(targets):
    """No-repo entries must say so, and say where a human should look."""
    no_repo = [t for t in targets if not t.get("repo")]
    assert no_repo, "expected the commercial no-repo entries to be present"
    for entry in no_repo:
        label = entry.get("name") or repr(entry)
        assert entry.get("trackable") is False, f"{label}: no repo but not marked trackable: false"
        assert entry.get("url"), f"{label}: not trackable and no url to review by hand"
        assert entry.get("name"), f"{label}: not trackable and no name"
        assert entry.get("notes"), f"{label}: not trackable and no rationale"


def test_trackable_entries_have_a_watch_list(targets):
    """A trackable entry with no signals declared collects nothing."""
    bad = [t["repo"] for t in targets if t.get("repo") and not t.get("watch")]
    assert not bad, f"trackable entries with empty watch: {bad}"


def test_repo_slugs_are_well_formed(targets):
    """owner/name, exactly one slash — a typo here 404s and is only a WARN."""
    bad = [t["repo"] for t in targets if t.get("repo") and t["repo"].count("/") != 1]
    assert not bad, f"malformed repo slugs: {bad}"


def test_brief_renders_subsystem_for_trackable_targets():
    """The subsystem must reach the brief, or it routes nothing."""
    brief = scout._generate_brief(
        [
            {
                "repo": "promptfoo/promptfoo",
                "category": "evaluation",
                "subsystem": "evaluation",
                "info": {"stars": 1, "description": "d", "language": "TypeScript"},
                "release": None,
            }
        ]
    )
    assert "promptfoo/promptfoo" in brief
    assert "**Subsystem:** evaluation" in brief


def test_brief_lists_manual_review_entries():
    """No-repo targets are surfaced for a human, not silently dropped."""
    brief = scout._generate_brief(
        [],
        manual_targets=[
            {
                "name": "Cortex.io",
                "url": "https://www.cortex.io/",
                "subsystem": "developer_portal",
                "trackable": False,
                "notes": "Commercial scorecard SaaS.",
            }
        ],
    )
    assert "Manual Review" in brief
    assert "Cortex.io" in brief
    assert "https://www.cortex.io/" in brief
    assert "**Subsystem:** developer_portal" in brief


def test_brief_omits_manual_section_when_all_targets_are_trackable():
    """No empty heading when there is nothing to review by hand."""
    brief = scout._generate_brief([], manual_targets=[])
    assert "Manual Review" not in brief


def test_run_routes_no_repo_entries_to_manual_review(monkeypatch, tmp_path):
    """End-to-end: a no-repo entry lands in the brief, not the bit bucket.

    Patches the loader and the GitHub calls so this stays offline.
    """
    monkeypatch.setattr(
        scout,
        "_load_targets",
        lambda: [
            {"repo": "promptfoo/promptfoo", "subsystem": "evaluation", "category": "evaluation", "watch": ["stars"]},
            {
                "name": "OpsLevel",
                "url": "https://docs.opslevel.com/",
                "subsystem": "developer_portal",
                "category": "platform_engineering",
                "trackable": False,
                "notes": "Commercial scorecard SaaS.",
            },
        ],
    )
    monkeypatch.setattr(
        scout,
        "_get_repo_info",
        lambda *a, **k: {
            "full_name": "promptfoo/promptfoo",
            "description": "",
            "stars": 1,
            "forks": 0,
            "language": "TypeScript",
            "updated_at": "",
            "open_issues": 0,
            "topics": [],
            "archived": False,
        },
    )
    monkeypatch.setattr(scout, "BASE_DIR", tmp_path)

    result = scout.run({}, None)

    assert result["success"] is True
    assert result["details"]["repos_scouted"] == 1
    assert result["details"]["manual_review_entries"] == 1

    brief = Path(result["details"]["brief_file"]).read_text(encoding="utf-8")
    assert "OpsLevel" in brief, "manual-review entry never reached the brief"
    assert "**Subsystem:** evaluation" in brief, "subsystem never reached the brief"
