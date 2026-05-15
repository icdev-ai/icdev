"""DevOps M05 — LocalStack Cloud Provisioner.

Goal: Build a provisioner that reads a DDC (Data Design Canvas) node graph
and creates the corresponding AWS resources in LocalStack:

  - ent-collection / ent-table  →  DynamoDB table
  - ent-queue                   →  SQS queue
  - ent-datalake / ent-file     →  S3 bucket

You won't need a running LocalStack instance to pass the auto-grader — the
resource name derivation and type mapping logic is tested with a stubbed boto3
client.  Resource naming must be deterministic and URL/DNS-safe.
"""

import re

# ── DDC node type → AWS resource type mapping ─────────────────────────────────

DDC_RESOURCE_MAP = {
    # TODO: fill in these mappings (values: "dynamodb", "sqs", or "s3")
    "ent-collection": "???",
    "ent-table":      "???",
    "ent-queue":      "???",
    "ent-datalake":   "???",
    "ent-file":       "???",
}

# ── Sample graph (used by grader — do not modify) ─────────────────────────────

SAMPLE_GRAPH = {
    "nodes": [
        {"id": "d1", "type": "ent-collection", "label": "User Events",   "config": {}},
        {"id": "d2", "type": "ent-table",       "label": "Audit Logs",    "config": {}},
        {"id": "d3", "type": "ent-queue",        "label": "Alert Queue",   "config": {}},
        {"id": "d4", "type": "ent-datalake",     "label": "Raw Data Lake", "config": {}},
        {"id": "d5", "type": "ent-file",         "label": "Config Store",  "config": {}},
        {"id": "d6", "type": "ent-pipeline",     "label": "ETL Pipeline",  "config": {}},
    ],
}

_SLUG_RE = re.compile(r"[^a-z0-9\-]")


def slugify(label: str, max_len: int = 63) -> str:
    """Convert a human label into a DNS/URL-safe resource name.

    Steps:
      1. Lowercase
      2. Replace spaces and underscores with hyphens
      3. Strip any character that is not a-z, 0-9, or hyphen
      4. Collapse consecutive hyphens into one
      5. Strip leading/trailing hyphens
      6. Truncate to max_len characters
      7. If the result is empty, return "unnamed"
    """
    # TODO: implement
    pass


def get_resource_type(node_type: str) -> str | None:
    """Return "dynamodb", "sqs", or "s3" for a DDC node type.

    Return None for unknown/unsupported types (they should be skipped).
    """
    # TODO: implement using DDC_RESOURCE_MAP
    pass


def build_resource_name(design_id: str, node_label: str, resource_type: str) -> str:
    """Derive a unique, deterministic resource name for a LocalStack resource.

    Format: "icdev-{design_slug}-{label_slug}"
    where design_slug is slugify(design_id, max_len=20)
    and   label_slug  is slugify(node_label, max_len=40)

    Examples:
      ("my-design", "User Events", "dynamodb") → "icdev-my-design-user-events"
      ("abc123",    "Alert Queue", "sqs")       → "icdev-abc123-alert-queue"
    """
    # TODO: implement
    pass


class LocalStackProvisioner:
    """Provision LocalStack resources from a DDC canvas graph.

    The boto3 client factory is injected so the class is testable without
    a running LocalStack instance.
    """

    def __init__(self, endpoint: str = "http://localhost:4566",
                 client_factory=None, region: str = "us-east-1"):
        """
        Args:
            endpoint:       LocalStack endpoint URL.
            client_factory: callable(service_name) → boto3 client.
                            If None, create real boto3 clients pointing at endpoint.
            region:         AWS region name (passed to boto3).
        """
        # TODO: store endpoint, region, and client_factory
        # If client_factory is None, set self._factory to a lambda that calls
        # boto3.client(service, endpoint_url=endpoint, region_name=region,
        #              aws_access_key_id="test", aws_secret_access_key="test")
        pass

    def _client(self, service: str):
        """Return a boto3 client for the given service."""
        # TODO: call self._factory(service)
        pass

    def provision_dynamodb(self, name: str) -> dict:
        """Create a DynamoDB table named `name` with a single String hash key "pk".

        boto3 call:
            client.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )

        Return {"action": "created", "name": name, "service": "dynamodb"}
        on success, or {"action": "error", "name": name, "error": str(e)} on failure.
        """
        # TODO: implement
        pass

    def provision_sqs(self, name: str) -> dict:
        """Create an SQS queue named `name`.

        boto3 call: client.create_queue(QueueName=name)

        Return {"action": "created", "name": name, "service": "sqs"}
        on success, or {"action": "error", ...} on failure.
        """
        # TODO: implement
        pass

    def provision_s3(self, name: str) -> dict:
        """Create an S3 bucket named `name`.

        boto3 call: client.create_bucket(Bucket=name)

        Return {"action": "created", "name": name, "service": "s3"}
        on success, or {"action": "error", ...} on failure.
        """
        # TODO: implement
        pass

    def provision_from_graph(self, design_id: str, graph: dict) -> dict:
        """Iterate graph nodes and provision the appropriate resource for each.

        Skip nodes whose type is not in DDC_RESOURCE_MAP.

        Returns:
            {
                "status":      "ok" | "partial",
                "provisioned": [{"action", "name", "service"}, ...],
                "skipped":     <int>,
                "errors":      [str, ...],
                "summary":     {"dynamodb": int, "sqs": int, "s3": int, "skipped": int},
            }
        """
        # TODO: implement
        pass


# ── Stub boto3 client for local testing (do not modify) ───────────────────────

class _StubBotoClient:
    """Records boto3 calls without hitting any real endpoint."""
    def __init__(self):
        self.calls = []

    def create_table(self, **kwargs):
        self.calls.append(("create_table", kwargs))
        return {"TableDescription": {"TableName": kwargs["TableName"]}}

    def create_queue(self, **kwargs):
        self.calls.append(("create_queue", kwargs))
        return {"QueueUrl": f"http://localhost:4566/000000000000/{kwargs['QueueName']}"}

    def create_bucket(self, **kwargs):
        self.calls.append(("create_bucket", kwargs))
        return {}


def _stub_factory(client_map: dict):
    """Return a client_factory that returns pre-built stub clients by service."""
    def factory(service):
        return client_map[service]
    return factory


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    stubs = {
        "dynamodb": _StubBotoClient(),
        "sqs":      _StubBotoClient(),
        "s3":       _StubBotoClient(),
    }
    provisioner = LocalStackProvisioner(
        endpoint="http://localhost:4566",
        client_factory=_stub_factory(stubs),
    )
    result = provisioner.provision_from_graph("my-design", SAMPLE_GRAPH)
    print(f"Provisioned: {len(result['provisioned'])}")
    print(f"Skipped: {result['skipped']}")
    print(f"Status: {result['status']}")
    print(f"DynamoDB: {result['summary']['dynamodb']}")
    print(f"SQS: {result['summary']['sqs']}")
    print(f"S3: {result['summary']['s3']}")
