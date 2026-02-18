#!/usr/bin/env python3
"""Generate Ansible playbooks for STIG hardening, application deployment,
and monitoring configuration. All generated files include CUI headers."""

import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

CUI_HEADER = (
    "# //CUI\n"
    "# CONTROLLED UNCLASSIFIED INFORMATION\n"
    "# Authorized for: Internal project use only\n"
    "# Generated: {timestamp}\n"
    "# Generator: ICDev Ansible Generator\n"
    "# //CUI\n"
)


def _cui_header() -> str:
    return CUI_HEADER.format(timestamp=datetime.utcnow().isoformat())


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# STIG Hardening Playbook
# ---------------------------------------------------------------------------
def generate_hardening(project_path: str) -> list:
    """Generate STIG hardening playbook: security updates, audit logging,
    disable unnecessary services, set file permissions."""
    ansible_dir = Path(project_path) / "ansible"
    files = []

    hardening_playbook = f"""{_cui_header()}
---
- name: STIG Hardening Playbook
  hosts: all
  become: true
  gather_facts: true

  vars:
    stig_classification: "CUI"
    audit_log_retention_days: 365
    unnecessary_services:
      - telnet
      - rsh
      - rlogin
      - rexec
      - tftp
      - xinetd
      - avahi-daemon
      - cups
      - nfs
      - rpcbind
    secure_file_permissions:
      - path: /etc/passwd
        mode: "0644"
        owner: root
        group: root
      - path: /etc/shadow
        mode: "0000"
        owner: root
        group: root
      - path: /etc/gshadow
        mode: "0000"
        owner: root
        group: root
      - path: /etc/group
        mode: "0644"
        owner: root
        group: root
      - path: /boot/grub2/grub.cfg
        mode: "0600"
        owner: root
        group: root
      - path: /etc/ssh/sshd_config
        mode: "0600"
        owner: root
        group: root

  tasks:
    # --- Security Updates ---
    - name: Install security updates (RHEL/CentOS)
      yum:
        name: "*"
        state: latest
        security: true
      when: ansible_os_family == "RedHat"
      tags: [updates, stig]

    - name: Install security updates (Ubuntu/Debian)
      apt:
        upgrade: safe
        update_cache: true
        cache_valid_time: 3600
      when: ansible_os_family == "Debian"
      tags: [updates, stig]

    - name: Install AIDE for file integrity monitoring
      package:
        name: aide
        state: present
      tags: [integrity, stig]

    - name: Initialize AIDE database
      command: aide --init
      args:
        creates: /var/lib/aide/aide.db.new.gz
      tags: [integrity, stig]

    # --- Audit Logging (NIST AU controls) ---
    - name: Install auditd
      package:
        name:
          - audit
          - audit-libs
        state: present
      tags: [audit, stig]

    - name: Configure auditd
      copy:
        dest: /etc/audit/auditd.conf
        mode: "0640"
        owner: root
        group: root
        content: |
          log_file = /var/log/audit/audit.log
          log_format = ENRICHED
          log_group = root
          priority_boost = 4
          flush = INCREMENTAL_ASYNC
          freq = 50
          max_log_file = 50
          num_logs = 10
          max_log_file_action = ROTATE
          space_left = 75
          space_left_action = email
          action_mail_acct = root
          admin_space_left = 50
          admin_space_left_action = single
          disk_full_action = halt
          disk_error_action = halt
          tcp_listen_queue = 5
          tcp_max_per_addr = 1
          tcp_client_max_idle = 0
      notify: restart auditd
      tags: [audit, stig]

    - name: Configure audit rules (logins, file access, privilege escalation)
      copy:
        dest: /etc/audit/rules.d/stig.rules
        mode: "0640"
        owner: root
        group: root
        content: |
          # STIG Audit Rules
          # Monitor login/logout events
          -w /var/log/lastlog -p wa -k logins
          -w /var/run/faillock/ -p wa -k logins
          -w /var/log/tallylog -p wa -k logins

          # Monitor user/group changes
          -w /etc/passwd -p wa -k identity
          -w /etc/group -p wa -k identity
          -w /etc/shadow -p wa -k identity
          -w /etc/gshadow -p wa -k identity
          -w /etc/security/opasswd -p wa -k identity

          # Monitor privilege escalation
          -a always,exit -F arch=b64 -S execve -C uid!=euid -F euid=0 -k privilege_escalation
          -a always,exit -F arch=b32 -S execve -C uid!=euid -F euid=0 -k privilege_escalation

          # Monitor sudo usage
          -w /etc/sudoers -p wa -k sudoers
          -w /etc/sudoers.d/ -p wa -k sudoers

          # Monitor file deletions
          -a always,exit -F arch=b64 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=4294967295 -k delete
          -a always,exit -F arch=b32 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=4294967295 -k delete

          # Monitor kernel module loading
          -w /sbin/insmod -p x -k modules
          -w /sbin/rmmod -p x -k modules
          -w /sbin/modprobe -p x -k modules
          -a always,exit -F arch=b64 -S init_module,finit_module -k modules
          -a always,exit -F arch=b64 -S delete_module -k modules

          # Make audit configuration immutable (must be last rule)
          -e 2
      notify: restart auditd
      tags: [audit, stig]

    - name: Enable and start auditd
      service:
        name: auditd
        state: started
        enabled: true
      tags: [audit, stig]

    # --- Disable Unnecessary Services ---
    - name: Disable unnecessary services
      service:
        name: "{{{{ item }}}}"
        state: stopped
        enabled: false
      loop: "{{{{ unnecessary_services }}}}"
      failed_when: false
      tags: [services, stig]

    - name: Remove unnecessary packages
      package:
        name:
          - telnet
          - rsh
          - rsh-server
          - tftp
          - tftp-server
          - xinetd
        state: absent
      tags: [services, stig]

    # --- Set File Permissions ---
    - name: Set secure file permissions
      file:
        path: "{{{{ item.path }}}}"
        mode: "{{{{ item.mode }}}}"
        owner: "{{{{ item.owner }}}}"
        group: "{{{{ item.group }}}}"
      loop: "{{{{ secure_file_permissions }}}}"
      when: item.path is exists
      tags: [permissions, stig]

    # --- SSH Hardening ---
    - name: Harden SSH configuration
      lineinfile:
        path: /etc/ssh/sshd_config
        regexp: "^{{{{ item.key }}}}"
        line: "{{{{ item.key }}}} {{{{ item.value }}}}"
        state: present
        validate: "sshd -t -f %s"
      loop:
        - {{ key: "Protocol", value: "2" }}
        - {{ key: "PermitRootLogin", value: "no" }}
        - {{ key: "PasswordAuthentication", value: "no" }}
        - {{ key: "PermitEmptyPasswords", value: "no" }}
        - {{ key: "X11Forwarding", value: "no" }}
        - {{ key: "MaxAuthTries", value: "3" }}
        - {{ key: "ClientAliveInterval", value: "300" }}
        - {{ key: "ClientAliveCountMax", value: "0" }}
        - {{ key: "LoginGraceTime", value: "60" }}
        - {{ key: "Banner", value: "/etc/issue.net" }}
        - {{ key: "Ciphers", value: "aes256-ctr,aes192-ctr,aes128-ctr" }}
        - {{ key: "MACs", value: "hmac-sha2-512,hmac-sha2-256" }}
      notify: restart sshd
      tags: [ssh, stig]

    - name: Set login warning banner
      copy:
        dest: /etc/issue.net
        mode: "0644"
        content: |
          *****WARNING*****
          This system is for authorized use only. All activity is monitored
          and recorded. Unauthorized access is prohibited and will be
          prosecuted to the fullest extent of the law.
          This system processes Controlled Unclassified Information (CUI).
      tags: [ssh, stig]

    # --- Kernel Hardening ---
    - name: Apply kernel hardening parameters
      sysctl:
        name: "{{{{ item.key }}}}"
        value: "{{{{ item.value }}}}"
        state: present
        sysctl_set: true
        reload: true
      loop:
        - {{ key: "net.ipv4.ip_forward", value: "0" }}
        - {{ key: "net.ipv4.conf.all.send_redirects", value: "0" }}
        - {{ key: "net.ipv4.conf.default.send_redirects", value: "0" }}
        - {{ key: "net.ipv4.conf.all.accept_redirects", value: "0" }}
        - {{ key: "net.ipv4.conf.default.accept_redirects", value: "0" }}
        - {{ key: "net.ipv4.conf.all.log_martians", value: "1" }}
        - {{ key: "net.ipv4.icmp_echo_ignore_broadcasts", value: "1" }}
        - {{ key: "net.ipv4.tcp_syncookies", value: "1" }}
        - {{ key: "kernel.randomize_va_space", value: "2" }}
        - {{ key: "fs.suid_dumpable", value: "0" }}
      tags: [kernel, stig]

  handlers:
    - name: restart auditd
      service:
        name: auditd
        state: restarted

    - name: restart sshd
      service:
        name: sshd
        state: restarted
"""
    p = _write(ansible_dir / "playbooks" / "hardening.yml", hardening_playbook)
    files.append(str(p))

    # Inventory template
    inventory = f"""{_cui_header()}
---
all:
  vars:
    ansible_python_interpreter: /usr/bin/python3
    ansible_ssh_common_args: '-o StrictHostKeyChecking=accept-new'
  children:
    application_servers:
      hosts:
        app-01:
          ansible_host: 10.0.1.10
        app-02:
          ansible_host: 10.0.1.11
    database_servers:
      hosts:
        db-01:
          ansible_host: 10.0.2.10
"""
    p = _write(ansible_dir / "inventory" / "hosts.yml", inventory)
    files.append(str(p))

    return files


# ---------------------------------------------------------------------------
# Application Deployment Playbook
# ---------------------------------------------------------------------------
def generate_deploy(project_path: str, app_config: dict = None) -> list:
    """Generate application deployment playbook."""
    config = app_config or {}
    app_name = config.get("app_name", "icdev-app")
    app_port = config.get("port", 8080)
    image = config.get("image", "registry.example.com/app:latest")

    ansible_dir = Path(project_path) / "ansible"
    files = []

    deploy_playbook = f"""{_cui_header()}
---
- name: Deploy Application
  hosts: application_servers
  become: true
  serial: 1
  max_fail_percentage: 0

  vars:
    app_name: "{app_name}"
    app_port: {app_port}
    app_image: "{image}"
    app_user: "appuser"
    app_group: "appgroup"
    app_dir: "/opt/{{{{ app_name }}}}"
    health_check_url: "http://localhost:{{{{ app_port }}}}/health"
    health_check_retries: 30
    health_check_delay: 5
    rollback_on_failure: true
    classification: "CUI"

  pre_tasks:
    - name: Record deployment start
      debug:
        msg: "Starting deployment of {{{{ app_image }}}} to {{{{ inventory_hostname }}}}"

    - name: Drain node from load balancer
      uri:
        url: "http://localhost:8500/v1/agent/service/deregister/{{{{ app_name }}}}"
        method: PUT
      failed_when: false
      tags: [deploy]

    - name: Wait for connections to drain
      pause:
        seconds: 30
      tags: [deploy]

  tasks:
    - name: Create application user
      user:
        name: "{{{{ app_user }}}}"
        system: true
        shell: /usr/sbin/nologin
        create_home: false
      tags: [setup]

    - name: Create application directory
      file:
        path: "{{{{ app_dir }}}}"
        state: directory
        owner: "{{{{ app_user }}}}"
        group: "{{{{ app_group }}}}"
        mode: "0750"
      tags: [setup]

    - name: Pull container image
      docker_image:
        name: "{{{{ app_image }}}}"
        source: pull
        force_source: true
      register: pull_result
      tags: [deploy]

    - name: Stop existing container
      docker_container:
        name: "{{{{ app_name }}}}"
        state: stopped
      failed_when: false
      tags: [deploy]

    - name: Record previous image for rollback
      shell: "docker inspect --format='{{{{{{{{ .Config.Image }}}}}}}}' {{{{ app_name }}}} 2>/dev/null || echo 'none'"
      register: previous_image
      changed_when: false
      tags: [deploy]

    - name: Start application container
      docker_container:
        name: "{{{{ app_name }}}}"
        image: "{{{{ app_image }}}}"
        state: started
        restart_policy: unless-stopped
        published_ports:
          - "{{{{ app_port }}}}:{{{{ app_port }}}}"
        env:
          APP_ENV: "{{{{ lookup('env', 'DEPLOY_ENV') | default('production', true) }}}}"
          LOG_LEVEL: "info"
          CLASSIFICATION: "{{{{ classification }}}}"
        user: "1000:1000"
        read_only: true
        tmpfs:
          - /tmp:size=100M
        security_opts:
          - no-new-privileges:true
        memory: "512m"
        cpu_quota: 100000
        log_driver: json-file
        log_options:
          max-size: "50m"
          max-file: "5"
        labels:
          classification: "CUI"
          managed_by: "ansible"
      register: container_result
      tags: [deploy]

    - name: Wait for health check to pass
      uri:
        url: "{{{{ health_check_url }}}}"
        status_code: 200
        timeout: 5
      register: health_result
      retries: "{{{{ health_check_retries }}}}"
      delay: "{{{{ health_check_delay }}}}"
      until: health_result.status == 200
      tags: [deploy]

  rescue:
    - name: Deployment failed — rolling back
      debug:
        msg: "Deployment failed. Rolling back to {{{{ previous_image.stdout }}}}"

    - name: Rollback to previous image
      docker_container:
        name: "{{{{ app_name }}}}"
        image: "{{{{ previous_image.stdout }}}}"
        state: started
        restart_policy: unless-stopped
        published_ports:
          - "{{{{ app_port }}}}:{{{{ app_port }}}}"
        user: "1000:1000"
        read_only: true
        security_opts:
          - no-new-privileges:true
      when:
        - rollback_on_failure
        - previous_image.stdout != 'none'

    - name: Verify rollback health
      uri:
        url: "{{{{ health_check_url }}}}"
        status_code: 200
        timeout: 5
      retries: 10
      delay: 5
      when: previous_image.stdout != 'none'

    - name: Fail the play after rollback
      fail:
        msg: "Deployment failed and was rolled back to {{{{ previous_image.stdout }}}}"

  post_tasks:
    - name: Re-register with load balancer
      uri:
        url: "http://localhost:8500/v1/agent/service/register"
        method: PUT
        body_format: json
        body:
          ID: "{{{{ app_name }}}}"
          Name: "{{{{ app_name }}}}"
          Port: "{{{{ app_port }}}}"
          Check:
            HTTP: "{{{{ health_check_url }}}}"
            Interval: "10s"
      failed_when: false
      tags: [deploy]

    - name: Log deployment success
      debug:
        msg: "Successfully deployed {{{{ app_image }}}} to {{{{ inventory_hostname }}}}"
      tags: [deploy]
"""
    p = _write(ansible_dir / "playbooks" / "deploy.yml", deploy_playbook)
    files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Monitoring Playbook
# ---------------------------------------------------------------------------
def generate_monitoring(project_path: str) -> list:
    """Generate monitoring playbook: Prometheus exporters + FluentBit."""
    ansible_dir = Path(project_path) / "ansible"
    files = []

    monitoring_playbook = f"""{_cui_header()}
---
- name: Configure Monitoring (Prometheus Exporters + FluentBit)
  hosts: all
  become: true
  gather_facts: true

  vars:
    node_exporter_version: "1.7.0"
    fluentbit_version: "2.2"
    prometheus_port: 9090
    node_exporter_port: 9100
    fluentbit_forward_host: "fluentbit-aggregator.monitoring.svc"
    fluentbit_forward_port: 24224
    log_classification: "CUI"

  tasks:
    # --- Prometheus Node Exporter ---
    - name: Create node_exporter user
      user:
        name: node_exporter
        system: true
        shell: /usr/sbin/nologin
        create_home: false
      tags: [prometheus]

    - name: Download node_exporter
      get_url:
        url: "https://github.com/prometheus/node_exporter/releases/download/v{{{{ node_exporter_version }}}}/node_exporter-{{{{ node_exporter_version }}}}.linux-amd64.tar.gz"
        dest: /tmp/node_exporter.tar.gz
        mode: "0644"
      tags: [prometheus]

    - name: Extract node_exporter
      unarchive:
        src: /tmp/node_exporter.tar.gz
        dest: /usr/local/bin/
        remote_src: true
        extra_opts:
          - --strip-components=1
        creates: /usr/local/bin/node_exporter
      tags: [prometheus]

    - name: Create node_exporter systemd service
      copy:
        dest: /etc/systemd/system/node_exporter.service
        mode: "0644"
        content: |
          [Unit]
          Description=Prometheus Node Exporter
          After=network-online.target
          Wants=network-online.target

          [Service]
          User=node_exporter
          Group=node_exporter
          Type=simple
          ExecStart=/usr/local/bin/node_exporter \\
            --collector.systemd \\
            --collector.processes \\
            --web.listen-address=:{{{{ node_exporter_port }}}}
          Restart=on-failure
          RestartSec=5
          NoNewPrivileges=true
          ProtectSystem=strict
          ProtectHome=true
          ReadOnlyPaths=/

          [Install]
          WantedBy=multi-user.target
      notify: restart node_exporter
      tags: [prometheus]

    - name: Enable and start node_exporter
      systemd:
        name: node_exporter
        state: started
        enabled: true
        daemon_reload: true
      tags: [prometheus]

    # --- Application Metrics Exporter (if app exposes /metrics) ---
    - name: Create Prometheus scrape config snippet
      copy:
        dest: /etc/prometheus/targets/app.json
        mode: "0644"
        content: |
          [
            {{
              "targets": ["localhost:8080"],
              "labels": {{
                "job": "application",
                "classification": "{{{{ log_classification }}}}"
              }}
            }}
          ]
      tags: [prometheus]

    # --- FluentBit ---
    - name: Add FluentBit repository key (RHEL/CentOS)
      rpm_key:
        key: https://packages.fluentbit.io/fluentbit.key
        state: present
      when: ansible_os_family == "RedHat"
      tags: [fluentbit]

    - name: Add FluentBit repository (RHEL/CentOS)
      yum_repository:
        name: fluent-bit
        description: "Fluent Bit"
        baseurl: "https://packages.fluentbit.io/centos/$releasever/$basearch/"
        gpgcheck: true
        gpgkey: https://packages.fluentbit.io/fluentbit.key
        enabled: true
      when: ansible_os_family == "RedHat"
      tags: [fluentbit]

    - name: Install FluentBit
      package:
        name: fluent-bit
        state: present
      tags: [fluentbit]

    - name: Configure FluentBit
      copy:
        dest: /etc/fluent-bit/fluent-bit.conf
        mode: "0640"
        owner: root
        group: root
        content: |
          # //CUI - Controlled Unclassified Information
          [SERVICE]
              Flush        5
              Daemon       Off
              Log_Level    info
              Parsers_File parsers.conf
              HTTP_Server  On
              HTTP_Listen  0.0.0.0
              HTTP_Port    2020

          # Collect system logs
          [INPUT]
              Name         systemd
              Tag          system.*
              Read_From_Tail On

          # Collect application logs
          [INPUT]
              Name         tail
              Tag          app.*
              Path         /var/log/app/*.log
              Parser       json
              Refresh_Interval 5
              Rotate_Wait  30
              Mem_Buf_Limit 10MB

          # Collect audit logs
          [INPUT]
              Name         tail
              Tag          audit.*
              Path         /var/log/audit/audit.log
              Parser       logfmt
              Refresh_Interval 5

          # Add classification metadata
          [FILTER]
              Name         modify
              Match        *
              Add          classification CUI
              Add          hostname ${{HOSTNAME}}

          # Detect sensitive data patterns and redact
          [FILTER]
              Name         lua
              Match        *
              script       /etc/fluent-bit/redact.lua
              call         redact_sensitive

          # Forward to aggregator
          [OUTPUT]
              Name         forward
              Match        *
              Host         {{{{ fluentbit_forward_host }}}}
              Port         {{{{ fluentbit_forward_port }}}}
              tls          on
              tls.verify   on

          # Local backup output
          [OUTPUT]
              Name         file
              Match        *
              Path         /var/log/fluent-bit/
              Format       out_file
      notify: restart fluentbit
      tags: [fluentbit]

    - name: Create FluentBit redaction script
      copy:
        dest: /etc/fluent-bit/redact.lua
        mode: "0644"
        content: |
          function redact_sensitive(tag, timestamp, record)
              local patterns = {{
                  -- SSN
                  {{"%d%d%d%-%d%d%-%d%d%d%d", "[SSN-REDACTED]"}},
                  -- Credit card
                  {{"%d%d%d%d%-%d%d%d%d%-%d%d%d%d%-%d%d%d%d", "[CC-REDACTED]"}},
              }}
              local modified = false
              for k, v in pairs(record) do
                  if type(v) == "string" then
                      for _, pat in ipairs(patterns) do
                          local new_v = string.gsub(v, pat[1], pat[2])
                          if new_v ~= v then
                              record[k] = new_v
                              modified = true
                              v = new_v
                          end
                      end
                  end
              end
              if modified then
                  return 1, timestamp, record
              end
              return 0, timestamp, record
          end
      tags: [fluentbit]

    - name: Create FluentBit log directory
      file:
        path: /var/log/fluent-bit
        state: directory
        owner: root
        group: root
        mode: "0750"
      tags: [fluentbit]

    - name: Enable and start FluentBit
      systemd:
        name: fluent-bit
        state: started
        enabled: true
        daemon_reload: true
      tags: [fluentbit]

  handlers:
    - name: restart node_exporter
      systemd:
        name: node_exporter
        state: restarted

    - name: restart fluentbit
      systemd:
        name: fluent-bit
        state: restarted
"""
    p = _write(ansible_dir / "playbooks" / "monitoring.yml", monitoring_playbook)
    files.append(str(p))

    # Ansible config
    ansible_cfg = f"""{_cui_header()}
[defaults]
inventory = inventory/hosts.yml
roles_path = roles
host_key_checking = False
retry_files_enabled = False
stdout_callback = yaml
forks = 10
timeout = 30

[privilege_escalation]
become = True
become_method = sudo
become_user = root
become_ask_pass = False

[ssh_connection]
pipelining = True
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=accept-new
"""
    p = _write(ansible_dir / "ansible.cfg", ansible_cfg)
    files.append(str(p))

    return files


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate Ansible playbooks")
    parser.add_argument("--project-path", required=True, help="Target project directory")
    parser.add_argument(
        "--playbooks",
        default="harden,deploy,monitoring",
        help="Comma-separated playbooks: harden,deploy,monitoring",
    )
    parser.add_argument("--app-name", default="icdev-app", help="Application name")
    parser.add_argument("--port", type=int, default=8080, help="Application port")
    parser.add_argument("--image", default="registry.example.com/app:latest", help="Container image")
    args = parser.parse_args()

    app_config = {
        "app_name": args.app_name,
        "port": args.port,
        "image": args.image,
    }

    playbooks = [p.strip() for p in args.playbooks.split(",")]
    all_files = []

    generators = {
        "harden": lambda: generate_hardening(args.project_path),
        "deploy": lambda: generate_deploy(args.project_path, app_config),
        "monitoring": lambda: generate_monitoring(args.project_path),
    }

    for pb in playbooks:
        if pb in generators:
            files = generators[pb]()
            all_files.extend(files)
            print(f"[ansible] Generated {pb}: {len(files)} files")
        else:
            print(f"[ansible] Unknown playbook: {pb}")

    print(f"\n[ansible] Total files generated: {len(all_files)}")
    for f in all_files:
        print(f"  -> {f}")


if __name__ == "__main__":
    main()
