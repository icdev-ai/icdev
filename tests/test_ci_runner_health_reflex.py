# CUI // SP-CTI
"""ci_runner_health re-registers a crash-looping self-hosted CI runner (mfx-boot-02).

Measured 2026-09-03 (and twice before): every docker runner sat in
`Restarting (1)` with `NotFound from POST .../actions/runner-registration`
(an expired registration token), the forge listed each one offline, and every
job in icdev_ft / icdev_rt QUEUED with no red anywhere. The recovery -- mint a
token, `docker compose -p <project> up -d` -- was performed by hand each time.

These tests pin that the reflex (a) acts ONLY on the intersection of the
forge's offline set and docker's Restarting set, and only when the container's
own log proves a registration failure; (b) reports and never touches a
container in one set only; (c) audits BEFORE acting and refuses unaudited;
(d) never lets the token reach argv, a report or an audit row; (e) is bounded,
cooled and never runs a destructive docker verb; (f) reads an unreadable gh /
docker / declaration as unmeasurable, never clean; and (g) is registered on
both sides the daemon needs.

Every command is scripted through the module's `_COMMAND_RUNNER` seam -- no
test here reaches `gh` or `docker`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import ci_runner_health as R  # noqa: E402

FT = "icdev-ai/icdev_ft"
RT = "icdev-ai/icdev_rt"
TOKEN = "AAAAAAAAAAAAAAAAAAAAAAAAAAAA7"          # 29 chars, the real shape
REG_FAIL_LOG = (
    "# Authentication\n"
    "Http response code: NotFound from 'POST https://api.github.com/actions/runner-registration'\n"
    "Response status code does not indicate success: 404 (Not Found).\n"
)


# --------------------------------------------------------------------------- #
# fixture: a scripted fleet
# --------------------------------------------------------------------------- #
class _Fleet:
    """Scripted gh + docker. `forge` is {repo: {runner: status} | None};
    `docker` is {container: (state, status, project)} | None; `logs` per container."""

    def __init__(self, forge, docker, logs=None, *, mint_ok=True, compose_rc=0,
                 after_up=None):
        self.forge, self.docker, self.logs = forge, docker, logs or {}
        self.mint_ok, self.compose_rc, self.after_up = mint_ok, compose_rc, after_up
        self.calls = []           # (argv, cwd, env)

    def __call__(self, argv, cwd=None, env=None, **_):
        self.calls.append((list(argv), cwd, env))
        tool, rest = argv[0], argv[1:]
        if tool == "gh":
            if rest[:2] == ["api", "-X"]:
                if not self.mint_ok:
                    return _cp(1, "", "HTTP 403")
                return _cp(0, TOKEN + "\n", "")
            repo = rest[1].split("/", 1)[1].rsplit("/actions", 1)[0]
            runners = self.forge.get(repo)
            if runners is None:
                return _cp(1, "", "gh: HTTP 401")
            return _cp(0, json.dumps({"runners": [{"name": n, "status": s} for n, s in runners.items()]}), "")
        if tool == "docker" and rest[:1] == ["ps"]:
            if self.docker is None:
                return _cp(1, "", "error during connect: docker daemon not running")
            lines = [f"{name}\t{st}\t{status}\t{proj}" for name, (st, status, proj) in self.docker.items()]
            return _cp(0, "\n".join(lines) + "\n", "")
        if tool == "docker" and rest[:1] == ["logs"]:
            text = self.logs.get(rest[-1])
            return _cp(0, "", text) if text is not None else _cp(1, "", "no such container")
        if tool == "docker" and rest[:1] == ["compose"]:
            if self.compose_rc == 0 and self.after_up:
                self.after_up(self, env or {})
            return _cp(self.compose_rc, "", "" if self.compose_rc == 0 else "boom")
        raise AssertionError(f"unscripted command {argv}")


def _cp(rc, out, err):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


def _declaration(tmp_path, **over):
    decl = {
        "version": 1, "max_reregistrations_per_run": 2, "reregister_cooldown_seconds": 21600,
        "confirm_wait_seconds": 30, "confirm_poll_seconds": 10,
        "registration_failure_signatures": ["actions/runner-registration", "already configured"],
        "gh_bin": "gh", "docker_bin": "docker",
        "repos": [
            {"repo": FT, "compose_dir": str(tmp_path / "ft"), "containers": [
                {"container": "icdev-ft-runner", "runner_name": "icdev-ft-docker", "project": "runner"},
                {"container": "icdev-ft-runner-2", "runner_name": "icdev-ft-docker-2", "project": "icdev-ft-runner-2"},
            ]},
            {"repo": RT, "compose_dir": str(tmp_path / "rt"), "containers": [
                {"container": "icdev-rt-runner", "runner_name": "icdev-rt-docker", "project": "icdev-rt-runner"},
            ]},
        ],
    }
    decl.update(over)
    for sub in ("ft", "rt"):
        (tmp_path / sub).mkdir(exist_ok=True)
        (tmp_path / sub / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    p = tmp_path / "ci_runners.yaml"
    p.write_text(yaml.safe_dump(decl), encoding="utf-8")
    return p


def _wire(monkeypatch, fleet, *, recent=None, audit=None):
    monkeypatch.setattr(R, "_COMMAND_RUNNER", fleet)
    monkeypatch.setattr(R, "_SLEEP", lambda s: None)
    monkeypatch.setattr(R, "_binary", lambda cfg, key, name: name)
    monkeypatch.setattr(R, "_recently_acted", lambda window: {} if recent is None else recent)
    rows = []

    def _writer(action, details):
        if audit == "fail":
            raise RuntimeError("audit_trail CHECK refused the type")
        rows.append((action, details))
        return len(rows)

    monkeypatch.setattr(R, "_audit", _writer)
    return rows


HEALTHY_FT = {"icdev-ft-docker": "online", "icdev-ft-docker-2": "online"}
UP = ("running", "Up 3 hours", None)


def _docker(rt_state=("restarting", "Restarting (1) 52 seconds ago", "icdev-rt-runner")):
    return {
        "icdev-ft-runner": ("running", "Up 3 hours", "runner"),
        "icdev-ft-runner-2": ("running", "Up 3 hours", "icdev-ft-runner-2"),
        "icdev-rt-runner": rt_state,
    }


def _comes_back(fleet, env):
    """What a successful `compose up` does to the world: ONLY the container
    the act named comes up, and ONLY its runner goes online."""
    c, r = env["RUNNER_CONTAINER"], env["RUNNER_NAME"]
    fleet.docker[c] = ("running", "Up 2 seconds", fleet.docker[c][2])
    for repo, runners in fleet.forge.items():
        if runners and r in runners:
            runners[r] = "online"


# --------------------------------------------------------------------------- #
# 1. the classification table -- pure, and the whole contract
# --------------------------------------------------------------------------- #
def test_classify_acts_only_on_offline_x_restarting():
    assert R.classify("offline", "restarting") == R.CLASS_CANDIDATE
    assert R.classify(None, "restarting") == R.CLASS_CANDIDATE, "unregistered on the forge is offline"
    assert R.classify("offline", "running") == R.CLASS_OFFLINE_BUT_UP
    assert R.classify("online", "restarting") == R.CLASS_RESTARTING_BUT_ONLINE
    assert R.classify("online", "running") == R.CLASS_HEALTHY
    assert R.classify("offline", "exited") == R.CLASS_STOPPED
    assert R.classify("offline", None) == R.CLASS_CONTAINER_ABSENT
    assert R.classify(None, "restarting", forge_measured=False) == R.CLASS_FORGE_UNMEASURED


def test_prove_needs_a_live_signature_and_an_unreadable_log_is_none():
    assert R.prove_registration_failure(REG_FAIL_LOG, ["actions/runner-registration"]) == (True, "actions/runner-registration")
    assert R.prove_registration_failure("run.sh: segfault", ["actions/runner-registration"]) == (False, None)
    assert R.prove_registration_failure(None, ["actions/runner-registration"]) == (None, None)


# --------------------------------------------------------------------------- #
# 2. THE case: offline x restarting x proven -> one compose up, token in env only
# --------------------------------------------------------------------------- #
def test_a_proven_candidate_is_reregistered_through_one_compose_up(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(),
                   {"icdev-rt-runner": REG_FAIL_LOG}, after_up=_comes_back)
    rows = _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})

    assert rep["status"] == R.STATUS_FINDINGS
    assert rep["classified"] == {R.CLASS_HEALTHY: 2, R.CLASS_CANDIDATE: 1}
    assert rep["reregistered"] == 1 and rep["outcomes"] == {R.APPLIED: 1}
    act = rep["acted"][0]
    assert act["container"] == "icdev-rt-runner" and act["confirmed"] is True
    assert act["matched_signature"] == "actions/runner-registration"

    composes = [c for c in fleet.calls if c[0][:2] == ["docker", "compose"]]
    assert len(composes) == 1, "exactly one compose up"
    argv, cwd, env = composes[0]
    assert argv == ["docker", "compose", "-p", "icdev-rt-runner", "-f", "docker-compose.yml", "up", "-d"]
    assert Path(cwd) == tmp_path / "rt"
    assert env["RUNNER_TOKEN"] == TOKEN and env["RUNNER_CONTAINER"] == "icdev-rt-runner"
    assert env["RUNNER_NAME"] == "icdev-rt-docker"
    # the token is in the ENVIRONMENT and nowhere else
    assert TOKEN not in json.dumps(argv)
    assert TOKEN not in json.dumps(rep, default=str)
    assert TOKEN not in json.dumps(rows, default=str)


def test_intent_is_audited_before_the_act_and_outcome_after(monkeypatch, tmp_path):
    order = []

    def _spied_comes_back(fleet, env):
        order.append("compose")
        _comes_back(fleet, env)

    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(),
                   {"icdev-rt-runner": REG_FAIL_LOG}, after_up=_spied_comes_back)
    rows = _wire(monkeypatch, fleet)
    real_audit = R._audit

    def _spy(action, details):
        order.append(action)
        return real_audit(action, details)

    monkeypatch.setattr(R, "_audit", _spy)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["reregistered"] == 1
    assert order == [R.ACTION_INTENT, "compose", R.ACTION_PREFIX + R.APPLIED]
    intent = rows[0][1]
    assert intent["container"] == "icdev-rt-runner" and intent["repo"] == RT and intent["phase"] == "intent"
    assert "at" in intent, "the cooldown reads `at` back from this row"
    assert not any("token" in k.lower() for row in rows for k in row[1])


def test_no_intent_row_means_no_act(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(),
                   {"icdev-rt-runner": REG_FAIL_LOG})
    _wire(monkeypatch, fleet, audit="fail")
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["outcomes"] == {R.UNAUDITED_REFUSED: 1} and rep["reregistered"] == 0
    assert not any(c[0][:2] == ["docker", "compose"] for c in fleet.calls)
    assert not any(c[0][:3] == ["gh", "api", "-X"] for c in fleet.calls), "no token is minted for a refused act"


def test_container_still_down_after_compose_is_failed_never_applied(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(),
                   {"icdev-rt-runner": REG_FAIL_LOG})          # no after_up: nothing changes
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["outcomes"] == {R.FAILED: 1} and rep["reregistered"] == 0
    assert rep["acted"][0]["confirm"] == {"docker_state": "restarting", "forge_status": "offline"}


def test_running_but_not_yet_online_is_applied_unconfirmed(monkeypatch, tmp_path):
    def _up_only(fleet, env):
        c = env["RUNNER_CONTAINER"]
        fleet.docker[c] = ("running", "Up", fleet.docker[c][2])

    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(),
                   {"icdev-rt-runner": REG_FAIL_LOG}, after_up=_up_only)
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["outcomes"] == {R.APPLIED_UNCONFIRMED: 1}
    assert rep["acted"][0]["confirmed"] is False


# --------------------------------------------------------------------------- #
# 3. one set only: reported, never touched
# --------------------------------------------------------------------------- #
def test_offline_but_up_and_restarting_but_online_are_reported_not_touched(monkeypatch, tmp_path):
    docker = _docker(("restarting", "Restarting (1) 5 seconds ago", "icdev-rt-runner"))
    docker["icdev-ft-runner-2"] = ("running", "Up 3 hours", "icdev-ft-runner-2")
    fleet = _Fleet({FT: {"icdev-ft-docker": "online", "icdev-ft-docker-2": "offline"},
                    RT: {"icdev-rt-docker": "online"}}, docker, {"icdev-rt-runner": REG_FAIL_LOG})
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["candidates"] == 0 and rep["acted"] == []
    assert {i["container"]: i["class"] for i in rep["reported"]} == {
        "icdev-ft-runner-2": R.CLASS_OFFLINE_BUT_UP, "icdev-rt-runner": R.CLASS_RESTARTING_BUT_ONLINE,
    }
    assert rep["status"] == R.STATUS_FINDINGS, "a reported anomaly is a finding, not ok"
    assert not any(c[0][:2] == ["docker", "compose"] for c in fleet.calls)
    assert not any(c[0][:2] == ["docker", "logs"] for c in fleet.calls), "no proof is attempted off the intersection"


def test_restarting_for_another_reason_is_unproven_and_untouched(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(),
                   {"icdev-rt-runner": "run.sh: Segmentation fault (core dumped)\n"})
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["candidates"] == 1 and rep["acted"] == []
    assert rep["reported"][0]["class"] == "restarting_unproven"
    assert rep["refused_by_reason"] == {"no registration-failure signature in the log": 1}
    assert not any(c[0][:2] == ["docker", "compose"] for c in fleet.calls)


def test_unreadable_log_refuses_rather_than_assumes(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(), {})
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["reported"][0]["class"] == "restarting_log_unreadable" and rep["acted"] == []


def test_project_label_mismatch_is_refused(monkeypatch, tmp_path):
    """2026-08-30: an RT `up` adopted FT's `runner` project and recreated
    icdev-ft-runner out from under it. A live label that disagrees with the
    declaration is that event, and acting on it would repeat it."""
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}},
                   _docker(("restarting", "Restarting (1)", "runner")), {"icdev-rt-runner": REG_FAIL_LOG})
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["reported"][0]["class"] == "project_label_mismatch" and rep["acted"] == []
    assert not any(c[0][:2] == ["docker", "compose"] for c in fleet.calls)


# --------------------------------------------------------------------------- #
# 4. bounded, cooled, dry
# --------------------------------------------------------------------------- #
def _three_down(tmp_path):
    decl = _declaration(tmp_path, max_reregistrations_per_run=1)
    docker = {n: ("restarting", "Restarting (1)", p) for n, p in (
        ("icdev-ft-runner", "runner"), ("icdev-ft-runner-2", "icdev-ft-runner-2"), ("icdev-rt-runner", "icdev-rt-runner"))}
    logs = {n: REG_FAIL_LOG for n in docker}
    forge = {FT: {"icdev-ft-docker": "offline", "icdev-ft-docker-2": "offline"}, RT: {"icdev-rt-docker": "offline"}}
    return decl, forge, docker, logs


def test_the_bound_defers_and_names_what_it_did_not_do(monkeypatch, tmp_path):
    decl, forge, docker, logs = _three_down(tmp_path)

    fleet = _Fleet(forge, docker, logs, after_up=_comes_back)
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(decl)})
    assert rep["reregistered"] == 1 and rep["deferred"] == 2
    deferred = [a for a in rep["acted"] if a.get("outcome") == R.DEFERRED]
    assert {a["container"] for a in deferred} == {"icdev-ft-runner-2", "icdev-rt-runner"}
    assert all(a["proven"] is True for a in deferred), "a deferred candidate was still PROVEN"
    assert sum(1 for c in fleet.calls if c[0][:2] == ["docker", "compose"]) == 1


def test_a_failed_act_spends_the_bound(monkeypatch, tmp_path):
    decl, forge, docker, logs = _three_down(tmp_path)
    fleet = _Fleet(forge, docker, logs, compose_rc=1)
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(decl)})
    assert rep["outcomes"] == {R.FAILED: 1} and rep["deferred"] == 2


def test_recently_acted_is_refused(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(), {"icdev-rt-runner": REG_FAIL_LOG})
    _wire(monkeypatch, fleet, recent={"icdev-rt-runner": "2026-09-04T10:00:00+00:00"})
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["reported"][0]["class"] == "recently_acted" and rep["acted"] == []
    assert rep["reported"][0]["last_intent_at"] == "2026-09-04T10:00:00+00:00"


def test_dry_run_proves_everything_and_acts_on_nothing(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, _docker(), {"icdev-rt-runner": REG_FAIL_LOG})
    rows = _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path)), "dry_run": True})
    assert rep["would_reregister"] == 1 and rep["reregistered"] == 0
    assert rep["acted"][0]["outcome"] == R.WOULD_APPLY and rep["acted"][0]["proven"] is True
    assert rows == [] and not any(c[0][:2] == ["docker", "compose"] for c in fleet.calls)
    assert not any(c[0][:3] == ["gh", "api", "-X"] for c in fleet.calls), "a dry run mints no token"


# --------------------------------------------------------------------------- #
# 5. unmeasurable is never clean
# --------------------------------------------------------------------------- #
def test_no_declaration_and_empty_fleet_are_unmeasurable(monkeypatch, tmp_path):
    _wire(monkeypatch, _Fleet({}, {}))
    rep = R.sweep({"declaration_path": str(tmp_path / "missing.yaml")})
    assert rep["status"] == R.STATUS_UNMEASURABLE and "declaration unreadable" in rep["errors"][0]
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path, repos=[]))})
    assert rep["status"] == R.STATUS_UNMEASURABLE and "no_runners_declared" in rep["errors"][0]


def test_docker_unreadable_is_unmeasurable(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "offline"}}, None)
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["status"] == R.STATUS_UNMEASURABLE and rep["docker_readable"] is False
    assert rep["classified"] == {}


def test_forge_unreadable_for_one_repo_reports_that_repo_unmeasured(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: None}, _docker(), {"icdev-rt-runner": REG_FAIL_LOG})
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["forge_readable"] == {FT: True, RT: False}
    assert rep["reported"][0]["class"] == R.CLASS_FORGE_UNMEASURED and rep["acted"] == []
    assert rep["status"] == R.STATUS_FINDINGS, "one repo measured; the other is a named finding"
    fleet = _Fleet({FT: None, RT: None}, _docker(), {"icdev-rt-runner": REG_FAIL_LOG})
    _wire(monkeypatch, fleet)
    rep = R.sweep({"declaration_path": str(_declaration(tmp_path))})
    assert rep["status"] == R.STATUS_UNMEASURABLE, "the forge answered for nobody"


def test_run_never_raises_and_keeps_the_breaker_closed(monkeypatch, tmp_path):
    fleet = _Fleet({FT: HEALTHY_FT, RT: {"icdev-rt-docker": "online"}}, _docker(("running", "Up", "icdev-rt-runner")))
    _wire(monkeypatch, fleet)
    out = R.run({"declaration_path": str(_declaration(tmp_path))}, None)
    assert out["success"] is True and out["status"] == R.STATUS_OK and out["metric_value"] == 0.0

    def _boom(*a, **k):
        raise RuntimeError("docker exploded")

    monkeypatch.setattr(R, "_COMMAND_RUNNER", _boom)
    out = R.run({"declaration_path": str(_declaration(tmp_path))}, None)
    assert out["success"] is True and out["status"] == R.STATUS_UNMEASURABLE


# --------------------------------------------------------------------------- #
# 6. structural: no destructive verb, no token on disk, mirrored, registered
# --------------------------------------------------------------------------- #
def test_the_reflex_never_runs_a_destructive_docker_verb():
    src = Path(R.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]          # past the module docstring, which names the verbs it refuses
    for forbidden in ('"rm"', '"down"', '"kill"', '"volume"', '"prune"', '"-v"', "shell=True"):
        assert forbidden not in body, f"{forbidden} in the reflex body"
    assert body.count('"compose"') == 1, "exactly one compose invocation"


def test_the_declaration_holds_no_token_and_declares_both_repos():
    path = ROOT / "args" / "ci_runners.yaml"
    assert R.declaration_holds_no_token(path)
    decl = yaml.safe_load(path.read_text(encoding="utf-8"))
    repos = {r["repo"] for r in decl["repos"]}
    assert repos == {FT, RT}
    for r in decl["repos"]:
        for c in r["containers"]:
            assert set(c) == {"container", "runner_name", "project"}, c
            assert "token" not in json.dumps(c).lower()
    assert decl["registration_failure_signatures"], "the proof needs at least one live signature"


def test_token_shape_detector_catches_a_real_looking_token(tmp_path):
    p = tmp_path / "leak.yaml"
    p.write_text(f"token: {TOKEN}\n", encoding="utf-8")
    assert R.declaration_holds_no_token(p) is False


def test_registered_in_reflex_names_and_genesis_config():
    from tools.genesis.daemon import REFLEX_NAMES

    assert R.REFLEX_NAME in REFLEX_NAMES
    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    block = cfg["reflexes"][R.REFLEX_NAME]
    assert block["enabled"] is True and block["risk_tier"] == "green"
    assert block["interval_seconds"] == 900 and block["dry_run"] is False
    assert block["success_metric"]["name"] == R.METRIC_NAME


def test_mirrored_byte_identical_into_the_icdev_package():
    a = ROOT / "tools" / "genesis" / "reflexes" / "ci_runner_health.py"
    b = ROOT / "icdev" / "tools" / "genesis" / "reflexes" / "ci_runner_health.py"
    assert b.exists(), "icdev/ mirror missing"
    assert a.read_bytes() == b.read_bytes()


def test_audit_event_type_is_already_admitted():
    """No migration: the reflex writes an EXISTING type with the surface in
    `action`. A type the deployed CHECK does not admit is rejected on
    log_event's first line and the act is refused as unaudited."""
    from tools.audit.audit_logger import VALID_EVENT_TYPES

    assert R.AUDIT_EVENT_TYPE in VALID_EVENT_TYPES
    assert R.ACTION_INTENT.startswith(R.ACTION_PREFIX)
