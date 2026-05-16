#!/usr/bin/env bash
# CUI // SP-CTI
# ICDEV™ OS-level security hardening script.
# Applies baseline security controls for NIST 800-53 / IL4/IL5 environments.
#
# Usage:
#   sudo ./scripts/harden_environment.sh [--dry-run] [--check]
#
# Flags:
#   --dry-run   Print actions without executing
#   --check     Audit-only: report compliance status, exit 1 if gaps found
#
# Supported platforms: Linux (Debian/RHEL families), macOS (partial)
# Must be run as root on Linux.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_FILE="${PROJECT_ROOT}/.tmp/harden_environment.log"

DRY_RUN=false
CHECK_ONLY=false
PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --dry-run) DRY_RUN=true ;;
        --check)   CHECK_ONLY=true ;;
        *) echo "[harden] Unknown argument: ${arg}" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
OS="$(uname -s)"
mkdir -p "${PROJECT_ROOT}/.tmp"

log() { echo "[harden] $*" | tee -a "${LOG_FILE}"; }
log_pass() { log "PASS: $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
log_fail() { log "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

run_cmd() {
    # run_cmd <description> <command...>
    local desc="$1"; shift
    if "${DRY_RUN}" || "${CHECK_ONLY}"; then
        log "[DRY-RUN] ${desc}: $*"
    else
        log "Applying: ${desc}"
        "$@" >> "${LOG_FILE}" 2>&1 || log "WARNING: '${desc}' returned non-zero (may be expected)"
    fi
}

require_root() {
    if [[ "${OS}" == "Linux" ]] && [[ "$(id -u)" -ne 0 ]]; then
        echo "[harden] ERROR: Must be run as root on Linux." >&2
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Phase 1: Kernel / sysctl hardening (Linux only)
# ---------------------------------------------------------------------------
harden_sysctl() {
    [[ "${OS}" != "Linux" ]] && return
    log "--- Phase 1: sysctl hardening ---"

    local params=(
        # Network: disable IP forwarding (not a router)
        "net.ipv4.ip_forward=0"
        "net.ipv6.conf.all.forwarding=0"
        # Prevent SYN flood attacks
        "net.ipv4.tcp_syncookies=1"
        # Ignore ICMP redirects
        "net.ipv4.conf.all.accept_redirects=0"
        "net.ipv6.conf.all.accept_redirects=0"
        "net.ipv4.conf.all.send_redirects=0"
        # Ignore source-routed packets
        "net.ipv4.conf.all.accept_source_route=0"
        "net.ipv6.conf.all.accept_source_route=0"
        # Log martian packets
        "net.ipv4.conf.all.log_martians=1"
        # Reverse path filtering (prevent IP spoofing)
        "net.ipv4.conf.all.rp_filter=1"
        "net.ipv4.conf.default.rp_filter=1"
        # Disable ICMP broadcast responses
        "net.ipv4.icmp_echo_ignore_broadcasts=1"
        # Protect against ICMP attacks
        "net.ipv4.icmp_ignore_bogus_error_responses=1"
        # ASLR: full randomisation
        "kernel.randomize_va_space=2"
        # Restrict dmesg to root
        "kernel.dmesg_restrict=1"
        # Restrict ptrace (prevents process injection)
        "kernel.yama.ptrace_scope=1"
        # Disable magic SysRq
        "kernel.sysrq=0"
        # Restrict core dumps
        "fs.suid_dumpable=0"
    )

    for param in "${params[@]}"; do
        local key="${param%%=*}"
        local expected="${param##*=}"
        local current
        current="$(sysctl -n "${key}" 2>/dev/null || echo "N/A")"
        if [[ "${current}" == "${expected}" ]]; then
            log_pass "sysctl ${key}=${expected}"
        else
            log_fail "sysctl ${key} (current=${current}, expected=${expected})"
            run_cmd "sysctl ${key}=${expected}" sysctl -w "${param}"
        fi
    done

    # Persist via drop-in file
    local conf="/etc/sysctl.d/99-icdev-hardening.conf"
    if [[ ! -f "${conf}" ]]; then
        run_cmd "Write sysctl drop-in ${conf}" bash -c "printf '%s\n' \
'# ICDEV™ security hardening — do not edit manually' \
'net.ipv4.ip_forward = 0' \
'net.ipv4.tcp_syncookies = 1' \
'net.ipv4.conf.all.accept_redirects = 0' \
'net.ipv4.conf.all.send_redirects = 0' \
'net.ipv4.conf.all.accept_source_route = 0' \
'net.ipv4.conf.all.log_martians = 1' \
'net.ipv4.conf.all.rp_filter = 1' \
'net.ipv4.icmp_echo_ignore_broadcasts = 1' \
'kernel.randomize_va_space = 2' \
'kernel.dmesg_restrict = 1' \
'kernel.yama.ptrace_scope = 1' \
'kernel.sysrq = 0' \
'fs.suid_dumpable = 0' \
> ${conf}"
    fi
}

# ---------------------------------------------------------------------------
# Phase 2: Disable unnecessary services (Linux systemd)
# ---------------------------------------------------------------------------
harden_services() {
    [[ "${OS}" != "Linux" ]] && return
    command -v systemctl >/dev/null 2>&1 || return
    log "--- Phase 2: Disable unnecessary services ---"

    local services=(
        avahi-daemon    # mDNS/DNS-SD — not needed in server environments
        cups            # printing daemon
        bluetooth       # Bluetooth stack
        rpcbind         # NFS portmapper — disable unless NFS required
        nfs-server      # NFS server
        telnet          # plaintext remote access
        rsh-server      # legacy remote shell
        xinetd          # legacy inet superdaemon
        tftp            # trivial FTP (unauthenticated)
        snmpd           # SNMP — disable unless monitored
    )

    for svc in "${services[@]}"; do
        if systemctl is-enabled "${svc}" 2>/dev/null | grep -q "enabled"; then
            log_fail "Service enabled: ${svc}"
            run_cmd "Disable service ${svc}" systemctl disable --now "${svc}"
        else
            log_pass "Service not enabled: ${svc}"
        fi
    done
}

# ---------------------------------------------------------------------------
# Phase 3: SSH daemon hardening
# ---------------------------------------------------------------------------
harden_ssh() {
    local sshd_config="/etc/ssh/sshd_config"
    [[ ! -f "${sshd_config}" ]] && log "SSH not present — skipping" && return
    log "--- Phase 3: SSH hardening ---"

    declare -A ssh_params=(
        [PermitRootLogin]="no"
        [PasswordAuthentication]="no"
        [PermitEmptyPasswords]="no"
        [X11Forwarding]="no"
        [MaxAuthTries]="3"
        [LoginGraceTime]="60"
        [ClientAliveInterval]="300"
        [ClientAliveCountMax]="2"
        [UseDNS]="no"
        [Protocol]="2"
        [PubkeyAuthentication]="yes"
        [AuthorizedKeysFile]=".ssh/authorized_keys"
        [AllowAgentForwarding]="no"
        [AllowTcpForwarding]="no"
        [PrintMotd]="no"
        [Banner]="/etc/issue.net"
    )

    for key in "${!ssh_params[@]}"; do
        local expected="${ssh_params[$key]}"
        local current
        current="$(grep -i "^${key}" "${sshd_config}" | awk '{print $2}' | head -1 || true)"
        if [[ "${current}" == "${expected}" ]]; then
            log_pass "sshd ${key}=${expected}"
        else
            log_fail "sshd ${key} (current='${current}', expected='${expected}')"
            if ! "${DRY_RUN}" && ! "${CHECK_ONLY}"; then
                # Remove existing setting and append corrected value
                sed -i "/^#*\s*${key}\s/d" "${sshd_config}" >> "${LOG_FILE}" 2>&1 || true
                echo "${key} ${expected}" >> "${sshd_config}"
                log "Updated sshd_config: ${key} ${expected}"
            fi
        fi
    done

    # Drop-in banner
    local banner_file="/etc/issue.net"
    local banner_text="NOTICE: This system is for authorized use only. All activity is monitored and recorded."
    if [[ "$(cat "${banner_file}" 2>/dev/null || true)" != "${banner_text}" ]]; then
        log_fail "SSH banner missing or incorrect"
        run_cmd "Write SSH banner" bash -c "echo '${banner_text}' > ${banner_file}"
    else
        log_pass "SSH banner set"
    fi

    # Restart sshd only if changes were applied and not dry-run
    if ! "${DRY_RUN}" && ! "${CHECK_ONLY}" && [[ "${FAIL_COUNT}" -gt 0 ]] && command -v systemctl >/dev/null 2>&1; then
        run_cmd "Reload sshd" systemctl reload-or-restart sshd || true
    fi
}

# ---------------------------------------------------------------------------
# Phase 4: File system permissions
# ---------------------------------------------------------------------------
harden_fs_permissions() {
    log "--- Phase 4: File system permissions ---"

    # Project .env must not be world-readable
    local env_file="${PROJECT_ROOT}/.env"
    if [[ -f "${env_file}" ]]; then
        local perms
        perms="$(stat -c '%a' "${env_file}" 2>/dev/null || stat -f '%A' "${env_file}" 2>/dev/null || echo "unknown")"
        if [[ "${perms}" == "600" || "${perms}" == "640" ]]; then
            log_pass ".env permissions: ${perms}"
        else
            log_fail ".env permissions too open: ${perms} (expected 600)"
            run_cmd "Restrict .env permissions" chmod 600 "${env_file}"
        fi
    fi

    # data/ directory — restrict to owner only
    local data_dir="${PROJECT_ROOT}/data"
    if [[ -d "${data_dir}" ]]; then
        local dperms
        dperms="$(stat -c '%a' "${data_dir}" 2>/dev/null || stat -f '%A' "${data_dir}" 2>/dev/null || echo "unknown")"
        if [[ "${dperms}" == "700" || "${dperms}" == "750" ]]; then
            log_pass "data/ permissions: ${dperms}"
        else
            log_fail "data/ permissions too open: ${dperms} (expected 700)"
            run_cmd "Restrict data/ permissions" chmod 700 "${data_dir}"
        fi
    fi

    # Linux-specific: world-writable directories check
    if [[ "${OS}" == "Linux" ]]; then
        local ww_dirs
        ww_dirs="$(find "${PROJECT_ROOT}" -not -path '*/.git/*' -not -path '*/.tmp/*' \
            -not -path '*/node_modules/*' -type d -perm -0002 2>/dev/null || true)"
        if [[ -z "${ww_dirs}" ]]; then
            log_pass "No world-writable directories found under project root"
        else
            log_fail "World-writable directories found:"
            echo "${ww_dirs}" | while read -r d; do
                log "  ${d}"
                run_cmd "Remove world-write on ${d}" chmod o-w "${d}"
            done
        fi
    fi
}

# ---------------------------------------------------------------------------
# Phase 5: Umask enforcement
# ---------------------------------------------------------------------------
harden_umask() {
    log "--- Phase 5: umask enforcement ---"

    # Enforce umask 027 in /etc/profile.d/ (Linux)
    if [[ "${OS}" == "Linux" ]]; then
        local umask_file="/etc/profile.d/icdev-umask.sh"
        if [[ -f "${umask_file}" ]] && grep -q "umask 027" "${umask_file}"; then
            log_pass "umask 027 profile.d entry present"
        else
            log_fail "umask 027 not enforced via /etc/profile.d"
            run_cmd "Write umask profile.d" bash -c "echo 'umask 027' > ${umask_file} && chmod 644 ${umask_file}"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Phase 6: Password / PAM policy (Linux only)
# ---------------------------------------------------------------------------
harden_password_policy() {
    [[ "${OS}" != "Linux" ]] && return
    log "--- Phase 6: Password policy ---"

    local login_defs="/etc/login.defs"
    if [[ -f "${login_defs}" ]]; then
        declare -A pw_params=(
            [PASS_MAX_DAYS]="90"
            [PASS_MIN_DAYS]="1"
            [PASS_WARN_AGE]="14"
        )
        for key in "${!pw_params[@]}"; do
            local expected="${pw_params[$key]}"
            local current
            current="$(grep "^${key}" "${login_defs}" | awk '{print $2}' | head -1 || true)"
            if [[ "${current}" == "${expected}" ]]; then
                log_pass "login.defs ${key}=${expected}"
            else
                log_fail "login.defs ${key} (current='${current}', expected='${expected}')"
                run_cmd "Set login.defs ${key}" sed -i "s/^${key}.*/${key}\t${expected}/" "${login_defs}"
            fi
        done
    fi

    # Require libpwquality / pam_cracklib
    if command -v pam-auth-update >/dev/null 2>&1; then
        if dpkg -l libpam-pwquality 2>/dev/null | grep -q "^ii"; then
            log_pass "libpam-pwquality installed"
        else
            log_fail "libpam-pwquality not installed"
            run_cmd "Install libpam-pwquality" apt-get install -y -q libpam-pwquality
        fi
    fi
}

# ---------------------------------------------------------------------------
# Phase 7: Firewall baseline (Linux)
# ---------------------------------------------------------------------------
harden_firewall() {
    [[ "${OS}" != "Linux" ]] && return
    log "--- Phase 7: Firewall baseline ---"

    if command -v ufw >/dev/null 2>&1; then
        local ufw_status
        ufw_status="$(ufw status 2>/dev/null | head -1 || true)"
        if echo "${ufw_status}" | grep -qi "active"; then
            log_pass "UFW active"
        else
            log_fail "UFW inactive"
            run_cmd "Enable UFW default-deny" bash -c "ufw default deny incoming && ufw default allow outgoing && ufw --force enable"
        fi
    elif command -v firewall-cmd >/dev/null 2>&1; then
        if firewall-cmd --state 2>/dev/null | grep -qi "running"; then
            log_pass "firewalld running"
        else
            log_fail "firewalld not running"
            run_cmd "Start firewalld" systemctl enable --now firewalld
        fi
    else
        log "WARNING: No supported firewall (ufw/firewalld) found — manual review required"
    fi
}

# ---------------------------------------------------------------------------
# Phase 8: Audit daemon (auditd)
# ---------------------------------------------------------------------------
harden_auditd() {
    [[ "${OS}" != "Linux" ]] && return
    command -v auditctl >/dev/null 2>&1 || { log "auditd not installed — skipping"; return; }
    log "--- Phase 8: auditd rules ---"

    local rules_file="/etc/audit/rules.d/icdev.rules"
    if [[ -f "${rules_file}" ]]; then
        log_pass "auditd rules file present: ${rules_file}"
    else
        log_fail "auditd rules file missing: ${rules_file}"
        run_cmd "Write auditd rules" bash -c "cat > ${rules_file} <<'EOF'
# ICDEV™ audit rules — NIST AU-2 / AU-12
-w /etc/passwd -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/sudoers -p wa -k sudoers
-w /var/log/auth.log -p wa -k auth
-a always,exit -F arch=b64 -S execve -k exec
-a always,exit -F arch=b64 -S open,openat -F exit=-EACCES -k access_denied
-a always,exit -F arch=b64 -S open,openat -F exit=-EPERM -k access_denied
-e 2
EOF"
        run_cmd "Reload auditd rules" augenrules --load || true
    fi
}

# ---------------------------------------------------------------------------
# Phase 9: Core dump restriction
# ---------------------------------------------------------------------------
harden_core_dumps() {
    log "--- Phase 9: Core dump restriction ---"

    if [[ "${OS}" == "Linux" ]]; then
        local limits_file="/etc/security/limits.d/icdev-coredump.conf"
        if [[ -f "${limits_file}" ]] && grep -q "hard core 0" "${limits_file}"; then
            log_pass "Core dumps disabled via limits.d"
        else
            log_fail "Core dump restriction not configured"
            run_cmd "Disable core dumps via limits.d" bash -c "echo '* hard core 0' > ${limits_file}"
        fi

        local systemd_coredump="/etc/systemd/coredump.conf.d/icdev.conf"
        if [[ -f "${systemd_coredump}" ]] && grep -q "Storage=none" "${systemd_coredump}"; then
            log_pass "systemd coredump Storage=none"
        else
            log_fail "systemd coredump not restricted"
            run_cmd "Disable systemd coredump storage" bash -c "mkdir -p /etc/systemd/coredump.conf.d && printf '[Coredump]\nStorage=none\nProcessSizeMax=0\n' > ${systemd_coredump}"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "========================================"
    log "ICDEV™ Environment Hardening Script"
    log "Date     : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    log "Host     : $(hostname)"
    log "OS       : ${OS} $(uname -r 2>/dev/null || true)"
    log "Dry-run  : ${DRY_RUN}"
    log "Check    : ${CHECK_ONLY}"
    log "========================================"

    [[ "${OS}" == "Linux" ]] && ! "${DRY_RUN}" && ! "${CHECK_ONLY}" && require_root

    harden_sysctl
    harden_services
    harden_ssh
    harden_fs_permissions
    harden_umask
    harden_password_policy
    harden_firewall
    harden_auditd
    harden_core_dumps

    log "========================================"
    log "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
    log "Log    : ${LOG_FILE}"
    log "========================================"

    if "${CHECK_ONLY}" && [[ "${FAIL_COUNT}" -gt 0 ]]; then
        log "AUDIT FAILED — ${FAIL_COUNT} control(s) out of compliance"
        exit 1
    fi

    log "Hardening complete."
}

main "$@"
