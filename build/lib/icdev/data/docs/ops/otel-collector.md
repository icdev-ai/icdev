# OTel Collector — ODC Twin Setup

OpenTelemetry Collector bridges application telemetry into ICDEV's ODC Twin MITRE coverage gap engine.
Config lives at `args/otel_collector_config.yaml`.

---

## Architecture

```
Application / Agent
      │  OTLP (gRPC :4317 or HTTP :4318)
      ▼
 OTel Collector
  ├─ receiver: otlp
  ├─ processor: batch
  └─ exporter: file → .tmp/otel/telemetry.jsonl
      │
      ▼
 ODC Twin gap engine (reads .tmp/otel/telemetry.jsonl)
```

---

## Install

### 1. Download the collector binary

```bash
# Linux/macOS — replace VERSION with the desired release (e.g. 0.102.0)
VERSION=0.102.0
curl -Lo otelcol "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VERSION}/otelcol_${VERSION}_linux_amd64"
chmod +x otelcol
sudo mv otelcol /usr/local/bin/otelcol
```

Windows: download the `.exe` from the [releases page](https://github.com/open-telemetry/opentelemetry-collector-releases/releases) and add it to `%PATH%`.

Air-gap: copy the binary from an internet-connected host and place it on `PATH` before the steps below.

### 2. Create the output directory

```bash
mkdir -p .tmp/otel
```

### 3. Validate the config

```bash
otelcol validate --config=args/otel_collector_config.yaml
```

Expected output:
```
2024/01/01 00:00:00 Everything is OK. Exiting with 0 status.
```

### 4. Run the collector

```bash
otelcol --config=args/otel_collector_config.yaml
```

For a long-running deployment, use a systemd unit or the Windows Service wrapper.

---

## Configuration reference

| Section | Key | Default | Notes |
|---------|-----|---------|-------|
| `receivers.otlp.protocols.grpc.endpoint` | gRPC listen address | `0.0.0.0:4317` | Change to `127.0.0.1:4317` to restrict to localhost |
| `receivers.otlp.protocols.http.endpoint` | HTTP listen address | `0.0.0.0:4318` | Change to `127.0.0.1:4318` to restrict to localhost |
| `processors.batch.timeout` | Max wait before flush | `10s` | Lower for lower latency |
| `processors.batch.send_batch_size` | Target batch size | `1024` | |
| `exporters.file.path` | Output JSONL file | `.tmp/otel/telemetry.jsonl` | Must be writable by the collector process |
| `exporters.file.rotation.max_megabytes` | Per-file size cap | `100` | |
| `exporters.file.rotation.max_days` | Retention window | `7` | Adjust to meet AU-11 requirements |
| `exporters.file.rotation.max_backups` | Rotated file count | `3` | |

All three pipelines (traces, metrics, logs) share the same receiver → batch → file chain.

---

## Sending test data

```bash
# Install the OTLP gRPC test tool (requires Go)
go install github.com/open-telemetry/opentelemetry-collector-contrib/cmd/telemetrygen@latest

# Send 10 synthetic traces
telemetrygen traces --otlp-insecure --traces 10
```

Or from Python (opentelemetry-sdk):

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("icdev.test")
with tracer.start_as_current_span("test-span"):
    pass
```

After running, verify `.tmp/otel/telemetry.jsonl` is non-empty.

---

## Security notes

- **Air-gap / IL4+:** bind endpoints to `127.0.0.1` and route only from trusted local agents.
- The file exporter writes plaintext JSONL. For IL5/IL6, replace with an encrypted exporter (e.g., OTLP-over-mTLS to a hardened backend) and set `max_days` to match AU-11 retention policy.
- Never expose ports 4317/4318 on a public network interface without TLS and authentication.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `validate` exits non-zero | YAML syntax error or unsupported field | Run `yamllint args/otel_collector_config.yaml` to identify the line |
| Collector starts but no output | Output directory missing | `mkdir -p .tmp/otel` |
| gRPC connection refused | Wrong port or collector not running | Check `otelcol` process and firewall rules |
| File grows unbounded | Rotation not supported by binary build | Use `otelcol-contrib` which includes the file exporter with rotation |
