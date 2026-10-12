"""A rendered harness guard plugin is per-host output, never repository content (omx-host-06)."""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RENDERED = (".opencode/plugin/icdev-guard.ts", ".pi/extensions/icdev-guard.ts")


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


@pytest.mark.parametrize("path", RENDERED)
def test_rendered_plugin_is_not_tracked(path):
    assert _git("ls-files", "--", path).stdout.strip() == ""


@pytest.mark.parametrize("path", RENDERED)
def test_rendered_plugin_is_ignored(path):
    assert _git("check-ignore", "-q", "--no-index", "--", path).returncode == 0
