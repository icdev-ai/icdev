# CUI // SP-CTI
"""The Studio provider override is RENAMED, and renaming it changed nothing (flx-studio-01).

``detect_mode()`` already decides on the seam and answers ``floci``
(flx-seam-01). What was left behind were two identifiers still carrying the old
product's name -- ``localstack_docker_endpoint`` and
``LOCALSTACK_PROVIDER_OVERRIDE`` -- and three ``terraform_*`` executors
importing them.

WHY A BYTE-IDENTITY TEST AND NOT A "LOOKS RIGHT" ONE
----------------------------------------------------
A rename of the constant that quietly also edits the Terraform it emits is a
behaviour change wearing a rename's commit message, and the failure mode is
GREEN: an ``endpoints{}`` entry dropped or a ``skip_*`` flag flipped produces a
provider block terraform still parses, so ``terraform validate`` passes and the
plan simply talks to somewhere else. So the whole block is frozen here as it
stood BEFORE the rename (read out of ``_base.py`` at merge base ``b4e0f214f``,
2026-09-04) and the two are compared BYTE FOR BYTE with the same ``ep`` and
``region`` substituted in.

floci consumes the identical shape -- it is a LocalStack drop-in speaking the
stock ``hashicorp/aws`` provider's ``endpoints{}`` / ``s3_use_path_style`` /
``skip_*`` / dummy-credential contract -- so there is nothing in the block that
SHOULD have moved. The region default moved from ``us-east-1`` to
``us-gov-west-1`` in flx-seam-01, and it moved in ``emulator.DEFAULT_REGION``,
not in this template: the template has only ever carried a ``{region}``
placeholder.

If a later card legitimately needs to change this block, this test fails and
that is the intended cost -- the frozen copy is the record of what the block was
when the rename claimed to change nothing, and a new baseline is a decision
somebody makes on purpose rather than a diff that slides past review.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.cloud import emulator  # noqa: E402
from tools.studio.executors import _base  # noqa: E402

_EXECUTORS = _ROOT / "tools" / "studio" / "executors"

#: The provider block EXACTLY as ``LOCALSTACK_PROVIDER_OVERRIDE`` held it at the
#: merge base, before this card renamed the constant. Do not "tidy" this string.
_FROZEN_PRE_RENAME_TEMPLATE = (
    "# LocalStack/SAM endpoint override — auto-injected by ICDEV Studio executor\n"
    'provider "aws" {{\n'
    '  access_key                  = "test"\n'
    '  secret_key                  = "test"\n'
    '  region                      = "{region}"\n'
    "  skip_credentials_validation = true\n"
    "  skip_metadata_api_check     = true\n"
    "  skip_requesting_account_id  = true\n"
    "  # Force path-style S3 URLs so Docker containers can reach LocalStack\n"
    "  # (virtual-hosted style — bucket.host — breaks inside Docker networking)\n"
    "  s3_use_path_style           = true\n"
    "  endpoints {{\n"
    '    s3          = "{ep}"\n'
    '    ec2         = "{ep}"\n'
    '    rds         = "{ep}"\n'
    '    neptune     = "{ep}"\n'
    '    elasticache = "{ep}"\n'
    '    iam         = "{ep}"\n'
    '    ssm         = "{ep}"\n'
    '    sts         = "{ep}"\n'
    '    kms         = "{ep}"\n'
    "  }}\n"
    "}}\n"
)

#: Identifiers this card retires. A survivor is a stale name, and in the
#: ``from ... import`` case an ImportError that takes the whole executor
#: package down at import time.
_RETIRED_NAMES = ("localstack_docker_endpoint", "LOCALSTACK_PROVIDER_OVERRIDE")


def _module_names(path: Path) -> set[str]:
    """Every bare identifier and imported alias in ``path``, docstrings dropped.

    AST rather than a substring scan for the reason the seam test gives: this
    package's own prose NAMES what it stopped using, and ``in src`` cannot tell
    an explanation from a reference.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                names.add(node.asname)
    return names


# -- The two renamed helpers exist -----------------------------------------

def test_emulator_docker_endpoint_replaces_the_localstack_named_helper():
    assert hasattr(_base, "emulator_docker_endpoint")
    assert not hasattr(_base, "localstack_docker_endpoint"), (
        "the old name survives, so nothing forced a consumer to move"
    )


def test_floci_provider_override_replaces_the_localstack_named_constant():
    assert hasattr(_base, "FLOCI_PROVIDER_OVERRIDE")
    assert not hasattr(_base, "LOCALSTACK_PROVIDER_OVERRIDE")


# -- The rewrite is unchanged ----------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:4566", "http://host.docker.internal:4566"),
        ("http://127.0.0.1:4566", "http://host.docker.internal:4566"),
        ("https://localhost:4566/", "https://host.docker.internal:4566/"),
        # Not a loopback name: left exactly alone, so a remote emulator
        # declared by an operator is not rewritten out from under them.
        ("http://floci.internal:4566", "http://floci.internal:4566"),
        ("http://host.docker.internal:4566", "http://host.docker.internal:4566"),
    ],
)
def test_the_docker_endpoint_rewrite_is_identical_under_the_new_name(raw, expected):
    assert _base.emulator_docker_endpoint(raw) == expected


# -- The block itself did not move -----------------------------------------

def test_the_rendered_provider_block_is_byte_identical_to_the_pre_rename_one():
    """THE CARD'S ACCEPTANCE CRITERION. Same ep, same region, same bytes."""
    ep = "http://host.docker.internal:4566"
    region = "us-gov-west-1"

    assert (
        _base.FLOCI_PROVIDER_OVERRIDE.format(ep=ep, region=region)
        == _FROZEN_PRE_RENAME_TEMPLATE.format(ep=ep, region=region)
    )


def test_the_block_is_byte_identical_for_every_region_and_endpoint_pair():
    """The two placeholders are the ONLY things that may differ between renders.

    Rendering both templates over several (ep, region) pairs proves the
    equality above is not an artifact of the one pair chosen: a template that
    had, say, hard-coded a region somewhere would agree on ``us-gov-west-1``
    and diverge on the next one.
    """
    pairs = [
        ("http://host.docker.internal:4566", "us-gov-west-1"),
        ("http://host.docker.internal:4566", "us-east-1"),
        ("http://10.0.0.5:4566", "us-gov-east-1"),
    ]
    for ep, region in pairs:
        assert (
            _base.FLOCI_PROVIDER_OVERRIDE.format(ep=ep, region=region)
            == _FROZEN_PRE_RENAME_TEMPLATE.format(ep=ep, region=region)
        ), f"the provider block changed for ep={ep} region={region}"


def test_the_rendered_block_still_carries_every_endpoint_and_skip_flag():
    """Belt to the byte-identity braces, stated in terraform's own terms.

    A dropped ``endpoints{}`` entry or a flipped ``skip_*`` still PARSES, so
    ``terraform validate`` is not the check that would catch it.
    """
    rendered = _base.FLOCI_PROVIDER_OVERRIDE.format(
        ep="http://host.docker.internal:4566", region="us-gov-west-1"
    )
    for service in ("s3", "ec2", "rds", "neptune", "elasticache",
                    "iam", "ssm", "sts", "kms"):
        assert f'{service:<11} = "http://host.docker.internal:4566"' in rendered
    for flag in ("skip_credentials_validation", "skip_metadata_api_check",
                 "skip_requesting_account_id", "s3_use_path_style"):
        assert f"{flag}           " in rendered or f"{flag} " in rendered
        assert "= true" in rendered
    assert 'access_key                  = "test"' in rendered
    assert 'secret_key                  = "test"' in rendered


# -- The region default is the emulator's, and it is GovCloud --------------

def test_the_region_default_under_the_override_is_us_gov_west_1():
    """Nothing declared -> the seam's default, which is ICDEV's target partition.

    Read through ``emulator.region`` rather than restated here: a second
    spelling of the default is a constant that can drift away from the one the
    executors actually use.
    """
    assert emulator.region({}) == "us-gov-west-1"
    assert emulator.DEFAULT_REGION == "us-gov-west-1"

    rendered = _base.FLOCI_PROVIDER_OVERRIDE.format(
        ep=_base.emulator_docker_endpoint(emulator.endpoint({})),
        region=emulator.region({}),
    )
    assert 'region                      = "us-gov-west-1"' in rendered
    assert "host.docker.internal:4566" in rendered


# -- Every consumer moved --------------------------------------------------

@pytest.mark.parametrize(
    "module",
    ["terraform_plan.py", "terraform_apply.py", "terraform_destroy.py",
     "aws_config_executor.py", "migration_reporter.py", "_base.py"],
)
def test_no_studio_executor_still_names_a_retired_identifier(module):
    """A ``from ._base import LOCALSTACK_PROVIDER_OVERRIDE`` left behind is an
    ImportError that takes EVERY executor in the package down at import."""
    names = _module_names(_EXECUTORS / module)
    for retired in _RETIRED_NAMES:
        assert retired not in names, f"{module} still names {retired}"


@pytest.mark.parametrize(
    "module", ["terraform_plan.py", "terraform_apply.py", "terraform_destroy.py"]
)
def test_the_three_iac_executors_import_the_renamed_pair(module):
    """The positive half. The test above is satisfied by a file that imports
    NEITHER name -- which is also how the override silently stops being written.
    """
    names = _module_names(_EXECUTORS / module)
    assert "FLOCI_PROVIDER_OVERRIDE" in names
    assert "emulator_docker_endpoint" in names


def test_the_written_override_artifact_is_no_longer_named_for_localstack():
    """The rename follows through to what the executors WRITE.

    The ``.tf`` filename and the finding's ``check`` key are the two places the
    old product name reached a reader. Neither has a consumer anywhere in the
    tree (grepped 2026-09-04: only these three writers and their icdev/ twins),
    so this is a rename with no contract behind it -- which is precisely why it
    would otherwise sit there indefinitely.
    """
    for module in ("terraform_plan.py", "terraform_apply.py", "terraform_destroy.py"):
        tree = ast.parse((_EXECUTORS / module).read_text(encoding="utf-8"))
        literals = {
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert "localstack_override.tf" not in literals, module
        assert "localstack_override" not in literals, module
        assert "floci_override.tf" in literals, module
