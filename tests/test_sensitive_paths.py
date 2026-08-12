# CUI // SP-CTI
"""The sensitive-path inventory itself (exa-bench-09).

``tests/test_skip_permissions_compensating_controls.py`` measures what the three
CONSUMERS do with this inventory. This module tests the inventory: what it
matches, what it deliberately does not, and the two ways it can be wrong in a
direction nobody notices.

Both failure directions matter and neither is theoretical:

* **too narrow** — the original defect. ``**/credentials.json`` looks like it
  covers a credential store right up until the store is ``~/.aws/credentials``,
  which has no extension at all.
* **too broad** — a guard that halts on a document *about* a credential, or on
  ``touch``, is a guard operators route around, and one that also silently
  reports a neighbouring gap (exa-bench-07) as closed.

No database, no LLM, no network: matching is pure, so this runs in a cold
worktree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.security import sensitive_paths as sp

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The five ``args/file_access_tiers.yaml`` missed, each with the reason its
#: old glob list could not reach it.
MISSED = (
    ("/home/victim/.aws/credentials", "no extension; the glob was credentials.JSON"),
    ("/home/victim/.config/gh/hosts.yml", "the gh OAuth token store, unenumerated"),
    ("/home/victim/.kube/config", "a file literally named `config`"),
    ("/home/victim/.docker/config.json", "registry auth, not a `credentials.json`"),
    ("/home/victim/.netrc", "plaintext login, no extension"),
)


class TestWhatItMatches:
    @pytest.mark.parametrize("path,why", MISSED, ids=[p.rsplit("/", 1)[-1] for p, _ in MISSED])
    def test_the_five_that_were_missed(self, path, why):
        assert sp.is_sensitive(path), why

    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            "/home/victim/.env",
            "/home/victim/.ssh/id_rsa",
            "C:/Users/victim/.ssh/id_ed25519",
            "/srv/app/certs/server.pem",
            "/home/victim/.gnupg/secring.gpg",
            "/home/victim/.git-credentials",
            "/home/victim/.npmrc",
            "infra/terraform.tfstate",
            "deploy/secrets.yaml",
        ],
    )
    def test_material_every_surface_already_agreed_on(self, path):
        assert sp.is_sensitive(path)

    def test_windows_separators_and_home_shorthand_both_resolve(self):
        """A path is matched as WRITTEN, on whichever platform reads it.

        ``Path.resolve()`` is deliberately not used: the path may name a host
        this process cannot see, and resolving it would either raise or invent a
        cwd-relative answer — the cwd sensitivity CLAUDE.md warns about.
        """
        assert sp.is_sensitive("C:\\Users\\victim\\.netrc")
        assert sp.is_sensitive("~/.aws/credentials")
        assert sp.is_sensitive("'/home/victim/.netrc'")   # quoted, from a command line

    def test_the_match_says_which_entry_named_it(self):
        hit = sp.match("/home/victim/.kube/config")
        assert hit is not None
        assert hit.label == "kubeconfig"
        assert hit.pattern == "**/.kube/config"
        assert "token" in hit.detail.lower()


class TestWhatItDeliberatelyDoesNot:
    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "tools/foo.py",
            "args/security_gates.yaml",
            "/etc/hosts",
            "/home/victim/.bashrc",                      # a WRITE target: exa-bench-07
            "C:/Windows/System32/drivers/etc/hosts",     # a WRITE target: exa-bench-07
        ],
    )
    def test_ordinary_paths_are_ordinary(self, path):
        assert not sp.is_sensitive(path)

    def test_committed_templates_are_carved_out(self):
        """Blocking a checked-in template makes the guard look broken, not strict."""
        assert not sp.is_sensitive(".env.sample")
        assert not sp.is_sensitive(".env.example")
        assert not sp.is_sensitive("docs/.env.example")
        assert sp.is_sensitive(".env.production")

    def test_an_exclusion_beats_a_match_rather_than_racing_it(self):
        """``.env.sample`` matches ``.env.*``; the carve-out must win regardless."""
        assert sp.match(".env.sample") is None


class TestToolArguments:
    def test_only_path_like_keys_are_inspected(self):
        assert sp.sensitive_args({"path": "/home/victim/.netrc"})
        assert not sp.sensitive_args(
            {"path": "docs/creds.md", "content": "never commit ~/.netrc"}
        ), "prose about a credential is not a read of one"

    def test_a_list_valued_path_argument_is_inspected_elementwise(self):
        hits = sp.sensitive_args({"paths": ["README.md", "/home/victim/.netrc"]})
        assert [h.label for h in hits] == ["netrc"]

    def test_a_non_dict_input_is_not_an_error(self):
        assert sp.sensitive_args(None) == []
        assert sp.sensitive_args("cat ~/.netrc") == []


class TestShellCommands:
    @pytest.mark.parametrize(
        "command",
        [
            "cat /home/victim/.aws/credentials",
            "type C:\\Users\\victim\\.netrc",
            "head -n 5 /home/victim/.kube/config",
            "grep token /home/victim/.config/gh/hosts.yml",
            "Get-Content /home/victim/.docker/config.json",
            "sudo cat /root/.netrc",
            "base64 /home/victim/.ssh/id_rsa",
            "wc -l < /home/victim/.netrc",            # input redirection, no read verb
        ],
    )
    def test_a_read_of_a_credential_is_seen(self, command):
        assert sp.command_disclosure(command), command

    @pytest.mark.parametrize(
        "command",
        [
            "env | grep -i key",
            "printenv AWS_SECRET_ACCESS_KEY",
            "gh auth token",
            "aws configure get aws_secret_access_key",
        ],
    )
    def test_a_credential_read_with_no_path_is_seen(self, command):
        """A path inventory structurally cannot match these, so they are named."""
        assert sp.disclosure_match(command), command

    @pytest.mark.parametrize(
        "command",
        [
            "touch /home/victim/.ssh/authorized_keys",
            "mkdir -p /home/victim/.aws",
            "rm -rf /home/victim/.aws",
            "echo x > /home/victim/.netrc",
        ],
    )
    def test_a_WRITE_to_a_sensitive_path_is_not_this_check(self, command):
        """exa-bench-07's gap, and it must stay visibly open.

        Absorbing write verbs here would flip that card's probes to COVERED
        while nothing about worktree containment changed — an unrecorded fix in
        reverse, which is the failure mode this whole series exists to catch.
        """
        assert not sp.command_disclosure(command), command

    @pytest.mark.parametrize(
        "command",
        ["git status", "cat README.md", "pytest tests/ -v", "ls -la /home/victim"],
    )
    def test_ordinary_commands_are_ordinary(self, command):
        assert not sp.command_disclosure(command)

    def test_an_unbalanced_quote_does_not_defeat_the_parser(self):
        """``shlex.split`` raises on this; a guard a malformed command defeats
        is not a guard."""
        assert sp.command_disclosure("cat '/home/victim/.netrc")


class TestTheInventoryIsTheSourceOfTruth:
    def test_it_loads_from_the_args_layer_not_from_python(self):
        cfg = sp._find_config()
        assert cfg is not None and cfg.name == "sensitive_paths.yaml"
        assert cfg.parent.name == "args"

    def test_exclusions_are_emitted_in_the_tier_files_bang_spelling(self):
        """So ``args/file_access_tiers.yaml`` can consume the list verbatim."""
        patterns = sp.patterns()
        assert "!.env.sample" in patterns
        assert "**/.aws/credentials" in patterns

    def test_a_missing_config_falls_back_rather_than_failing_open(self, monkeypatch):
        """A file that will not load must never make a credential look ordinary."""
        monkeypatch.setenv(sp.CONFIG_ENV, str(REPO_ROOT / "does-not-exist.yaml"))
        sp.reset_cache()
        try:
            for path, _ in MISSED:
                assert sp.is_sensitive(path), f"{path} ordinary under the fallback"
        finally:
            monkeypatch.delenv(sp.CONFIG_ENV, raising=False)
            sp.reset_cache()

    def test_the_module_has_no_first_party_imports(self):
        """It is loaded BY PATH from the pre_tool_use hook.

        That hook is a fresh interpreter on every tool call and ``import tools``
        alone costs ~92ms there, so a first-party import added here is a cost
        paid per tool call, forever.
        """
        import ast

        path = REPO_ROOT / "tools" / "security" / "sensitive_paths.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Parsed, not grepped: the module's own docstring explains this rule and
        # names the imports it forbids, so a substring search matches the prose.
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        offenders = [
            name for name in imported
            if name.split(".")[0] in ("tools", "icdev")
        ]
        assert not offenders, f"{offenders} makes the hook pay for them"
