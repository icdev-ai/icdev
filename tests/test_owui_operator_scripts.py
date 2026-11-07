"""owui-ship-09: the governed demo's operator scripts are versioned, safe to read and safe to run.

tools/openwebui/refresh_gateway.sh and tools/openwebui/verify_roles.sh lived only outside any repo
on 2026-11-07 -- one in ~/apps, one in a session scratchpad that was wiped twice that day. And the
Keycloak password grant verify_roles needs had been left ENABLED on the demo's open-webui client.
These pin what makes the scripts safe: the refresh swaps only after every smoke check, the role
check turns the password grant back off in an EXIT trap set before it turns it on, and neither
carries a credential.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REFRESH = REPO / "tools" / "openwebui" / "refresh_gateway.sh"
VERIFY = REPO / "tools" / "openwebui" / "verify_roles.sh"
SCRIPTS = (REFRESH, VERIFY)

# Shapes of the secrets these scripts handle; none may appear as a literal.
CREDENTIAL = re.compile(r"icdev_[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|(?:password|secret)\s*=\s*['\"][^$'\"]{6,}",
                        re.IGNORECASE)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_is_executable_valid_bash_and_carries_no_credential(script):
    assert script.is_file() and os.access(script, os.X_OK)
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True, check=False).returncode == 0
    assert not CREDENTIAL.findall(script.read_text(encoding="utf-8"))


def test_refresh_swaps_the_live_gateway_only_after_every_smoke_check():
    text = REFRESH.read_text(encoding="utf-8")
    swap = text.index('mv "$D/src" ')
    checks = [m.start() for m in re.finditer(r'\|\| fail "', text)]
    assert len(checks) >= 5          # start, /v1/models, chat, MCP initialize, nist_lookup
    assert max(checks) < swap
    assert text.index("systemctl --user restart") > swap


def test_verify_turns_the_password_grant_off_on_exit_before_it_turns_it_on():
    text = VERIFY.read_text(encoding="utf-8")
    trap = text.index("trap 'grants false")
    assert "directAccessGrantsEnabled=$1" in text
    assert trap < text.index("grants true")


def test_verify_never_calls_the_write_tool_as_admin():
    text = VERIFY.read_text(encoding="utf-8")
    branch = text.index('if [ "$group" = "$ADMIN_GROUP" ]')
    assert text.index("project_create", branch) > branch
    assert "not called for" in text[branch:text.index("fi", branch)]


def test_both_are_registered_and_documented():
    manifest = (REPO / "tools" / "manifest" / "external-integration.md").read_text(encoding="utf-8")
    doc = (REPO / "docs" / "features" / "openwebui-governed-client.md").read_text(encoding="utf-8")
    for script in SCRIPTS:
        rel = script.relative_to(REPO).as_posix()
        assert rel in manifest and rel in doc
