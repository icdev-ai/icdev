# CUI // SP-CTI
"""The emulator connector answers by the name `floci`, and says when it cannot
answer at all (flx-bridge-01).

TWO THINGS ARE PINNED HERE AND THEY ARE DIFFERENT QUESTIONS.

1. **The rename is real.** ``localstack_connector.py`` is gone, the registry key
   is ``floci``, and the class is ``FlociConnector``. A connector registers as an
   IMPORT SIDE EFFECT, so the interesting assertion is not "the class exists" --
   it is "a caller that names ``floci`` and imports nothing gets an instance".
   Importing the module in the test would prove only that the test imported it,
   which is exactly the shape that left all 33 connectors unreachable before
   cef-fnd-03. So reachability is asserted in a FRESH INTERPRETER that resolves
   the name through the broker's own import spelling and never touches the
   connector module by hand.

2. **An unanswerable question is not an empty answer.** ``lambda_functions`` is
   backed by a CONTAINER, so a host with no docker socket cannot serve it. It
   used to reach boto3, raise, and come back as an ``error``; a caller that
   flattened that to a list saw ``[]`` -- indistinguishable from an account with
   no functions. That is the ``rmf-disc-02`` defect (every local NQE query raised
   on a table with no DDL, was swallowed, returned ``[]``, and the attack-surface
   map correlated every advisory against ZERO devices while reporting success).
   The connector now returns ``unsupported_without_docker``.

WHAT ``broker.list_available()`` CAN AND CANNOT SAY is pinned too, because it is
easy to reach for as a reachability check and it is not one -- see
``test_list_available_answers_the_grant_question_not_the_reachability_one``.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tools.cloud import emulator
from tools.databridge.connector import ConnectorRequest, ConnectorResponse
from tools.databridge.connectors.floci_connector import (
    TABLES,
    FlociConnector,
    table_is_docker_backed,
    table_service,
)

_ROOT = Path(__file__).resolve().parents[2]

#: The seven logical tables the connector has always served. Spelled out rather
#: than read from TABLES: this is the contract the rename had to PRESERVE, and a
#: list derived from the module under test cannot notice the module dropping one.
_EXPECTED_TABLES = (
    "health",
    "services",
    "s3_buckets",
    "dynamodb_tables",
    "lambda_functions",
    "sqs_queues",
    "ecr_repositories",
)


@pytest.fixture
def emulator_env(monkeypatch):
    """A clean, deterministic emulator environment.

    Every switch the seam reads is deleted first: this suite must not report a
    different verdict on a developer box that happens to export DOCKER_HOST.
    """
    for key in (
        "FLOCI_ENABLED",
        "FLOCI_ENDPOINT",
        "FLOCI_REGION",
        "FLOCI_DOCKER_SOCKET",
        "LOCALSTACK_ENABLED",
        "LOCALSTACK_ENDPOINT",
        "LOCALSTACK_REGION",
        "DOCKER_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _enabled_with_docker_absent(env):
    """Emulator on, docker socket PROVEN absent -> status degraded_no_docker."""
    env.setenv("FLOCI_ENABLED", "true")
    # A path that starts with "/" is stat-ed, and this one does not exist, so
    # the seam reaches BASIS_SOCKET_ABSENT on every platform -- including
    # Windows, where the *default* answer is deliberately "cannot tell".
    env.setenv("DOCKER_HOST", "/nonexistent/flx-bridge-01/docker.sock")


def _enabled_with_docker_unproven(env):
    """Emulator on, socket declared in a form the seam cannot parse -> None."""
    env.setenv("FLOCI_ENABLED", "true")
    env.setenv("DOCKER_HOST", "not-a-parseable-socket-spec")


# -- 1. The rename, and reachability by NAME --------------------------------


def test_connector_is_reachable_by_name_without_this_test_importing_it():
    """A fresh interpreter resolves `floci` having imported no connector module.

    Run as a SUBPROCESS on purpose. This module imports ``floci_connector`` at
    the top for the unit tests below, and that import would register the class
    into the shared registry -- so an in-process assertion here would pass even
    if nothing on a runtime path could ever reach the connector. The subprocess
    asserts the module is absent from ``sys.modules`` BEFORE the lookup, so the
    registry's own autoload is the only thing that can produce the instance.

    The lookup uses ``icdev.tools.databridge.registry``, which is the spelling
    ``broker.fetch()`` imports.
    """
    probe = (
        "import sys\n"
        "from icdev.tools.databridge.registry import get_connector_instance\n"
        "mod = 'tools.databridge.connectors.floci_connector'\n"
        "assert mod not in sys.modules, 'connector was already imported: ' + mod\n"
        "assert 'icdev.' + mod not in sys.modules\n"
        "inst = get_connector_instance('floci')\n"
        "assert inst is not None, 'floci did not resolve through the registry'\n"
        "print(type(inst).__name__)\n"
        "print(inst.connector_name)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr[-2000:]!r}"
    assert proc.stdout.split() == ["FlociConnector", "floci"], proc.stdout


def test_the_localstack_name_is_gone_rather_than_aliased():
    """No module file, and the registry answers None.

    An alias kept "for compatibility" is the failure this rename exists to
    avoid: two names for one connector means two things to keep in step, and a
    caller left on the old one never learns it is stale.
    """
    for rel in (
        "tools/databridge/connectors/localstack_connector.py",
        "icdev/tools/databridge/connectors/localstack_connector.py",
    ):
        assert not (_ROOT / rel).exists(), f"{rel} should have been renamed away"

    from icdev.tools.databridge.registry import get_connector_instance

    assert get_connector_instance("localstack") is None


def test_no_source_file_still_imports_the_old_module():
    """A stale import fails at IMPORT time, which is loud -- but only if it runs.

    ``floci_adapter._build_connector`` imports the connector INSIDE the function
    (deferred, so the adapter does not pay for it at import), which means a stale
    import there would surface only when someone actually built a connector.
    Asserted over the source instead.
    """
    offenders = []
    for base in ("tools", "icdev/tools"):
        for path in (_ROOT / base).rglob("*.py"):
            try:
                src = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "localstack_connector" in src or "LocalStackConnector" in src:
                offenders.append(path.relative_to(_ROOT).as_posix())
    assert offenders == [], "stale connector reference(s):\n" + "\n".join(sorted(offenders))


def test_the_seven_logical_tables_survived_the_rename():
    assert tuple(TABLES) == _EXPECTED_TABLES
    assert FlociConnector().list_tables() == list(_EXPECTED_TABLES)


# -- 2. docker_backed, DERIVED from the seam --------------------------------


@pytest.mark.parametrize("table", _EXPECTED_TABLES)
def test_every_table_declares_docker_backed(table):
    """Declared for EVERY table, including the ones that never need a socket.

    ``False`` is an answer. A flag present only on the container-backed tables
    would make its ABSENCE ambiguous -- "not container-backed" and "nobody
    said" would render identically, which is the whole defect one level up.
    """
    schema = FlociConnector().infer_schema(table)
    assert "docker_backed" in schema.metadata, schema.metadata
    assert isinstance(schema.metadata["docker_backed"], bool)
    assert schema.metadata["table"] == table
    assert schema.metadata["service"] == table_service(table)


def test_only_lambda_functions_is_container_backed_today():
    backed = {t for t in _EXPECTED_TABLES if table_is_docker_backed(t)}
    assert backed == {"lambda_functions"}
    # The emulator's OWN health API is not an emulated AWS service, so it has no
    # service name and can never be refused for a missing socket.
    assert table_service("health") is None
    assert table_service("services") is None


def test_docker_backed_tracks_the_seam_rather_than_a_second_list(monkeypatch):
    """Behavioural proof of derivation, not a grep.

    A hand-written copy of the service names here would pass every other test in
    this file and then go silently stale the moment the seam gains a service.
    Widening ``CONTAINER_BACKED_SERVICES`` must move this connector's answer.
    """
    assert table_is_docker_backed("s3_buckets") is False
    monkeypatch.setattr(
        emulator, "CONTAINER_BACKED_SERVICES", frozenset({"s3"}), raising=True
    )
    assert table_is_docker_backed("s3_buckets") is True
    assert table_is_docker_backed("lambda_functions") is False


def test_infer_schema_does_not_raise_for_any_table():
    """It raised TypeError on EVERY call before this card.

    ``SchemaDefinition`` has no ``table_name`` field; the old code passed one as
    a keyword. Nothing caught it because nothing called ``infer_schema``, which
    is the declared-but-unconsumed shape this repo ships most.
    """
    c = FlociConnector()
    for table in (*_EXPECTED_TABLES, "not_a_table"):
        schema = c.infer_schema(table)
        assert schema.fields, table


# -- 3. unsupported_without_docker, NEVER an empty list ---------------------


def test_container_backed_table_reports_unsupported_not_an_empty_list(emulator_env):
    _enabled_with_docker_absent(emulator_env)
    assert emulator.status(probe=False) == emulator.STATUS_DEGRADED_NO_DOCKER

    resp = FlociConnector().read(ConnectorRequest(table_name="lambda_functions"))

    assert resp.status == emulator.UNSUPPORTED_WITHOUT_DOCKER
    # The distinction lives in the STATUS. `data` is empty because there is
    # genuinely nothing to hand back -- inventing rows would be worse -- so the
    # assertion that matters is that no reader checking status can take this for
    # a row count of zero.
    assert resp.status != "ok"
    assert resp.data == []
    assert resp.row_count == 0
    assert resp.errors, "a refusal with no stated reason is unactionable"
    assert resp.metadata["docker_backed"] is False
    assert resp.metadata["service"] == "lambda"
    assert resp.metadata["emulator_status"] == emulator.STATUS_DEGRADED_NO_DOCKER


def test_the_refusal_precedes_boto3_and_the_network(emulator_env, monkeypatch):
    """"This deployment cannot answer" does not depend on an optional SDK.

    If the refusal sat inside ``_read_boto3`` it would report a boto3 install
    problem on a host that has no socket, and nothing at all on a host that has
    neither.
    """
    _enabled_with_docker_absent(emulator_env)
    c = FlociConnector()

    def _must_not_run(*_a, **_kw):
        raise AssertionError("reached the boto3 path for a refused table")

    monkeypatch.setattr(c, "_read_boto3", _must_not_run)
    monkeypatch.setattr(c, "_read_urllib", _must_not_run)

    assert c.read(
        ConnectorRequest(table_name="lambda_functions")
    ).status == emulator.UNSUPPORTED_WITHOUT_DOCKER


def test_a_non_container_backed_table_is_never_refused_for_a_missing_socket(
    emulator_env, monkeypatch
):
    """s3/dynamodb/sqs/ecr are served IN-PROCESS by the emulator.

    Refusing them for a missing socket would be a fabricated refusal -- the same
    defect class as the fabricated ``[]``, pointing the other way.
    """
    _enabled_with_docker_absent(emulator_env)
    c = FlociConnector()
    marker = ConnectorResponse(status="ok", data=[{"reached": True}], row_count=1)
    monkeypatch.setattr(c, "_read_boto3", lambda *a, **kw: marker)

    for table in ("s3_buckets", "dynamodb_tables", "sqs_queues", "ecr_repositories"):
        assert c.read(ConnectorRequest(table_name=table)) is marker, table


def test_an_unproven_socket_permits_the_call(emulator_env, monkeypatch):
    """REFUSE ONLY WHAT IS PROVEN UNAVAILABLE.

    Docker Desktop 28.5.1 was measured RUNNING on this host while a plain
    existence check on the Windows named pipe returned False. Treating "cannot
    tell" as "absent" would refuse a service that works.
    """
    _enabled_with_docker_unproven(emulator_env)
    assert emulator.docker_backed() is None
    assert emulator.status(probe=False) == emulator.STATUS_ENABLED

    c = FlociConnector()
    marker = ConnectorResponse(status="ok", data=[], row_count=0)
    monkeypatch.setattr(c, "_read_boto3", lambda *a, **kw: marker)
    assert c.read(ConnectorRequest(table_name="lambda_functions")) is marker


def test_a_write_to_a_container_backed_service_is_refused_on_the_same_terms(
    emulator_env,
):
    _enabled_with_docker_absent(emulator_env)
    resp = FlociConnector().write(
        ConnectorRequest(
            table_name="lambda_functions",
            query="create_function",
            filters={"service": "lambda"},
        ),
        data={"FunctionName": "x"},
    )
    assert resp.status == emulator.UNSUPPORTED_WITHOUT_DOCKER
    assert resp.metadata["service"] == "lambda"


def test_health_check_names_what_it_cannot_serve(emulator_env):
    """An empty ``unsupported_tables`` must mean "nothing PROVABLY unavailable".

    ``docker_basis`` rides beside it so a reader can tell that from "the socket
    is present".
    """
    _enabled_with_docker_absent(emulator_env)
    c = FlociConnector()
    assert c.unsupported_tables() == ["lambda_functions"]

    health = c.health_check()
    # Unreachable here -- nothing is listening -- and the docker facts are
    # reported on the unhealthy leg too, which is the leg an operator reads.
    assert health["status"] in ("healthy", "unhealthy")
    assert health["docker_backed"] is False
    assert health["docker_basis"] == emulator.BASIS_SOCKET_ABSENT
    assert health["unsupported_tables"] == ["lambda_functions"]


def test_unproven_socket_names_nothing_as_unsupported(emulator_env):
    _enabled_with_docker_unproven(emulator_env)
    c = FlociConnector()
    assert c.unsupported_tables() == []
    assert c.health_check()["docker_backed"] is None


# -- 4. The disabled path: `disabled`, and never a raise --------------------


def test_disabled_returns_disabled_and_never_raises(emulator_env):
    """Default OFF, air-gap safe, and no exception anywhere on the path."""
    assert emulator.enabled() is False
    c = FlociConnector()

    health = c.health_check()
    assert health["status"] == "disabled"

    for table in _EXPECTED_TABLES:
        resp = c.read(ConnectorRequest(table_name=table))
        assert resp.status == "disabled", table
        assert resp.data == []
        assert resp.errors, table

    write = c.write(
        ConnectorRequest(table_name="s3_buckets", query="create_bucket",
                         filters={"service": "s3"}),
        data={"Bucket": "b"},
    )
    assert write.status == "disabled"


def test_disabled_outranks_the_docker_verdict(emulator_env):
    """`disabled` says nothing about the host, so it must come FIRST.

    Reporting ``unsupported_without_docker`` for a switched-off emulator would
    send an operator to install docker when the repair is a flag.
    """
    _enabled_with_docker_absent(emulator_env)
    emulator_env.setenv("FLOCI_ENABLED", "false")
    resp = FlociConnector().read(ConnectorRequest(table_name="lambda_functions"))
    assert resp.status == "disabled"


def test_the_disabled_response_is_not_a_shared_mutable_singleton(emulator_env):
    """A module-level dataclass constant is one caller's `.append` from poison."""
    c = FlociConnector()
    first = c.read(ConnectorRequest(table_name="health"))
    first.data.append({"poison": True})
    first.errors.append("poison")

    second = c.read(ConnectorRequest(table_name="health"))
    assert second.data == []
    assert "poison" not in second.errors


def test_the_disabled_path_opens_no_socket(emulator_env, monkeypatch):
    """Asserted, not assumed: air-gap safety is the reason the default is off."""
    import urllib.request

    def _boom(*_a, **_kw):
        raise AssertionError("the disabled path opened a socket")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    c = FlociConnector()
    assert c.health_check()["status"] == "disabled"
    for table in _EXPECTED_TABLES:
        assert c.read(ConnectorRequest(table_name=table)).status == "disabled"


# -- 5. The seam is read, not re-derived ------------------------------------


def test_the_connector_reads_the_seam_and_declares_no_second_switch():
    """No second reader of the environment.

    Two switches for one emulator is the flx-seam-01 defect; a connector that
    grew its own ``os.getenv("FLOCI_ENABLED")`` would reintroduce it, and the
    symptom would be the flag off while the connector talked to an emulator.
    """
    src = (_ROOT / "tools/databridge/connectors/floci_connector.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    env_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in ("getenv", "environ")
    ]
    assert env_reads == [], "the connector must read tools/cloud/emulator.py, not os.environ"
    assert "from tools.cloud import emulator" in src


def test_the_health_path_is_the_seams_and_not_a_second_literal():
    from tools.databridge.connectors import floci_connector

    assert floci_connector._URLLIB_ENDPOINTS["health"] == emulator.HEALTH_PATH
    assert floci_connector._URLLIB_ENDPOINTS["services"] == emulator.HEALTH_PATH


# -- 6. What list_available() can and cannot say ----------------------------


def test_list_available_answers_the_grant_question_not_the_reachability_one():
    """MEASURED, and stated because it is easy to reach for as a liveness check.

    ``broker.list_available()`` reads ``args/databridge_agent_access.yaml`` and
    NEVER touches the registry -- so it cannot say whether a connector module
    would import, and a connector missing from it is UNGRANTED rather than
    unreachable. ``floci`` is deliberately ungranted: that manifest is an
    authorization boundary whose shipped grant is one public, credential-free
    feed, and an emulator that can be handed a docker socket is a per-deployment
    decision for the operator, not a default.

    Reachability is asserted at the top of this file, in a fresh interpreter.
    """
    from icdev.tools.databridge import broker
    from icdev.tools.databridge.registry import get_connector_instance

    granted = {entry["connector"] for entry in broker.list_available()}
    assert "floci" not in granted, (
        "floci must not ship as an agent grant; it is a per-deployment decision"
    )
    # ... and being ungranted says nothing about whether it resolves.
    assert get_connector_instance("floci") is not None
