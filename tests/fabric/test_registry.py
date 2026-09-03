# CUI // SP-CTI
"""Fabric registry (rmf-fab-01).

Pins the three rules the module states: a classification is a LABEL and a
banner is refused; the in-repo file is a synthetic fixture that names no real
fabric and never reaches the posture seam unasked; a private overlay OUTSIDE
the repository overrides per fabric. Plus the crosswalk key defect found on the
way: ``IL_KEYS`` named columns the crosswalk data does not carry, so every
"required controls for IL5" answer was an empty list.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from icdev.core.paths import repo_root
from tools.fabric import registry as R

REPO = repo_root()
FIXTURE = REPO / "args" / "fabric_registry.yaml"


def _write(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _base(tmp_path: Path, **overrides) -> Path:
    doc = {
        "schema_version": 1,
        "fabrics": [
            {
                "key": "t-one",
                "display_name": "Test One",
                "classification": "cui",
                "impact_level": "IL5",
                "authoritative_inventory_source": "netbox",
                "discovery_adapters": ["ping"],
            }
        ],
    }
    doc.update(overrides)
    return _write(tmp_path / "base.yaml", doc)


@pytest.fixture(autouse=True)
def _no_ambient_overlay(monkeypatch):
    monkeypatch.delenv(R.OVERLAY_ENV, raising=False)


# ---------------------------------------------------------------------------
# 1. The in-repo fixture loads, synthetic, and names no real fabric
# ---------------------------------------------------------------------------

def test_in_repo_fixture_loads_with_synthetic_fabrics():
    reg = R.load()
    assert len(reg.fabrics) >= 2
    assert all(f["synthetic"] is True for f in reg.fabrics)
    assert all(f["source"] == R.SOURCE_FIXTURE for f in reg.fabrics)
    assert reg.source["base_is_fixture"] is True
    assert reg.source["overlay_active"] is False
    profiles = R.load_profiles()
    for f in reg.fabrics:
        assert f["classification"] in profiles
        # The banner is DERIVED from the profile, never a stored classification.
        assert f["banner"] == (profiles[f["classification"]].get("banner") or {}).get("text") or f["banner"] is None
        assert f["impact_level"] in {"IL2", "IL4", "IL5", "IL6"}


def test_no_real_fabric_name_in_repo():
    doc = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert doc.get("fixture") == "synthetic"
    for f in doc["fabrics"]:
        assert f["key"].startswith("fx-"), f["key"]
        assert "Fixture" in f["display_name"], f["display_name"]


def test_an_in_repo_registry_without_the_fixture_marker_is_refused(tmp_path):
    """A base file INSIDE the repo must declare itself a fixture; outside it need not."""
    # Outside the repo: a real registry, fabrics are real.
    reg = R.load(_base(tmp_path), overlay_path="")
    assert reg.fabrics[0]["synthetic"] is False
    # Inside the repo, without the marker: refused by name.
    inside = REPO / ".tmp" / "fabric-registry-test-unmarked.yaml"
    inside.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write(inside, {"schema_version": 1, "fabrics": []})
        with pytest.raises(R.FabricRegistryError) as ei:
            R.load(inside, overlay_path="")
        assert {r["reason"] for r in ei.value.refusals} == {"in_repo_registry_must_be_fixture"}
    finally:
        inside.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 2. A BANNER is refused; the hint names the label
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, hint_label",
    [
        ("CUI // SP-CTI", "cui_sp_cti"),
        ("SECRET // NOFORN", "secret"),
        ("CUI", "cui"),
        ("FOUO", "fouo"),
    ],
)
def test_banner_is_refused_with_the_label_it_should_have_been(tmp_path, value, hint_label):
    base = _base(tmp_path)
    doc = yaml.safe_load(base.read_text(encoding="utf-8"))
    doc["fabrics"][0]["classification"] = value
    _write(base, doc)
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load(base, overlay_path="")
    refusals = [r for r in ei.value.refusals if r["field"] == "classification"]
    assert refusals and refusals[0]["reason"] == "banner_not_label"
    assert hint_label in refusals[0]["hint"]


def test_unknown_label_and_missing_label_are_distinct_from_a_banner(tmp_path):
    base = _base(tmp_path)
    doc = yaml.safe_load(base.read_text(encoding="utf-8"))
    doc["fabrics"][0]["classification"] = "restricted"
    doc["fabrics"].append({**doc["fabrics"][0], "key": "t-two", "classification": None})
    _write(base, doc)
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load(base, overlay_path="")
    reasons = sorted(r["reason"] for r in ei.value.refusals)
    assert reasons == ["classification_missing", "unknown_label"]


def test_impact_level_the_profile_does_not_admit_is_refused(tmp_path):
    base = _base(tmp_path)
    doc = yaml.safe_load(base.read_text(encoding="utf-8"))
    doc["fabrics"][0].update(classification="public", impact_level="IL5")
    doc["fabrics"].append({**doc["fabrics"][0], "key": "t-two", "classification": "cui", "impact_level": "IL9"})
    _write(base, doc)
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load(base, overlay_path="")
    reasons = sorted(r["reason"] for r in ei.value.refusals)
    assert reasons == ["impact_level_not_declared_by_domain", "impact_level_not_in_profile"]


def test_fixture_and_drawing_are_never_authoritative_and_unknown_adapters_refuse(tmp_path):
    base = _base(tmp_path)
    doc = yaml.safe_load(base.read_text(encoding="utf-8"))
    doc["fabrics"][0].update(authoritative_inventory_source="synthetic", discovery_adapters=["nmap"])
    _write(base, doc)
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load(base, overlay_path="")
    reasons = sorted(r["reason"] for r in ei.value.refusals)
    assert reasons == ["discovery_adapter_unknown", "inventory_source_unknown"]


# ---------------------------------------------------------------------------
# 3. The overlay: outside the repo, overrides PER FABRIC
# ---------------------------------------------------------------------------

def test_overlay_outside_the_repo_overrides_per_fabric(tmp_path, monkeypatch):
    overlay = _write(
        tmp_path / "overlay.yaml",
        {
            "schema_version": 1,
            "fabrics": [
                # Same key as a fixture entry: REPLACED whole, and real.
                {
                    "key": "fx-bravo",
                    "display_name": "Overlay Bravo",
                    "classification": "secret",
                    "impact_level": "IL6",
                    "authoritative_inventory_source": "csv",
                    "discovery_adapters": ["csv"],
                },
                # A new key: added.
                {
                    "key": "t-real",
                    "display_name": "Overlay Real",
                    "classification": "cui_sp_cti",
                    "impact_level": "IL4",
                    "authoritative_inventory_source": None,
                    "discovery_adapters": [],
                },
            ],
            "drop": ["fx-charlie"],
        },
    )
    monkeypatch.setenv(R.OVERLAY_ENV, str(overlay))
    reg = R.load()
    by_key = {f["key"]: f for f in reg.fabrics}

    assert reg.source["overlay_active"] is True
    assert by_key["fx-bravo"]["classification"] == "secret"
    assert by_key["fx-bravo"]["synthetic"] is False
    assert by_key["fx-bravo"]["source"] == R.SOURCE_OVERLAY
    assert by_key["t-real"]["synthetic"] is False
    assert "fx-charlie" not in by_key
    # Fixture entries the overlay did not name are untouched, and still synthetic.
    assert by_key["fx-alpha"]["classification"] == "public"
    assert by_key["fx-alpha"]["synthetic"] is True

    # And the posture seam now sees the real ones ONLY.
    seam = R.load_registry()
    assert sorted(f["key"] for f in seam["fabrics"]) == ["fx-bravo", "t-real"]
    assert seam["synthetic_excluded"] == 1
    assert seam["reason"] is None


def test_overlay_inside_the_repo_is_refused_before_it_is_read():
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load(overlay_path=str(FIXTURE))
    assert ei.value.refusals[0]["reason"] == "overlay_inside_repo"


def test_overlay_named_by_env_but_missing_is_refused_not_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv(R.OVERLAY_ENV, str(tmp_path / "nope.yaml"))
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load()
    assert ei.value.refusals[0]["reason"] == "overlay_missing"


def test_overlay_fabric_carrying_a_banner_is_refused_too(tmp_path):
    overlay = _write(
        tmp_path / "overlay.yaml",
        {"fabrics": [{"key": "t", "display_name": "T", "classification": "ITAR CONTROLLED", "impact_level": "IL4"}]},
    )
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load(overlay_path=str(overlay))
    assert ei.value.refusals[0]["reason"] == "banner_not_label"
    assert "itar" in ei.value.refusals[0]["hint"]


# ---------------------------------------------------------------------------
# 4. The posture seam excludes fixtures by default and SAYS so
# ---------------------------------------------------------------------------

def test_posture_seam_excludes_synthetic_by_default_and_reports_the_count():
    seam = R.load_registry()
    assert seam["fabrics"] == []
    assert seam["synthetic_excluded"] >= 2
    assert "synthetic" in seam["reason"] and R.OVERLAY_ENV in seam["reason"]
    assert R.load_registry(include_synthetic=True)["synthetic_excluded"] == 0
    assert len(R.load_registry(include_synthetic=True)["fabrics"]) == seam["fabric_count_declared"]


def test_posture_load_fabrics_consumes_this_registry_and_carries_the_reason():
    from tools.fabric import posture as P

    fabrics, meta = P.load_fabrics()
    assert meta["entry_point"] == "load_registry"
    assert fabrics == []
    assert meta["state"] == "declared_empty"
    assert "synthetic" in (meta.get("reason") or "")
    assert meta.get("synthetic_excluded", 0) >= 2


# ---------------------------------------------------------------------------
# 5. Traversal is declared separately; direction is derived; downward needs a guard
# ---------------------------------------------------------------------------

def test_traversal_direction_is_derived_from_sensitivity_and_downward_needs_a_guard(tmp_path):
    reg = R.load()
    by = {(t["from"], t["to"]): t for t in reg.traversals}
    assert by[("fx-bravo", "fx-charlie")]["direction"] == "upward"
    assert by[("fx-charlie", "fx-bravo")]["direction"] == "downward"
    assert by[("fx-charlie", "fx-bravo")]["guard"]
    # Nothing on a fabric says where it may traverse to.
    assert not any("traversals" in f or "peers" in f for f in reg.fabrics)

    base = _base(tmp_path)
    doc = yaml.safe_load(base.read_text(encoding="utf-8"))
    doc["fabrics"].append({**doc["fabrics"][0], "key": "t-low", "classification": "public", "impact_level": "IL2"})
    doc["traversals"] = [
        {"from": "t-one", "to": "t-low", "kind": "direct"},          # downward, unguarded
        {"from": "t-one", "to": "t-none", "kind": "direct"},         # undeclared endpoint
        {"from": "t-one", "to": "t-one", "kind": "data_diode"},      # self
    ]
    _write(base, doc)
    with pytest.raises(R.FabricRegistryError) as ei:
        R.load(base, overlay_path="")
    reasons = sorted(r["reason"] for r in ei.value.refusals)
    assert reasons == ["downward_traversal_unguarded", "traversal_endpoint_undeclared", "traversal_to_self"]


# ---------------------------------------------------------------------------
# 6. Required controls reuse the crosswalk, and IL2 is None never 0
# ---------------------------------------------------------------------------

def test_required_controls_reads_the_crosswalk_by_impact_level():
    reg = R.load()
    il5 = R.required_controls(reg.get("fx-bravo"))
    assert il5["impact_level"] == "IL5"
    assert il5["state"] == "declared"
    assert il5["count"] and il5["count"] == len(il5["control_ids"])

    il2 = R.required_controls(reg.get("fx-alpha"))
    assert il2["state"] == "no_crosswalk_for_impact_level"
    assert il2["count"] is None and il2["control_ids"] is None


def test_crosswalk_il_keys_name_columns_the_data_actually_carries():
    """Pre-existing defect: IL_KEYS said `il4`, the data says `il4_required`.

    ``get_controls_for_impact_level`` therefore returned [] for every level since
    it was written, and its own test accepted that. Every key must appear on at
    least one crosswalk entry, and every level must answer with controls.
    """
    from tools.compliance import crosswalk_engine as C

    crosswalk = C.load_crosswalk()
    present = set().union(*(e.keys() for e in crosswalk))
    for il, key in C.IL_KEYS.items():
        assert key in present, f"{il} -> {key!r} names a column no crosswalk entry carries"
        assert C.get_controls_for_impact_level(il), f"{il} answers with no controls"


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------

def test_cli_check_and_json(capsys):
    assert R.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert R.main(["--json", "--include-synthetic"]) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["fabrics"] and all(f["synthetic"] for f in payload["fabrics"])
    assert all("required_controls" in f for f in payload["fabrics"])
    assert R.main(["--json", "--overlay", str(FIXTURE)]) == 1
    assert json.loads(capsys.readouterr().out)["refusals"][0]["reason"] == "overlay_inside_repo"
