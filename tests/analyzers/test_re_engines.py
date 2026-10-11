# CUI // SP-CTI
"""tools/analyzers/re_engines.py -- engine registry + license gate (rea-eng-01).

Every decision is asserted on its NAMED reason, not just ``allowed``: two
refusals for different causes send the user to different fixes, and a test that
only checks the bool would pass on the wrong one. The declaration is written to
``tmp_path`` (outside any git work tree) and passed explicitly -- no test reads
the developer's real ``~/.config/icdev/re_licenses.yaml``.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from icdev.tools.analyzers import re_engines as rx

MODULE = Path(rx.__file__)
REPO = Path(__file__).resolve().parents[2]

LOCAL = {}  # no shared-deployment signal
SAAS = {"ICDEV_SAAS_MODE": "true"}
MULTI = {"ICDEV_MULTI_TENANT": "1"}
NO_INSTALL = {"installed": False, "path": None, "version": None, "edition_detected": None}


def _decl(tmp_path, body: str):
    p = tmp_path / "re_licenses.yaml"
    p.write_text(body, encoding="utf-8")
    return rx.load_declaration(p)


def _ida(tmp_path, edition, use, env=LOCAL, own=True, detection=NO_INSTALL):
    d = _decl(tmp_path, f"own_machine: {str(own).lower()}\nengines:\n  ida:\n"
                        f"    edition: {edition}\n    use: {use}\n")
    return rx.evaluate("ida", env=env, declaration=d, detection=detection, plat="linux")


def _hopper(tmp_path, body, env=LOCAL, plat="linux", user="alice", host="box1"):
    d = _decl(tmp_path, "own_machine: true\nengines:\n  hopper:\n" + body)
    return rx.evaluate("hopper", env=env, declaration=d, detection=NO_INSTALL,
                       plat=plat, user=user, host=host)


# --- Ghidra -----------------------------------------------------------------

@pytest.mark.parametrize("env", [LOCAL, SAAS, MULTI])
@pytest.mark.parametrize("plat", ["windows", "linux", "macos"])
def test_ghidra_is_never_refused_on_license_grounds(env, plat):
    d = rx.evaluate("ghidra", env=env, declaration=None, detection=NO_INSTALL, plat=plat)
    assert d["allowed"] is True
    assert d["reason"] == rx.REASON_OSS
    assert d["provenance"]["license"] == "Apache-2.0"
    assert d["provenance"]["license_basis"] == "oss"
    assert d["declaration"] is None  # no declaration consulted


def test_registry_declares_the_three_engines_with_their_license_classes():
    reg = rx.load_registry()
    eng = reg["engines"]
    assert eng["ghidra"]["license_class"] == "oss_redistributable"
    assert eng["hopper"]["license_class"] == "byo_single_user"
    assert set(eng["hopper"]["platforms"]) == {"macos", "linux"}
    assert eng["ida"]["license_class"] == "oem_required_for_shared"
    assert eng["ida"]["editions"]["free"]["non_commercial_only"] is True


# --- IDA --------------------------------------------------------------------

def test_ida_free_commercial_is_refused_quoting_and_linking_the_term(tmp_path):
    d = _ida(tmp_path, "free", "commercial")
    assert d["allowed"] is False
    assert d["reason"] == rx.REASON_IDA_FREE_COMMERCIAL
    assert "non-commercial" in d["term"]["text"]
    assert d["term"]["url"].startswith("https://hex-rays.com/")
    assert d["term"]["url"] in d["message"]


def test_ida_free_non_commercial_on_own_machine_is_allowed(tmp_path):
    d = _ida(tmp_path, "free", "non_commercial")
    assert (d["allowed"], d["reason"]) == (True, rx.REASON_ALLOWED)
    assert d["provenance"]["declared_edition"] == "free"
    assert d["provenance"]["declared_use"] == "non_commercial"


@pytest.mark.parametrize("edition", ["home", "pro", "classroom"])
@pytest.mark.parametrize("use", ["commercial", "non_commercial"])
def test_ida_paid_editions_are_allowed_on_the_users_own_machine(tmp_path, edition, use):
    d = _ida(tmp_path, edition, use)
    assert (d["allowed"], d["reason"]) == (True, rx.REASON_ALLOWED)
    assert d["provenance"]["license_basis"] == "user_declaration"
    assert d["provenance"]["declared_edition"] == edition


@pytest.mark.parametrize("edition", ["home", "pro", "classroom"])
def test_ida_paid_editions_need_the_host_declared_own(tmp_path, edition):
    d = _ida(tmp_path, edition, "commercial", own=False)
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_HOST_NOT_OWN)


@pytest.mark.parametrize("env", [SAAS, MULTI])
@pytest.mark.parametrize("edition,use", [("free", "non_commercial"), ("home", "commercial"),
                                         ("pro", "commercial"), ("classroom", "non_commercial")])
def test_any_non_oem_ida_on_a_shared_deployment_is_refused(tmp_path, env, edition, use):
    d = _ida(tmp_path, edition, use, env=env)
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_SHARED)
    assert "OEM" in d["term"]["text"]
    assert d["deployment"]["shared"] is True


@pytest.mark.parametrize("env", [LOCAL, SAAS, MULTI])
def test_ida_oem_is_allowed_on_shared_and_local(tmp_path, env):
    d = _ida(tmp_path, "oem", "commercial", env=env, own=False)
    assert (d["allowed"], d["reason"]) == (True, rx.REASON_ALLOWED)


def test_ida_declared_pro_but_installed_freeware_is_refused(tmp_path):
    det = dict(NO_INSTALL, installed=True, path="/opt/IDA Freeware 9.0/ida64", edition_detected="free")
    d = _ida(tmp_path, "pro", "commercial", detection=det)
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_CONTRADICTS_INSTALL)


def test_ida_freeware_install_dir_is_detected_as_free(tmp_path):
    home = tmp_path / "IDA Freeware 9.0"
    home.mkdir()
    (home / ("ida64.exe" if rx.host_platform() == "windows" else "ida64")).write_bytes(b"")
    det = rx.detect_engine("ida", env={"IDADIR": str(home)})
    assert det["installed"] is True
    assert det["edition_detected"] == "free"
    assert det["idalib"] is False


def test_ida_absent_reports_none_not_guesses():
    det = rx.detect_engine("ida", env={})
    assert det["installed"] is False
    assert det["idalib"] is None and det["edition_detected"] is None and det["version"] is None


@pytest.mark.parametrize("edition,use,reason", [
    ("ultimate", "commercial", rx.REASON_EDITION_INVALID),
    ("pro", "business", rx.REASON_USE_INVALID),
])
def test_ida_unknown_edition_or_use_is_refused_by_name(tmp_path, edition, use, reason):
    assert _ida(tmp_path, edition, use)["reason"] == reason


# --- Hopper -----------------------------------------------------------------

def test_hopper_personal_allowed_for_the_named_user(tmp_path):
    d = _hopper(tmp_path, "    edition: personal\n    use: commercial\n    user: alice\n")
    assert (d["allowed"], d["reason"]) == (True, rx.REASON_ALLOWED)


def test_hopper_personal_refused_for_another_user(tmp_path):
    d = _hopper(tmp_path, "    edition: personal\n    use: commercial\n    user: alice\n", user="bob")
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_USER_MISMATCH)


def test_hopper_personal_without_a_named_user_is_refused(tmp_path):
    d = _hopper(tmp_path, "    edition: personal\n    use: commercial\n")
    assert d["reason"] == rx.REASON_USER_UNDECLARED


def test_hopper_computer_allowed_on_that_host_only(tmp_path):
    body = "    edition: computer\n    use: commercial\n    host: box1\n"
    assert _hopper(tmp_path, body)["reason"] == rx.REASON_ALLOWED
    assert _hopper(tmp_path, body, host="box2")["reason"] == rx.REASON_HOST_MISMATCH
    assert _hopper(tmp_path, "    edition: computer\n    use: commercial\n")["reason"] == \
        rx.REASON_HOST_UNDECLARED


def test_hopper_demo_is_non_commercial_only(tmp_path):
    assert _hopper(tmp_path, "    edition: demo\n    use: non_commercial\n")["allowed"] is True
    d = _hopper(tmp_path, "    edition: demo\n    use: commercial\n")
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_NON_COMMERCIAL_ONLY)


@pytest.mark.parametrize("env", [SAAS, MULTI])
def test_hopper_on_a_shared_deployment_is_refused(tmp_path, env):
    d = _hopper(tmp_path, "    edition: personal\n    use: commercial\n    user: alice\n", env=env)
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_SHARED)


def test_hopper_is_off_on_windows(tmp_path):
    d = _hopper(tmp_path, "    edition: personal\n    use: commercial\n    user: alice\n", plat="windows")
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_PLATFORM)
    assert rx.detect_engine("hopper", env={}, plat="windows")["reason"] == rx.REASON_PLATFORM


# --- Undeclared / malformed / location --------------------------------------

@pytest.mark.parametrize("engine", ["ida", "hopper"])
def test_undeclared_commercial_engine_is_not_used_and_says_how_to_declare(tmp_path, engine):
    d = rx.evaluate(engine, env={rx.ENV_DECLARATION: str(tmp_path / "nope.yaml")},
                    detection=NO_INSTALL, plat="linux")
    assert (d["allowed"], d["reason"]) == (False, rx.REASON_UNDECLARED)
    assert d["declaration"]["status"] == "absent"
    assert "--template" in d["message"] and rx.ENV_DECLARATION in d["message"]
    assert d["provenance"]["license_basis"] == "none"


def test_malformed_declaration_is_refused_by_name(tmp_path):
    d = _decl(tmp_path, "engines: [not, a, mapping]\n")
    assert d["status"] == "malformed"
    out = rx.evaluate("ida", env=LOCAL, declaration=d, detection=NO_INSTALL, plat="linux")
    assert out["reason"] == rx.REASON_DECL_MALFORMED


def test_declaration_inside_a_git_work_tree_is_refused(tmp_path):
    repo = tmp_path / "somerepo"
    (repo / ".git").mkdir(parents=True)
    p = repo / "re_licenses.yaml"
    p.write_text("own_machine: true\nengines:\n  ida: {edition: pro, use: commercial}\n", encoding="utf-8")
    d = rx.load_declaration(p)
    assert d["status"] == "in_repo" and d["engines"] == {}
    out = rx.evaluate("ida", env=LOCAL, declaration=d, detection=NO_INSTALL, plat="linux")
    assert out["reason"] == rx.REASON_DECL_IN_REPO


def test_declaration_path_is_env_overridable(tmp_path):
    assert rx.declaration_path({rx.ENV_DECLARATION: str(tmp_path / "x.yaml")}) == tmp_path / "x.yaml"
    assert rx.declaration_path({}).parts[-3:] == (".config", "icdev", "re_licenses.yaml")


# --- No keys ----------------------------------------------------------------

def test_no_license_key_is_read_or_repeated(tmp_path):
    secret = "SERIAL-9F8E-7D6C-SECRET"
    d = _decl(tmp_path, f"own_machine: true\nlicense_key: {secret}\nengines:\n  ida:\n"
                        f"    edition: pro\n    use: commercial\n    serial: {secret}\n"
                        f"    key_file: /home/a/{secret}.hexlic\n")
    assert "engines.ida.serial" in d["ignored_fields"] and "license_key" in d["ignored_fields"]
    assert set(d["engines"]["ida"]) <= set(rx.ENGINE_FIELDS)
    out = rx.evaluate("ida", env=LOCAL, declaration=d, detection=NO_INSTALL, plat="linux")
    assert out["allowed"] is True
    assert secret not in json.dumps(d) and secret not in json.dumps(out, default=str)


def test_the_registry_holds_no_key_or_serial_fields():
    data = rx.load_registry(str(REPO / "args" / "re_engines.yaml"))

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield str(k).lower()
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    assert not {k for k in keys(data) if any(w in k for w in ("serial", "license_key", "secret"))}


# --- No install / download path ----------------------------------------------

_FORBIDDEN_IMPORTS = {"subprocess", "socket", "urllib", "http", "requests", "httpx", "ftplib",
                      "shutil", "pip", "ensurepip", "tarfile", "zipfile", "ctypes", "webbrowser"}


def test_the_gate_module_cannot_download_install_or_execute():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"system", "popen", "startfile", "execv", "execvp",
                                          "spawnv", "urlopen", "urlretrieve"}, node.func.attr
    assert not imported & _FORBIDDEN_IMPORTS, imported & _FORBIDDEN_IMPORTS


_VENDOR_HOSTS = ("hex-rays.com", "hopperapp.com")


def test_no_tools_module_carries_a_hopper_or_ida_vendor_url():
    """Vendor URLs live ONLY in args/re_engines.yaml (for quoting terms).

    A string constant naming a vendor host inside code is the precondition of
    any download/install path, so none may exist under tools/ or icdev/tools/.
    Text pre-filter first, then AST, so a comment mentioning a host is fine.
    """
    hits = []
    for root in ("tools", "icdev/tools"):
        for path in (REPO / root).rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not any(h in text for h in _VENDOR_HOSTS):
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                        any(h in node.value for h in _VENDOR_HOSTS):
                    hits.append(str(path.relative_to(REPO)))
    assert hits == []


def test_mirror_is_identical():
    a = (REPO / "tools" / "analyzers" / "re_engines.py").read_bytes()
    b = (REPO / "icdev" / "tools" / "analyzers" / "re_engines.py").read_bytes()
    assert a == b


def test_cli_template_and_json_run(tmp_path):
    env = {**os.environ, rx.ENV_DECLARATION: str(tmp_path / "absent.yaml"),
           "ICDEV_SAAS_MODE": "", "ICDEV_MULTI_TENANT": ""}
    out = subprocess.run([sys.executable, "-m", "tools.analyzers.re_engines", "--json"],
                         cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["ghidra"]["allowed"] is True
    assert data["ida"]["reason"] == rx.REASON_UNDECLARED
    tpl = subprocess.run([sys.executable, "-m", "tools.analyzers.re_engines", "--template"],
                         cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert "own_machine: true" in tpl.stdout and "edition:" in tpl.stdout
