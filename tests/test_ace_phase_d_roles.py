# CUI // SP-CTI
"""Tests for ACE Phase D canvas-specific role expansion (role_generator.py)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── role_generator public API ─────────────────────────────────────────────────

def test_generate_role_returns_required_fields():
    """generate_role() returns a dict with all required YAML fields."""
    from tools.ace.role_generator import generate_role
    spec = generate_role("idc", "Infrastructure Design Canvas", "Cloud, IaC, Terraform")
    assert spec["role_id"] == "idc_analyst"
    assert spec["display_name"] == "Infrastructure Design Canvas Analyst"
    assert spec["canvas"] == "idc"
    assert isinstance(spec["capabilities"] if "capabilities" in spec else spec["personality"]["capabilities"], list)
    assert len(spec["personality"]["capabilities"]) >= 2
    assert "communication" in spec
    assert "listen_topics" in spec["communication"]
    assert "rules" in spec
    assert len(spec["rules"]) >= 1


def test_generate_role_capability_routing_network():
    """Network-keyword canvases get network_analysis capabilities."""
    from tools.ace.role_generator import generate_role
    spec = generate_role("ndc", "Network Design Canvas", "Topology and routing")
    caps = spec["personality"]["capabilities"]
    assert "network_analysis" in caps or "topology_review" in caps


def test_generate_role_capability_routing_security():
    """Security-keyword canvases get security_assessment capabilities."""
    from tools.ace.role_generator import generate_role
    spec = generate_role("dsoc", "DDoS Security Ops Canvas", "DDoS and security operations")
    caps = spec["personality"]["capabilities"]
    assert "security_assessment" in caps or "vulnerability_triage" in caps


def test_generate_role_capability_routing_ai():
    """AI/ML-keyword canvases get model_analysis capabilities."""
    from tools.ace.role_generator import generate_role
    spec = generate_role("aimc", "AI/ML Canvas", "AI/ML model catalog")
    caps = spec["personality"]["capabilities"]
    assert "model_analysis" in caps or "training_review" in caps


def test_generate_role_idempotent():
    """generate_role() called twice with same args returns identical role_id."""
    from tools.ace.role_generator import generate_role
    s1 = generate_role("qdc", "Quality Design Canvas", "Test strategy")
    s2 = generate_role("qdc", "Quality Design Canvas", "Test strategy")
    assert s1["role_id"] == s2["role_id"] == "qdc_analyst"


def test_no_duplicate_role_ids_on_disk():
    """Every generated role YAML has a unique role_id."""
    import yaml
    roles_dir = Path(__file__).parents[1] / "args" / "ace" / "roles"
    seen: set[str] = set()
    for f in roles_dir.glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        role_id = data.get("role_id", "")
        assert role_id not in seen, f"Duplicate role_id {role_id!r} in {f.name}"
        if role_id:
            seen.add(role_id)


def test_generated_roles_have_canvas_field():
    """Generated roles (source=role_generator) all have canvas: field set."""
    import yaml
    roles_dir = Path(__file__).parents[1] / "args" / "ace" / "roles"
    bad = []
    for f in roles_dir.glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if data.get("source") == "role_generator":
            if not data.get("canvas"):
                bad.append(f.name)
    assert bad == [], f"Generated roles missing canvas field: {bad}"


def test_at_least_30_canvas_roles_exist():
    """At least 30 canvas-specific roles exist in args/ace/roles/."""
    import yaml
    roles_dir = Path(__file__).parents[1] / "args" / "ace" / "roles"
    canvas_roles = []
    for f in roles_dir.glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if data.get("canvas"):
            canvas_roles.append(data["canvas"])
    assert len(canvas_roles) >= 30, f"Only {len(canvas_roles)} canvas roles found"


def test_zero_gaps_after_generation():
    """canvas_role_gap.detect_gaps() reports 0 gaps after Phase D generation."""
    from tools.ace.canvas_role_gap import detect_gaps
    result = detect_gaps()
    assert result["gap_count"] == 0, (
        f"{result['gap_count']} gaps remain: "
        + ", ".join(g["canvas_key"] for g in result["gaps"])
    )


def test_fill_gaps_dry_run_does_not_write(tmp_path):
    """fill_gaps(dry_run=True) does not write any files to the output dir."""
    from tools.ace.role_generator import fill_gaps
    result = fill_gaps(dry_run=True, out_dir=tmp_path)
    written_files = list(tmp_path.glob("*.yaml"))
    assert written_files == [], f"dry_run wrote files: {written_files}"
    # But generated list may be non-empty (would-be list)
    assert "generated" in result


def test_fill_gaps_writes_to_custom_dir(tmp_path):
    """fill_gaps writes role YAMLs to a custom out_dir."""
    import yaml
    from tools.ace.role_generator import fill_gaps
    result = fill_gaps(dry_run=False, out_dir=tmp_path)
    written = list(tmp_path.glob("*.yaml"))
    # If all gaps were already filled, nothing is written to tmp_path
    # (skipped). But if there are gaps they should be there.
    total_expected = len(result["generated"])
    assert len(written) == total_expected
    for f in written:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        assert data.get("role_id"), f"{f.name} missing role_id"
        assert data.get("canvas"), f"{f.name} missing canvas"


def test_role_generator_importable():
    """role_generator module is importable and exposes expected public API."""
    import tools.ace.role_generator as rg
    assert callable(rg.generate_role)
    assert callable(rg.write_role)
    assert callable(rg.fill_gaps)
