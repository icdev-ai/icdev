# CUI // SP-CTI — the DOCKER-ONLY half of the floci suite (flx-test-01)
"""What only a RUNNING emulator can prove, and nothing on the default runners.

THIS FILE IS DELIBERATELY NOT GATED. It is excluded by name in
``args/test_gating_gate.yaml`` with a written reason, which is the sanctioned
door for a file gating would not buy a signal from. It is NOT in
``args/ci_test_backlog.txt`` — a backlog line says "should be gated, is not
yet", and this file should never be gated on the default runners. The two
claims are different and the census keeps them apart.

The exclusion is what makes the ``pytest.importorskip`` below lawful. A skip
inside a GATED file owes an ``args/ci_skip_census.txt`` entry by name and
breaches ``skip_max``, which may only go down; ``skip_census`` scopes itself to
gated modules, so an excluded file may skip freely. Do not gate this file
without deleting the skip first.

WHAT IT ASSERTS, AND WHY EACH ONE NEEDS A CONTAINER
====================================================
The no-Docker suite (389 gated tests across fifteen files) asserts the *shape*
of every floci claim: that the seam names one image, that the health path is a
single constant, that the twin's four verdicts are computed the way they are
written, that the broker is the only door to the estate. Every one of those is
a structural assertion over a stub. None of them can tell you the constants are
RIGHT.

That is this file's whole subject:

1. ``seam.IMAGE`` — the pinned tag — actually starts, and it is the pin the
   deployment carries, not the library's default (measured: the library ships
   ``DEFAULT_TAG = "latest"``).
2. ``seam.HEALTH_PATH`` is a path floci actually serves. It is
   ``/_localstack/health``, the drop-in-compat alias, and a constant naming a
   route the emulator does not answer would look identical in every stubbed
   test in the tree.
3. ``us-gov-west-1`` — the operator's declared region — is accepted. The
   testcontainers module defaults to ``us-east-1``.
4. The GOVERNED read really returns rows.
   ``tests/databridge/test_floci_grant.py`` proves one audit row lands per
   fetch with the connector STUBBED and says so in its own docstring ("nothing
   dials the emulator"). Here the stub comes out and the same call reaches a
   live emulator.
5. ``resource_count`` on a twin snapshot is a MEASUREMENT. The gated twin suite
   can prove the writer records ``None`` when unmeasured and a number when
   measured; only a container can prove the number counts something real.

MEASURED 2026-09-05 on Docker Desktop 28.5.1 (linux/amd64), Windows host,
``floci/floci:2.0.1`` from the local cache: container up, BOTH health paths 200,
region honoured, one bucket created, ``broker.fetch`` -> 1 row / audited, twin
snapshot -> verdict ``pass``, basis ``all_tables_answered``, ``resource_count``
1, provenance ``emulated``, and 8 ``databridge_agent_access_log`` rows all under
``twin_observatory_analyst``.

NEVER SOURCE A PERFORMANCE, COST OR CAPACITY CLAIM FROM THIS FILE. An emulator
reproduces the AWS API contract, not its performance characteristics — the
standing guard from ``docs/spikes/twx-spk-01-localstack-go-no-go.md``, which
survives the switch from LocalStack to floci unchanged.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request

import pytest

#: The distribution is `testcontainers-floci`; the top-level module it installs
#: is the generic name `floci`. Both are named here because the reason string is
#: what a reader sees when the suite skips, and "No module named 'floci'" alone
#: does not tell anyone what to install.
_tc_floci = pytest.importorskip(
    "floci",
    reason="the docker-only floci suite needs `pip install testcontainers-floci` "
           "(distribution: testcontainers-floci, module: floci) and a reachable "
           "Docker daemon",
)
FlociContainer = _tc_floci.FlociContainer

from tools.cloud import emulator as seam  # noqa: E402

GRANTED_ROLE = "twin_observatory_analyst"
CONNECTION_ID = "floci-emulator-local"

#: A bucket name this suite owns. Lowercase and no underscores — S3 refuses
#: anything else, and a bad name would read as an emulator defect.
PROBE_BUCKET = "flx-test-01-governed-read"


# ── The fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def emulator():
    """One container for the module. Starting it costs seconds, not milliseconds.

    Pinned to ``seam.IMAGE`` EXPLICITLY. ``FlociContainer()`` with no argument
    resolves ``floci/floci:latest`` (``floci.container.DEFAULT_TAG``), which
    would (a) pull at run time, defeating the pre-populated image cache the
    air-gap posture rests on, and (b) test a different emulator from the one the
    deployment runs. ``test_the_suite_pins_the_image_the_deployment_runs`` below
    turns that into an assertion rather than a habit.
    """
    # CONSTRUCTION is inside the guard, not just `.start()`. Measured
    # 2026-09-05 with `DOCKER_HOST=tcp://127.0.0.1:1`: `FlociContainer(...)`
    # resolves a docker client eagerly and raises `DockerException` from
    # `__init__`, so a guard around `.start()` alone turned nine SKIPS into nine
    # ERRORS on any host with the library installed and no daemon — which is
    # every developer laptop running `pytest tests/`. An excluded suite that
    # errors instead of skipping is worse than one that is never run.
    try:
        container = FlociContainer(image=seam.IMAGE).with_region(seam.region())
        container.start()
    except Exception as exc:  # pragma: no cover - infrastructure, not logic
        pytest.skip("no reachable Docker daemon for %s: %r" % (seam.IMAGE, exc))
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def governed_db(tmp_path, monkeypatch, emulator):
    """A real SQLite DB with the shipped connection row seeded into it.

    Seeded through the REAL seeder rather than a hand-written INSERT, for the
    same reason ``test_floci_grant.py`` does: the round trip is what proves the
    seeder's output is the shape the broker reads back.
    """
    db = tmp_path / "floci_docker.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE databridge_agent_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'unknown',
            connector_name TEXT NOT NULL DEFAULT '',
            table_name TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT 'denied'
                CHECK(decision IN ('allowed','denied')),
            reason TEXT NOT NULL DEFAULT '',
            rows_returned INTEGER NOT NULL DEFAULT 0,
            redactions_applied INTEGER NOT NULL DEFAULT 0,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            classification TEXT NOT NULL DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE db_connections (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, connector_type TEXT NOT NULL,
            connector_name TEXT NOT NULL, config_yaml TEXT NOT NULL,
            auth_method TEXT NOT NULL DEFAULT 'none', auth_secret_ref TEXT,
            sync_direction TEXT DEFAULT 'read', status TEXT DEFAULT 'configured',
            health_status TEXT DEFAULT 'unknown', last_health_check TEXT,
            last_sync TEXT, sync_cadence_minutes INTEGER DEFAULT 60,
            classification TEXT DEFAULT 'UNCLASSIFIED', impact_level TEXT DEFAULT 'IL4',
            tenant_id TEXT NOT NULL DEFAULT 'default', project_id TEXT,
            created_by TEXT, created_at TEXT, updated_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    # The seam points at the live container. Every other switch is cleared so
    # an ambient LOCALSTACK_* or DOCKER_HOST on the runner cannot decide this.
    for key in ("FLOCI_REGION", "FLOCI_DOCKER_SOCKET", "DOCKER_HOST",
                "LOCALSTACK_ENABLED", "LOCALSTACK_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FLOCI_ENABLED", "true")
    monkeypatch.setenv("FLOCI_ENDPOINT", emulator.get_endpoint())

    from icdev.tools.databridge.seed_connections import seed

    created = seed()["created"]
    assert CONNECTION_ID in created, (
        "the shipped connection row did not seed: %r" % (created,)
    )
    return db


@pytest.fixture
def s3(emulator):
    boto3 = pytest.importorskip(
        "boto3",
        reason="boto3 is not a declared dependency of this repo; five of the "
               "seven floci tables need it (see the twin adapter's "
               "`sdk_unavailable` basis)",
    )
    return boto3.client(
        "s3",
        endpoint_url=emulator.get_endpoint(),
        region_name=seam.region(),
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def _audit_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT agent_id, connector_name, table_name, decision, rows_returned "
            "FROM databridge_agent_access_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _make_bucket(s3_client, name=PROBE_BUCKET):
    """Create the probe bucket, tolerating a re-run inside the module session."""
    try:
        s3_client.create_bucket(
            Bucket=name,
            CreateBucketConfiguration={"LocationConstraint": seam.region()},
        )
    except Exception as exc:  # already there from an earlier test in this module
        if "BucketAlreadyOwnedByYou" not in repr(exc) and "BucketAlreadyExists" not in repr(exc):
            raise
    return name


# ── 1. The pin is the deployment's, not the library's ──────────────────────


def test_the_suite_pins_the_image_the_deployment_runs(emulator):
    """The library's default is `latest`; this suite must never take it.

    Asserted against the library's OWN constants rather than a copy, so the day
    upstream changes its default this test still describes reality.
    """
    from floci import container as tc_container

    assert tc_container.DEFAULT_TAG == "latest", (
        "upstream changed its default tag — re-read this test's premise before "
        "relaxing anything"
    )
    assert seam.IMAGE_TAG != "latest"
    assert emulator.image == seam.IMAGE, (
        "the running container is not the image the deployment pins"
    )


def test_the_pinned_image_actually_starts_and_serves(emulator):
    """The tag in the seam resolves to something that runs. Nothing else asks."""
    endpoint = emulator.get_endpoint()
    with urllib.request.urlopen(endpoint + "/_floci/health", timeout=10) as resp:
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
    assert payload.get("services"), "a health response naming no service is not health"


# ── 2. The seam's health path is one floci serves ──────────────────────────


def test_the_seams_health_path_is_a_route_the_emulator_answers(emulator):
    """`HEALTH_PATH` is `/_localstack/health` — the drop-in-compat alias.

    A constant naming a route the emulator does NOT serve looks identical in
    every stubbed test in the tree, and would surface only as a health check
    that never goes green on a deployment nobody had started yet.
    """
    url = emulator.get_endpoint() + seam.HEALTH_PATH
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
    assert payload.get("services"), (
        "%s answered but named no service" % seam.HEALTH_PATH
    )


def test_the_compat_alias_and_the_native_path_agree(emulator):
    """Both are served, and they describe the same emulator.

    If they ever diverged, the seam would be reporting the health of something
    other than what the testcontainers module waited for.
    """
    seen = {}
    for path in ("/_floci/health", seam.HEALTH_PATH):
        with urllib.request.urlopen(emulator.get_endpoint() + path, timeout=10) as resp:
            seen[path] = json.loads(resp.read().decode("utf-8")).get("services")
    assert seen["/_floci/health"] == seen[seam.HEALTH_PATH]


# ── 3. The declared region is accepted ─────────────────────────────────────


def test_the_declared_govcloud_region_is_honoured(emulator, s3):
    """`us-gov-west-1` is the operator's decision; the library defaults to
    `us-east-1`. A bucket created under the declared region proves the emulator
    accepts it rather than silently substituting its own."""
    assert seam.region() == "us-gov-west-1"
    assert emulator.get_region() == seam.region()

    _make_bucket(s3)
    location = s3.get_bucket_location(Bucket=PROBE_BUCKET)
    assert location["LocationConstraint"] == seam.region()


# ── 4. The governed read reaches a real emulator ───────────────────────────


def test_a_governed_fetch_returns_real_rows_and_audits_them(governed_db, s3):
    """The stub comes out.

    `test_floci_grant.py::test_an_allowed_fetch_writes_exactly_one_audit_row`
    proves the audit contract with a MagicMock connector and says so. This is
    the same call with a live emulator behind it: the rows are the emulator's,
    and the audit row still lands exactly once.
    """
    from icdev.tools.databridge import broker

    _make_bucket(s3)

    outcome = broker.fetch(GRANTED_ROLE, "floci", "s3_buckets")

    assert outcome.ok is True
    assert outcome.audited is True
    assert outcome.connector_status == "ok"
    assert outcome.row_count >= 1
    names = {row.get("Name") for row in (outcome.rows or [])}
    assert PROBE_BUCKET in names, "the created bucket is absent from %r" % (names,)

    rows = _audit_rows(governed_db)
    assert len(rows) == 1, "one fetch, %d audit rows: %r" % (len(rows), rows)
    assert rows[0][:4] == (GRANTED_ROLE, "floci", "s3_buckets", "allowed")


def test_the_shipped_egress_allowlist_admits_the_mapped_container_port(governed_db):
    """The container binds a RANDOM host port, and the fetch above still ran.

    That is the shipped allowlist (`localhost`, `127.0.0.1`, `::1`) doing its
    job on HOST rather than port — asserted here so that a future narrowing to
    a literal `http://localhost:4566` is caught by a test rather than by an
    outage. No relaxation is applied anywhere in this file.
    """
    import yaml

    from icdev.tools.databridge.connection_manager import get_connection

    row = get_connection(CONNECTION_ID)
    assert row is not None
    allowlist = (yaml.safe_load(row["config_yaml"]) or {}).get("egress_allowlist")
    assert allowlist, "the connection declares no allowlist, so nothing is bounded"
    assert not any(str(entry).strip().endswith(":4566") for entry in allowlist), (
        "the allowlist pins a PORT; a testcontainers-mapped port would be refused"
    )


# ── 5. resource_count counts something real ────────────────────────────────


def test_a_twin_snapshot_measures_the_live_estate(governed_db, s3):
    """`pass` / `resource_count` from a REAL read.

    The gated twin suite proves the ladder is computed as written and that an
    unmeasured snapshot records `None` rather than 0. It cannot prove a measured
    count counts anything. This creates one bucket and reads the number back.
    """
    from tools.twin_core.registry import TwinRegistry

    _make_bucket(s3)

    twin = TwinRegistry.get("floci")
    snapshot = twin.take_snapshot("local", label="flx-test-01-docker")

    assert snapshot["verdict"] == "pass", (
        "a reachable emulator scored %r (%r)"
        % (snapshot["verdict"], snapshot.get("verdict_basis"))
    )
    assert snapshot["verdict_basis"] == "all_tables_answered"
    assert snapshot["resource_count"] is not None
    assert snapshot["resource_count"] >= 1, (
        "a bucket was created and the estate counted nothing"
    )


def test_a_live_estate_is_still_recorded_as_emulated(governed_db, s3):
    """Reachability never upgrades provenance.

    This is the one assertion in the file that a running emulator makes MORE
    valuable rather than merely possible: the snapshot with real rows behind it
    is exactly the one a reader is most likely to mistake for an observed
    estate. It is `emulated`, and floci's own state is a container's, not an
    inventory's.
    """
    from tools.twin_core import schema
    from tools.twin_core.registry import TwinRegistry

    _make_bucket(s3)

    twin = TwinRegistry.get("floci")
    snapshot = twin.take_snapshot("local", label="flx-test-01-provenance")

    assert snapshot["provenance"] == schema.PROVENANCE_EMULATED
    assert snapshot["provenance"] == "emulated"
    assert snapshot["resource_count"] is not None, "this must be the MEASURED case"
