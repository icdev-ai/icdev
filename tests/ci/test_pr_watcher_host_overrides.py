"""Per-host switches for the watcher's un-draft and merge steps (omx-host-07)."""

import pytest

from tools.ci import pr_watcher as pw

YAML = {"auto_merge_enabled": True, "auto_ready_draft_prs": True, "poll_interval_seconds": 30}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in pw.HOST_OVERRIDE_ENVS:
        monkeypatch.delenv(env, raising=False)


@pytest.mark.parametrize("env,key", sorted(pw.HOST_OVERRIDE_ENVS.items()))
@pytest.mark.parametrize("raw,want", [("0", False), ("off", False), ("1", True), ("YES", True)])
def test_env_overrides_its_key(monkeypatch, env, key, raw, want):
    monkeypatch.setenv(env, raw)
    cfg = pw.apply_host_overrides({**YAML, key: not want})
    assert cfg[key] is want


@pytest.mark.parametrize("raw", [None, "", "maybe"])
def test_unset_or_unrecognised_falls_through_to_yaml(monkeypatch, raw):
    if raw is not None:
        for env in pw.HOST_OVERRIDE_ENVS:
            monkeypatch.setenv(env, raw)
    assert pw.apply_host_overrides(YAML) == YAML


def test_review_only_host_leaves_other_keys_alone(monkeypatch):
    for env in pw.HOST_OVERRIDE_ENVS:
        monkeypatch.setenv(env, "0")
    cfg = pw.apply_host_overrides(YAML)
    assert cfg == {**YAML, "auto_merge_enabled": False, "auto_ready_draft_prs": False}
    assert YAML["auto_merge_enabled"] is True  # input not mutated


def test_main_applies_the_overrides(monkeypatch):
    for env in pw.HOST_OVERRIDE_ENVS:
        monkeypatch.setenv(env, "0")
    seen = {}

    class _Stub:
        def __init__(self, config=None, dry_run=False):
            seen.update(config)

        def poll_once(self, task_id=None):
            return pw.PollReport() if hasattr(pw, "PollReport") else type(
                "R", (), {"tasks_checked": 0, "actions": []})()

    monkeypatch.setattr(pw, "load_config", lambda path=None: dict(YAML))
    monkeypatch.setattr(pw, "PRWatcher", _Stub)
    pw.main(["--once", "--dry-run"])
    assert seen["auto_merge_enabled"] is False and seen["auto_ready_draft_prs"] is False
