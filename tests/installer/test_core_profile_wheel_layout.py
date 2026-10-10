# CUI // SP-CTI
"""core_profiles.yaml must resolve in an installed wheel, not only a checkout.

In a wheel, repo_root() is the `icdev` package dir and FORGE data ships under
`data/args/`. core_profile looked only at `<base>/args/`, so `icdev init
--profile air-gap` on any pip install said "Valid profiles: none" -- caught by
the 1.2.43 release air-gap smoke step.
"""
from __future__ import annotations

from tools.config.core_profile import _default_path, load_profiles


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_wheel_layout_data_args_is_found(tmp_path):
    _write(tmp_path / "data" / "args" / "core_profiles.yaml", "profiles:\n  air-gap: {}\n")
    path = _default_path(tmp_path)
    assert path == tmp_path / "data" / "args" / "core_profiles.yaml"
    assert "air-gap" in load_profiles(path)


def test_checkout_layout_args_wins(tmp_path):
    _write(tmp_path / "args" / "core_profiles.yaml", "profiles:\n  local-dev: {}\n")
    _write(tmp_path / "data" / "args" / "core_profiles.yaml", "profiles:\n  other: {}\n")
    assert _default_path(tmp_path) == tmp_path / "args" / "core_profiles.yaml"


def test_neither_layout_keeps_the_old_default(tmp_path):
    assert _default_path(tmp_path) == tmp_path / "args" / "core_profiles.yaml"
