# CUI // SP-CTI
"""The workflow templates' mode vocabulary IS detect_mode()'s return set (flx-studio-02).

THE DEFECT. ``args/workflow_templates/shared_iac_executors.yaml`` and
``ddc_workflow.yaml`` each carried a four-mode block -- ``aws | localstack |
sam | dry_run`` -- written as PROSE, describing what
``tools/studio/executors/_base.py::detect_mode()`` returns. flx-seam-01 renamed
the emulator to ``floci`` and flx-studio-01 moved ``detect_mode`` onto the seam;
neither touched the templates, because nothing pointed at them. The declaration
and the code then described two different systems and NOTHING WENT RED -- an
operator reading the template to decide whether a ``terraform apply`` was safe
was reading about a mode the executor can no longer return.

RED AT THE MERGE BASE. Both templates carried the vocabulary only inside YAML
COMMENTS, which ``yaml.safe_load`` discards, so ``executor_modes`` does not
exist there at all and every assertion below fails on its absence. That is the
same finding stated structurally: a declaration a parser cannot reach is a
declaration nothing can check.

WHY THE EXPECTED SET IS DERIVED AND NEVER SPELLED OUT
-----------------------------------------------------
``_detect_mode_return_modes()`` re-derives the vocabulary by walking
``detect_mode``'s OWN SOURCE for ``return`` statements and resolving
``emulator.MODE`` through the seam that declares it. A test carrying its own
copy of the list agrees with a stale template forever: it would prove the four
names were typed three times, which was never in question. Derived, it fails
the day the function grows, loses or renames a mode.

An unresolvable ``return`` is a hard failure, never a silently dropped one. A
set quietly missing an arm compares clean against a template that is also
missing it -- two empty sides reading as agreement, which is the defect
``claim_verifier`` exists to refuse.

DECLARED IS NOT REACHABLE, AND BOTH ARE ASSERTED
------------------------------------------------
Equality with the source's ``return`` set proves the template names what the
function can SAY. It does not prove an operator can get there:
``detect_mode``'s arms are ordered and short-circuit, so a mode can be
structurally present and unreachable behind an earlier branch -- declared and
inert, this repo's signature bug. ``_REACHING_ENV`` therefore drives the
SHIPPED ``detect_mode`` with a minimal environment per mode and asserts each
one actually comes back. Its keys are asserted equal to the derived set too, so
a new mode cannot be added here without someone stating how it is reached.

THE PACKAGED COPY IS CHECKED. ``icdev/data/args/`` is the FORGE data layer a
wheel ships and ``sync_package_tree.py`` regenerates it at prebuild -- so a
hand-edit to ``args/`` alone leaves the INSTALLED template stale while the
source checkout reads correct. That is kpr-rvfy-06's shape exactly, and it is
cheap to pin here.

SCOPE OF THE STALE-NAME CHECK. The retired product name is derived from
``emulator.DEPRECATED_ALIASES`` rather than hard-coded, so it follows the next
rename. It is asserted over THESE TEMPLATES ONLY. The deprecated ``LOCALSTACK_*``
env aliases are still honoured and are documented in ``.env.example``
(flx-seam-02) -- that is the right place for them; a workflow template's job is
to name the mode the executor returns.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.cloud import emulator
from tools.studio.executors import _base

_ROOT = Path(__file__).resolve().parents[2]

#: Every template that declares the executor mode vocabulary, and its packaged twin.
_TEMPLATES = (
    Path("args/workflow_templates/shared_iac_executors.yaml"),
    Path("args/workflow_templates/ddc_workflow.yaml"),
)
_PACKAGED_ROOT = Path("icdev/data")

#: A minimal environment that must produce each mode. Deliberately NOT a copy of
#: detect_mode()'s rule -- it is one witness per arm, and the assertion is made
#: by calling the shipped function rather than by re-stating its conditions.
_REACHING_ENV: dict[str, dict[str, str]] = {
    "floci": {"FLOCI_ENABLED": "true"},
    "sam": {"AWS_SAM_LOCAL": "true"},
    "aws": {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLEEXAMPLE"},
    "dry_run": {},
}


def _detect_mode_return_modes() -> set[str]:
    """Re-derive detect_mode()'s return vocabulary from its own source."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(_base.detect_mode)))
    modes: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            modes.add(value.value)
        elif (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "emulator"
        ):
            resolved = getattr(emulator, value.attr, None)
            if isinstance(resolved, str):
                modes.add(resolved)
            else:
                unresolved.append(ast.unparse(value))
        else:
            unresolved.append(ast.unparse(value))

    assert not unresolved, (
        "detect_mode() has a return this test cannot resolve to a mode name: "
        f"{unresolved}. Teach the resolver rather than letting it drop the arm "
        "-- a silently short set compares clean against a template that is also "
        "missing the mode."
    )
    assert modes, "derived NO modes from detect_mode() -- the resolver is broken, not the function"
    return modes


def _retired_mode_names() -> set[str]:
    """Product names the seam has moved off, from its own deprecation map.

    ``LOCALSTACK_ENABLED`` -> ``localstack``. Derived so the check follows the
    NEXT rename instead of encoding today's.
    """
    return {alias.split("_", 1)[0].lower() for alias in emulator.DEPRECATED_ALIASES.values()}


def _text(path: Path) -> str:
    """File content with newlines normalised to ``\\n``."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _load(relpath: Path) -> dict:
    return yaml.safe_load(_text(_ROOT / relpath))


@pytest.mark.parametrize("relpath", _TEMPLATES, ids=lambda p: p.name)
def test_template_mode_vocabulary_equals_detect_mode_return_set(relpath: Path) -> None:
    doc = _load(relpath)
    declared = doc.get("executor_modes")
    assert isinstance(declared, dict) and declared, (
        f"{relpath} declares no `executor_modes` mapping. The vocabulary must be "
        "DATA -- a comment cannot be checked, which is how this block went on "
        "naming a mode detect_mode() no longer returns."
    )
    assert set(declared) == _detect_mode_return_modes(), (
        f"{relpath} `executor_modes` disagrees with detect_mode()'s return set. "
        "Update the template; never widen this test."
    )


@pytest.mark.parametrize("relpath", _TEMPLATES, ids=lambda p: p.name)
def test_every_declared_mode_carries_a_gloss(relpath: Path) -> None:
    """A bare name is not a declaration -- say what selects the mode."""
    for mode, gloss in _load(relpath)["executor_modes"].items():
        assert isinstance(gloss, str) and gloss.strip(), f"{relpath}: `{mode}` has no gloss"


def test_reaching_env_covers_exactly_the_declared_modes() -> None:
    assert set(_REACHING_ENV) == _detect_mode_return_modes(), (
        "a mode was added to or removed from detect_mode() without stating the "
        "environment that reaches it"
    )


@pytest.mark.parametrize("mode", sorted(_REACHING_ENV), ids=str)
def test_every_declared_mode_is_reachable(mode: str) -> None:
    """Declared is not reachable. Ask the SHIPPED function, not a copy of its rule."""
    assert _base.detect_mode(_REACHING_ENV[mode]) == mode, (
        f"`{mode}` is declared but no environment produces it -- a mode behind an "
        "earlier short-circuiting arm is declared and inert."
    )


@pytest.mark.parametrize("relpath", _TEMPLATES, ids=lambda p: p.name)
def test_no_retired_emulator_name_survives_in_the_template(relpath: Path) -> None:
    text = _text(_ROOT / relpath)
    for retired in _retired_mode_names():
        assert not re.search(rf"\b{re.escape(retired)}\b", text, re.IGNORECASE), (
            f"{relpath} still names the retired emulator `{retired}`. The mode is "
            f"`{emulator.MODE}`; the deprecated env aliases belong in .env.example."
        )


@pytest.mark.parametrize("relpath", _TEMPLATES, ids=lambda p: p.name)
def test_packaged_copy_is_not_stale(relpath: Path) -> None:
    """The wheel ships icdev/data/args/ -- a fix applied to one copy fixes nothing there."""
    source = _ROOT / relpath
    packaged = _ROOT / _PACKAGED_ROOT / relpath
    assert packaged.exists(), f"{packaged} is missing -- the FORGE data layer lost a template"
    # CONTENT, with newlines normalised -- not raw bytes. `.gitattributes` pins
    # these to `eol=lf` at rest, but a working copy touched by a tool that writes
    # platform newlines differs from its twin by every line while saying exactly
    # the same thing. A staleness check that fires on a line ending is a check
    # people learn to skip.
    assert _text(packaged) == _text(source), (
        f"{packaged} has drifted from {relpath}. Re-sync with "
        "`python tools/installer/sync_package_tree.py`, or copy it by hand."
    )
