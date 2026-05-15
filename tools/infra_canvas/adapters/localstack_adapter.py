# CUI // SP-CTI
"""ICDEV™ Infrastructure Canvas — LocalStack AWS Emulation Adapter.

All HTTP egress routes through DataBridge LocalStackConnector so that secret
resolution, audit logging, and health probing use the standard DataBridge
pipeline (ADR D360+: all external comms via DataBridge).

Feature flag: LOCALSTACK_ENABLED in .env (default: false, air-gap safe).
When disabled, all public methods return a typed disabled response — no network
calls are made and no exceptions raised. IDC canvas features that depend on
LocalStack (pre-apply validation, local deploy) degrade gracefully.

Usage::

    from tools.infra_canvas.adapters.localstack_adapter import LocalStackAdapter

    adapter = LocalStackAdapter.from_env()
    print(adapter.health())

    if adapter.is_enabled():
        adapter.create_bucket("my-test-bucket")
        adapter.deploy_terraform_local(plan_dir="/tmp/plan", namespace="dev")
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.databridge.feature_flags import IntegrationFeatureFlags

DEFAULT_TIMEOUT = 15.0


def _build_connector(endpoint: str, region: str, access_key: str, secret_key: str, timeout: float):
    """Return a configured LocalStackConnector without triggering health checks."""
    from tools.databridge.connectors.localstack_connector import LocalStackConnector  # noqa: PLC0415

    cfg = {
        "endpoint": endpoint,
        "region": region,
        "access_key": access_key,
        "secret_key": secret_key,
        "timeout": timeout,
    }
    c = LocalStackConnector()
    c._config = cfg
    c._endpoint = endpoint
    c._base_url = endpoint
    c._region = region
    c._access_key = access_key
    c._secret_key = secret_key
    c._auth_headers = {}
    c._connected = True
    return c


class LocalStackAdapter:
    """LocalStack AWS emulation adapter for the Infrastructure Canvas.

    Thin wrapper over LocalStackConnector. Checks LOCALSTACK_ENABLED at init
    time — all public methods return a disabled dict when the flag is off.
    """

    def __init__(
        self,
        endpoint: str = "",
        region: str = "",
        access_key: str = "",
        secret_key: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._flag = IntegrationFeatureFlags.localstack()
        endpoint = endpoint or IntegrationFeatureFlags.localstack_endpoint()
        region = region or IntegrationFeatureFlags.localstack_region()
        ak, sk = IntegrationFeatureFlags.localstack_credentials()
        access_key = access_key or ak
        secret_key = secret_key or sk

        self._endpoint = endpoint
        self._region = region
        self._timeout = timeout
        self._connector = (
            _build_connector(endpoint, region, access_key, secret_key, timeout)
            if self._flag.enabled
            else None
        )

    @classmethod
    def from_env(cls) -> "LocalStackAdapter":
        """Construct adapter entirely from environment variables."""
        endpoint = IntegrationFeatureFlags.localstack_endpoint()
        region = IntegrationFeatureFlags.localstack_region()
        ak, sk = IntegrationFeatureFlags.localstack_credentials()
        timeout = IntegrationFeatureFlags.localstack_timeout()
        return cls(endpoint=endpoint, region=region, access_key=ak, secret_key=sk, timeout=timeout)

    # ------------------------------------------------------------------ #
    # Status helpers
    # ------------------------------------------------------------------ #

    def is_enabled(self) -> bool:
        return self._flag.enabled

    def _disabled(self) -> Dict[str, Any]:
        return self._flag.as_disabled_response()

    def get_endpoint(self) -> str:
        """Return the LocalStack endpoint URL (for env-var injection into Terraform)."""
        return self._endpoint

    def get_env_vars(self) -> Dict[str, str]:
        """Return the AWS env vars needed to point boto3/awscli at LocalStack.

        Inject these into subprocess calls or Terraform workspace environments::

            env = {**os.environ, **adapter.get_env_vars()}
            subprocess.run(["terraform", "apply"], env=env)
        """
        return {
            "AWS_ENDPOINT_URL": self._endpoint,
            "AWS_DEFAULT_REGION": self._region,
            "AWS_ACCESS_KEY_ID": self._connector._access_key if self._connector else "test",
            "AWS_SECRET_ACCESS_KEY": self._connector._secret_key if self._connector else "test",
            # Service-specific endpoint overrides (Terraform aws provider)
            "AWS_ENDPOINT_URL_S3": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_DYNAMODB": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_LAMBDA": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_SQS": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_SNS": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_API_GATEWAY": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_CLOUDWATCH": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_SSM": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_SECRETS_MANAGER": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_ECR": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_STS": f"{self._endpoint}",
            "AWS_ENDPOINT_URL_IAM": f"{self._endpoint}",
        }

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    def health(self) -> Dict[str, Any]:
        """Check LocalStack reachability and list running services."""
        if not self._flag.enabled:
            return self._disabled()
        return self._connector.health_check()

    def list_services(self) -> List[Dict[str, str]]:
        """Return list of {service, status} dicts for all LocalStack services."""
        if not self._flag.enabled:
            return []
        from tools.databridge.connector import ConnectorRequest  # noqa: PLC0415

        resp = self._connector.read(ConnectorRequest(table_name="services"))
        return resp.data if resp.status == "ok" and resp.data else []

    # ------------------------------------------------------------------ #
    # S3
    # ------------------------------------------------------------------ #

    def list_buckets(self) -> List[Dict[str, Any]]:
        """List S3 buckets in LocalStack."""
        if not self._flag.enabled:
            return []
        return self._boto3_read("s3_buckets")

    def create_bucket(self, name: str) -> Dict[str, Any]:
        """Create an S3 bucket in LocalStack."""
        if not self._flag.enabled:
            return self._disabled()
        kwargs: Dict[str, Any] = {"Bucket": name}
        if self._region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
        return self._boto3_write("s3", "create_bucket", kwargs)

    def delete_bucket(self, name: str, force: bool = True) -> Dict[str, Any]:
        """Delete an S3 bucket. force=True empties it first."""
        if not self._flag.enabled:
            return self._disabled()
        if force:
            # Empty the bucket before deleting
            self._boto3_write("s3", "delete_objects", {
                "Bucket": name,
                "Delete": {"Objects": []},
            })
        return self._boto3_write("s3", "delete_bucket", {"Bucket": name})

    # ------------------------------------------------------------------ #
    # DynamoDB
    # ------------------------------------------------------------------ #

    def list_tables(self) -> List[str]:
        """List DynamoDB table names in LocalStack."""
        if not self._flag.enabled:
            return []
        rows = self._boto3_read("dynamodb_tables")
        return [r if isinstance(r, str) else r.get("TableName", "") for r in rows]

    def create_table(
        self,
        name: str,
        partition_key: str,
        partition_key_type: str = "S",
        sort_key: Optional[str] = None,
        sort_key_type: str = "S",
        billing_mode: str = "PAY_PER_REQUEST",
    ) -> Dict[str, Any]:
        """Create a DynamoDB table in LocalStack."""
        if not self._flag.enabled:
            return self._disabled()
        attr_defs = [{"AttributeName": partition_key, "AttributeType": partition_key_type}]
        key_schema = [{"AttributeName": partition_key, "KeyType": "HASH"}]
        if sort_key:
            attr_defs.append({"AttributeName": sort_key, "AttributeType": sort_key_type})
            key_schema.append({"AttributeName": sort_key, "KeyType": "RANGE"})
        return self._boto3_write("dynamodb", "create_table", {
            "TableName": name,
            "AttributeDefinitions": attr_defs,
            "KeySchema": key_schema,
            "BillingMode": billing_mode,
        })

    # ------------------------------------------------------------------ #
    # Lambda
    # ------------------------------------------------------------------ #

    def list_functions(self) -> List[Dict[str, Any]]:
        """List Lambda functions in LocalStack."""
        if not self._flag.enabled:
            return []
        return self._boto3_read("lambda_functions")

    def invoke_lambda(
        self, function_name: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Invoke a Lambda function in LocalStack and return the parsed response."""
        if not self._flag.enabled:
            return self._disabled()
        import json as _json

        kwargs: Dict[str, Any] = {"FunctionName": function_name, "InvocationType": "RequestResponse"}
        if payload:
            kwargs["Payload"] = _json.dumps(payload).encode("utf-8")
        result = self._boto3_write("lambda", "invoke", kwargs)
        if result.get("status") == "error":
            return result
        # Decode streaming body if present
        body = result.get("Payload", b"")
        if hasattr(body, "read"):
            body = body.read()
        if isinstance(body, (bytes, bytearray)):
            try:
                result["Payload"] = _json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                result["Payload"] = body.decode("utf-8", errors="replace")
        return result

    # ------------------------------------------------------------------ #
    # SQS
    # ------------------------------------------------------------------ #

    def list_queues(self) -> List[str]:
        """List SQS queue URLs in LocalStack."""
        if not self._flag.enabled:
            return []
        rows = self._boto3_read("sqs_queues")
        return [r if isinstance(r, str) else r.get("QueueUrl", "") for r in rows]

    def create_queue(self, name: str, fifo: bool = False) -> Dict[str, Any]:
        """Create an SQS queue in LocalStack."""
        if not self._flag.enabled:
            return self._disabled()
        attrs: Dict[str, str] = {}
        queue_name = f"{name}.fifo" if fifo and not name.endswith(".fifo") else name
        if fifo:
            attrs["FifoQueue"] = "true"
        kwargs: Dict[str, Any] = {"QueueName": queue_name}
        if attrs:
            kwargs["Attributes"] = attrs
        return self._boto3_write("sqs", "create_queue", kwargs)

    # ------------------------------------------------------------------ #
    # ECR
    # ------------------------------------------------------------------ #

    def list_repositories(self) -> List[Dict[str, Any]]:
        """List ECR repositories in LocalStack."""
        if not self._flag.enabled:
            return []
        return self._boto3_read("ecr_repositories")

    def create_repository(self, name: str) -> Dict[str, Any]:
        """Create an ECR repository in LocalStack."""
        if not self._flag.enabled:
            return self._disabled()
        return self._boto3_write("ecr", "create_repository", {"repositoryName": name})

    # ------------------------------------------------------------------ #
    # Terraform local deploy
    # ------------------------------------------------------------------ #

    def deploy_terraform_local(
        self,
        plan_dir: str,
        namespace: str = "local",
        auto_approve: bool = True,
        extra_vars: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Run terraform apply in plan_dir pointed at LocalStack.

        Injects AWS endpoint env vars so all provider calls go to LocalStack.
        Tags resources with namespace for later cleanup via destroy_namespace().

        Returns::

            {"status": "ok"|"error", "stdout": "...", "stderr": "...", "returncode": 0}
        """
        if not self._flag.enabled:
            return self._disabled()

        plan_path = Path(plan_dir)
        if not plan_path.is_dir():
            return {"status": "error", "error": f"Plan directory not found: {plan_dir}"}

        env = {**os.environ, **self.get_env_vars()}
        if extra_vars:
            env.update(extra_vars)
        env["TF_VAR_namespace"] = namespace

        try:
            # terraform init (idempotent)
            init_result = subprocess.run(  # noqa: S603
                ["terraform", "init", "-no-color"],
                cwd=str(plan_path),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if init_result.returncode != 0:
                return {
                    "status": "error",
                    "phase": "init",
                    "stdout": init_result.stdout,
                    "stderr": init_result.stderr,
                    "returncode": init_result.returncode,
                }

            cmd = ["terraform", "apply", "-no-color"]
            if auto_approve:
                cmd.append("-auto-approve")

            apply_result = subprocess.run(  # noqa: S603
                cmd,
                cwd=str(plan_path),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            return {
                "status": "ok" if apply_result.returncode == 0 else "error",
                "phase": "apply",
                "namespace": namespace,
                "endpoint": self._endpoint,
                "stdout": apply_result.stdout,
                "stderr": apply_result.stderr,
                "returncode": apply_result.returncode,
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "error": "terraform binary not found in PATH",
                "hint": "Install Terraform: https://developer.hashicorp.com/terraform/install",
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "terraform apply timed out after 300s"}

    def destroy_namespace(self, plan_dir: str, namespace: str = "local") -> Dict[str, Any]:
        """Run terraform destroy for a previously deployed namespace."""
        if not self._flag.enabled:
            return self._disabled()

        plan_path = Path(plan_dir)
        if not plan_path.is_dir():
            return {"status": "error", "error": f"Plan directory not found: {plan_dir}"}

        env = {**os.environ, **self.get_env_vars()}
        env["TF_VAR_namespace"] = namespace

        try:
            result = subprocess.run(  # noqa: S603
                ["terraform", "destroy", "-auto-approve", "-no-color"],
                cwd=str(plan_path),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            return {
                "status": "ok" if result.returncode == 0 else "error",
                "namespace": namespace,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except FileNotFoundError:
            return {"status": "error", "error": "terraform binary not found in PATH"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "terraform destroy timed out after 300s"}

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _boto3_read(self, table_name: str) -> List[Any]:
        from tools.databridge.connector import ConnectorRequest  # noqa: PLC0415

        resp = self._connector.read(ConnectorRequest(table_name=table_name))
        if resp.status == "ok" and resp.data:
            return resp.data
        return []

    def _boto3_write(self, service: str, method: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        from tools.databridge.connector import ConnectorRequest  # noqa: PLC0415

        resp = self._connector.write(
            ConnectorRequest(query=method, filters={"service": service}),
            data=kwargs,
        )
        if resp.status == "ok":
            result = resp.data[0] if resp.data else {}
            result.setdefault("status", "ok")
            return result
        return {"status": "error", "error": resp.errors[0] if resp.errors else "unknown"}
