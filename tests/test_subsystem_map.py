#!/usr/bin/env python3
# CUI // SP-CTI
"""Guards for the project -> subsystem -> ICDEV lookup (xbm-cmp-01-d2).

``tools/innovation/subsystem_map.py`` joins the scout watchlist (which external
project benchmarks what) to the subsystem inventory (what ICDEV holds for that
tag). What these pin, and why each is here rather than being obvious:

1. **Ambiguity raises.** ``cortex`` names two watchlisted projects in two
   different subsystems. Silently resolving it would route a security-ops
   finding into observability — a wrong comparison, which is worse than a
   refused one. The bare name must raise while both full slugs still resolve.

2. **The map covers every shipped tag.** The three config files
   (``competitors.yaml``, ``xbm_subsystem_inventory.yaml``,
   ``innovation_promoter.yaml``) are edited independently, so the shipped set is
   checked for drift directly rather than only through synthetic fixtures.

3. **Nothing is silently dropped.** An untagged target, or one tagged with a
   subsystem the inventory never declared, lands in ``unmapped_projects``. A
   comparison that quietly loses a project reports better coverage than it has.

4. **The doctests run.** The acceptance criterion for this task is a mapping
   verified by doctest or unit test; the module's doctests are executed here so
   they cannot rot unnoticed.

Offline and deterministic: literal fixtures and the shipped YAML. No database,
no network — the module opens neither.
"""

import doctest
import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import subsystem_map as sm

WATCHLIST = BASE_DIR / "context" / "genesis" / "competitors.yaml"
INVENTORY = BASE_DIR / "args" / "xbm_subsystem_inventory.yaml"
PROMOTER = BASE_DIR / "args" / "innovation_promoter.yaml"


# ── fixtures ─────────────────────────────────────────────────────────────


TARGETS = [
    {"repo": "backstage/backstage", "subsystem": "developer_portal",
     "category": "platform_engineering"},
    {"name": "Cortex.io", "url": "https://www.cortex.io/", "trackable": False,
     "subsystem": "developer_portal", "category": "platform_engineering"},
    {"repo": "cortexproject/cortex", "subsystem": "observability",
     "category": "observability"},
    {"repo": "TheHive-Project/Cortex", "subsystem": "security_ops",
     "category": "security", "adaptation": {"source": "thehive_cortex"}},
    {"repo": "langchain-ai/langgraph", "subsystem": "agent_runtime",
     "category": "agent_framework"},
]

INVENTORY_FIXTURE = {
    "developer_portal": {"title": "Developer portal / catalog", "section": 1,
                         "modules": ["tools/idp/*.py"], "tables": ["idp_*"]},
    "observability": {"title": "Observability / LLM telemetry", "section": 2,
                      "modules": ["tools/observability/*.py"], "tables": ["otel_*"]},
    "security_ops": {"title": "Security ops / threat analysis", "section": 4,
                     "modules": ["tools/security/*.py"], "tables": ["threat_*"]},
    "agent_runtime": {"title": "Agent runtime & orchestration", "section": 3,
                      "modules": ["tools/agent/*.py"], "tables": ["agent_*"]},
    "embedded": {"title": "SparkPilot / RTOS", "section": None,
                 "modules": [], "tables": [], "note": "Deliberately empty."},
}

BENCHMARK_FIXTURE = {
    "developer_portal": {"section": 1, "verdict": "gap",
                         "benchmarked_against": "Backstage, Cortex.io",
                         "icdev_surface": "args/component_registry.yaml",
                         "categories": ["platform_engineering"]},
    "observability": {"section": 2, "verdict": "ahead",
                      "icdev_surface": "tools/observability/"},
}


@pytest.fixture(scope="module")
def shipped_targets():
    return sm.load_targets(WATCHLIST)


@pytest.fixture(scope="module")
def shipped_inventory():
    return yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))["subsystems"]


@pytest.fixture(scope="module")
def shipped_benchmark():
    return sm.load_benchmark_subsystems(PROMOTER)


# ── identifier folding ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Backstage/Backstage", "backstage/backstage"),
        ("  langgraph  ", "langgraph"),
        ("https://github.com/langfuse/langfuse", "langfuse/langfuse"),
        ("http://github.com/langfuse/langfuse/", "langfuse/langfuse"),
        ("github.com/promptfoo/promptfoo.git", "promptfoo/promptfoo"),
        ("", ""),
    ],
)
def test_normalize_identifier(raw, expected):
    assert sm.normalize_identifier(raw) == expected


def test_aliases_cover_slug_bare_name_display_name_and_source():
    aliases = sm.project_aliases(TARGETS[3])
    assert aliases == ["thehive-project/cortex", "cortex", "thehive_cortex"]


def test_untrackable_target_still_resolves_by_display_name():
    """The three commercial products have no repo. They must still map."""
    assert sm.subsystem_for_project("Cortex.io", targets=TARGETS) == "developer_portal"


# ── the mapping itself: test tags -> correct subsystem names ─────────────


@pytest.mark.parametrize(
    "identifier,expected",
    [
        ("backstage/backstage", "developer_portal"),
        ("backstage", "developer_portal"),
        ("langgraph", "agent_runtime"),
        ("langchain-ai/langgraph", "agent_runtime"),
        ("https://github.com/langchain-ai/langgraph", "agent_runtime"),
        ("cortexproject/cortex", "observability"),
        ("TheHive-Project/Cortex", "security_ops"),
        ("thehive_cortex", "security_ops"),
    ],
)
def test_subsystem_for_project(identifier, expected):
    assert sm.subsystem_for_project(identifier, targets=TARGETS) == expected


def test_unknown_project_is_none_not_an_error():
    """An untracked project is a normal answer; only a bad tag is an error."""
    assert sm.subsystem_for_project("some/unwatched-repo", targets=TARGETS) is None


def test_ambiguous_bare_name_raises_with_candidates():
    with pytest.raises(sm.AmbiguousProjectError) as excinfo:
        sm.subsystem_for_project("cortex", targets=TARGETS)
    assert excinfo.value.subsystems == ["observability", "security_ops"]
    assert "owner/name" in str(excinfo.value)


def test_ambiguity_does_not_block_the_unambiguous_slugs():
    """The collision is confined to the bare name it is actually about."""
    index = sm.build_project_index(TARGETS)
    assert sm.ambiguous_aliases(index) == {"cortex": ["observability", "security_ops"]}
    assert sm.subsystem_for_project("cortexproject/cortex", index=index) == "observability"


def test_untagged_target_claims_no_alias():
    index = sm.build_project_index([{"repo": "nobody/untagged"}])
    assert index == {}


# ── subsystem -> ICDEV counterpart ───────────────────────────────────────


def test_counterpart_merges_inventory_and_promoter():
    counterpart = sm.icdev_counterpart(
        "developer_portal", inventory=INVENTORY_FIXTURE, benchmark=BENCHMARK_FIXTURE
    )
    assert counterpart["module_patterns"] == ["tools/idp/*.py"]
    assert counterpart["table_patterns"] == ["idp_*"]
    assert counterpart["declared_verdict"] == "gap"
    assert counterpart["icdev_surface"] == "args/component_registry.yaml"
    assert counterpart["benchmarked"] is True


def test_counterpart_for_a_tag_the_promoter_never_covered():
    """Five tags predate the benchmark map. They map; they have no verdict."""
    counterpart = sm.icdev_counterpart(
        "embedded", inventory=INVENTORY_FIXTURE, benchmark=BENCHMARK_FIXTURE
    )
    assert counterpart["benchmarked"] is False
    assert counterpart["declared_verdict"] is None
    assert counterpart["module_patterns"] == []
    assert counterpart["note"]


def test_unknown_tag_raises_and_names_the_known_ones():
    with pytest.raises(KeyError) as excinfo:
        sm.icdev_counterpart("not_a_subsystem", inventory=INVENTORY_FIXTURE, benchmark={})
    assert "observability" in excinfo.value.args[0]


# ── the comparison data structure ────────────────────────────────────────


def test_comparison_map_pairs_projects_with_their_counterpart():
    result = sm.build_comparison_map(
        targets=TARGETS, inventory=INVENTORY_FIXTURE, benchmark=BENCHMARK_FIXTURE
    )
    portal = result["subsystems"]["developer_portal"]
    assert [e["project"] for e in portal["external"]] == ["backstage/backstage", "Cortex.io"]
    assert portal["external_count"] == 2
    assert portal["declared_verdict"] == "gap"
    assert result["total_projects"] == len(TARGETS)


def test_subsystem_with_no_external_project_is_kept_not_dropped():
    """An unbenchmarked ICDEV subsystem is a finding about the watchlist."""
    result = sm.build_comparison_map(
        targets=TARGETS, inventory=INVENTORY_FIXTURE, benchmark=BENCHMARK_FIXTURE
    )
    assert result["subsystems"]["embedded"]["external"] == []
    assert result["total_subsystems"] == len(INVENTORY_FIXTURE)


def test_untagged_and_unknown_tagged_projects_land_in_unmapped():
    targets = TARGETS + [
        {"repo": "nobody/untagged"},
        {"repo": "nobody/bad-tag", "subsystem": "no_such_subsystem"},
    ]
    result = sm.build_comparison_map(
        targets=targets, inventory=INVENTORY_FIXTURE, benchmark=BENCHMARK_FIXTURE
    )
    unmapped = {e["project"]: e["reason"] for e in result["unmapped_projects"]}
    assert unmapped == {
        "nobody/untagged": "untagged",
        "nobody/bad-tag": "tag not in inventory",
    }
    assert result["total_projects"] == len(TARGETS)


def test_trackable_and_adaptation_flags_survive_into_the_map():
    result = sm.build_comparison_map(
        targets=TARGETS, inventory=INVENTORY_FIXTURE, benchmark=BENCHMARK_FIXTURE
    )
    portal = {e["project"]: e for e in result["subsystems"]["developer_portal"]["external"]}
    assert portal["Cortex.io"]["trackable"] is False
    assert portal["backstage/backstage"]["trackable"] is True
    hive = result["subsystems"]["security_ops"]["external"][0]
    assert hive["adaptation_candidate"] is True


# ── integrity of the shipped configs ─────────────────────────────────────


def test_validate_flags_a_tag_with_no_inventory_entry():
    report = sm.validate(
        targets=[{"repo": "a/b", "subsystem": "invented"}],
        inventory=INVENTORY_FIXTURE,
        benchmark={},
    )
    assert report["ok"] is False
    assert report["watchlist_tags_missing_from_inventory"] == ["invented"]


def test_validate_flags_an_untagged_target():
    report = sm.validate(
        targets=[{"repo": "a/b"}], inventory=INVENTORY_FIXTURE, benchmark={}
    )
    assert report["ok"] is False
    assert report["untagged_targets"] == ["a/b"]


def test_validate_flags_a_promoter_tag_the_inventory_lacks():
    report = sm.validate(
        targets=TARGETS,
        inventory=INVENTORY_FIXTURE,
        benchmark={"ghost_subsystem": {"verdict": "gap"}},
    )
    assert report["ok"] is False
    assert report["promoter_tags_missing_from_inventory"] == ["ghost_subsystem"]


def test_ambiguity_alone_does_not_fail_validation():
    """`cortex` is permanently ambiguous. That is a fact, not a defect."""
    report = sm.validate(targets=TARGETS, inventory=INVENTORY_FIXTURE, benchmark={})
    assert report["ok"] is True
    assert "cortex" in report["ambiguous_aliases"]


def test_shipped_configs_validate(shipped_targets, shipped_inventory, shipped_benchmark):
    """Drift guard on the three files this map is joined from."""
    report = sm.validate(
        targets=shipped_targets, inventory=shipped_inventory, benchmark=shipped_benchmark
    )
    assert report["ok"], report


def test_every_shipped_target_maps_to_a_declared_subsystem(
    shipped_targets, shipped_inventory
):
    result = sm.build_comparison_map(
        targets=shipped_targets, inventory=shipped_inventory, benchmark={}
    )
    assert result["unmapped_projects"] == []
    assert result["total_projects"] == len(shipped_targets)


def test_shipped_map_reproduces_known_routings(shipped_targets):
    """Spot-checks against docs/research/external-benchmark-map.md §2, §3, §9."""
    index = sm.build_project_index(shipped_targets)
    assert sm.subsystem_for_project("langfuse/langfuse", index=index) == "observability"
    assert sm.subsystem_for_project("temporalio/temporal", index=index) == "agent_runtime"
    assert sm.subsystem_for_project("promptfoo/promptfoo", index=index) == "evaluation"
    assert sm.subsystem_for_project("TheHive-Project/Cortex", index=index) == "security_ops"


def test_shipped_promoter_verdicts_reach_the_map(shipped_inventory, shipped_benchmark):
    """The ten benchmarked tags carry their verdict; the other five say so."""
    benchmarked = [
        tag for tag in shipped_inventory
        if sm.icdev_counterpart(
            tag, inventory=shipped_inventory, benchmark=shipped_benchmark
        )["declared_verdict"]
    ]
    assert len(benchmarked) == len(shipped_benchmark)
    observability = sm.icdev_counterpart(
        "observability", inventory=shipped_inventory, benchmark=shipped_benchmark
    )
    assert observability["declared_verdict"] == "ahead"


# ── the module's own doctests ────────────────────────────────────────────


def test_module_doctests_pass():
    result = doctest.testmod(sm, verbose=False)
    assert result.failed == 0, f"{result.failed} of {result.attempted} doctests failed"
    assert result.attempted >= 15
