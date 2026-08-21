# [TEMPLATE: CUI // SP-CTI]
"""Single resolution point for the ICDEV™ LLM configuration file.

The problem this solves: `tools/llm/router.py` and `icdev/tools/llm/router.py` are
byte-identical files, and each computed `Path(__file__).parent.parent.parent / "args"
/ "llm_config.yaml"`. That resolves to `<repo>/args/llm_config.yaml` for one and
`<repo>/icdev/args/llm_config.yaml` for the other. The two files drifted: they
disagreed on model ids, on which provider serves Claude, and on `two_tier.tier2_model`.
Which planner model you got therefore depended on whether the calling module happened
to write `from tools.llm...` or `from icdev.tools.llm...`.

Resolution order (first hit wins):

1. ``$ICDEV_LLM_CONFIG`` — explicit override, for deployments that keep config
   outside the source tree.
2. The nearest ``args/llm_config.yaml`` walking up from the project root. This is
   the file you edit; there is exactly one per installation.
3. The packaged default ``icdev/args/llm_config.yaml``, so a pip-installed wheel
   outside any repository still starts.

`icdev/args/llm_config.yaml` is a generated mirror of the canonical file — edit
`args/llm_config.yaml` and copy it across. Drift between the two fails
`tests/test_llm_config_single_source.py::test_packaged_copy_matches_canonical`.
"""
from __future__ import annotations

import os
from pathlib import Path

CONFIG_ENV_VAR = "ICDEV_LLM_CONFIG"
CONFIG_RELPATH = Path("args") / "llm_config.yaml"

# tools/llm/config_path.py -> tools/llm -> tools -> <repo>
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent.parent

# The packaged fallback always sits next to the icdev package, whichever copy of
# this module is imported.
_PACKAGED = _PROJECT_ROOT / "icdev" / CONFIG_RELPATH
if not _PACKAGED.exists() and _PROJECT_ROOT.name == "icdev":
    _PACKAGED = _PROJECT_ROOT / CONFIG_RELPATH


def _walk_up_for_config(start: Path) -> Path | None:
    """Return the nearest args/llm_config.yaml at or above `start`."""
    for candidate in (start, *start.parents):
        # Never match the packaged copy while walking; it is the last resort.
        if candidate.name == "icdev":
            continue
        found = candidate / CONFIG_RELPATH
        if found.is_file():
            return found
    return None


def resolve_llm_config_path() -> Path:
    """Return the LLM config path that every ICDEV™ component should read.

    xit-decl-01: delegates to :func:`icdev.core.paths.config_path`, the one
    resolver every parent shares. The walk-up rule that skips a directory named
    ``icdev`` is preserved by handing it the packaged copy explicitly.
    """
    from icdev.core.paths import config_path

    resolved = config_path(
        CONFIG_RELPATH, env=CONFIG_ENV_VAR, anchor=_HERE, packaged=_PACKAGED
    )
    if resolved == _PACKAGED:
        # The repo root had no args/llm_config.yaml: keep the legacy walk, which
        # also finds a config ABOVE the repo (a monorepo or a parent checkout).
        project = _walk_up_for_config(_PROJECT_ROOT)
        if project is not None:
            return project
    return resolved


def describe_resolution() -> dict:
    """Diagnostics for `icdev status` / support: which file won, and why."""
    override = os.environ.get(CONFIG_ENV_VAR, "").strip()
    project = _walk_up_for_config(_PROJECT_ROOT)
    resolved = resolve_llm_config_path()
    if override:
        source = "env"
    elif project is not None:
        source = "project"
    else:
        source = "packaged"
    return {
        "resolved": str(resolved),
        "exists": resolved.is_file(),
        "source": source,
        "env_var": CONFIG_ENV_VAR,
        "env_value": override or None,
        "project_candidate": str(project) if project else None,
        "packaged_default": str(_PACKAGED),
    }
