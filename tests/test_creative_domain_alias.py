"""Tests for creative competitor_discoverer domain alias resolution (fdt-qfix-03)."""

from unittest import mock


def _fake_config():
    return {
        "domain": {"name": "proposal management", "g2_category_url": ""},
        "competitor_discovery": {"max_competitors_per_category": 5, "min_review_count": 0},
        "trading": {
            "name": "trading platforms",
            "g2_category_url": "https://g2.com/categories/trading-platforms",
            "capterra_category_url": "",
            "trustradius_category_url": "",
        },
    }


def _make_competitor():
    return {"name": "TradeApp", "source": "g2", "review_count": 100, "source_url": ""}


def test_run_discovery_resolves_yaml_key_alias():
    """run_discovery matches --domain by YAML section key (e.g. 'trading'), not just display name."""
    from tools.creative import competitor_discoverer as cd

    with mock.patch.object(cd, "_load_config", return_value=_fake_config()):
        with mock.patch.object(cd, "discover_from_g2", return_value=[_make_competitor()]):
            with mock.patch.object(cd, "store_competitors", return_value={"stored": 1, "duplicates": 0}):
                with mock.patch.object(cd, "_audit"):
                    result = cd.run_discovery(domain="trading")

    assert "error" not in result
    assert result.get("domain") == "trading"
    assert result.get("competitors_discovered") == 1


def test_run_discovery_resolves_display_name():
    """run_discovery also matches --domain by the section's display name field."""
    from tools.creative import competitor_discoverer as cd

    with mock.patch.object(cd, "_load_config", return_value=_fake_config()):
        with mock.patch.object(cd, "discover_from_g2", return_value=[_make_competitor()]):
            with mock.patch.object(cd, "store_competitors", return_value={"stored": 1, "duplicates": 0}):
                with mock.patch.object(cd, "_audit"):
                    result = cd.run_discovery(domain="trading platforms")

    assert "error" not in result
    assert result.get("competitors_discovered") == 1
