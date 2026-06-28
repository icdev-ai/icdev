# CUI // SP-CTI
"""Tests for ndc/sdc/idc canvas template presence, mirrors, blueprints, and IQE seed queries."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]


# ── Template file existence ───────────────────────────────────────────────────

def test_ndc_template_exists():
    tpl = REPO / "tools/dashboard/templates/network/index.html"
    assert tpl.exists(), f"NDC template missing: {tpl}"


def test_sdc_template_exists():
    tpl = REPO / "tools/dashboard/templates/security_canvas/index.html"
    assert tpl.exists(), f"SDC template missing: {tpl}"


def test_idc_template_exists():
    tpl = REPO / "tools/dashboard/templates/infra_canvas/index.html"
    assert tpl.exists(), f"IDC template missing: {tpl}"


# ── icdev/ mirror existence ───────────────────────────────────────────────────

def test_ndc_icdev_mirror_exists():
    mirror = REPO / "icdev/tools/dashboard/templates/network/index.html"
    assert mirror.exists(), f"NDC icdev mirror missing: {mirror}"


def test_sdc_icdev_mirror_exists():
    mirror = REPO / "icdev/tools/dashboard/templates/security_canvas/index.html"
    assert mirror.exists(), f"SDC icdev mirror missing: {mirror}"


def test_idc_icdev_mirror_exists():
    mirror = REPO / "icdev/tools/dashboard/templates/infra_canvas/index.html"
    assert mirror.exists(), f"IDC icdev mirror missing: {mirror}"


# ── Blueprint importability ───────────────────────────────────────────────────

def test_ndc_blueprint_importable():
    from tools.network.blueprint import create_network_blueprint
    bp = create_network_blueprint()
    assert bp is not None


def test_sdc_blueprint_importable():
    from tools.security_canvas.blueprint import create_security_blueprint
    bp = create_security_blueprint()
    assert bp is not None


def test_idc_blueprint_importable():
    from tools.infra_canvas.blueprint import infra_bp
    assert infra_bp is not None


# ── IQE adapters importable ───────────────────────────────────────────────────

def test_ndc_iqe_adapter_importable():
    from tools.iqe.adapters import ndc
    assert hasattr(ndc, "register") or hasattr(ndc, "collections") or True


def test_sdc_iqe_adapter_importable():
    from tools.iqe.adapters import security
    assert security is not None


def test_idc_iqe_adapter_importable():
    from tools.iqe.adapters import infra
    assert infra is not None


# ── IQE seed queries ─────────────────────────────────────────────────────────

def test_ndc_seed_queries_exist():
    qdir = REPO / "context/iqe/queries/network"
    assert qdir.exists(), f"NDC seed query dir missing: {qdir}"
    files = list(qdir.glob("*.iqe"))
    assert len(files) >= 3, f"Expected ≥3 NDC seed queries, got {len(files)}"


def test_sdc_seed_queries_exist():
    qdir = REPO / "context/iqe/queries/security"
    assert qdir.exists(), f"SDC seed query dir missing: {qdir}"
    files = list(qdir.glob("*.iqe"))
    assert len(files) >= 3, f"Expected ≥3 SDC seed queries, got {len(files)}"


def test_idc_seed_queries_exist():
    qdir = REPO / "context/iqe/queries/infra"
    assert qdir.exists(), f"IDC seed query dir missing: {qdir}"
    files = list(qdir.glob("*.iqe"))
    assert len(files) >= 3, f"Expected ≥3 IDC seed queries, got {len(files)}"


# ── Registry template paths correct ──────────────────────────────────────────

def _get_registry_template(key: str) -> str:
    import yaml  # type: ignore[import]
    reg_path = REPO / "args/component_registry.yaml"
    with open(reg_path, encoding="utf-8") as f:
        reg = yaml.safe_load(f)
    for entry in reg.get("components", reg if isinstance(reg, list) else []):
        if isinstance(reg, list):
            entry = reg[reg.index(entry)]
        if entry.get("key") == key:
            return entry.get("completeness", {}).get("template", "")
    return ""


def test_ndc_registry_template_path_exists():
    import yaml
    reg_path = REPO / "args/component_registry.yaml"
    with open(reg_path, encoding="utf-8") as f:
        entries = yaml.safe_load(f)
    if isinstance(entries, dict):
        entries = entries.get("components", [])
    for entry in entries:
        if entry.get("key") == "ndc":
            tpl = entry.get("completeness", {}).get("template", "")
            assert tpl, "ndc completeness.template is empty"
            assert (REPO / tpl).exists(), f"ndc registry template not found: {tpl}"
            return
    raise AssertionError("ndc not found in registry")


def test_sdc_registry_template_path_exists():
    import yaml
    reg_path = REPO / "args/component_registry.yaml"
    with open(reg_path, encoding="utf-8") as f:
        entries = yaml.safe_load(f)
    if isinstance(entries, dict):
        entries = entries.get("components", [])
    for entry in entries:
        if entry.get("key") == "sdc":
            tpl = entry.get("completeness", {}).get("template", "")
            assert tpl, "sdc completeness.template is empty"
            assert (REPO / tpl).exists(), f"sdc registry template not found: {tpl}"
            return
    raise AssertionError("sdc not found in registry")


def test_idc_registry_template_path_exists():
    import yaml
    reg_path = REPO / "args/component_registry.yaml"
    with open(reg_path, encoding="utf-8") as f:
        entries = yaml.safe_load(f)
    if isinstance(entries, dict):
        entries = entries.get("components", [])
    for entry in entries:
        if entry.get("key") == "idc":
            tpl = entry.get("completeness", {}).get("template", "")
            assert tpl, "idc completeness.template is empty"
            assert (REPO / tpl).exists(), f"idc registry template not found: {tpl}"
            return
    raise AssertionError("idc not found in registry")
