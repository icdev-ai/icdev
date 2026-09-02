# CUI // SP-CTI
"""rmf-disc-02 — the discovery engine is actually reachable, and honest about it.

WHAT THESE TESTS PIN, AND WHY EACH ONE COULD REGRESS SILENTLY

1. THE PAGE IS NOT A PLACEHOLDER. ``/network/discovery`` rendered the string
   "Discovery page coming soon" with HTTP 200 because its route guarded
   ``render_template`` behind an ``os.path.exists`` on a directory that has
   never existed. A 200 is what every uptime check and every route smoke test
   looks for, so nothing anywhere went red for as long as it took someone to
   open the page by hand.

2. THE FIVE ENDPOINTS EXIST. The template's JavaScript named five URLs and none
   of them was defined. A template referencing a URL is not a call — nothing
   collects those strings, so no import error, no route-coverage check and no
   test could see it.

3. PROVENANCE CANNOT BE CROSSED. ``seed_synthetic_devices`` must be unable to
   write ``discovery`` and ``import_scan_devices`` must be unable to write
   ``synthetic``. This is asserted against the SOURCE, not just against a call's
   result, because the failure mode is a future edit passing a caller-supplied
   label through — which a behavioural test with the right fixture would still
   pass.

4. THE LEARNER STILL REFUSES FABRICATED ROWS. ``args/docmod/inventory_feeds.yaml``
   ranks ``ni_devices`` as an observed deployed estate at the best precedence
   there is. If ``exclude_when`` is dropped or the label changes, synthetic demo
   devices silently become the platform's strongest evidence about what hardware
   is fielded, and nothing else in the tree would notice.

5. CREDENTIALS ARE NOT PERSISTED. A scan config is read back by an endpoint and
   rendered on a page.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from tools.network import discovery_store as store

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 1. The page route ────────────────────────────────────────────────────────

def test_discovery_page_route_has_no_filesystem_guard():
    """The route must render the template, not probe for it first.

    The guard it used to carry pointed at ``tools/network/dashboard/templates/``
    -- a path that does not exist in this tree -- so it was permanently false.
    Any ``os.path.exists`` in this route is that defect returning: a missing
    template must raise something a reader can act on, never degrade to a 200
    carrying a placeholder string.
    """
    src = (REPO_ROOT / "tools" / "network" / "routes" / "discovery.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "nc_discovery_page"),
        None,
    )
    assert fn is not None, "nc_discovery_page must live in routes/discovery.py"
    body_src = ast.unparse(fn)
    assert "os.path.exists" not in body_src
    assert "coming soon" not in body_src.lower()


def test_old_placeholder_route_is_gone_from_analytics():
    """analytics.py must not re-register a second /discovery page route.

    Asserted against the AST, not the file text: the comment left where the
    route used to live QUOTES the placeholder string, and a substring search
    would flag the explanation of the fix as the defect.
    """
    src = (REPO_ROOT / "tools" / "network" / "routes" / "analytics.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    names = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "nc_discovery_page" not in names, (
        "the /discovery page route lives in routes/discovery.py; two "
        "registrations of the same endpoint name abort blueprint registration"
    )
    literals = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert not any("coming soon" in v.lower() for v in literals), (
        "a placeholder string returned with HTTP 200 is how this page hid"
    )


# ── 2. The five endpoints ────────────────────────────────────────────────────

#: Exactly the URLs tools/dashboard/templates/network/discovery.html fetches.
#: Read off the template's JavaScript, not invented here.
REQUIRED_RULES = {
    "/network/api/discovery/scan": {"POST"},
    "/network/api/discovery/scans/<scan_id>": {"GET", "DELETE"},
    "/network/api/discovery/scans/<scan_id>/import/<topology_id>": {"POST"},
    "/network/api/discovery/diff": {"POST"},
    "/network/discovery": {"GET"},
}


@pytest.fixture(scope="module")
def ndc_app():
    from flask import Flask

    from tools.network.blueprint import create_network_blueprint

    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "tools" / "dashboard" / "templates"),
    )
    app.register_blueprint(create_network_blueprint(), url_prefix="/network")
    return app


def test_every_endpoint_the_template_calls_is_registered(ndc_app):
    registered: dict[str, set[str]] = {}
    for rule in ndc_app.url_map.iter_rules():
        registered.setdefault(str(rule), set()).update(rule.methods or set())

    missing = []
    for path, methods in REQUIRED_RULES.items():
        have = registered.get(path)
        if have is None:
            missing.append(f"{path} (no rule)")
            continue
        absent = methods - have
        if absent:
            missing.append(f"{path} (missing {sorted(absent)})")
    assert not missing, f"endpoints the discovery page calls are unreachable: {missing}"


def test_template_calls_no_url_we_do_not_serve(ndc_app):
    """Every /api/discovery/ URL literal in the template resolves to a rule.

    Catches the ORIGINAL defect shape rather than today's five URLs: a new
    fetch() added to the page with no route behind it fails here.
    """
    import re

    tpl = (
        REPO_ROOT / "tools" / "dashboard" / "templates" / "network" / "discovery.html"
    ).read_text(encoding="utf-8")
    # Literal prefixes only -- the template builds the rest by concatenation.
    literals = set(re.findall(r"'(/network/api/discovery/[a-z/]*)", tpl))
    assert literals, "template fetches no discovery endpoint -- did it change?"

    served = {str(r).split("<")[0].rstrip("/") for r in ndc_app.url_map.iter_rules()}
    for lit in literals:
        stem = lit.rstrip("/")
        assert any(s == stem or s.startswith(stem) for s in served), (
            f"template calls {lit} and no route serves it"
        )


# ── 3. Provenance cannot be crossed ──────────────────────────────────────────

def _source_of(func_name: str) -> str:
    src = (REPO_ROOT / "tools" / "network" / "discovery_store.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == func_name),
        None,
    )
    assert fn is not None, f"{func_name} not found in discovery_store.py"
    return ast.unparse(fn)


def test_synthetic_seeder_cannot_write_the_discovery_label():
    body = _source_of("seed_synthetic_devices")
    assert "SOURCE_SYNTHETIC" in body
    assert "SOURCE_DISCOVERY" not in body, (
        "the synthetic seeder must not be able to label a fabricated device as "
        "an observation"
    )


def test_discovery_importer_cannot_write_the_synthetic_label():
    body = _source_of("import_scan_devices")
    assert "SOURCE_DISCOVERY" in body
    assert "SOURCE_SYNTHETIC" not in body


def test_neither_writer_takes_a_caller_supplied_source():
    """A `source=` parameter on either writer would reopen the whole hole."""
    for name in ("seed_synthetic_devices", "import_scan_devices"):
        src = (REPO_ROOT / "tools" / "network" / "discovery_store.py").read_text(
            encoding="utf-8"
        )
        fn = next(
            n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        params = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
        assert "source" not in params, (
            f"{name} must hard-code its provenance label, never accept one"
        )


# ── 4. The de-facto learner still refuses fabricated rows ────────────────────

def test_ni_devices_feed_excludes_the_fabricating_labels():
    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "docmod" / "inventory_feeds.yaml").read_text(
            encoding="utf-8"
        )
    )
    feed = next(f for f in cfg["feeds"] if f["id"] == "ni_devices")
    assert feed["evidence_kind"] == "inventory"
    excluded = {str(v).lower() for v in (feed.get("exclude_when") or {}).get("source", [])}
    assert store.SOURCE_SYNTHETIC in excluded, (
        "ni_devices is ranked as an OBSERVED DEPLOYED ESTATE at the best "
        "precedence in the file. Without this exclusion, fabricated demo "
        "devices become the platform's strongest evidence about fielded hardware."
    )
    assert "topology_ingest" in excluded, (
        "devices re-ingested from a design diagram are already counted by the "
        "`topology_nodes` feed as `design`; reading them here promotes a "
        "drawing to `inventory` and double-counts it"
    )
    assert store.SOURCE_DISCOVERY not in excluded, (
        "a scan that reached a host and got an answer IS an observation"
    )


def test_row_filter_keeps_null_and_is_case_insensitive():
    from tools.doc_modernization.defacto_learner import _apply_row_filter

    feed = {"id": "t", "exclude_when": {"source": ["synthetic"]}}
    rows = [
        {"source": "synthetic", "model": "drop-lower"},
        {"source": "SYNTHETIC", "model": "drop-upper"},
        {"source": "discovery", "model": "keep-observed"},
        {"source": None, "model": "keep-unknown"},
        {"model": "keep-absent"},
    ]
    kept = {r["model"] for r in _apply_row_filter(feed, rows)}
    assert kept == {"keep-observed", "keep-unknown", "keep-absent"}


def test_row_filter_is_a_no_op_without_the_key():
    from tools.doc_modernization.defacto_learner import _apply_row_filter

    rows = [{"source": "synthetic"}, {"source": "netbox"}]
    assert _apply_row_filter({"id": "t"}, rows) == rows


# ── 5. Credentials are never persisted ───────────────────────────────────────

@pytest.mark.parametrize("secret_key", ["community", "password", "credential_ref"])
def test_scan_config_never_persists_a_credential(secret_key):
    cfg = {secret_key: "s3cr3t-value", "hop_limit": 2, "layout": "grid"}
    safe = store._safe_config(cfg)
    assert secret_key not in safe
    assert "s3cr3t-value" not in json.dumps(safe)
    assert safe["hop_limit"] == 2, "stripping a secret must not drop the config"


def test_scan_config_records_that_authentication_happened():
    """Stripping the secret must not erase the FACT of authentication.

    An unauthenticated ICMP sweep and a credentialed SNMP poll are different
    acts against live infrastructure, and a reader of the scan history has to be
    able to tell them apart.
    """
    assert store._safe_config({"community": "public"})["auth"] == "community"
    assert store._safe_config({"password": "hunter2"})["auth"] == "password"
    assert "auth" not in store._safe_config({"hop_limit": 0})


# ── The reflex ───────────────────────────────────────────────────────────────

def test_reflex_registered_in_both_places():
    """A reflex in only the config has never run -- the daemon dispatches from
    REFLEX_NAMES (xbm-wake-02)."""
    from tools.genesis.daemon import REFLEX_NAMES

    assert "asset_discovery" in REFLEX_NAMES
    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8")
    )
    block = cfg["reflexes"]["asset_discovery"]
    assert block["enabled"] is True
    assert block["interval_seconds"] == 86400


def test_reflex_ships_passive_with_no_targets():
    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8")
    )
    block = cfg["reflexes"]["asset_discovery"]
    assert block["targets"] == [], (
        "nothing can guess a deployment's address space; a default here would "
        "sweep whatever happens to sit on the other end of it"
    )
    assert block["method"] in store.PASSIVE_METHODS
    assert block["allow_active_scan"] is False, (
        "an SNMP/SSH sweep presents a credential to live infrastructure on a "
        "schedule with no human present -- that is an operator decision"
    )


def test_reflex_never_seeds_synthetic_devices():
    """A daemon fabricating inventory rows on a cadence manufactures its own
    evidence.

    Asserted against every NAME and ATTRIBUTE node in the module, so a call
    through any alias is caught -- and NOT against the file text, because the
    module docstring names the function in order to say it does not call it.
    """
    src = (
        REPO_ROOT / "tools" / "genesis" / "reflexes" / "asset_discovery.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    referenced = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.alias):
            referenced.add(node.name.rsplit(".", 1)[-1])
    assert "seed_synthetic_devices" not in referenced
    assert "ensure_demo_topology" not in referenced


def test_reflex_reports_success_key():
    """A reflex returning no `success` key is scored a failure on every cycle
    forever (tools/daemon/base.py::classify_failure)."""
    from tools.genesis.reflexes.asset_discovery import run

    result = run({"targets": []})
    assert result["success"] is True
    assert result["status"] == "unmeasured", (
        "a deployment that declared no targets discovered nothing and must not "
        "report a clean run"
    )
    assert "no_targets_declared" in result["refusals"]
    assert result["metric_value"] == 0.0


def test_reflex_refuses_an_active_method_without_opt_in():
    from tools.genesis.reflexes.asset_discovery import run

    result = run({"targets": ["10.0.0.1"], "method": "snmp"})
    assert result["status"] == "refused"
    assert any("allow_active_scan" in r for r in result["refusals"])
    assert result["scan_id"] is None, "a refused sweep must not record a scan"


def test_reflex_refuses_an_oversized_sweep_whole():
    """Refused, never truncated: a sweep that quietly scanned a prefix of the
    declared space would report a partial estate as a complete one."""
    from tools.genesis.reflexes.asset_discovery import run

    result = run({"targets": ["10.0.0.0/8"], "method": "ping"})
    assert result["status"] == "refused"
    assert result["addresses_expanded"] == 16777216
    assert result["scan_id"] is None


# ── The NQE local fallback ───────────────────────────────────────────────────

def test_nqe_local_fallback_names_no_table_without_ddl():
    """nc_nodes / nc_edges have no CREATE TABLE anywhere in this repository.

    Every local NQE query used to name one of them, raise, get swallowed, and
    return [] -- so the PVM attack-surface map correlated every advisory against
    zero devices and reported success.
    """
    from tools.network.nqe_client import _COLLECTION_TABLES

    tables = {t for t, _cols in _COLLECTION_TABLES.values()}
    assert "nc_nodes" not in tables
    assert "nc_edges" not in tables
    assert _COLLECTION_TABLES["network.devices"][0] == "ni_devices"


def test_unsupported_collection_is_labelled_not_reported_as_empty():
    """`unsupported_locally` is not a measurement.

    This schema stores no interface / BGP / ACL state at all, so an empty list
    there says nothing about the network. Reporting it as `local_mapping` with
    zero rows is what made a structurally dead query look like a live one.
    """
    from tools.network.nqe_client import _local_source

    assert _local_source("foreach d in network.devices select d") == "local_mapping"
    assert (
        _local_source("foreach i in network.interfaces select i")
        == "unsupported_locally"
    )


def test_device_projection_carries_the_model_where_the_matcher_looks():
    """The advisory matcher compares affected-model strings against
    `platform.ostype` -- so the model has to be there, or the attack-surface map
    stays empty even once devices arrive."""
    from tools.network.nqe_client import _project_device_row

    row = {
        "id": "d1", "node_id": "n1", "label": "dc-sw-01",
        "device_type": "switch-l3", "vendor": "Cisco", "model": "Catalyst 6500",
        "firmware_version": "15.2(7)E3", "site": "DC-East",
        "properties_json": json.dumps({"ip_address": "10.1.1.1"}),
        "source": "synthetic",
    }
    out = _project_device_row(row)
    assert out["name"] == "dc-sw-01"
    assert out["managementIp"] == "10.1.1.1"
    assert out["platform"]["ostype"] == "Catalyst 6500"
    assert out["source"] == "synthetic", "provenance travels with the record"


def test_device_projection_survives_unparseable_properties():
    from tools.network.nqe_client import _project_device_row

    out = _project_device_row({"label": "x", "properties_json": "{not json"})
    assert out["managementIp"] == ""


# ── Inventory stats honesty ──────────────────────────────────────────────────

class _RaisingConn:
    def execute(self, *_a, **_k):
        raise RuntimeError("relation \"ni_devices\" does not exist")

    def close(self):
        pass


def test_inventory_stats_report_unmeasurable_not_zero():
    """"Nothing is deployed" and "I could not look" justify opposite decisions."""
    stats = store.device_inventory_stats(conn=_RaisingConn())
    assert stats["measurable"] is False
    assert stats["total"] is None
    assert stats["observed"] is None
    assert stats["synthetic"] is None


# ── The synthetic generator ──────────────────────────────────────────────────

def test_synthetic_devices_are_deterministic_and_device_shaped():
    from icdev.tools.showcase.synthetic_data_engine import DOMAINS, SyntheticDataEngine

    assert "network_devices" in DOMAINS
    a = SyntheticDataEngine(seed=11).generate("network_devices", 8)
    b = SyntheticDataEngine(seed=11).generate("network_devices", 8)
    assert a == b, "same seed must give the same fleet, so re-seeding is idempotent"

    for dev in a:
        # The device shape, not the segment shape -- `_gen_network` produces
        # subnets and utilisation figures, which can populate a capacity chart
        # and can never populate a hardware inventory.
        for field in ("vendor", "model", "device_type", "firmware_version", "site"):
            assert dev[field], f"{field} missing from a synthetic device record"


def test_synthetic_eol_dates_straddle_today():
    """A fleet uniformly current exercises none of the EOL logic that reads this
    table; one uniformly expired renders as an implausible wall of red."""
    from datetime import datetime, timezone

    from icdev.tools.showcase.synthetic_data_engine import SyntheticDataEngine

    devices = SyntheticDataEngine(seed=3).generate("network_devices", 60)
    dated = [d for d in devices if d["eol_date"]]
    assert dated, "no device carries an EOL date"
    assert len(dated) < len(devices), (
        "a device whose EOL nobody has recorded is the common real case and "
        "must not be invented as 'fine'"
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert any(d["eol_date"] < today for d in dated), "no device is past EOL"
    assert any(d["eol_date"] > today for d in dated), "no device is approaching EOL"
