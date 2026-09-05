# CUI // SP-CTI
"""The pinned `floci` compose profile, and the state it writes staying out of git.

RED AT THE MERGE BASE: `docker-compose.yml` declares no `floci` service there
and `.gitignore` does not cover `data/floci/`, so every test in this file fails.

WHAT EACH TEST PINS
-------------------
Three separable hazards, and none of them is "the YAML parses":

  1. THE EMULATOR STARTING BY ACCIDENT. It holds the host docker socket, so a
     service that starts on a bare `docker compose up` is root-equivalent
     access nobody asked for. The profile is the control.
  2. STATE REACHING A PUBLIC REPO. `FLOCI_STORAGE_MODE=persistent` makes the
     emulator write buckets, queues, tables and Lambda bundles under
     ./data/floci. Measured before this card: `git check-ignore data/floci/x`
     matched NOTHING.
  3. THE TWO DOCKER-SOCKET VARIABLES BEING CONFLATED. The compose mount source
     and `emulator.docker_basis()`'s input answer DIFFERENT questions, and
     giving them one name produces a fabricated refusal -- see
     `test_mount_variable_is_not_the_seams_socket_variable`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.cloud import emulator

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_GITIGNORE = _ROOT / ".gitignore"

#: The mount source for the host docker socket. NOT `FLOCI_DOCKER_SOCKET`.
MOUNT_VAR = "FLOCI_DOCKER_SOCKET_MOUNT"

#: The one spelling that serves Docker Desktop on Windows AND a Linux host.
MOUNT_DEFAULT = "//var/run/docker.sock"

#: A declared socket that exists on NO host, so a test using it measures the
#: seam rather than the runner. `MOUNT_DEFAULT` is unusable for that: on a
#: Linux CI runner with Docker installed it names the LIVE socket, which is how
#: an earlier version of this file passed on Windows and failed on CI.
ABSENT_SOCKET = "unix:///nonexistent-icdev-floci-probe/docker.sock"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def floci(compose) -> dict:
    assert "floci" in compose["services"], (
        "docker-compose.yml declares no `floci` service. The emulator seam "
        "(tools/cloud/emulator.py) points at a service that does not exist."
    )
    return compose["services"]["floci"]


# -- 1. It never starts by accident -----------------------------------------


def test_floci_is_behind_its_own_profile(floci):
    """`profiles: ["floci"]` is the control that keeps a socket-holding
    container out of a bare `docker compose up` and out of `/start`."""
    assert floci.get("profiles") == ["floci"]


def test_floci_is_absent_from_the_default_start_set(compose):
    """A service with NO `profiles:` key starts by default. floci must not be
    in that set -- this is the assertion, not the profile string above."""
    default_start = [
        name for name, svc in compose["services"].items() if not svc.get("profiles")
    ]
    assert "floci" not in default_start
    # Negative control: the set is non-empty, so the assertion above is not
    # passing merely because nothing starts by default.
    assert len(default_start) > 10, default_start


def test_image_is_pinned_and_never_latest(floci):
    """An air-gapped bundle has to be reproducible, so the committed file
    carries a version tag. `:latest` moves under the deployment."""
    image = floci["image"]
    assert image == "floci/floci:2.0.1"
    assert not image.endswith(":latest")
    tag = image.rsplit(":", 1)[1]
    assert re.fullmatch(r"\d+\.\d+\.\d+", tag), f"tag {tag!r} is not a pinned version"


def test_no_service_anywhere_in_the_file_uses_a_floating_floci_tag(compose):
    """The pin is on the SERVICE, so a second floci service added later with
    `:latest` would not be caught by the test above."""
    floating = [
        name
        for name, svc in compose["services"].items()
        if "floci" in str(svc.get("image", "")) and str(svc["image"]).endswith(":latest")
    ]
    assert floating == []


# -- 2. What the card specifies ---------------------------------------------


def test_ports_cover_the_edge_port_and_both_proxy_ranges(floci):
    """4566 is the AWS API edge port; the ranges are the ones the
    container-backed services in use need."""
    published = [str(p) for p in floci["ports"]]
    assert any(p.endswith("4566:4566") for p in published), published
    assert any("6379-6399:6379-6399" in p for p in published), published
    assert any("7001-7099:7001-7099" in p for p in published), published


def test_every_published_port_is_loopback_only(floci):
    """An emulator holding the host docker socket must not be reachable
    off-host. Services on icdev-net still reach it by service name."""
    for spec in floci["ports"]:
        assert str(spec).startswith("127.0.0.1:"), (
            f"{spec!r} publishes off-host. The litellm-proxy precedent in this "
            "same file binds to loopback for the same reason."
        )


def test_storage_is_persistent_and_region_is_the_target_partition(floci):
    env = floci["environment"]
    assert env["FLOCI_STORAGE_MODE"] == "persistent"
    assert "FLOCI_DEFAULT_ACCOUNT_ID" in env
    # The default in the interpolation must be the seam's default, or the
    # emulator and every ICDEV caller disagree about which region/account they
    # are talking about.
    assert emulator.DEFAULT_REGION in env["FLOCI_DEFAULT_REGION"]
    assert emulator.DEFAULT_ACCOUNT_ID in env["FLOCI_DEFAULT_ACCOUNT_ID"]


def test_state_volume_is_the_gitignored_path(floci):
    """The bind source must be exactly the path .gitignore covers. A mount at
    a sibling path would persist state into a tracked directory."""
    assert "./data/floci:/var/lib/floci" in floci["volumes"]


def test_service_is_on_the_icdev_network(floci):
    assert floci["networks"] == ["icdev-net"]


def test_healthcheck_probes_the_seams_health_path(floci):
    """floci keeps LocalStack's health path -- that is what "drop-in" means.
    Derived from emulator.HEALTH_PATH so the two cannot drift apart."""
    test = floci["healthcheck"]["test"]
    joined = " ".join(test)
    assert "curl" in joined
    assert emulator.HEALTH_PATH in joined, (test, emulator.HEALTH_PATH)
    assert "4566" in joined


def test_healthcheck_start_period_beats_the_shared_default(floci, compose):
    """A JVM emulator bringing up many backends cannot be healthy in the
    shared 10s. An explicit key must override the `<<:` merge key -- if it did
    not, the container would flap unhealthy on every start."""
    shared = compose["x-healthcheck-defaults"]["start_period"]
    assert shared == "10s", "the shared default moved; re-check this override"
    assert floci["healthcheck"]["start_period"] == "60s"


# -- 3. The docker socket ---------------------------------------------------


def test_socket_mount_is_env_driven_with_the_docker_desktop_default(floci):
    """Not hardcoded either way: read from an env var, defaulted to the one
    spelling that works on Docker Desktop AND on Linux."""
    mounts = [v for v in floci["volumes"] if v.endswith(":/var/run/docker.sock")]
    assert len(mounts) == 1, mounts
    source = mounts[0].rsplit(":/var/run/docker.sock", 1)[0]
    assert source == "${%s:-%s}" % (MOUNT_VAR, MOUNT_DEFAULT), source


def test_socket_mount_source_is_never_a_single_slash_literal(floci):
    """A bare `/var/run/docker.sock` as the SOURCE is rewritten by MSYS/Git
    Bash path conversion on Windows and the mount silently binds the wrong
    thing. The target side is correctly single-slash -- it is a Linux path
    inside the container -- so this asserts on the source only."""
    mounts = [v for v in floci["volumes"] if v.endswith(":/var/run/docker.sock")]
    source = mounts[0].rsplit(":/var/run/docker.sock", 1)[0]
    assert MOUNT_DEFAULT in source
    assert not source.startswith("/var/run")


def test_remote_docker_host_is_left_unset(floci):
    """Operator decision 2026-09-05: locally hosted Docker for now. floci talks
    to the daemon it is handed. A remote daemon is a named follow-on
    (flx-airgap-02), not something this file quietly configures.

    Asserted on the resolved `environment:` mapping, NOT on the file text: the
    block deliberately explains in a comment that the variable is left unset,
    and a text search cannot tell that apart from setting it.
    """
    assert "FLOCI_DOCKER_DOCKER_HOST" not in floci["environment"]
    assert floci.get("env_file") is None, (
        "an env_file would let .env set FLOCI_DOCKER_DOCKER_HOST (and hand the "
        "emulator every API key in it) without this file saying so"
    )


#: Services allowed to mount the host docker socket. ENUMERATED, never a
#: pattern: each entry is an emulator that spawns service containers and each
#: one is an operator decision recorded in docs/security/sandbox-coverage.md.
#: `floci` is the AWS emulator (Gap 65); `floci-az` is the Azure emulator
#: (flx-az-01) -- Azure Functions spawns runtime containers the same way.
_SOCKET_GRANTED_SERVICES = {"floci", "floci-az"}


def test_only_profiled_emulators_are_granted_the_docker_socket(compose):
    """The socket grant is confined to enumerated emulators, and ALL are profiled.

    This is the invariant the sandbox-coverage decision (Gap 65) rests on, and
    it is TWO claims, not one. The grant is acceptable BECAUSE it never starts
    by default, so:

      * no service outside :data:`_SOCKET_GRANTED_SERVICES` may mount the
        socket -- a new grant is an operator decision and must be recorded in
        docs/security/sandbox-coverage.md before it appears here; and
      * EVERY granted service must sit behind a profile. This is the half that
        actually carries the safety property, and it is asserted over the whole
        set rather than over one hardcoded name -- widening the set without it
        would let a second emulator inherit the exemption while starting by
        default, which is precisely the deployment surprise this test exists to
        prevent.

    The set was ``{"floci"}`` until flx-az-01 added the Azure emulator. It was
    widened by enumeration rather than by relaxing the predicate, so a THIRD
    grant still fails here.
    """
    granted = {
        name
        for name, svc in compose["services"].items()
        for vol in (svc.get("volumes") or [])
        if str(vol).endswith(":/var/run/docker.sock")
    }
    assert granted == _SOCKET_GRANTED_SERVICES, granted
    for name in granted:
        assert compose["services"][name].get("profiles"), (
            f"the socket grant is only acceptable behind a profile; {name} has none"
        )


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_mount_variable_is_not_the_seams_socket_variable(monkeypatch, platform):
    r"""THE CONFLATION HAZARD, asserted on a POSIX **and** a Windows host.

    `FLOCI_DOCKER_SOCKET` is read by `emulator.docker_basis()` to answer how
    the ICDEV *Python process on the host* would reach the daemon. The compose
    mount source is a path in Docker Desktop's Linux VM namespace that does not
    exist on the Windows filesystem at all. They are different questions.

    MEASURED 2026-09-04 on this Windows host: setting FLOCI_DOCKER_SOCKET to
    the mount spelling makes `docker_backed()` return False and
    `service_supported("lambda")` return False -- a FABRICATED refusal for a
    Lambda that works, because the socket IS mounted into the container. That
    is the same defect class as a fabricated `[]` pointing the other way.

    THE INVARIANT IS INVISIBILITY, not a fixed verdict. What the seam should
    answer with nothing declared depends on the host it is running on (on a
    real Linux box with no daemon, `False` is the CORRECT answer), so asserting
    a particular value here would be asserting a property of the test runner.
    What must hold on every platform is that setting the compose mount variable
    does not MOVE the seam's answer, while setting the seam's own variable
    does.

    THE VALUE FED TO THE SEAM'S VARIABLE IS DELIBERATELY NOT THE REAL MOUNT
    SPELLING, and CI is what taught this file that. `docker_basis()` stats the
    REAL filesystem for a declared path, and on a Linux runner with Docker
    installed `//var/run/docker.sock` IS the live socket -- so an assertion
    that it reads absent passes on Windows and fails on CI. That is the very
    mistake the paragraph above names. The absent-path probe below cannot exist
    on any host, so what is pinned is which VARIABLE the seam consumes rather
    than what this machine happens to have mounted.
    """
    # NOTE: `emulator.sys` IS the stdlib sys module -- this patch is
    # process-global for the duration of the test, and monkeypatch is what
    # restores it. Spelled through `sys` so that is visible at the call
    # site rather than looking module-scoped.
    monkeypatch.setattr(sys, "platform", platform)

    baseline = emulator.docker_backed({})

    # The compose mount name is invisible to the seam: same answer either way,
    # and for the REAL mount spelling, which is the value that will be set.
    correct = {MOUNT_VAR: MOUNT_DEFAULT}
    assert emulator.docker_backed(correct) is baseline
    assert emulator.service_supported("lambda", correct) is emulator.service_supported(
        "lambda", {}
    )
    # Invisible for the absent-path probe too, so the equality above is not an
    # accident of this host agreeing with the baseline.
    assert emulator.docker_backed({MOUNT_VAR: ABSENT_SOCKET}) is baseline

    # The seam's OWN variable does move it -- which is why the two must not
    # share a name. `unix://` + a path that exists on no host makes this a
    # statement about the seam, not about the runner.
    conflated = {"FLOCI_DOCKER_SOCKET": ABSENT_SOCKET}
    assert emulator.docker_basis(conflated) == emulator.BASIS_SOCKET_ABSENT
    assert emulator.docker_backed(conflated) is False
    assert emulator.service_supported("lambda", conflated) is False


def test_conflating_the_two_socket_variables_fabricates_a_refusal(monkeypatch):
    """The SHAPE of the measured incident, pinned host-independently.

    MEASURED 2026-09-04 on the authoring Windows host: with nothing declared the
    seam answers `None` (cannot tell -- a Windows named pipe is not reliably
    stat-able), and feeding it the compose mount spelling
    `//var/run/docker.sock` -- a path inside Docker Desktop's Linux VM, so
    absent from the Windows filesystem -- turned that honest `None` into a
    definite `False`, flipping `service_supported("lambda")` from True to False
    for a Lambda the mounted socket would have served.

    That real path is NOT used here: on a Linux CI runner it names the live
    docker socket, so the same assertion would be measuring the runner. The
    absent-path probe reproduces the identical transition -- an honest `None`
    becoming a fabricated `False` -- on every host.

    `None` is not `False`, and this is the distinction that protects.
    """
    monkeypatch.setattr(sys, "platform", "win32")  # process-global; restored

    assert emulator.docker_backed({}) is None
    assert emulator.service_supported("lambda", {}) is True

    conflated = {"FLOCI_DOCKER_SOCKET": ABSENT_SOCKET}
    assert emulator.docker_backed(conflated) is False
    assert emulator.service_supported("lambda", conflated) is False


def test_seam_does_not_read_the_mount_variable():
    """The seam must stay ignorant of the compose mount name -- reading it
    would reintroduce the conflation this card separates."""
    src = (_ROOT / "tools" / "cloud" / "emulator.py").read_text(encoding="utf-8")
    assert MOUNT_VAR not in src


# -- 4. Persistent state stays out of a PUBLIC repo -------------------------


def _check_ignore(relpath: str) -> bool:
    """True if git ignores `relpath`. Exit 0 = ignored, 1 = not; anything else
    is an error and fails loudly rather than reading as "not ignored"."""
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", relpath],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(
            f"git check-ignore failed ({proc.returncode}) for {relpath}: {proc.stderr}"
        )
    return proc.returncode == 0


def _tracked(relpath: str) -> bool:
    """Is `relpath` in git's index? Used to prove a negative control is real."""
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relpath],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def test_emulator_state_is_gitignored():
    """The real predicate -- git's own -- not a substring search of .gitignore."""
    assert _check_ignore("data/floci/x")
    assert _check_ignore("data/floci/lambda/fn.zip")
    assert _check_ignore("data/floci/s3/bucket/object.bin")


def test_the_pattern_is_anchored_and_not_a_bare_data_directory():
    """An unanchored `data/` once silently dropped a CODE directory here.

    The negative control is a file git actually TRACKS under data/ (453 of them
    exist). Tracking is what makes it a control: git would keep reporting a
    tracked file whatever .gitignore said, so a path that is both tracked and
    NOT matched by any pattern proves the new rule did not widen into a
    directory carrying real content.
    """
    lines = [
        ln.strip()
        for ln in _GITIGNORE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert "data/floci/" in lines
    assert "data/" not in lines
    assert "data" not in lines

    control = "data/cam_artifacts/proj-analytics-k8s-aws/1e207af7-7709-48fd-84b2-15158083a0e2/snapshot_restore.py"
    assert _tracked(control), f"{control} is no longer tracked; pick another control"
    assert not _check_ignore(control)


def test_the_ignored_path_is_the_one_compose_mounts(floci):
    """Closes the loop: whatever compose persists to is what git ignores."""
    mount = [v for v in floci["volumes"] if v.endswith(":/var/lib/floci")][0]
    host_path = mount.split(":", 1)[0].lstrip("./")
    assert _check_ignore(f"{host_path}/probe.bin")


# -- 5. The socket decision is recorded where a reviewer looks ---------------


def test_docker_socket_decision_is_recorded_in_sandbox_coverage():
    """A container holding the host docker socket is root-equivalent on the
    host. That decision belongs in the coherence-checked file, not only in a
    compose comment."""
    doc = (_ROOT / "docs" / "security" / "sandbox-coverage.md").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "floci" in doc
    assert "docker.sock" in doc
    # The mitigations the card requires ship with the decision.
    assert "root-equivalent" in doc.lower()
    assert "profile" in doc.lower()
