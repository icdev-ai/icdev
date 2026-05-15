# Auto-grader for DevOps M05 Step 1: LocalStack Cloud Provisioner

import sys
from io import StringIO

# ── slugify tests ─────────────────────────────────────────────────────────────

assert slugify("User Events")    == "user-events",     f"Got {slugify('User Events')}"
assert slugify("Alert Queue")    == "alert-queue",     f"Got {slugify('Alert Queue')}"
assert slugify("Raw Data Lake")  == "raw-data-lake",   f"Got {slugify('Raw Data Lake')}"
assert slugify("Config_Store")   == "config-store",    f"Got {slugify('Config_Store')}"
assert slugify("  Leading  ")    == "leading",         f"Got {slugify('  Leading  ')}"
assert slugify("")               == "unnamed",         "Empty string must return 'unnamed'"
assert slugify("!!!###")         == "unnamed",         "All-special chars must return 'unnamed'"
assert slugify("a" * 100, 63)   == "a" * 63,          "slugify must truncate to max_len"
assert len(slugify("x" * 200))  <= 63,                 "Default max_len is 63"

# ── get_resource_type tests ───────────────────────────────────────────────────

assert get_resource_type("ent-collection") == "dynamodb", \
    f"ent-collection → dynamodb, got {get_resource_type('ent-collection')}"
assert get_resource_type("ent-table")      == "dynamodb", \
    f"ent-table → dynamodb, got {get_resource_type('ent-table')}"
assert get_resource_type("ent-queue")      == "sqs", \
    f"ent-queue → sqs, got {get_resource_type('ent-queue')}"
assert get_resource_type("ent-datalake")   == "s3", \
    f"ent-datalake → s3, got {get_resource_type('ent-datalake')}"
assert get_resource_type("ent-file")       == "s3", \
    f"ent-file → s3, got {get_resource_type('ent-file')}"
assert get_resource_type("ent-pipeline")   is None, \
    f"ent-pipeline is not provisionable — must return None, got {get_resource_type('ent-pipeline')}"
assert get_resource_type("unknown-type")   is None, \
    "Unknown types must return None"

# ── build_resource_name tests ─────────────────────────────────────────────────

name1 = build_resource_name("my-design", "User Events", "dynamodb")
assert name1 == "icdev-my-design-user-events", \
    f"Expected 'icdev-my-design-user-events', got {name1!r}"

name2 = build_resource_name("abc123", "Alert Queue", "sqs")
assert name2 == "icdev-abc123-alert-queue", \
    f"Expected 'icdev-abc123-alert-queue', got {name2!r}"

name3 = build_resource_name("raw-data-lake", "Config Store", "s3")
assert name3 == "icdev-raw-data-lake-config-store", \
    f"Expected 'icdev-raw-data-lake-config-store', got {name3!r}"

# Resource names must be DNS-safe (no spaces, no uppercase, no special chars)
for name in [name1, name2, name3]:
    assert name == name.lower(), f"Resource name must be lowercase: {name}"
    assert " " not in name, f"Resource name must not contain spaces: {name}"
    assert name.startswith("icdev-"), f"Resource name must start with 'icdev-': {name}"

# ── LocalStackProvisioner unit tests (stub clients) ───────────────────────────

dynamo_stub = _StubBotoClient()
sqs_stub    = _StubBotoClient()
s3_stub     = _StubBotoClient()

provisioner = LocalStackProvisioner(
    endpoint="http://localhost:4566",
    client_factory=_stub_factory({
        "dynamodb": dynamo_stub,
        "sqs":      sqs_stub,
        "s3":       s3_stub,
    }),
)

# provision_dynamodb
d_result = provisioner.provision_dynamodb("icdev-test-user-events")
assert d_result is not None, "provision_dynamodb() returned None"
assert d_result.get("action") == "created", \
    f"provision_dynamodb action must be 'created', got {d_result.get('action')}"
assert d_result.get("service") == "dynamodb", \
    f"service must be 'dynamodb', got {d_result.get('service')}"
assert d_result.get("name") == "icdev-test-user-events", \
    f"name must match argument, got {d_result.get('name')}"
assert len(dynamo_stub.calls) == 1, "provision_dynamodb must call create_table once"
call_name, call_kwargs = dynamo_stub.calls[0]
assert call_name == "create_table", f"Expected create_table, got {call_name}"
assert call_kwargs.get("TableName") == "icdev-test-user-events"
assert any(ks.get("KeyType") == "HASH" for ks in call_kwargs.get("KeySchema", [])), \
    "KeySchema must include a HASH key"

# provision_sqs
q_result = provisioner.provision_sqs("icdev-test-alert-queue")
assert q_result.get("action")  == "created",  f"sqs action={q_result.get('action')}"
assert q_result.get("service") == "sqs",      f"sqs service={q_result.get('service')}"
assert len(sqs_stub.calls) == 1, "provision_sqs must call create_queue once"
_, sq_kwargs = sqs_stub.calls[0]
assert sq_kwargs.get("QueueName") == "icdev-test-alert-queue"

# provision_s3
s3_result = provisioner.provision_s3("icdev-test-raw-data-lake")
assert s3_result.get("action")  == "created", f"s3 action={s3_result.get('action')}"
assert s3_result.get("service") == "s3",      f"s3 service={s3_result.get('service')}"
assert len(s3_stub.calls) == 1, "provision_s3 must call create_bucket once"
_, s3_kwargs = s3_stub.calls[0]
assert s3_kwargs.get("Bucket") == "icdev-test-raw-data-lake"

# ── provision_from_graph end-to-end test ──────────────────────────────────────

dynamo_stub2 = _StubBotoClient()
sqs_stub2    = _StubBotoClient()
s3_stub2     = _StubBotoClient()

provisioner2 = LocalStackProvisioner(
    endpoint="http://localhost:4566",
    client_factory=_stub_factory({
        "dynamodb": dynamo_stub2,
        "sqs":      sqs_stub2,
        "s3":       s3_stub2,
    }),
)

graph_result = provisioner2.provision_from_graph("my-design", SAMPLE_GRAPH)

assert graph_result is not None, "provision_from_graph() returned None"
assert "status"      in graph_result, "result must have 'status'"
assert "provisioned" in graph_result, "result must have 'provisioned'"
assert "skipped"     in graph_result, "result must have 'skipped'"
assert "errors"      in graph_result, "result must have 'errors'"
assert "summary"     in graph_result, "result must have 'summary'"

# SAMPLE_GRAPH: 2 dynamodb, 1 sqs, 2 s3, 1 skipped (ent-pipeline)
assert graph_result["skipped"] == 1, \
    f"ent-pipeline is not provisionable → skipped=1, got {graph_result['skipped']}"
assert len(graph_result["provisioned"]) == 5, \
    f"5 nodes should be provisioned, got {len(graph_result['provisioned'])}"

summary = graph_result["summary"]
assert summary.get("dynamodb") == 2, f"2 DynamoDB tables expected, got {summary.get('dynamodb')}"
assert summary.get("sqs")      == 1, f"1 SQS queue expected, got {summary.get('sqs')}"
assert summary.get("s3")       == 2, f"2 S3 buckets expected, got {summary.get('s3')}"
assert summary.get("skipped")  == 1, f"skipped=1 in summary, got {summary.get('skipped')}"

assert graph_result["status"] in ("ok", "partial"), \
    f"status must be 'ok' or 'partial', got {graph_result['status']}"
assert graph_result["errors"] == [], f"Stub session should produce no errors, got {graph_result['errors']}"

# Verify resource names are deterministic and correctly prefixed
prov_names = [p["name"] for p in graph_result["provisioned"]]
for n in prov_names:
    assert n.startswith("icdev-my-design-"), \
        f"Resource name must start with 'icdev-my-design-', got {n!r}"

# ── main block print test ─────────────────────────────────────────────────────

captured = StringIO()
sys.stdout = captured
try:
    stubs_main = {
        "dynamodb": _StubBotoClient(),
        "sqs":      _StubBotoClient(),
        "s3":       _StubBotoClient(),
    }
    prov_main = LocalStackProvisioner(
        endpoint="http://localhost:4566",
        client_factory=_stub_factory(stubs_main),
    )
    main_result = prov_main.provision_from_graph("my-design", SAMPLE_GRAPH)
    print(f"Provisioned: {len(main_result['provisioned'])}")
    print(f"Skipped: {main_result['skipped']}")
    print(f"Status: {main_result['status']}")
    print(f"DynamoDB: {main_result['summary']['dynamodb']}")
    print(f"SQS: {main_result['summary']['sqs']}")
    print(f"S3: {main_result['summary']['s3']}")
finally:
    sys.stdout = sys.__stdout__

main_out = captured.getvalue()
assert "Provisioned: 5" in main_out, "Print 'Provisioned: 5' in your main block"
assert "Skipped: 1"     in main_out, "Print 'Skipped: 1' in your main block"
assert "DynamoDB: 2"    in main_out, "Print 'DynamoDB: 2' in your main block"
assert "SQS: 1"         in main_out, "Print 'SQS: 1' in your main block"
assert "S3: 2"          in main_out, "Print 'S3: 2' in your main block"

print("PASS: LocalStack Cloud Provisioner complete. slugify + get_resource_type + build_resource_name + LocalStackProvisioner all verified.")
