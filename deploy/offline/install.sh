#!/bin/bash
# CUI // SP-CTI
# ICDEV On-Premises Air-Gapped Installer
# Usage: ./install.sh [--namespace icdev] [--values values.yaml] [--license license.json]
#
# This script installs ICDEV into an air-gapped Kubernetes cluster.
# No internet access required — all images loaded from local tarball.
set -euo pipefail

# -----------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------
NAMESPACE="icdev"
VALUES_FILE=""
LICENSE_FILE=""
IMAGE_TARBALL="icdev-images.tar.gz"
HELM_CHART_DIR="$(cd "$(dirname "$0")/../helm" && pwd)"
TLS_CERT=""
TLS_KEY=""
DB_PASSWORD=""
SKIP_IMAGES=false
TIMEOUT=600

# -----------------------------------------------------------------------
# Colors
# -----------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# -----------------------------------------------------------------------
# Banner
# -----------------------------------------------------------------------
banner() {
    echo ""
    echo -e "${CYAN}================================================================${NC}"
    echo -e "${CYAN}  CUI // SP-CTI${NC}"
    echo -e "${CYAN}  ICDEV On-Premises Installer v21.0.0${NC}"
    echo -e "${CYAN}  Intelligent Coding Development Platform${NC}"
    echo -e "${CYAN}================================================================${NC}"
    echo ""
}

# -----------------------------------------------------------------------
# Logging helpers
# -----------------------------------------------------------------------
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo -e "\n${CYAN}==> $*${NC}"; }

# -----------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --namespace)     NAMESPACE="$2";      shift 2 ;;
        --values)        VALUES_FILE="$2";    shift 2 ;;
        --license)       LICENSE_FILE="$2";   shift 2 ;;
        --images)        IMAGE_TARBALL="$2";  shift 2 ;;
        --tls-cert)      TLS_CERT="$2";       shift 2 ;;
        --tls-key)       TLS_KEY="$2";        shift 2 ;;
        --db-password)   DB_PASSWORD="$2";    shift 2 ;;
        --skip-images)   SKIP_IMAGES=true;    shift   ;;
        --timeout)       TIMEOUT="$2";        shift 2 ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --namespace NAME       Kubernetes namespace (default: icdev)"
            echo "  --values FILE          Custom values.yaml override"
            echo "  --license FILE         Path to license.json"
            echo "  --images FILE          Path to images tarball (default: icdev-images.tar.gz)"
            echo "  --tls-cert FILE        Path to TLS certificate PEM"
            echo "  --tls-key FILE         Path to TLS private key PEM"
            echo "  --db-password PASS     Database password (prompted if empty)"
            echo "  --skip-images          Skip image loading (already in registry)"
            echo "  --timeout SECONDS      Pod readiness timeout (default: 600)"
            echo "  --help                 Show this help message"
            exit 0
            ;;
        *)
            err "Unknown option: $1"
            exit 1
            ;;
    esac
done

# -----------------------------------------------------------------------
# Step 1: Prerequisites
# -----------------------------------------------------------------------
banner

step "Step 1/9: Checking prerequisites"

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        err "$1 is required but not found in PATH"
        return 1
    fi
    info "$1 found: $(command -v "$1")"
}

PREREQ_OK=true
check_cmd kubectl || PREREQ_OK=false
check_cmd helm    || PREREQ_OK=false

# Docker or Podman for image loading
if ! $SKIP_IMAGES; then
    if command -v docker &>/dev/null; then
        CONTAINER_CLI="docker"
        info "Container runtime: docker"
    elif command -v podman &>/dev/null; then
        CONTAINER_CLI="podman"
        info "Container runtime: podman"
    else
        err "docker or podman required for image loading (use --skip-images to bypass)"
        PREREQ_OK=false
    fi
fi

if ! $PREREQ_OK; then
    err "Prerequisites not met. Aborting."
    exit 1
fi

# Verify cluster connectivity
if ! kubectl cluster-info &>/dev/null; then
    err "Cannot connect to Kubernetes cluster. Check your kubeconfig."
    exit 1
fi
info "Kubernetes cluster reachable"

# Verify Helm chart exists
if [[ ! -f "${HELM_CHART_DIR}/Chart.yaml" ]]; then
    err "Helm chart not found at ${HELM_CHART_DIR}/Chart.yaml"
    exit 1
fi
info "Helm chart found: ${HELM_CHART_DIR}"

# -----------------------------------------------------------------------
# Step 2: Load container images
# -----------------------------------------------------------------------
step "Step 2/9: Loading container images"

if $SKIP_IMAGES; then
    info "Skipping image loading (--skip-images)"
else
    if [[ ! -f "$IMAGE_TARBALL" ]]; then
        err "Image tarball not found: $IMAGE_TARBALL"
        err "Build with: docker save icdev/* | gzip > icdev-images.tar.gz"
        exit 1
    fi
    info "Loading images from ${IMAGE_TARBALL} ..."
    $CONTAINER_CLI load < "$IMAGE_TARBALL"
    info "Images loaded successfully"
fi

# -----------------------------------------------------------------------
# Step 3: Create namespace
# -----------------------------------------------------------------------
step "Step 3/9: Creating namespace '${NAMESPACE}'"

if kubectl get namespace "$NAMESPACE" &>/dev/null; then
    info "Namespace '${NAMESPACE}' already exists"
else
    kubectl create namespace "$NAMESPACE"
    kubectl label namespace "$NAMESPACE" \
        classification="${NAMESPACE}" \
        app.kubernetes.io/part-of=icdev \
        --overwrite
    info "Namespace '${NAMESPACE}' created"
fi

# -----------------------------------------------------------------------
# Step 4: Create license secret
# -----------------------------------------------------------------------
step "Step 4/9: Creating license secret"

if [[ -z "$LICENSE_FILE" ]]; then
    warn "No --license provided. Checking for ./license.json ..."
    if [[ -f "./license.json" ]]; then
        LICENSE_FILE="./license.json"
    else
        err "License file not found. Provide with --license /path/to/license.json"
        exit 1
    fi
fi

if [[ ! -f "$LICENSE_FILE" ]]; then
    err "License file not found: $LICENSE_FILE"
    exit 1
fi

kubectl create secret generic icdev-license \
    --from-file=license.json="$LICENSE_FILE" \
    --namespace "$NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -
info "License secret created/updated"

# -----------------------------------------------------------------------
# Step 5: Create TLS secret
# -----------------------------------------------------------------------
step "Step 5/9: Configuring TLS"

if [[ -n "$TLS_CERT" && -n "$TLS_KEY" ]]; then
    if [[ ! -f "$TLS_CERT" ]]; then
        err "TLS certificate not found: $TLS_CERT"
        exit 1
    fi
    if [[ ! -f "$TLS_KEY" ]]; then
        err "TLS key not found: $TLS_KEY"
        exit 1
    fi
    kubectl create secret tls icdev-tls \
        --cert="$TLS_CERT" \
        --key="$TLS_KEY" \
        --namespace "$NAMESPACE" \
        --dry-run=client -o yaml | kubectl apply -f -
    info "TLS secret created/updated"
else
    if kubectl get secret icdev-tls -n "$NAMESPACE" &>/dev/null; then
        info "Existing TLS secret found"
    else
        warn "No TLS cert/key provided. Generating self-signed certificate ..."
        if command -v openssl &>/dev/null; then
            TMPDIR=$(mktemp -d)
            openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
                -keyout "${TMPDIR}/tls.key" \
                -out "${TMPDIR}/tls.crt" \
                -subj "/CN=icdev.local/O=ICDEV" \
                2>/dev/null
            kubectl create secret tls icdev-tls \
                --cert="${TMPDIR}/tls.crt" \
                --key="${TMPDIR}/tls.key" \
                --namespace "$NAMESPACE" \
                --dry-run=client -o yaml | kubectl apply -f -
            rm -rf "$TMPDIR"
            warn "Self-signed TLS cert created. Replace with proper cert for production."
        else
            err "openssl not found. Provide TLS cert/key with --tls-cert and --tls-key"
            exit 1
        fi
    fi
fi

# -----------------------------------------------------------------------
# Step 6: Create DB credentials secret
# -----------------------------------------------------------------------
step "Step 6/9: Creating database credentials"

if kubectl get secret icdev-db-credentials -n "$NAMESPACE" &>/dev/null; then
    info "Existing DB credentials secret found"
else
    if [[ -z "$DB_PASSWORD" ]]; then
        # Generate a random password
        DB_PASSWORD=$(openssl rand -base64 24 2>/dev/null || head -c 24 /dev/urandom | base64)
        info "Generated random database password"
    fi
    kubectl create secret generic icdev-db-credentials \
        --from-literal=password="$DB_PASSWORD" \
        --namespace "$NAMESPACE" \
        --dry-run=client -o yaml | kubectl apply -f -
    info "DB credentials secret created"
fi

# -----------------------------------------------------------------------
# Step 7: Helm install / upgrade
# -----------------------------------------------------------------------
step "Step 7/9: Installing ICDEV via Helm"

HELM_ARGS=(
    install icdev "$HELM_CHART_DIR"
    --namespace "$NAMESPACE"
    --timeout "${TIMEOUT}s"
    --wait
)

if [[ -n "$VALUES_FILE" ]]; then
    if [[ ! -f "$VALUES_FILE" ]]; then
        err "Values file not found: $VALUES_FILE"
        exit 1
    fi
    HELM_ARGS+=(-f "$VALUES_FILE")
fi

# Upgrade if already installed
if helm status icdev -n "$NAMESPACE" &>/dev/null; then
    info "Existing installation detected — upgrading"
    HELM_ARGS[0]="upgrade"
fi

helm "${HELM_ARGS[@]}"
info "Helm install/upgrade complete"

# -----------------------------------------------------------------------
# Step 8: Wait for pods
# -----------------------------------------------------------------------
step "Step 8/9: Waiting for pods to be ready"

kubectl wait --for=condition=ready pod \
    --selector=app.kubernetes.io/instance=icdev \
    --namespace "$NAMESPACE" \
    --timeout="${TIMEOUT}s" || {
        warn "Some pods did not become ready within ${TIMEOUT}s"
        warn "Check status: kubectl get pods -n $NAMESPACE"
    }

info "Pod status:"
kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/instance=icdev

# -----------------------------------------------------------------------
# Step 9: Health check and summary
# -----------------------------------------------------------------------
step "Step 9/9: Running health check"

GATEWAY_POD=$(kubectl get pods -n "$NAMESPACE" \
    -l app.kubernetes.io/component=api-gateway \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [[ -n "$GATEWAY_POD" ]]; then
    HEALTH=$(kubectl exec "$GATEWAY_POD" -n "$NAMESPACE" -- \
        wget -qO- --no-check-certificate https://localhost:8443/healthz 2>/dev/null || echo "unreachable")
    info "API Gateway health: $HEALTH"
else
    warn "API Gateway pod not found — manual health check required"
fi

echo ""
echo -e "${CYAN}================================================================${NC}"
echo -e "${GREEN}  ICDEV installation complete!${NC}"
echo -e "${CYAN}================================================================${NC}"
echo ""
echo "  Namespace    : $NAMESPACE"
echo "  Helm release : icdev"
echo ""
echo "  Access URL   : https://icdev.local (configure DNS/ingress)"
echo ""
echo "  Useful commands:"
echo "    kubectl get pods -n $NAMESPACE"
echo "    kubectl logs -n $NAMESPACE -l app.kubernetes.io/component=api-gateway"
echo "    helm status icdev -n $NAMESPACE"
echo "    helm upgrade icdev ${HELM_CHART_DIR} -n $NAMESPACE -f values.yaml"
echo ""
echo -e "${CYAN}  CUI // SP-CTI${NC}"
echo ""
