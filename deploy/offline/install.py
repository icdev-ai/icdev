#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV On-Premises Air-Gapped Installer.

Installs ICDEV into an air-gapped Kubernetes cluster.
No internet access required -- all images loaded from local tarball.

Usage:
    python install.py [OPTIONS]
    python install.py --namespace icdev --values values.yaml --license license.json
    python install.py --skip-images --license license.json
"""

import argparse
import base64
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VERSION = "21.0.0"
SCRIPT_DIR = Path(__file__).resolve().parent
HELM_CHART_DIR = SCRIPT_DIR.parent / "helm"

# ---------------------------------------------------------------------------
# Colors (ANSI -- disabled on Windows without ANSI support)
# ---------------------------------------------------------------------------
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
if os.name == "nt":
    # Enable ANSI on Windows 10+ if possible
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        _USE_COLOR = True
    except Exception:
        _USE_COLOR = False


def _c(code, text):
    if _USE_COLOR:
        return "\033[{}m{}\033[0m".format(code, text)
    return text


def _cyan(t):
    return _c("0;36", t)


def _green(t):
    return _c("0;32", t)


def _yellow(t):
    return _c("1;33", t)


def _red(t):
    return _c("0;31", t)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
def info(msg):
    print("{}  {}".format(_green("[INFO]"), msg))


def warn(msg):
    print("{}  {}".format(_yellow("[WARN]"), msg))


def err(msg):
    print("{}  {}".format(_red("[ERROR]"), msg), file=sys.stderr)


def step(msg):
    print("\n{}".format(_cyan("==> " + msg)))


# ---------------------------------------------------------------------------
# Shell command runner
# ---------------------------------------------------------------------------
def run(cmd, check=True, capture=False, stdin_data=None):
    """Run a shell command. Returns CompletedProcess."""
    kwargs = {
        "shell": isinstance(cmd, str),
        "check": check,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if stdin_data is not None:
        kwargs["input"] = stdin_data
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    try:
        return subprocess.run(cmd if isinstance(cmd, str) else cmd, **kwargs)
    except subprocess.CalledProcessError as exc:
        if capture or stdin_data is not None:
            stderr_text = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            if stderr_text:
                err(stderr_text.strip())
        raise
    except FileNotFoundError:
        return None


def cmd_exists(name):
    """Check if a command exists in PATH."""
    return shutil.which(name) is not None


def run_quiet(cmd):
    """Run a command, return True if exit code 0."""
    try:
        subprocess.run(
            cmd if isinstance(cmd, list) else cmd.split(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
def banner():
    print()
    print(_cyan("================================================================"))
    print(_cyan("  CUI // SP-CTI"))
    print(_cyan("  ICDEV On-Premises Installer v{}".format(VERSION)))
    print(_cyan("  Intelligent Coding Development Platform"))
    print(_cyan("================================================================"))
    print()


# ---------------------------------------------------------------------------
# Step 1: Prerequisites
# ---------------------------------------------------------------------------
def check_prerequisites(skip_images):
    """Verify kubectl, helm, and container runtime are available."""
    step("Step 1/9: Checking prerequisites")
    ok = True

    for cmd_name in ("kubectl", "helm"):
        if cmd_exists(cmd_name):
            info("{} found: {}".format(cmd_name, shutil.which(cmd_name)))
        else:
            err("{} is required but not found in PATH".format(cmd_name))
            ok = False

    container_cli = None
    if not skip_images:
        if cmd_exists("docker"):
            container_cli = "docker"
            info("Container runtime: docker")
        elif cmd_exists("podman"):
            container_cli = "podman"
            info("Container runtime: podman")
        else:
            err("docker or podman required for image loading "
                "(use --skip-images to bypass)")
            ok = False

    if not ok:
        err("Prerequisites not met. Aborting.")
        sys.exit(1)

    # Verify cluster connectivity
    if not run_quiet(["kubectl", "cluster-info"]):
        err("Cannot connect to Kubernetes cluster. Check your kubeconfig.")
        sys.exit(1)
    info("Kubernetes cluster reachable")

    # Verify Helm chart exists
    chart_yaml = HELM_CHART_DIR / "Chart.yaml"
    if not chart_yaml.exists():
        err("Helm chart not found at {}".format(chart_yaml))
        sys.exit(1)
    info("Helm chart found: {}".format(HELM_CHART_DIR))

    return container_cli


# ---------------------------------------------------------------------------
# Step 2: Load container images
# ---------------------------------------------------------------------------
def load_images(skip_images, container_cli, image_tarball):
    """Load container images from tarball."""
    step("Step 2/9: Loading container images")

    if skip_images:
        info("Skipping image loading (--skip-images)")
        return

    tarball = Path(image_tarball)
    if not tarball.exists():
        err("Image tarball not found: {}".format(image_tarball))
        err("Build with: docker save icdev/* | gzip > icdev-images.tar.gz")
        sys.exit(1)

    info("Loading images from {} ...".format(image_tarball))
    with open(str(tarball), "rb") as f:
        run([container_cli, "load"], stdin_data=f.read())
    info("Images loaded successfully")


# ---------------------------------------------------------------------------
# Step 3: Create namespace
# ---------------------------------------------------------------------------
def create_namespace(namespace):
    """Create the K8s namespace if it doesn't exist."""
    step("Step 3/9: Creating namespace '{}'".format(namespace))

    if run_quiet(["kubectl", "get", "namespace", namespace]):
        info("Namespace '{}' already exists".format(namespace))
    else:
        run(["kubectl", "create", "namespace", namespace])
        run([
            "kubectl", "label", "namespace", namespace,
            "classification={}".format(namespace),
            "app.kubernetes.io/part-of=icdev",
            "--overwrite",
        ])
        info("Namespace '{}' created".format(namespace))


# ---------------------------------------------------------------------------
# Step 4: Create license secret
# ---------------------------------------------------------------------------
def create_license_secret(namespace, license_file):
    """Create the license K8s secret."""
    step("Step 4/9: Creating license secret")

    if not license_file:
        warn("No --license provided. Checking for ./license.json ...")
        if Path("license.json").exists():
            license_file = "license.json"
        else:
            err("License file not found. Provide with --license /path/to/license.json")
            sys.exit(1)

    license_path = Path(license_file)
    if not license_path.exists():
        err("License file not found: {}".format(license_file))
        sys.exit(1)

    # kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -
    result = run(
        [
            "kubectl", "create", "secret", "generic", "icdev-license",
            "--from-file=license.json={}".format(str(license_path)),
            "--namespace", namespace,
            "--dry-run=client", "-o", "yaml",
        ],
        capture=True,
    )
    run(
        ["kubectl", "apply", "-f", "-"],
        stdin_data=result.stdout,
    )
    info("License secret created/updated")


# ---------------------------------------------------------------------------
# Step 5: Configure TLS
# ---------------------------------------------------------------------------
def configure_tls(namespace, tls_cert, tls_key):
    """Create TLS secret from provided certs, existing secret, or self-signed."""
    step("Step 5/9: Configuring TLS")

    if tls_cert and tls_key:
        if not Path(tls_cert).exists():
            err("TLS certificate not found: {}".format(tls_cert))
            sys.exit(1)
        if not Path(tls_key).exists():
            err("TLS key not found: {}".format(tls_key))
            sys.exit(1)

        result = run(
            [
                "kubectl", "create", "secret", "tls", "icdev-tls",
                "--cert={}".format(tls_cert),
                "--key={}".format(tls_key),
                "--namespace", namespace,
                "--dry-run=client", "-o", "yaml",
            ],
            capture=True,
        )
        run(["kubectl", "apply", "-f", "-"], stdin_data=result.stdout)
        info("TLS secret created/updated")

    elif run_quiet(["kubectl", "get", "secret", "icdev-tls", "-n", namespace]):
        info("Existing TLS secret found")

    else:
        warn("No TLS cert/key provided. Generating self-signed certificate ...")

        if cmd_exists("openssl"):
            tmpdir = tempfile.mkdtemp()
            try:
                key_path = os.path.join(tmpdir, "tls.key")
                crt_path = os.path.join(tmpdir, "tls.crt")
                run([
                    "openssl", "req", "-x509", "-nodes",
                    "-days", "365", "-newkey", "rsa:2048",
                    "-keyout", key_path, "-out", crt_path,
                    "-subj", "/CN=icdev.local/O=ICDEV",
                ], capture=True)

                result = run(
                    [
                        "kubectl", "create", "secret", "tls", "icdev-tls",
                        "--cert={}".format(crt_path),
                        "--key={}".format(key_path),
                        "--namespace", namespace,
                        "--dry-run=client", "-o", "yaml",
                    ],
                    capture=True,
                )
                run(["kubectl", "apply", "-f", "-"], stdin_data=result.stdout)
                warn("Self-signed TLS cert created. Replace with proper cert "
                     "for production.")
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            # Python fallback: generate self-signed cert without openssl CLI
            try:
                from cryptography import x509
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import rsa
                from cryptography.x509.oid import NameOID
                import datetime

                private_key = rsa.generate_private_key(
                    public_exponent=65537, key_size=2048)
                subject = issuer = x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, "icdev.local"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICDEV"),
                ])
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(subject)
                    .issuer_name(issuer)
                    .public_key(private_key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(datetime.datetime.utcnow())
                    .not_valid_after(
                        datetime.datetime.utcnow() + datetime.timedelta(days=365))
                    .sign(private_key, hashes.SHA256())
                )

                tmpdir = tempfile.mkdtemp()
                try:
                    key_path = os.path.join(tmpdir, "tls.key")
                    crt_path = os.path.join(tmpdir, "tls.crt")
                    with open(key_path, "wb") as f:
                        f.write(private_key.private_bytes(
                            serialization.Encoding.PEM,
                            serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()))
                    with open(crt_path, "wb") as f:
                        f.write(cert.public_bytes(serialization.Encoding.PEM))

                    result = run(
                        [
                            "kubectl", "create", "secret", "tls", "icdev-tls",
                            "--cert={}".format(crt_path),
                            "--key={}".format(key_path),
                            "--namespace", namespace,
                            "--dry-run=client", "-o", "yaml",
                        ],
                        capture=True,
                    )
                    run(["kubectl", "apply", "-f", "-"], stdin_data=result.stdout)
                    warn("Self-signed TLS cert created (via Python cryptography). "
                         "Replace with proper cert for production.")
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)

            except ImportError:
                err("Neither openssl CLI nor Python cryptography library available.")
                err("Provide TLS cert/key with --tls-cert and --tls-key")
                sys.exit(1)


# ---------------------------------------------------------------------------
# Step 6: Create DB credentials secret
# ---------------------------------------------------------------------------
def create_db_credentials(namespace, db_password):
    """Create the database credentials K8s secret."""
    step("Step 6/9: Creating database credentials")

    if run_quiet([
        "kubectl", "get", "secret", "icdev-db-credentials", "-n", namespace
    ]):
        info("Existing DB credentials secret found")
        return

    if not db_password:
        # Generate a random password (base64 of 24 random bytes)
        db_password = base64.b64encode(secrets.token_bytes(24)).decode("ascii")
        info("Generated random database password")

    result = run(
        [
            "kubectl", "create", "secret", "generic", "icdev-db-credentials",
            "--from-literal=password={}".format(db_password),
            "--namespace", namespace,
            "--dry-run=client", "-o", "yaml",
        ],
        capture=True,
    )
    run(["kubectl", "apply", "-f", "-"], stdin_data=result.stdout)
    info("DB credentials secret created")


# ---------------------------------------------------------------------------
# Step 7: Helm install / upgrade
# ---------------------------------------------------------------------------
def helm_install(namespace, values_file, timeout):
    """Install or upgrade ICDEV via Helm."""
    step("Step 7/9: Installing ICDEV via Helm")

    # Check if already installed
    already_installed = run_quiet([
        "helm", "status", "icdev", "-n", namespace
    ])

    action = "upgrade" if already_installed else "install"
    if already_installed:
        info("Existing installation detected -- upgrading")

    helm_cmd = [
        "helm", action, "icdev", str(HELM_CHART_DIR),
        "--namespace", namespace,
        "--timeout", "{}s".format(timeout),
        "--wait",
    ]

    if values_file:
        if not Path(values_file).exists():
            err("Values file not found: {}".format(values_file))
            sys.exit(1)
        helm_cmd.extend(["-f", values_file])

    run(helm_cmd)
    info("Helm install/upgrade complete")


# ---------------------------------------------------------------------------
# Step 8: Wait for pods
# ---------------------------------------------------------------------------
def wait_for_pods(namespace, timeout):
    """Wait for all ICDEV pods to become ready."""
    step("Step 8/9: Waiting for pods to be ready")

    try:
        run([
            "kubectl", "wait", "--for=condition=ready", "pod",
            "--selector=app.kubernetes.io/instance=icdev",
            "--namespace", namespace,
            "--timeout={}s".format(timeout),
        ])
    except subprocess.CalledProcessError:
        warn("Some pods did not become ready within {}s".format(timeout))
        warn("Check status: kubectl get pods -n {}".format(namespace))

    info("Pod status:")
    run(["kubectl", "get", "pods", "-n", namespace,
         "-l", "app.kubernetes.io/instance=icdev"])


# ---------------------------------------------------------------------------
# Step 9: Health check and summary
# ---------------------------------------------------------------------------
def health_check_and_summary(namespace):
    """Run health check and print installation summary."""
    step("Step 9/9: Running health check")

    # Get gateway pod name
    gateway_pod = ""
    try:
        result = run(
            [
                "kubectl", "get", "pods", "-n", namespace,
                "-l", "app.kubernetes.io/component=api-gateway",
                "-o", "jsonpath={.items[0].metadata.name}",
            ],
            capture=True,
            check=False,
        )
        if result and result.returncode == 0 and result.stdout:
            gateway_pod = result.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        pass

    if gateway_pod:
        try:
            result = run(
                [
                    "kubectl", "exec", gateway_pod, "-n", namespace, "--",
                    "wget", "-qO-", "--no-check-certificate",
                    "https://localhost:8443/healthz",
                ],
                capture=True,
                check=False,
            )
            health = (result.stdout.decode("utf-8", errors="replace").strip()
                      if result and result.stdout else "unreachable")
            info("API Gateway health: {}".format(health))
        except Exception:
            warn("API Gateway health check failed")
    else:
        warn("API Gateway pod not found -- manual health check required")

    # Summary
    print()
    print(_cyan("================================================================"))
    print(_green("  ICDEV installation complete!"))
    print(_cyan("================================================================"))
    print()
    print("  Namespace    : {}".format(namespace))
    print("  Helm release : icdev")
    print()
    print("  Access URL   : https://icdev.local (configure DNS/ingress)")
    print()
    print("  Useful commands:")
    print("    kubectl get pods -n {}".format(namespace))
    print("    kubectl logs -n {} -l app.kubernetes.io/component=api-gateway"
          .format(namespace))
    print("    helm status icdev -n {}".format(namespace))
    print("    helm upgrade icdev {} -n {} -f values.yaml"
          .format(HELM_CHART_DIR, namespace))
    print()
    print(_cyan("  CUI // SP-CTI"))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV On-Premises Air-Gapped Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python install.py --license license.json\n"
            "  python install.py --namespace icdev-prod --values prod-values.yaml "
            "--license license.json\n"
            "  python install.py --skip-images --license license.json\n"
            "\nCUI // SP-CTI"
        ),
    )
    parser.add_argument(
        "--namespace", default="icdev",
        help="Kubernetes namespace (default: icdev)")
    parser.add_argument(
        "--values", default="",
        help="Custom values.yaml override")
    parser.add_argument(
        "--license", default="", dest="license_file",
        help="Path to license.json")
    parser.add_argument(
        "--images", default="icdev-images.tar.gz",
        help="Path to images tarball (default: icdev-images.tar.gz)")
    parser.add_argument(
        "--tls-cert", default="",
        help="Path to TLS certificate PEM")
    parser.add_argument(
        "--tls-key", default="",
        help="Path to TLS private key PEM")
    parser.add_argument(
        "--db-password", default="",
        help="Database password (auto-generated if empty)")
    parser.add_argument(
        "--skip-images", action="store_true",
        help="Skip image loading (already in registry)")
    parser.add_argument(
        "--timeout", type=int, default=600,
        help="Pod readiness timeout in seconds (default: 600)")

    args = parser.parse_args()

    banner()

    # Step 1: Prerequisites
    container_cli = check_prerequisites(args.skip_images)

    # Step 2: Load images
    load_images(args.skip_images, container_cli, args.images)

    # Step 3: Create namespace
    create_namespace(args.namespace)

    # Step 4: License secret
    create_license_secret(args.namespace, args.license_file)

    # Step 5: TLS
    configure_tls(args.namespace, args.tls_cert, args.tls_key)

    # Step 6: DB credentials
    create_db_credentials(args.namespace, args.db_password)

    # Step 7: Helm install
    helm_install(args.namespace, args.values, args.timeout)

    # Step 8: Wait for pods
    wait_for_pods(args.namespace, args.timeout)

    # Step 9: Health check and summary
    health_check_and_summary(args.namespace)


if __name__ == "__main__":
    main()
