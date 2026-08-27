# CUI // SP-CTI
"""xit-decl-04 -- icdev.core.sensitivity: the ONE sensitivity seam."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from icdev.core import domain as core_domain
from icdev.core import sensitivity as sens

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def it_domain():
    return core_domain.load_domain(REPO_ROOT / "icdev_domain.yaml")


@pytest.fixture
def ft_domain(tmp_path):
    p = tmp_path / "icdev_domain.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "domain": {"key": "ft", "name": "ICDEV[FT]", "env_prefix": "FIN"},
        "db": {"databases": ["icdev_ft"]},
        "sensitivity": {
            "column": "classification", "default": "public",
            "order": ["public", "internal", "pii", "mnpi", "account_secret"],
            "egress_restricted": ["pii", "mnpi", "account_secret"], "levels": [],
        },
    }), encoding="utf-8")
    return core_domain.load_domain(p)


def test_it_labels_reproduce_the_runtime_ladder(it_domain):
    assert sens.labels(it_domain) == ("public", "unclassified", "cui", "eci", "secret", "top_secret", "top_secret_sci")
    assert sens.label_column(it_domain) == "classification"
    assert sens.default_label(it_domain) == "public"
    assert sens.rank("TOP SECRET//SCI", it_domain) == 6
    assert sens.rank("CUI", it_domain) == 2
    assert sens.dominates("SECRET", "CUI", it_domain) is True
    assert sens.dominates("CUI", "SECRET", it_domain) is False
    assert sens.dominates("CUI", "CUI", it_domain) is True


def test_unknown_labels_fail_closed(it_domain):
    assert sens.rank("ITAR", it_domain) is None
    assert sens.dominates("TOP SECRET//SCI", "ITAR", it_domain) is False
    assert sens.is_egress_restricted("ITAR", it_domain) is True  # unknown -> restricted
    assert sens.is_egress_restricted("public", it_domain) is False
    assert sens.is_egress_restricted("cui", it_domain) is True
    assert sens.is_egress_restricted(None, it_domain) is False  # default label is public


def test_ft_declares_its_own_order_and_egress(ft_domain):
    assert sens.labels(ft_domain) == ("public", "internal", "pii", "mnpi", "account_secret")
    assert sens.is_egress_restricted("MNPI", ft_domain) is True
    assert sens.is_egress_restricted("internal", ft_domain) is False
    assert sens.dominates("mnpi", "pii", ft_domain) is True
    assert sens.describe(ft_domain)["domain"] == "ft"


def test_normalise_folds_separators():
    assert sens.normalise("TOP SECRET//SCI") == "top_secret_sci"
    assert sens.normalise(" Cui ") == "cui"
    assert sens.normalise(None) == ""


def test_rls_exempt_tables_reads_the_generated_manifests(it_domain, monkeypatch):
    sens._manifest_exempt.cache_clear()
    assert sens.rls_exempt_tables(it_domain) == frozenset()  # nothing exempt at adoption
    monkeypatch.setenv("ICDEV_RLS_EXEMPT_DISABLE", "1")
    assert sens.rls_exempt_tables(it_domain) == frozenset()


def test_module_imports_nothing_from_tools():
    """Read from the INSTALLED distribution since xcore-cut-02 — see the note on
    tests/core/test_domain_declaration.py::test_core_package_is_stdlib_plus_yaml_only."""
    from tools.workflow.core_api_manifest import module_source

    src = module_source("icdev.core.sensitivity").read_text(encoding="utf-8")
    assert "from tools." not in src and "import tools" not in src
