# CUI // SP-CTI
"""The ICDEV[FT] dispatch-proof seed and the generalised seeder (xit-rm-04).

The property that matters here is not "the probe works" — it is that a task believed to target
the PRIVATE sibling can never be built inside THIS repository, which is PUBLIC. An unregistered
prefix resolves to ICDev by default, so the seeder refuses unless the resolver independently
agrees; these tests pin that refusal, in both directions.
"""
from __future__ import annotations

import pathlib

import pytest

from tools.kanban.repo_registry import resolve_task_repo
from tools.kanban.seed_compass_dispatch_probe import check_routing, load_seed

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FT_SEED = REPO_ROOT / "args" / "kanban_seed_ft_dispatch.yaml"
COMPASS_SEED = REPO_ROOT / "args" / "kanban_seed_compass_dispatch.yaml"


# ---------------------------------------------------------------------------
# The seed itself
# ---------------------------------------------------------------------------


def test_the_ft_seed_exists_and_is_valid():
    task = load_seed(FT_SEED)
    for field in ("id", "title", "task_type", "target_repo", "complexity"):
        assert task.get(field), f"the seed must declare {field}"
    assert task["id"].startswith("xft-"), (
        "the probe must use the xft- prefix: ftl- and fdx- are the TRADING streams and are "
        "held behind their own gates, because live-trading code is never built unattended"
    )


def test_the_ft_seed_targets_the_private_sibling():
    task = load_seed(FT_SEED)
    assert task["target_repo"] == "icdev-ai/icdev_ft"


def test_the_probe_carries_no_trading_scope():
    """A dispatch proof exercises the PIPELINE. Anything else makes the proof unreadable."""
    task = load_seed(FT_SEED)
    body = (task.get("description") or "").lower()
    for forbidden in ("assert_may_trade", "check_risk", "kill switch", "live_authorization"):
        assert forbidden not in body, f"the probe must not touch {forbidden}"
    assert task["task_type"] in ("fix", "chore", "docs", "build")


# ---------------------------------------------------------------------------
# Routing — the leak case
# ---------------------------------------------------------------------------


def test_the_probe_id_routes_to_ft_and_not_to_this_repo():
    """The one that matters. This repository is PUBLIC and ICDEV[FT] is private."""
    target = resolve_task_repo("xft-vfy-01")
    assert target.is_external, (
        "xft-vfy-01 resolved to ICDev — an unregistered prefix would have the dispatcher "
        "build private-sibling work inside this PUBLIC tree"
    )
    assert target.name == "icdev_ft"


@pytest.mark.parametrize("task_id", ["ftl-live-01", "fdx-quant-09", "xft-anything-01"])
def test_every_ft_stream_is_registered_external(task_id):
    """Registering a stream never ENABLES it; it only stops it building here.

    `ftl-` was once absent from the registry and therefore resolved to ICDev, which meant
    releasing its gate would have had the dispatcher build trading work inside this public
    repository — the leak class, reached through the dispatcher rather than a commit.
    """
    target = resolve_task_repo(task_id)
    assert target.is_external and target.name == "icdev_ft"


def test_check_routing_accepts_the_ft_seed():
    routing = check_routing(load_seed(FT_SEED))
    assert routing["repo"] == "icdev_ft"
    assert routing["task_id"] == "xft-vfy-01"


def test_registry_repo_is_honoured_when_it_differs_from_target_repo():
    """The registry's logical name need not equal the forge repo name."""
    task = dict(load_seed(FT_SEED))
    task["registry_repo"] = "icdev_ft"
    task["target_repo"] = "icdev-ai/some-other-name"
    assert check_routing(task)["repo"] == "icdev_ft"


def test_a_claim_the_resolver_disagrees_with_is_refused():
    task = dict(load_seed(FT_SEED))
    task["registry_repo"] = "compass"
    with pytest.raises(ValueError, match="resolves to repo"):
        check_routing(task)


def test_an_unregistered_prefix_is_refused_rather_than_built_here():
    task = dict(load_seed(FT_SEED))
    task["id"] = "definitely-not-registered-01"
    with pytest.raises(ValueError, match="resolves to ICDev"):
        check_routing(task)


# ---------------------------------------------------------------------------
# The generalisation did not break the original
# ---------------------------------------------------------------------------


def test_the_compass_seed_still_loads_by_default():
    """`--seed-file` is additive: the no-argument path must be unchanged."""
    assert load_seed()["id"] == load_seed(COMPASS_SEED)["id"]
    assert check_routing(load_seed())["repo"] == "compass"


def test_the_two_seeds_are_distinct_tasks():
    assert load_seed(FT_SEED)["id"] != load_seed(COMPASS_SEED)["id"]


def test_the_seed_file_flag_is_offered():
    import argparse
    import contextlib
    import io

    from tools.kanban import seed_compass_dispatch_probe as mod

    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        mod.main(["--help"])
    assert "--seed-file" in buf.getvalue()
    assert isinstance(argparse.ArgumentParser(), argparse.ArgumentParser)


def test_the_premise_named_in_the_seed_is_checkable():
    """The compass seed's own header demands the premise be verified against the target repo.

    The claim here is that ICDEV[FT]'s README still tells the reader to install ICDEV[IT]
    with a command that now fails. The target repo is not checked out in CI, so what is
    asserted is that the seed STATES a checkable premise — a version, a file and an error —
    rather than a vague "improve the docs".
    """
    body = load_seed(FT_SEED)["description"]
    assert "README.md" in body
    assert "icdev-core" in body
    assert "ACCEPTANCE" in body, "a probe with no acceptance line cannot be judged finished"
