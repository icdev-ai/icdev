# CUI // SP-CTI
"""A pinned artifact is asked whether it is still the newest one (xrv-pin-01).

The floci pins are cryptographically verifiable -- `image_vendor.parse_pin`
refuses a tag and `verify_bundle` re-hashes every blob -- and NOTHING ever
asked upstream whether what we pinned was still current. The `floci/floci`
2.0.1 pin is a 2026-09-01 snapshot re-asserted only by a test that two files
agree with each other, and two files can agree perfectly about a version that
shipped a year ago.

These tests pin that the survey (a) reaches all THREE states and never merges
them, (b) reads an air-gapped host as `unmeasurable` and NEVER `current`, (c)
decides `behind` on the tag ordering where one exists and on DIGEST DRIFT where
the pinned tag is mutable, (d) refuses to cross image lines when comparing
tags, (e) never fabricates a rate over an empty denominator, (f) files exactly
ONE card per (artifact, upstream release) however many cycles observe it, and
(g) cannot pull an image or write a pin file -- which is asserted against the
module's AST, because a behavioural test over today's callers would still pass
the day an edit threads a "just bump it" flag through.

NOTHING HERE OPENS A SOCKET. Every registry request is scripted through the
module's `_HTTP` seam and the PyPI lane through the existing
`dependency_scanner._check_pypi_latest`.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.airgap import artifact_freshness as AF  # noqa: E402
from tools.genesis.reflexes import artifact_freshness as REFLEX  # noqa: E402

MANIFEST = ROOT / "args" / "pinned_artifacts.yaml"

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


# --------------------------------------------------------------------------- #
# fixture: a scripted registry
# --------------------------------------------------------------------------- #
class _Registry:
    """Scripted OCI registry v2 + token endpoint.

    `manifests` maps "<host>/<repo>:<tag>" -> digest | int (an HTTP status).
    `tags` maps "<host>/<repo>" -> list | int.
    `challenge` True makes the first request 401 with a Bearer challenge, so
    the token path is exercised rather than assumed.
    """

    REALM = "https://auth.example/token"

    def __init__(self, manifests=None, tags=None, *, challenge=True, token="tok"):
        self.manifests = manifests or {}
        self.tags = tags or {}
        self.challenge = challenge
        self.token = token
        self.calls = []

    def __call__(self, url, *, method="GET", headers=None, timeout=10.0):
        headers = headers or {}
        self.calls.append((method, url, headers.get("Authorization")))

        if url.startswith(self.REALM):
            if self.token is None:
                return {"status": 500, "headers": {}, "body": ""}
            return {"status": 200, "headers": {}, "body": json.dumps({"token": self.token})}

        assert url.startswith("https://"), url
        host, _, rest = url[len("https://"):].partition("/v2/")
        if self.challenge and "Authorization" not in headers:
            return {
                "status": 401,
                "headers": {
                    "www-authenticate": (
                        f'Bearer realm="{self.REALM}",service="reg",scope="repository:{rest}:pull"'
                    )
                },
                "body": "",
            }

        if rest.endswith("/tags/list"):
            repo = rest[: -len("/tags/list")]
            answer = self.tags.get(f"{host}/{repo}")
            if isinstance(answer, int):
                return {"status": answer, "headers": {}, "body": ""}
            if answer is None:
                return {"status": 404, "headers": {}, "body": ""}
            return {"status": 200, "headers": {}, "body": json.dumps({"tags": answer})}

        repo, _, tag = rest.partition("/manifests/")
        answer = self.manifests.get(f"{host}/{repo}:{tag}")
        if isinstance(answer, int):
            return {"status": answer, "headers": {}, "body": ""}
        if answer is None:
            return {"status": 404, "headers": {}, "body": ""}
        return {"status": 200, "headers": {"docker-content-digest": answer}, "body": ""}


def _entry(**over):
    base = {
        "name": "demo",
        "kind": "image",
        "ref": "acme/demo",
        "pinned": "1.2.0",
        "pin_source": "declared_here",
        "upstream": "dockerhub",
    }
    base.update(over)
    return base


def _config(entries, **over):
    cfg = {
        "enabled": True,
        "timeout_seconds": 1,
        "budget_seconds": 60,
        "artifacts": entries,
    }
    cfg.update(over)
    return cfg


@pytest.fixture
def registry(monkeypatch):
    def _install(reg):
        monkeypatch.setattr(AF, "_HTTP", reg)
        return reg

    return _install


# --------------------------------------------------------------------------- #
# the three states, each reachable and none merged
# --------------------------------------------------------------------------- #
def test_current_is_only_written_by_an_answered_request(registry):
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/demo": ["1.0.0", "1.2.0", "latest"]},
    ))
    row = AF.check_artifact(_entry())
    assert row["status"] == AF.STATUS_CURRENT
    assert row["basis"] == AF.BASIS_VERSION_TAG
    assert row["newest"] is None


def test_behind_names_the_newer_release(registry):
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/demo": ["1.2.0", "1.3.0", "1.2.9"]},
    ))
    row = AF.check_artifact(_entry())
    assert row["status"] == AF.STATUS_BEHIND
    assert row["newest"] == "1.3.0"          # named, never a bare boolean
    assert "1.3.0" in row["reason"]


def test_a_registry_that_cannot_answer_is_unmeasurable_never_current(registry):
    registry(_Registry(manifests={"registry-1.docker.io/acme/demo:1.2.0": 404}))
    row = AF.check_artifact(_entry())
    assert row["status"] == AF.STATUS_UNMEASURABLE
    assert row["status"] != AF.STATUS_CURRENT
    assert "404" in row["reason"]


def test_a_tags_listing_that_cannot_answer_is_unmeasurable(registry):
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/demo": 500},
    ))
    row = AF.check_artifact(_entry())
    assert row["status"] == AF.STATUS_UNMEASURABLE


def test_the_three_statuses_are_distinct_constants():
    assert len({AF.STATUS_CURRENT, AF.STATUS_BEHIND, AF.STATUS_UNMEASURABLE}) == 3
    assert set(AF.STATUSES) == {AF.STATUS_CURRENT, AF.STATUS_BEHIND, AF.STATUS_UNMEASURABLE}


# --------------------------------------------------------------------------- #
# offline — the deployment tools/airgap exists for
# --------------------------------------------------------------------------- #
def test_offline_reports_every_artifact_unmeasurable_and_none_current(registry):
    reg = registry(_Registry())
    report = AF.survey(config=_config([_entry(), _entry(name="other", ref="acme/other")]),
                       offline=True)
    assert report["counts"]["unmeasurable"] == 2
    assert report["counts"]["current"] == 0
    assert report["counts"]["behind"] == 0
    assert sorted(report["unmeasurable"]) == ["demo", "other"]
    assert all("airgap" in r["reason"] for r in report["results"])
    # An air-gapped survey must not have opened a socket at all.
    assert reg.calls == []


def test_offline_never_fabricates_a_rate(registry):
    registry(_Registry())
    report = AF.survey(config=_config([_entry()]), offline=True)
    # None, never 0.0 and never 100.0, over an empty denominator.
    assert report["current_pct"] is None
    assert report["measured"] == 0


def test_a_measured_zero_is_a_real_zero(registry):
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/demo": ["1.2.0", "2.0.0"]},
    ))
    report = AF.survey(config=_config([_entry()]), offline=False)
    # One artifact, measured, and behind: 0.0% current is a MEASUREMENT and
    # must keep rendering as one. Only an EMPTY denominator is None.
    assert report["measured"] == 1
    assert report["current_pct"] == 0.0


def test_rate_is_none_over_an_empty_denominator_and_never_a_perfect_score():
    assert AF._rate(0, 0) is None
    assert AF._rate(1, 1) == 100.0


# --------------------------------------------------------------------------- #
# the two bases for `behind`
# --------------------------------------------------------------------------- #
def test_a_mutable_tag_is_decided_by_digest_drift(registry, monkeypatch):
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": DIGEST_A, "tag": None, "pin_strength": "digest", "errors": []})
    registry(_Registry(manifests={"registry-1.docker.io/acme/demo:latest": DIGEST_B}))
    row = AF.check_artifact(_entry(pinned="latest"))
    assert row["status"] == AF.STATUS_BEHIND
    assert row["basis"] == AF.BASIS_DIGEST
    assert row["newest"] == DIGEST_B
    assert row["digest_drift"] is True


def test_a_mutable_tag_that_has_not_moved_is_current(registry, monkeypatch):
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": DIGEST_A, "tag": None, "pin_strength": "digest", "errors": []})
    registry(_Registry(manifests={"registry-1.docker.io/acme/demo:latest": DIGEST_A}))
    row = AF.check_artifact(_entry(pinned="latest"))
    assert row["status"] == AF.STATUS_CURRENT
    assert row["basis"] == AF.BASIS_DIGEST
    assert row["digest_drift"] is False


def test_a_mutable_tag_with_no_declared_digest_is_unmeasurable(registry, monkeypatch):
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": None, "tag": None, "pin_strength": None, "errors": []})
    registry(_Registry(manifests={"registry-1.docker.io/acme/demo:latest": DIGEST_B}))
    row = AF.check_artifact(_entry(pinned="latest"))
    assert row["status"] == AF.STATUS_UNMEASURABLE
    assert "nothing to compare" in row["reason"]


def test_digest_drift_on_a_semver_pin_is_reported_beside_the_status(registry, monkeypatch):
    """A moved tag under a digest pin is a SECOND finding, not the status."""
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": DIGEST_A, "tag": None, "pin_strength": "digest", "errors": []})
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_B},
        tags={"registry-1.docker.io/acme/demo": ["1.2.0"]},
    ))
    row = AF.check_artifact(_entry())
    assert row["status"] == AF.STATUS_CURRENT       # no newer tag exists
    assert row["basis"] == AF.BASIS_VERSION_TAG     # and the tag ordering decided it
    assert row["digest_drift"] is True              # while the drift is still named


def test_digest_drift_is_none_when_it_could_not_be_compared(registry, monkeypatch):
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": None, "tag": None, "pin_strength": None, "errors": []})
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_B},
        tags={"registry-1.docker.io/acme/demo": ["1.2.0"]},
    ))
    row = AF.check_artifact(_entry())
    # None means "could not compare", never "no drift".
    assert row["digest_drift"] is None


# --------------------------------------------------------------------------- #
# a tag the CONSUMER chooses is not chasing upstream's release train
# --------------------------------------------------------------------------- #
def _consumer_entry(**over):
    base = _entry(pinned="16.3-alpine", decided_by="consumer", consumer="floci")
    base.update(over)
    return base


def _pg_registry(pinned_digest=DIGEST_A):
    """Upstream's postgres shape: the pinned tag, a newer one in the same major
    line, and a newer major line."""
    return _Registry(
        manifests={"registry-1.docker.io/acme/demo:16.3-alpine": pinned_digest},
        tags={"registry-1.docker.io/acme/demo": [
            "16.3-alpine", "16.15-alpine", "18.6-alpine"]},
    )


def _digest_pin(digest):
    return lambda entry: {
        "digest": digest,
        "tag": None,
        "pin_strength": "digest" if digest else None,
        "errors": [],
    }


def test_a_consumer_decided_pin_is_not_behind_a_release_the_consumer_never_asks_for(
    registry, monkeypatch
):
    """MEASURED 2026-09-12: floci 2.0.1 builds `postgres:<EngineVersion>-alpine`
    and defaults to 16.3, so `18.6-alpine` is an image it never requests.
    Calling the pin `behind` it recommends vendoring an image the emulator will
    not pull -- and leaves the one it DOES pull out of the air-gap bundle."""
    monkeypatch.setattr(AF, "resolve_pin", _digest_pin(DIGEST_A))
    registry(_pg_registry())
    row = AF.check_artifact(_consumer_entry())
    assert row["status"] == AF.STATUS_CURRENT
    assert row["basis"] == AF.BASIS_DIGEST
    assert row["decided_by"] == AF.DECIDED_BY_CONSUMER
    assert row["consumer"] == "floci"


def test_a_consumer_decided_pin_still_names_the_release_it_is_not_chasing(
    registry, monkeypatch
):
    """Not chasing a release is not the same as not knowing about it. The newer
    tag is looked up and carried; it just does not decide the status."""
    monkeypatch.setattr(AF, "resolve_pin", _digest_pin(DIGEST_A))
    registry(_pg_registry())
    row = AF.check_artifact(_consumer_entry())
    assert row["upstream_newest"] == "18.6-alpine"
    assert "floci" in row["reason"]
    assert "18.6-alpine" in row["reason"]


def test_a_consumer_decided_pin_is_behind_when_its_tag_stops_serving_the_pinned_bytes(
    registry, monkeypatch
):
    """The one currency question upstream CAN answer about a tag chosen by
    somebody else -- and on an immutable tag it is a supply-chain alarm."""
    monkeypatch.setattr(AF, "resolve_pin", _digest_pin(DIGEST_A))
    registry(_pg_registry(pinned_digest=DIGEST_B))
    row = AF.check_artifact(_consumer_entry())
    assert row["status"] == AF.STATUS_BEHIND
    assert row["basis"] == AF.BASIS_DIGEST
    assert row["digest_drift"] is True
    assert row["newest"] == DIGEST_B


def test_a_consumer_decided_pin_with_no_declared_digest_is_unmeasurable_never_current(
    registry, monkeypatch
):
    monkeypatch.setattr(AF, "resolve_pin", _digest_pin(None))
    registry(_pg_registry())
    row = AF.check_artifact(_consumer_entry())
    assert row["status"] == AF.STATUS_UNMEASURABLE
    assert "nothing to compare" in row["reason"]


def test_decided_by_consumer_with_no_consumer_named_is_unmeasurable(registry, monkeypatch):
    monkeypatch.setattr(AF, "resolve_pin", _digest_pin(DIGEST_A))
    registry(_pg_registry())
    row = AF.check_artifact(_consumer_entry(consumer=None))
    assert row["status"] == AF.STATUS_UNMEASURABLE


def test_an_unknown_decided_by_is_unmeasurable_never_current(registry, monkeypatch):
    """An unreadable declaration must never resolve to a clean bill."""
    monkeypatch.setattr(AF, "resolve_pin", _digest_pin(DIGEST_A))
    registry(_pg_registry())
    row = AF.check_artifact(_consumer_entry(decided_by="vibes"))
    assert row["status"] == AF.STATUS_UNMEASURABLE
    assert "vibes" in row["reason"]


def test_every_consumer_names_a_declared_artifact():
    """The delegation has to point somewhere real: `rds-postgres` is current
    only for as long as `floci` is, and the `floci` entry is what re-asks it."""
    config = AF.load_config()
    names = {e["name"] for e in config["artifacts"]}
    delegating = [e for e in config["artifacts"]
                  if e.get("decided_by") == AF.DECIDED_BY_CONSUMER]
    assert delegating, "the shipped manifest is supposed to carry the postgres decision"
    for entry in delegating:
        assert entry.get("consumer") in names, entry["name"]


def test_the_shipped_postgres_pin_is_decided_by_floci_not_by_postgres_releases():
    """artifact-fresh-5b1750f240. Re-arming the upstream tag comparison on this
    entry files a card recommending an image floci never pulls."""
    entry = next(e for e in AF.load_config()["artifacts"] if e["name"] == "rds-postgres")
    assert entry["pinned"] == "16.3-alpine"
    assert entry["decided_by"] == AF.DECIDED_BY_CONSUMER
    assert entry["consumer"] == "floci"


def test_the_shipped_ec2_pin_is_decided_by_floci_not_by_amazon_linux_releases():
    """artifact-fresh-b5394142b3, the same defect one service over.

    MEASURED 2026-09-12 by driving floci 2.0.1: its AMI catalogue maps each
    ImageId it knows to a docker image, and an ImageId it does not know is
    answered `AmiImageResolver  Unknown AMI ID ami-0f00f00f00f00f00f; falling
    back to default image public.ecr.aws/amazonlinux/amazonlinux:2023`. `2027`
    is a real Amazon Linux release -- and floci 2.0.1 has no code path that
    requests it, so vendoring it would leave the DEFAULT path out of the bundle.
    """
    entry = next(e for e in AF.load_config()["artifacts"] if e["name"] == "ec2-amazonlinux")
    assert entry["pinned"] == "2023"

def test_the_shipped_valkey_pin_is_decided_by_floci_which_hard_codes_the_tag():
    """artifact-fresh-ee3339893b. Harder than the postgres case: that one is
    floci's DEFAULT for a knob a caller can turn, this one is a CONSTANT. Four
    CreateReplicationGroup calls -- default, EngineVersion=7.1,
    EngineVersion=99.99 and Engine=valkey/EngineVersion=9 -- ALL started
    valkey/valkey:8 (measured 2026-09-12), so no declared configuration reaches
    valkey 9 and bumping this pin unvendors EVERY ElastiCache path."""
    entry = next(e for e in AF.load_config()["artifacts"]
                 if e["name"] == "elasticache-valkey")
    assert entry["pinned"] == "8"

def test_the_shipped_mysql_pin_is_decided_by_floci_not_by_mysql_releases():
    """artifact-fresh-da63da118f. MEASURED 2026-09-12: a default
    CreateDBInstance(Engine=mysql) returns EngineVersion 8.0.36 and floci pulls
    `mysql:<EngineVersion>` -- so `26.7.0` is a tag floci never asks for, and
    acting on that card would drop the default path out of the air-gap bundle."""
    entry = next(e for e in AF.load_config()["artifacts"] if e["name"] == "rds-mysql")
    assert entry["pinned"] == "8.0.36"
    assert entry["decided_by"] == AF.DECIDED_BY_CONSUMER
    assert entry["consumer"] == "floci"


def test_the_valkey_digest_is_the_one_re_measured_after_the_tag_moved():
    """The version half of that card was refused; the DIGEST half was real and
    was acted on. A revert to the stale digest would make an air-gap bundle
    re-cut from the tag disagree with the file that names it."""
    remeasured = (
        "sha256:3fbd2e3e4b6e85e046c1e7c215e8f79087bc0357789184305806664e320996f3"
    )
    stale = (
        "sha256:98c6217ccc2fe5e6c4b5dcd5c40eef4de2a68924e7ecef50d5a0a30b57dfaef6"
    )
    pins = (ROOT / "vendor" / "images" / "images-floci-runtime.txt").read_text(
        encoding="utf-8"
    )
    # Only the PIN lines decide what gets vendored; the stale digest is still
    # named in a comment above them, on purpose, as the thing that moved.
    active = [ln.strip() for ln in pins.splitlines()
              if ln.strip() and not ln.lstrip().startswith("#")]
    valkey = [ln for ln in active if ln.startswith("valkey/valkey@")]
    assert valkey == [f"valkey/valkey@{remeasured}"]
    assert not any(stale in ln for ln in active)


# --------------------------------------------------------------------------- #
# ordering — same SHAPE only
# --------------------------------------------------------------------------- #
def test_a_different_suffix_is_a_different_image_line():
    tags = ["16.3-alpine", "16.4-alpine", "18.0", "17.2-bookworm"]
    assert AF.newest_comparable("16.3-alpine", tags) == "16.4-alpine"


def test_a_different_component_count_is_not_a_successor():
    assert AF.newest_comparable("16.3", ["16.4.1", "16.3"]) is None
    assert AF.newest_comparable("16.3", ["16.4", "16.4.1"]) == "16.4"


def test_a_tag_with_no_numeric_core_carries_no_ordering():
    assert AF.version_shape("latest") is None
    assert AF.newest_comparable("latest", ["1.0", "2.0", "latest"]) is None


def test_version_shape_splits_core_from_suffix():
    assert AF.version_shape("2.0.1") == ((2, 0, 1), "")
    assert AF.version_shape("16.3-alpine") == ((16, 3), "-alpine")


# --------------------------------------------------------------------------- #
# ref -> (registry host, repository)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ref,expected", [
    ("postgres", ("registry-1.docker.io", "library/postgres")),
    ("valkey/valkey", ("registry-1.docker.io", "valkey/valkey")),
    ("public.ecr.aws/lambda/python", ("public.ecr.aws", "lambda/python")),
    ("ghcr.io/owner/name", ("ghcr.io", "owner/name")),
])
def test_split_ref(ref, expected):
    assert AF.split_ref(ref) == expected


def test_a_declared_upstream_that_disagrees_with_the_ref_is_unmeasurable(registry):
    registry(_Registry())
    row = AF.check_artifact(_entry(ref="public.ecr.aws/lambda/python", upstream="dockerhub"))
    assert row["status"] == AF.STATUS_UNMEASURABLE
    assert "declared_upstream_mismatch" in row["reason"]


def test_the_token_comes_from_the_registrys_own_challenge(registry):
    reg = registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/demo": ["1.2.0"]},
    ))
    AF.check_artifact(_entry())
    assert any(url.startswith(_Registry.REALM) for _m, url, _a in reg.calls)
    assert any(auth == "Bearer tok" for _m, _u, auth in reg.calls)


def test_a_token_endpoint_that_refuses_leaves_the_artifact_unmeasurable(registry):
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_A},
        token=None,
    ))
    row = AF.check_artifact(_entry())
    assert row["status"] == AF.STATUS_UNMEASURABLE


# --------------------------------------------------------------------------- #
# the pin lives in ONE place, and a disagreement is never resolved by preference
# --------------------------------------------------------------------------- #
def test_a_pin_source_that_disagrees_with_the_manifest_is_unmeasurable(registry, monkeypatch):
    registry(_Registry())
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": None, "tag": "9.9.9", "pin_strength": "tag", "errors": []})
    row = AF.check_artifact(_entry(pinned="1.2.0"))
    assert row["status"] == AF.STATUS_UNMEASURABLE
    assert "pin_source_disagrees" in row["reason"]


def test_an_unreadable_pin_source_is_unmeasurable(registry, monkeypatch):
    registry(_Registry())
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": None, "tag": None, "pin_strength": None, "errors": ["no such file"]})
    row = AF.check_artifact(_entry())
    assert row["status"] == AF.STATUS_UNMEASURABLE
    assert "pin_source_unreadable" in row["reason"]


def test_every_declared_artifact_resolves_in_the_file_that_holds_its_pin():
    """The manifest is a declaration, not a second copy -- so it must RESOLVE.

    A `pin_source` naming a file that does not carry the pin makes the entry
    permanently `unmeasurable`, which is the quiet way this manifest would rot.
    """
    config = AF.load_config()
    unresolved = []
    for entry in config["artifacts"]:
        pin = AF.resolve_pin(entry)
        if pin["errors"]:
            unresolved.append((entry["name"], pin["errors"]))
        elif pin["tag"] is not None and pin["tag"] != entry["pinned"]:
            unresolved.append((entry["name"], f"tag {pin['tag']!r} != {entry['pinned']!r}"))
    assert not unresolved, f"pin sources do not resolve: {unresolved}"


def test_every_digest_pinned_entry_finds_its_digest():
    config = AF.load_config()
    from_pin_files = [
        e for e in config["artifacts"]
        if str(e.get("pin_source", "")).startswith("vendor/images/") or e.get("digest_source")
    ]
    assert from_pin_files, "the manifest is supposed to be seeded from the floci pin files"
    for entry in from_pin_files:
        assert AF.resolve_pin(entry)["digest"], f"{entry['name']} resolved no digest"


def test_the_shipped_manifest_declares_only_supported_kinds_and_upstreams():
    config = AF.load_config()
    for entry in config["artifacts"]:
        assert entry["kind"] in (AF.KIND_IMAGE, AF.KIND_PACKAGE), entry["name"]
        if entry["kind"] == AF.KIND_IMAGE:
            assert entry["upstream"] in AF.UPSTREAM_HOSTS, entry["name"]
            assert AF.split_ref(entry["ref"])[0] == AF.UPSTREAM_HOSTS[entry["upstream"]]
        else:
            assert entry["upstream"] == "pypi", entry["name"]


def test_artifact_names_are_unique():
    names = [e["name"] for e in AF.load_config()["artifacts"]]
    assert len(names) == len(set(names))


# --------------------------------------------------------------------------- #
# the package lane reuses the existing seam
# --------------------------------------------------------------------------- #
def test_a_package_behind_its_floor_is_behind(monkeypatch):
    import tools.maintenance.dependency_scanner as DS

    monkeypatch.setattr(DS, "_check_pypi_latest", lambda name: ("0.4.0", "2026-09-01"))
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": None, "tag": "0.1.1", "pin_strength": "floor", "errors": []})
    row = AF.check_artifact(_entry(name="pkg", kind="package", ref="demo-dist",
                                   pinned="0.1.1", upstream="pypi"))
    assert row["status"] == AF.STATUS_BEHIND
    assert row["newest"] == "0.4.0"
    assert row["basis"] == AF.BASIS_VERSION
    assert row["pin_strength"] == "floor"     # a floor is a weaker claim, and says so


def test_a_package_index_that_cannot_answer_is_unmeasurable(monkeypatch):
    import tools.maintenance.dependency_scanner as DS

    monkeypatch.setattr(DS, "_check_pypi_latest", lambda name: (None, None))
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": None, "tag": "0.1.1", "pin_strength": "floor", "errors": []})
    row = AF.check_artifact(_entry(name="pkg", kind="package", ref="demo-dist",
                                   pinned="0.1.1", upstream="pypi"))
    assert row["status"] == AF.STATUS_UNMEASURABLE


# --------------------------------------------------------------------------- #
# the survey's own bounds
# --------------------------------------------------------------------------- #
def test_an_exhausted_budget_names_the_artifacts_it_did_not_ask(registry, monkeypatch):
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/demo:1.2.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/demo": ["1.2.0"]},
    ))
    report = AF.survey(
        config=_config([_entry(), _entry(name="second")], budget_seconds=-1), offline=False)
    assert report["counts"]["unmeasurable"] == 2
    assert all("budget_exhausted" in r["reason"] for r in report["results"])


def test_one_raising_entry_never_kills_the_survey(registry, monkeypatch):
    registry(_Registry(
        manifests={"registry-1.docker.io/acme/ok:1.2.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/ok": ["1.2.0"]},
    ))
    real = AF.check_artifact

    def _boom(entry, **kw):
        if entry["name"] == "bad":
            raise RuntimeError("probe blew up")
        return real(entry, **kw)

    monkeypatch.setattr(AF, "check_artifact", _boom)
    report = AF.survey(
        config=_config([_entry(name="bad"), _entry(name="ok", ref="acme/ok")]), offline=False)
    assert report["counts"]["current"] == 1
    assert report["unmeasurable"] == ["bad"]


def test_an_unreadable_manifest_is_exit_two_never_an_empty_survey(tmp_path, monkeypatch):
    monkeypatch.setattr(AF, "CONFIG_PATH", tmp_path / "nope.yaml")
    assert AF.main(["--survey", "--json"]) == 2


def test_a_produced_survey_exits_zero_whatever_it_says(registry, capsys):
    registry(_Registry())
    assert AF.main(["--survey", "--offline", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["unmeasurable"] == report["artifacts"]


def test_the_human_render_says_unmeasurable_is_not_clean(registry, capsys):
    registry(_Registry())
    AF.main(["--survey", "--offline"])
    out = capsys.readouterr().out
    assert "UNMEASURABLE IS NOT CLEAN" in out
    assert "not measured (nothing was asked)" in out


# --------------------------------------------------------------------------- #
# the reflex — one card per finding, once
# --------------------------------------------------------------------------- #
class _Board:
    """A create_tasks stand-in enforcing what task_factory enforces: one row
    per idempotency_key, and a duplicate key inserts nothing."""

    def __init__(self):
        self.specs = []
        self.keys = set()

    def __call__(self, specs, **_):
        created = []
        for spec in specs:
            key = spec.get("idempotency_key")
            if key in self.keys:
                continue
            self.keys.add(key)
            self.specs.append(spec)
            created.append(spec["id"])
        return created


@pytest.fixture
def board(monkeypatch):
    import tools.kanban.task_factory as TF

    fake = _Board()
    monkeypatch.setattr(TF, "create_tasks", fake)
    return fake


def _behind_registry(monkeypatch):
    monkeypatch.setattr(AF, "load_config", lambda path=None: _config([
        _entry(name="rds-postgres", ref="postgres", pinned="16.3-alpine"),
        _entry(name="steady", ref="acme/steady", pinned="1.0.0"),
    ]))
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": DIGEST_A, "tag": None, "pin_strength": "digest", "errors": []})
    monkeypatch.setattr(AF, "_HTTP", _Registry(
        manifests={
            "registry-1.docker.io/library/postgres:16.3-alpine": DIGEST_A,
            "registry-1.docker.io/acme/steady:1.0.0": DIGEST_A,
        },
        tags={
            "registry-1.docker.io/library/postgres": ["16.3-alpine", "18.6-alpine"],
            "registry-1.docker.io/acme/steady": ["1.0.0"],
        },
    ))


def test_a_behind_artifact_files_exactly_one_card(monkeypatch, board):
    _behind_registry(monkeypatch)
    result = REFLEX.run({})
    assert result["success"] is True
    assert result["status"] == REFLEX.STATUS_FINDINGS
    assert result["findings"] == 1
    assert result["cards_filed"] == 1
    assert len(board.specs) == 1
    spec = board.specs[0]
    assert spec["idempotency_key"] == "artifact-freshness:rds-postgres:18.6-alpine"
    assert spec["status"] == "suggested"
    assert spec["task_type"] == "chore"
    assert spec["acceptance_criteria"].strip()
    assert "18.6-alpine" in spec["description"]


def test_a_second_run_over_the_same_finding_files_nothing(monkeypatch, board):
    _behind_registry(monkeypatch)
    first = REFLEX.run({})
    second = REFLEX.run({})
    assert first["cards_filed"] == 1
    assert second["cards_filed"] == 0
    assert len(board.specs) == 1
    # The finding is still REPORTED on the second run -- deduping the card must
    # never dedupe the measurement.
    assert second["findings"] == 1


def test_a_further_upstream_release_is_a_new_key(monkeypatch, board):
    _behind_registry(monkeypatch)
    REFLEX.run({})
    monkeypatch.setattr(AF, "_HTTP", _Registry(
        manifests={
            "registry-1.docker.io/library/postgres:16.3-alpine": DIGEST_A,
            "registry-1.docker.io/acme/steady:1.0.0": DIGEST_A,
        },
        tags={
            "registry-1.docker.io/library/postgres": ["16.3-alpine", "18.6-alpine", "19.0-alpine"],
            "registry-1.docker.io/acme/steady": ["1.0.0"],
        },
    ))
    REFLEX.run({})
    assert [s["idempotency_key"] for s in board.specs] == [
        "artifact-freshness:rds-postgres:18.6-alpine",
        "artifact-freshness:rds-postgres:19.0-alpine",
    ]


def test_the_card_id_is_deterministic_in_the_same_pair():
    assert REFLEX._card_id("p-", "a", "1") == REFLEX._card_id("p-", "a", "1")
    assert REFLEX._card_id("p-", "a", "1") != REFLEX._card_id("p-", "a", "2")


def test_dry_run_reports_the_finding_and_files_nothing(monkeypatch, board):
    _behind_registry(monkeypatch)
    result = REFLEX.run({"dry_run": True})
    assert result["findings"] == 1
    assert result["cards_filed"] == 0
    assert board.specs == []


def test_the_card_bound_is_reported_by_name_never_dropped(monkeypatch, board):
    _behind_registry(monkeypatch)
    result = REFLEX.run({"max_cards_per_run": 0})
    assert result["cards_filed"] == 0
    assert result["cards_deferred"] == ["rds-postgres"]
    assert result["findings"] == 1


def test_an_offline_cycle_is_unmeasurable_and_never_ok(monkeypatch, board):
    real_survey = AF.survey
    monkeypatch.setattr(AF, "load_config", lambda path=None: _config([_entry()]))
    monkeypatch.setattr(
        AF, "survey", lambda **kw: real_survey(config=_config([_entry()]), offline=True))
    result = REFLEX.run({})
    assert result["status"] == REFLEX.STATUS_UNMEASURABLE
    assert result["status"] != REFLEX.STATUS_OK
    assert result["metric_value"] == 0.0
    # success stays True so the breaker cannot make the reflex permanently
    # inert on exactly the deployments tools/airgap exists to serve.
    assert result["success"] is True
    assert board.specs == []


def test_a_clean_measured_cycle_is_ok(monkeypatch, board):
    monkeypatch.setattr(AF, "load_config", lambda path=None: _config([
        _entry(name="steady", ref="acme/steady", pinned="1.0.0")]))
    monkeypatch.setattr(AF, "resolve_pin", lambda e: {
        "digest": None, "tag": None, "pin_strength": None, "errors": []})
    monkeypatch.setattr(AF, "_HTTP", _Registry(
        manifests={"registry-1.docker.io/acme/steady:1.0.0": DIGEST_A},
        tags={"registry-1.docker.io/acme/steady": ["1.0.0"]},
    ))
    result = REFLEX.run({})
    assert result["status"] == REFLEX.STATUS_OK
    assert result["artifacts_measured"] == 1
    assert board.specs == []


def test_a_survey_that_cannot_be_produced_is_an_error_not_unmeasurable(monkeypatch, board):
    def _boom(**_):
        raise FileNotFoundError("no manifest")

    monkeypatch.setattr(AF, "survey", _boom)
    result = REFLEX.run({})
    assert result["status"] == REFLEX.STATUS_ERROR
    assert result["success"] is False
    assert result["errors"]


def test_the_reflex_persists_its_report_under_details(monkeypatch, board):
    """rmf-inert-03: a reflex that never sets `details` records `{}` forever."""
    _behind_registry(monkeypatch)
    result = REFLEX.run({})
    details = result["details"]
    for key in ("status", "counts", "findings", "finding_detail", "cards_filed",
                "cards_deferred", "unmeasurable_artifacts", "digest_drift", "offline"):
        assert key in details, key
    assert details["finding_detail"][0]["name"] == "rds-postgres"
    assert result["metric_value"] == float(result["artifacts_measured"])


# --------------------------------------------------------------------------- #
# structural: it cannot pull, and it cannot write a pin
# --------------------------------------------------------------------------- #
def _module_ast(module):
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _imported_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("module", [AF, REFLEX])
def test_neither_module_can_reach_a_subprocess(module):
    """`docker pull` cannot be spelled without one, so it cannot be spelled.

    Asserted against the AST and NOT against the source text: both modules say
    in prose that they never touch ``subprocess``, and a grep would flag their
    own explanation of themselves -- the model_id_gate trap, and exactly why
    that gate parses instead of grepping. ``os`` and ``shutil`` are refused for
    the same reason: they are the other two doors to a filesystem write and to
    ``os.system``.
    """
    tree = _module_ast(module)
    imported = _imported_names(tree)
    for banned in ("subprocess", "os", "shutil"):
        assert banned not in imported, f"{module.__name__} imports {banned}"
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.value.id for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    assert "subprocess" not in used, f"{module.__name__} references subprocess in code"


@pytest.mark.parametrize("module", [AF, REFLEX])
def test_neither_module_opens_anything_for_writing(module):
    """No pin file, compose file or requirements line is ever rewritten.

    ``rename``/``replace`` are deliberately NOT in the banned set: ``replace``
    is ``str.replace`` at three sites here, and a check that fires on it would
    be turned off rather than obeyed. The write doors that matter are closed by
    the ``os``/``shutil`` refusal above plus the names below.
    """
    tree = _module_ast(module)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        assert name not in {"write_text", "write_bytes", "unlink", "mkdir", "touch"}, (
            f"{module.__name__} calls {name}() -- this survey observes, it never acts"
        )
        if name == "open":
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    assert "r" in str(kw.value.value), "open() for writing"
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                assert "r" in str(node.args[1].value), "open() for writing"


def test_the_http_door_is_get_and_head_only():
    assert AF.ALLOWED_HTTP_METHODS == frozenset({"GET", "HEAD"})
    with pytest.raises(RuntimeError, match="refusing HTTP"):
        AF._http("https://example/", method="POST")
    with pytest.raises(RuntimeError, match="refusing HTTP"):
        AF._http("https://example/", method="DELETE")


def test_the_reflex_seeds_through_the_canonical_seeder_never_a_raw_insert():
    source = Path(REFLEX.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO" not in source.upper()
    assert "from tools.kanban.task_factory import create_tasks" in source


def test_the_pin_line_parser_is_image_vendors_own():
    """A second parser is how the vendor and this survey would come to disagree
    about a file neither of them changed."""
    source = Path(AF.__file__).read_text(encoding="utf-8")
    assert "from tools.airgap.image_vendor import parse_pin" in source


def test_the_pypi_lane_reuses_the_existing_seam():
    source = Path(AF.__file__).read_text(encoding="utf-8")
    assert "from tools.maintenance.dependency_scanner import _check_pypi_latest" in source
    assert "pypi.org" not in source, "a second PyPI client would be a second seam"


# --------------------------------------------------------------------------- #
# registration — missing either side makes the reflex silently inert
# --------------------------------------------------------------------------- #
def test_the_reflex_is_registered_on_both_sides_the_daemon_needs():
    from tools.genesis.daemon import REFLEX_NAMES

    assert "artifact_freshness" in REFLEX_NAMES
    config = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    block = (config.get("reflexes") or {}).get("artifact_freshness")
    assert block, "no args/genesis_config.yaml block -- the daemon would never schedule it"
    assert block["enabled"] is True
    assert block["risk_tier"] == "green"
    assert block["schedule"] == "every 24h"
    assert block["success_metric"]["name"] == REFLEX.METRIC_NAME


def test_the_manifest_ships_and_declares_artifacts():
    assert MANIFEST.exists()
    config = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert config["enabled"] is True
    assert len(config["artifacts"]) >= 16
    # Seeded from the two floci pin files, the four floci/* compose services
    # and testcontainers-floci -- the card's stated scope.
    names = {e["name"] for e in config["artifacts"]}
    assert {"floci", "floci-az", "floci-gcp", "floci-oci"} <= names
    assert "testcontainers-floci" in names
    sources = {str(e["pin_source"]) for e in config["artifacts"]}
    assert "vendor/images/images-floci-runtime.txt" in sources
