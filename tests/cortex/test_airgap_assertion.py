# CUI // SP-CTI
"""Tests for the Cortex air-gap startup assertion + behavior config (ctx-core-03).

Covers:
- assert_airgap_ready() against tmp llm_config fixtures (complete, broken,
  missing entry, cloud-only variant, stub config).
- The import-time invocation in tools/cortex/api.py (reload with a broken
  config raises CortexAirgapError).
- airgap_exclusions() / ICDEV_AIRGAP / CortexContext.air_gap forcing
  local-only resolution through LLMRouter.invoke(exclude_model_ids=...).
- load_cortex_config(): file values consumed, defaults on missing file,
  deep-merge, and the checked-in args/cortex_config.yaml shape.
"""
import importlib
import os

import pytest
import yaml

from tools.cortex import CortexContext, classify, complete, extract
from tools.cortex.config import (
    CORTEX_ROUTING_FUNCTIONS,
    CortexAirgapError,
    airgap_exclusions,
    assert_airgap_ready,
    load_cortex_config,
    resolve_cortex_config_path,
)
from tools.llm.provider import LLMResponse


def _base_llm_config() -> dict:
    """Minimal llm_config shape: one local-tier, one cloud-variant, one API."""
    return {
        "providers": {
            "local_provider": {"type": "ollama", "base_url": "http://127.0.0.1:11434"},
            "cloud_provider": {"type": "ollama", "api_key_env": "CLOUD_PROVIDER_KEY"},
            "api_provider": {"type": "anthropic", "api_key_env": "API_PROVIDER_KEY"},
        },
        "models": {
            "worker-local": {"provider": "local_provider", "model_id": "worker:latest"},
            "worker-cloud": {"provider": "cloud_provider", "model_id": "worker-xl:cloud"},
            "frontier": {"provider": "api_provider", "model_id": "frontier-9000"},
        },
        "routing": {
            fn: {"chain": ["frontier", "worker-cloud", "worker-local"], "effort": "low"}
            for fn in CORTEX_ROUTING_FUNCTIONS
        },
    }


def _write_config(tmp_path, config, name="llm_config.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clean_airgap_env(monkeypatch):
    """Tests own ICDEV_AIRGAP; never inherit it from the dev environment."""
    monkeypatch.delenv("ICDEV_AIRGAP", raising=False)


# ---------------------------------------------------------------------------
# assert_airgap_ready()
# ---------------------------------------------------------------------------
def test_passes_with_complete_config(tmp_path):
    path = _write_config(tmp_path, _base_llm_config())
    assert assert_airgap_ready(path) is None


def test_repo_llm_config_is_airgap_ready():
    # The checked-in args/llm_config.yaml must satisfy the day-one invariant.
    assert_airgap_ready()


def test_raises_when_local_model_removed_from_one_chain(tmp_path):
    config = _base_llm_config()
    config["routing"]["cortex_extract"]["chain"] = ["frontier", "worker-cloud"]
    path = _write_config(tmp_path, config)
    with pytest.raises(CortexAirgapError) as excinfo:
        assert_airgap_ready(path)
    message = str(excinfo.value)
    assert "cortex_extract" in message
    assert str(path) in message
    # Healthy chains are not reported as findings.
    assert "  - cortex_complete" not in message


def test_raises_when_routing_entry_missing(tmp_path):
    config = _base_llm_config()
    del config["routing"]["cortex_analyst"]
    path = _write_config(tmp_path, config)
    with pytest.raises(CortexAirgapError) as excinfo:
        assert_airgap_ready(path)
    assert "cortex_analyst" in str(excinfo.value)
    assert "no routing entry" in str(excinfo.value)


def test_lists_every_missing_entry(tmp_path):
    config = _base_llm_config()
    config["routing"]["cortex_classify"]["chain"] = ["frontier"]
    del config["routing"]["cortex_search_rewrite"]
    path = _write_config(tmp_path, config)
    with pytest.raises(CortexAirgapError) as excinfo:
        assert_airgap_ready(path)
    message = str(excinfo.value)
    assert "cortex_classify" in message
    assert "cortex_search_rewrite" in message


def test_cloud_variant_of_local_provider_type_does_not_count(tmp_path):
    # Same provider *type* but with api_key_env => egress, not air-gap safe.
    config = _base_llm_config()
    config["routing"]["cortex_complete"]["chain"] = ["worker-cloud"]
    path = _write_config(tmp_path, config)
    with pytest.raises(CortexAirgapError) as excinfo:
        assert_airgap_ready(path)
    assert "cortex_complete" in str(excinfo.value)


def test_stub_config_without_routing_is_skipped(tmp_path):
    # Minimal/stub environments have nothing meaningful to validate.
    path = _write_config(tmp_path, {"providers": {}})
    assert assert_airgap_ready(path) is None


def test_missing_config_file_is_skipped(tmp_path):
    assert assert_airgap_ready(tmp_path / "does_not_exist.yaml") is None


def test_import_time_assertion_fires_on_broken_config(tmp_path, monkeypatch):
    config = _base_llm_config()
    config["routing"]["cortex_complete"]["chain"] = ["frontier"]
    bad_path = _write_config(tmp_path, config)
    module = importlib.import_module("tools.cortex.api")
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(bad_path))
    try:
        with pytest.raises(CortexAirgapError):
            importlib.reload(module)
    finally:
        monkeypatch.delenv("ICDEV_LLM_CONFIG", raising=False)
        importlib.reload(module)  # restore a cleanly-imported module


def test_routing_functions_pin_api_constants():
    api = importlib.import_module("tools.cortex.api")
    assert CORTEX_ROUTING_FUNCTIONS == (
        api.CORTEX_COMPLETE_FUNCTION,
        api.CORTEX_CLASSIFY_FUNCTION,
        api.CORTEX_EXTRACT_FUNCTION,
        api.CORTEX_SEARCH_REWRITE_FUNCTION,
        api.CORTEX_ANALYST_FUNCTION,
        # ctx-trust-01 added cortex_summarize to CORTEX_ROUTING_FUNCTIONS but
        # left this pin behind, so the test that exists to stop the two drifting
        # was itself red on main. That is the whole point of a pin: update both.
        api.CORTEX_SUMMARIZE_FUNCTION,
    )


# ---------------------------------------------------------------------------
# airgap_exclusions() — local-only resolution
# ---------------------------------------------------------------------------
def test_no_exclusions_when_airgap_inactive(tmp_path):
    path = _write_config(tmp_path, _base_llm_config())
    assert airgap_exclusions(None, config_path=path) is None
    assert airgap_exclusions(CortexContext(), config_path=path) is None


def test_env_var_forces_exclusions(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_AIRGAP", "1")
    path = _write_config(tmp_path, _base_llm_config())
    exclusions = airgap_exclusions(None, config_path=path)
    assert exclusions == ["frontier-9000", "worker-xl:cloud"]


def test_context_air_gap_forces_exclusions(tmp_path):
    path = _write_config(tmp_path, _base_llm_config())
    ctx = CortexContext(air_gap=True)
    exclusions = airgap_exclusions(ctx, config_path=path)
    assert "frontier-9000" in exclusions
    assert "worker:latest" not in exclusions


def test_llm_config_is_parsed_once_across_repeated_exclusion_lookups(tmp_path, monkeypatch):
    # airgap_exclusions() runs on every _invoke when air-gapped; the config
    # read behind it is mtime-cached, so N lookups parse the YAML once.
    from tools.cortex import config as cortex_config

    monkeypatch.setenv("ICDEV_AIRGAP", "1")
    path = _write_config(tmp_path, _base_llm_config())
    parses = []
    real_load_yaml = cortex_config._load_yaml
    monkeypatch.setattr(
        cortex_config,
        "_load_yaml",
        lambda p: (parses.append(str(p)), real_load_yaml(p))[1],
    )

    first = cortex_config.airgap_exclusions(None, config_path=path)
    for _ in range(4):
        assert cortex_config.airgap_exclusions(None, config_path=path) == first

    assert parses.count(str(path)) == 1


def test_llm_config_cache_refresh_and_mtime_invalidation(tmp_path, monkeypatch):
    from tools.cortex import config as cortex_config

    path = _write_config(tmp_path, _base_llm_config())
    assert "frontier" in cortex_config._load_llm_config(path)["models"]

    changed = _base_llm_config()
    del changed["models"]["frontier"]
    path.write_text(yaml.safe_dump(changed), encoding="utf-8")
    # refresh= is the escape hatch that ignores an unchanged mtime.
    os.utime(path, (1_700_000_000, 1_700_000_000))
    assert "frontier" not in cortex_config._load_llm_config(path, refresh=True)["models"]

    # A later mtime invalidates the cache without an explicit refresh.
    path.write_text(yaml.safe_dump(_base_llm_config()), encoding="utf-8")
    os.utime(path, (1_700_000_060, 1_700_000_060))
    assert "frontier" in cortex_config._load_llm_config(path)["models"]


def test_model_id_also_served_locally_is_never_excluded(tmp_path):
    config = _base_llm_config()
    # The same model_id resolves through both a cloud and a local entry.
    config["models"]["worker-mirror"] = {
        "provider": "cloud_provider",
        "model_id": "worker:latest",
    }
    path = _write_config(tmp_path, config)
    exclusions = airgap_exclusions(CortexContext(air_gap=True), config_path=path)
    assert "worker:latest" not in exclusions


# ---------------------------------------------------------------------------
# Facade wiring: invoke() receives exclude_model_ids only when air-gapped
# ---------------------------------------------------------------------------
class KwargRouter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, function, request, **kwargs):
        self.calls.append((function, request, kwargs))
        return self.response


@pytest.fixture
def install_router(monkeypatch):
    def _install(router):
        llm_pkg = importlib.import_module("tools.llm")
        monkeypatch.setattr(llm_pkg, "get_router", lambda config_path=None: router)
        return router

    return _install


def _response(**overrides):
    defaults = dict(content="ok", provider="fake", model_id="m", duration_ms=1)
    defaults.update(overrides)
    return LLMResponse(**defaults)


def test_complete_passes_exclusions_when_env_airgapped(tmp_path, monkeypatch, install_router):
    monkeypatch.setenv("ICDEV_AIRGAP", "1")
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(_write_config(tmp_path, _base_llm_config())))
    router = install_router(KwargRouter(_response()))
    complete("say hello")
    _, _, kwargs = router.calls[0]
    assert "frontier-9000" in kwargs["exclude_model_ids"]
    assert "worker:latest" not in kwargs["exclude_model_ids"]


def test_complete_omits_kwarg_when_not_airgapped(install_router):
    router = install_router(KwargRouter(_response()))
    complete("say hello")
    _, _, kwargs = router.calls[0]
    assert kwargs == {}


def test_extract_honors_context_air_gap(tmp_path, monkeypatch, install_router):
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(_write_config(tmp_path, _base_llm_config())))
    router = install_router(KwargRouter(_response(structured_output={"a": 1})))
    extract("text", {"type": "object"}, ctx=CortexContext(air_gap=True))
    _, _, kwargs = router.calls[0]
    assert "worker-xl:cloud" in kwargs["exclude_model_ids"]


def test_classify_honors_context_air_gap(tmp_path, monkeypatch, install_router):
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(_write_config(tmp_path, _base_llm_config())))
    router = install_router(KwargRouter(_response(content="alpha")))
    classify("text", ["alpha", "beta"], ctx={"air_gap": True})
    _, _, kwargs = router.calls[0]
    assert "frontier-9000" in kwargs["exclude_model_ids"]


def test_invokes_parse_llm_config_once_when_airgapped(tmp_path, monkeypatch, install_router):
    # The acceptance case: N air-gapped complete() calls, one YAML parse.
    from tools.cortex import config as cortex_config

    monkeypatch.setenv("ICDEV_AIRGAP", "1")
    path = _write_config(tmp_path, _base_llm_config())
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(path))
    parses = []
    real_load_yaml = cortex_config._load_yaml
    monkeypatch.setattr(
        cortex_config,
        "_load_yaml",
        lambda p: (parses.append(str(p)), real_load_yaml(p))[1],
    )

    router = install_router(KwargRouter(_response()))
    for _ in range(5):
        complete("say hello")

    assert len(router.calls) == 5
    assert all("frontier-9000" in kwargs["exclude_model_ids"] for _, _, kwargs in router.calls)
    assert parses.count(str(path)) == 1


# ---------------------------------------------------------------------------
# load_cortex_config()
# ---------------------------------------------------------------------------
def test_load_cortex_config_reads_file_values(tmp_path):
    path = tmp_path / "cortex_config.yaml"
    path.write_text(yaml.safe_dump({"search": {"rrf_k": 7}}), encoding="utf-8")
    config = load_cortex_config(config_path=path, refresh=True)
    assert config["search"]["rrf_k"] == 7


def test_load_cortex_config_env_override(tmp_path, monkeypatch):
    path = tmp_path / "custom_cortex.yaml"
    path.write_text(yaml.safe_dump({"analyst": {"nlq_fallback_enabled": False}}), encoding="utf-8")
    monkeypatch.setenv("ICDEV_CORTEX_CONFIG", str(path))
    assert resolve_cortex_config_path() == path.resolve()
    config = load_cortex_config(refresh=True)
    assert config["analyst"]["nlq_fallback_enabled"] is False


def test_load_cortex_config_defaults_when_file_missing(tmp_path):
    config = load_cortex_config(config_path=tmp_path / "nope.yaml", refresh=True)
    assert config["search"]["rrf_k"] == 60
    # Fallback preserves the platform's actual (fail-open) behavior.
    assert config["governance"]["fail_closed"] is False
    assert config["analyst"]["nlq_fallback_enabled"] is True


def test_load_cortex_config_deep_merges_over_defaults(tmp_path):
    path = tmp_path / "cortex_config.yaml"
    path.write_text(yaml.safe_dump({"governance": {"fail_closed": True}}), encoding="utf-8")
    config = load_cortex_config(config_path=path, refresh=True)
    assert config["governance"]["fail_closed"] is True
    # Sibling default keys survive a partial override.
    assert config["governance"]["skip_grounding_for_plain_complete"] is True
    assert config["search"]["strategy_weights"]["rag"] == 1.0


# ---------------------------------------------------------------------------
# resolve_fail_closed(): explicit ctx wins; None falls back to config policy
# ---------------------------------------------------------------------------
def test_resolve_fail_closed_explicit_wins_over_config(tmp_path):
    from tools.cortex.config import resolve_fail_closed

    # A config that says fail-closed must NOT override an explicit False.
    path = tmp_path / "cortex_config.yaml"
    path.write_text(yaml.safe_dump({"governance": {"fail_closed": True}}), encoding="utf-8")
    assert resolve_fail_closed(CortexContext(fail_closed=False), config_path=path) is False
    assert resolve_fail_closed(CortexContext(fail_closed=True), config_path=path) is True


def test_resolve_fail_closed_none_uses_config_true(tmp_path):
    from tools.cortex.config import resolve_fail_closed

    path = tmp_path / "cortex_config.yaml"
    path.write_text(yaml.safe_dump({"governance": {"fail_closed": True}}), encoding="utf-8")
    # Unset ctx (fail_closed=None, the default) resolves to the config policy.
    assert resolve_fail_closed(CortexContext(), config_path=path) is True
    assert resolve_fail_closed(None, config_path=path) is True


def test_resolve_fail_closed_none_uses_config_false(tmp_path):
    from tools.cortex.config import resolve_fail_closed

    path = tmp_path / "cortex_config.yaml"
    path.write_text(yaml.safe_dump({"governance": {"fail_closed": False}}), encoding="utf-8")
    assert resolve_fail_closed(CortexContext(), config_path=path) is False


def test_resolve_fail_closed_default_is_fail_open(tmp_path):
    from tools.cortex.config import resolve_fail_closed

    # Missing file -> defaults -> fail-open (matches shipped behavior).
    assert resolve_fail_closed(CortexContext(), config_path=tmp_path / "nope.yaml") is False


def test_repo_cortex_config_has_required_blocks():
    # The checked-in args/cortex_config.yaml is the source later Cortex
    # search/governance/analyst modules consume — pin its shape.
    path = resolve_cortex_config_path()
    assert path.is_file(), f"missing {path}"
    config = load_cortex_config(config_path=path, refresh=True)
    search = config["search"]
    assert set(search["strategy_weights"]) == {"rag", "graph", "dic", "kb"}
    assert search["rrf_k"] == 60
    assert 0.0 < search["crag_threshold"] < 1.0
    assert "default" in search["timeouts"]
    governance = config["governance"]
    # Shipped fail-open (the key is now live via resolve_fail_closed but ships
    # false to preserve the platform's actual behavior — operators opt in).
    assert governance["fail_closed"] is False
    assert governance["skip_grounding_for_plain_complete"] is True
    assert config["analyst"]["nlq_fallback_enabled"] is True
