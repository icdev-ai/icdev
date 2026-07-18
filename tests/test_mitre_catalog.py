from __future__ import annotations
# CUI // SP-CTI
"""Integrity tests for the consolidated ODC MITRE catalog.

Covers:
  * unique technique ids, every entry carries id/name/tactic(s)
  * the three legacy views (coverage twin, canvas sigma, obs sigma) derive
    correctly from the single source of truth
  * T1078 Valid Accounts multi-tactic resolution is documented and enforced
  * the exporter-delegation adapters route through exporters/ and preserve the
    legacy response shape (strings keyed splunk_spl/elastic_kql/sentinel_kql)
"""

import importlib

from tools.observability_canvas import mitre_catalog


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------

def test_catalog_ids_unique_and_technique_shaped():
    """Every catalog id is a distinct MITRE technique id (T####[.###])."""
    ids = list(mitre_catalog.MITRE_CATALOG.keys())
    assert len(ids) == len(set(ids)), "duplicate technique ids in catalog"
    for tid in ids:
        assert tid.startswith("T"), f"{tid} is not a MITRE technique id"
        assert tid[1:].split(".")[0].isdigit(), f"{tid} has a non-numeric base"


def test_every_entry_has_name_and_tactics():
    """Each entry carries a name and a non-empty ordered tactics list."""
    for tid, entry in mitre_catalog.MITRE_CATALOG.items():
        assert entry.get("name"), f"{tid} missing name"
        tactics = entry.get("tactics")
        assert isinstance(tactics, list) and tactics, f"{tid} missing tactics list"
        assert all(isinstance(t, str) and t for t in tactics), f"{tid} bad tactics"


def test_primary_tactic_is_first_in_list():
    """primary_tactic() returns tactics[0] and tactics_for() returns the full list."""
    for tid, entry in mitre_catalog.MITRE_CATALOG.items():
        assert mitre_catalog.primary_tactic(tid) == entry["tactics"][0]
        assert mitre_catalog.tactics_for(tid) == entry["tactics"]


def test_catalog_is_superset_union_of_three_legacy_catalogs():
    """The catalog contains every technique from all three legacy catalogs."""
    coverage = mitre_catalog.build_coverage_catalog()
    canvas = mitre_catalog.build_canvas_detections()
    obs = mitre_catalog.build_obs_detections()
    union = set(coverage) | set(canvas) | set(obs)
    assert union <= set(mitre_catalog.MITRE_CATALOG), "catalog is not a superset"
    # sanity: the three legacy sizes are preserved
    assert len(coverage) == 20
    assert len(canvas) == 10
    assert len(obs) == 17


# ---------------------------------------------------------------------------
# T1078 tactic-conflict resolution (documented invariant)
# ---------------------------------------------------------------------------

def test_t1078_multi_tactic_resolution():
    """T1078 Valid Accounts spans four tactics; primary resolves to defense-evasion.

    The coverage twin historically used 'initial_access' while both Sigma
    generators used 'defense-evasion'. Consolidation resolves the conflict to
    the MITRE ATT&CK multi-tactic set with defense-evasion as the primary
    scalar, so the Sigma tag output is preserved and only the coverage twin's
    by_tactic grouping shifts.
    """
    entry = mitre_catalog.MITRE_CATALOG["T1078"]
    assert entry["tactics"] == [
        "defense-evasion",
        "persistence",
        "privilege-escalation",
        "initial-access",
    ]
    # primary scalar (hyphen form) used by the Sigma generators
    assert mitre_catalog.primary_tactic("T1078") == "defense-evasion"
    # coverage-twin view renders the primary in underscore vocabulary
    coverage = mitre_catalog.build_coverage_catalog()
    assert coverage["T1078"]["tactic"] == "defense_evasion"


# ---------------------------------------------------------------------------
# Legacy views derive correctly
# ---------------------------------------------------------------------------

def test_coverage_view_matches_module_export():
    """mitre_coverage_twin.MITRE_TECHNIQUE_CATALOG is the derived coverage view."""
    twin = importlib.import_module("tools.observability_canvas.mitre_coverage_twin")
    assert twin.MITRE_TECHNIQUE_CATALOG == mitre_catalog.build_coverage_catalog()
    # coverage entries carry the coverage schema
    for tid, cov in twin.MITRE_TECHNIQUE_CATALOG.items():
        assert "signal_sources" in cov and "min_sources" in cov
        assert "tactic" in cov and isinstance(cov["tactic"], str)


def test_canvas_sigma_view_matches_module_export():
    """sigma_generator._TECHNIQUE_DETECTIONS is the derived canvas-detection view."""
    sg = importlib.import_module("tools.observability_canvas.sigma_generator")
    assert sg._TECHNIQUE_DETECTIONS == mitre_catalog.build_canvas_detections()
    # every detection block is keyed by logsource category with a selection
    for tid, cats in sg._TECHNIQUE_DETECTIONS.items():
        for cat, block in cats.items():
            assert "selection" in block and "condition" in block


def test_obs_sigma_view_matches_module_export():
    """observability.sigma_generator._DETECTIONS is the derived obs template view."""
    sg = importlib.import_module("tools.observability.sigma_generator")
    assert sg._DETECTIONS == mitre_catalog.build_obs_detections()
    for tid, tmpl in sg._DETECTIONS.items():
        assert {"name", "tactic", "logsource", "detection"} <= set(tmpl)


def test_replay_verify_sigma_known_ttps_nonempty():
    """replay_verify still sees the canvas detection keys as sigma-known TTPs."""
    rv = importlib.import_module("tools.observability_canvas.replay_verify")
    known = rv._sigma_known_ttps()
    assert known == set(mitre_catalog.build_canvas_detections().keys())
    assert known  # non-empty


# ---------------------------------------------------------------------------
# Exporter delegation
# ---------------------------------------------------------------------------

RULE_CMD_EXEC = (
    "title: Detect T1059 via OS Log\n"
    "id: cccccccc-0004-0004-0004-000000000004\n"
    "tags:\n"
    "  - attack.t1059\n"
    "  - attack.execution\n"
    "logsource:\n"
    "  category: process_creation\n"
    "  product: windows\n"
    "detection:\n"
    "  selection:\n"
    "    CommandLine|contains:\n"
    "      - cmd.exe\n"
    "      - powershell\n"
    "  condition: selection\n"
    "level: medium\n"
)


def test_to_splunk_delegates_to_exporter():
    """_to_splunk output equals the exporters/ batch_to_spl output (CIM mapped)."""
    sg = importlib.import_module("tools.observability_canvas.sigma_generator")
    from tools.observability_canvas.exporters.splunk import batch_to_spl

    out = sg._to_splunk([RULE_CMD_EXEC])
    assert isinstance(out, str)
    assert out == batch_to_spl([RULE_CMD_EXEC])
    # CIM field mapping is the exporter's signature (crude converter never did this)
    assert "process.command_line" in out


def test_to_elastic_delegates_to_exporter():
    """_to_elastic output equals the exporters/ batch_to_eql output (ECS mapped)."""
    sg = importlib.import_module("tools.observability_canvas.sigma_generator")
    from tools.observability_canvas.exporters.elastic import batch_to_eql

    out = sg._to_elastic([RULE_CMD_EXEC])
    assert isinstance(out, str)
    assert out == batch_to_eql([RULE_CMD_EXEC])
    assert "process.command_line" in out


def test_to_sentinel_delegates_to_exporter():
    """_to_sentinel output equals the exporters/ batch_to_kql output (table mapped)."""
    sg = importlib.import_module("tools.observability_canvas.sigma_generator")
    from tools.observability_canvas.exporters.sentinel import batch_to_kql

    out = sg._to_sentinel([RULE_CMD_EXEC])
    assert isinstance(out, str)
    assert out == batch_to_kql([RULE_CMD_EXEC])
    assert "DeviceProcessEvents" in out


def test_delegation_shim_aware_patch():
    """Patching the exporter module attribute routes through the adapter.

    Proves _to_splunk imports the exporter at call time (shim-aware: patch via
    importlib.import_module + setattr so tools.* and icdev.tools.* stay distinct).
    """
    splunk_mod = importlib.import_module("tools.observability_canvas.exporters.splunk")
    sg = importlib.import_module("tools.observability_canvas.sigma_generator")
    original = splunk_mod.batch_to_spl
    try:
        setattr(splunk_mod, "batch_to_spl", lambda rules: "SENTINEL_DELEGATION_MARKER")
        assert sg._to_splunk([RULE_CMD_EXEC]) == "SENTINEL_DELEGATION_MARKER"
    finally:
        setattr(splunk_mod, "batch_to_spl", original)


def test_generate_sigma_rules_response_envelope_preserved():
    """generate_sigma_rules keeps the string-valued exports envelope contract."""
    sg = importlib.import_module("tools.observability_canvas.sigma_generator")
    graph = {
        "nodes": [
            {"id": "s1", "type": "src-os-log"},
            {
                "id": "b1",
                "type": "cmp-baseline",
                "config_json": {"techniques": [{"id": "T1059", "covered": False}]},
            },
        ],
        "edges": [],
    }
    result = sg.generate_sigma_rules(graph, design_name="unit")
    exports = result["exports"]
    assert set(exports) == {"sigma_yaml", "splunk_spl", "elastic_kql", "sentinel_kql"}
    assert all(isinstance(v, str) for v in exports.values())
    assert result["rule_count"] >= 1
