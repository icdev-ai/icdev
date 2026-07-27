#!/usr/bin/env python3
"""The Helm chart must be able to run what it deploys. CUI // SP-CTI.

The chart's platform database used `postgresql:15-hardened` and the word
"pgvector" appeared nowhere in `deploy/helm/`. ICDEV stores embeddings in a
`vector` column, so RAG and the knowledge graph could not work on a
Helm-deployed cluster at all — `CREATE EXTENSION vector` fails and every
embedding write raises.

`values.yaml` also advertised `platformDb.type: internal` while `Chart.yaml`
claimed `appVersion: 21.0.0`, a version the package has never had.

These are the same two failure shapes seen throughout packaging this week:
config that promises a capability the deployment cannot deliver, and a version
number that answers "which build is this?" incorrectly.
"""
from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

CHART_DIR = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "helm"


def _values() -> dict:
    return yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))


def _chart() -> dict:
    return yaml.safe_load((CHART_DIR / "Chart.yaml").read_text(encoding="utf-8"))


def _deployment_tpl() -> str:
    return (CHART_DIR / "templates" / "deployment.yaml").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# RAG needs pgvector
# --------------------------------------------------------------------------- #


def test_rag_values_block_exists():
    v = _values()
    assert "rag" in v, "chart cannot express whether RAG is on"
    assert {"enabled", "vectorImage", "embeddingDim"} <= set(v["rag"])


def test_rag_uses_a_pgvector_image():
    """A stock postgres image cannot host the RAG schema."""
    assert "pgvector" in _values()["rag"]["vectorImage"]


def test_database_image_is_conditional_on_rag():
    """RAG on must select the vector image; RAG off keeps the hardened base."""
    tpl = _deployment_tpl()
    assert ".Values.rag.enabled" in tpl
    assert ".Values.rag.vectorImage" in tpl


def test_the_vector_extension_is_created():
    """Without CREATE EXTENSION the column type does not exist, so the first
    migration fails rather than degrading."""
    tpl = _deployment_tpl()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in tpl


def test_extension_bootstrap_is_idempotent():
    """Re-deploying onto an existing volume must be a no-op, not an error."""
    assert "IF NOT EXISTS" in _deployment_tpl()


def test_embedding_dimension_is_the_airgap_safe_default():
    """768 matches nomic / gemini-004 / ibm-slate; 1536 matches only cloud OpenAI."""
    assert _values()["rag"]["embeddingDim"] == 768


@pytest.mark.parametrize("ref", ["enabled", "vectorImage"])
def test_every_rag_value_referenced_by_a_template_exists(ref):
    """A template referencing a missing value renders empty and fails at apply."""
    assert ref in _values()["rag"]


# --------------------------------------------------------------------------- #
# Version truth
# --------------------------------------------------------------------------- #


def test_chart_appversion_matches_the_package():
    """It had drifted to 21.0.0 while the package was 1.2.42.

    release.py bumps this alongside pyproject/_version/brand.yaml so the two
    cannot separate again.
    """
    pkg = (CHART_DIR.parents[1] / "icdev" / "_version.py").read_text(encoding="utf-8")
    version = re.search(r'__version__ = "([^"]+)"', pkg).group(1)
    assert _chart()["appVersion"] == version


def test_chart_version_is_independent_of_appversion():
    """`version:` is the CHART's revision — it moves when templates change, not
    when the app does. Bumping it on every app release would be a lie."""
    from tools.installer import release as rel

    files = [f for f, _p, _fmt in rel.VERSION_FILES]
    assert "deploy/helm/Chart.yaml" in files
    _v, pattern, _fmt = next(x for x in rel.VERSION_FILES
                             if x[0] == "deploy/helm/Chart.yaml")
    assert "appVersion" in pattern


def test_release_bumps_the_chart(tmp_path):
    """`release.py --bump` must reach the chart, not leave it to a human."""
    from tools.installer import release as rel

    assert rel.read_versions().get("deploy/helm/Chart.yaml")


# --------------------------------------------------------------------------- #
# Chart sanity
# --------------------------------------------------------------------------- #


def test_values_and_chart_parse_as_yaml():
    assert _values() and _chart()


def test_template_braces_are_balanced():
    tpl = _deployment_tpl()
    assert tpl.count("{{") == tpl.count("}}")


def test_platformdb_internal_has_a_statefulset():
    """values.yaml advertises `type: internal (StatefulSet)` — it must exist."""
    assert "kind: StatefulSet" in _deployment_tpl()
