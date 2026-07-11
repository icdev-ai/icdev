# CUI // SP-CTI
"""pr_watcher.main() loads .env so the enforced done-gate is durable across
bare restarts (not just when a shell exported KANBAN_PIPELINE_ENFORCE)."""
import dotenv

import tools.ci.pr_watcher as prw


class _FakeReport:
    tasks_checked = 0
    actions: list = []

    def to_dict(self):
        return {}


class _FakeWatcher:
    def __init__(self, *a, **k):
        pass

    def poll_once(self, task_id=None):
        return _FakeReport()


def test_main_loads_dotenv_from_repo_root(monkeypatch):
    seen = {}
    monkeypatch.setattr(dotenv, "load_dotenv", lambda p=None: seen.__setitem__("path", str(p)))
    monkeypatch.setattr(prw, "PRWatcher", _FakeWatcher)

    rc = prw.main(["--once"])
    assert rc == 0
    assert seen.get("path", "").endswith(".env")
    # resolves against the repo root (ROOT), not the cwd
    assert str(prw.ROOT) in seen["path"]
