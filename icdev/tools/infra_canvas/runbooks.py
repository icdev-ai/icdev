from __future__ import annotations
# CUI // SP-CTI
"""Infrastructure Design Canvas — Runbooks.

Operational runbooks for common infrastructure incidents. Triggered by
specific alert conditions and include step-by-step remediation with
expected outcomes and rollback actions.

Examples: server provisioning failure, capacity threshold breach,
cloud drift detected, patch rollback.
"""

import json
import uuid
from datetime import datetime, timezone


def _get_conn():
    from tools.infra_canvas.db.init_db import get_connection
    return get_connection()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_json_field(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    if value is None:
        return default
    return value


def _runbook_to_dict(row):
    if not row:
        return None
    d = dict(row)
    d["steps"] = _parse_json_field(d.get("steps"), [])
    d["nist_controls"] = _parse_json_field(d.get("nist_controls"), [])
    d["tags"] = _parse_json_field(d.get("tags"), [])
    return d


# ── Read ─────────────────────────────────────────────────────────────────────

def get_all_runbooks(category=None, severity=None):
    """Return all runbooks, optionally filtered by category and/or severity."""
    if category and severity:
        sql = "SELECT * FROM idc_runbooks WHERE category=? AND severity=? ORDER BY severity, title"
        params = [category, severity]
    elif category:
        sql = "SELECT * FROM idc_runbooks WHERE category=? ORDER BY severity, title"
        params = [category]
    elif severity:
        sql = "SELECT * FROM idc_runbooks WHERE severity=? ORDER BY title"
        params = [severity]
    else:
        sql = "SELECT * FROM idc_runbooks ORDER BY severity, title"
        params = []
    conn = _get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_runbook_to_dict(r) for r in rows]


def get_runbook_by_id(runbook_id):
    """Return a single runbook dict or None."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM idc_runbooks WHERE id=%s", (runbook_id,)).fetchone()
    finally:
        conn.close()
    return _runbook_to_dict(row)


# ── Write ────────────────────────────────────────────────────────────────────

def create_runbook(data):
    """Create a new runbook. Returns the new runbook dict."""
    rb_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps(data.get("steps", []))
    nist_controls = json.dumps(data.get("nist_controls", []))
    tags = json.dumps(data.get("tags", []))
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO idc_runbooks
               (id, title, category, trigger_condition, severity, description,
                steps, nist_controls, tags, owner, estimated_duration_min,
                classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                rb_id,
                data.get("title", "Untitled Runbook"),
                data.get("category", "general"),
                data.get("trigger_condition", ""),
                data.get("severity", "medium"),
                data.get("description", ""),
                steps,
                nist_controls,
                tags,
                data.get("owner", ""),
                int(data.get("estimated_duration_min", 30)),
                data.get("classification", "CUI"),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_runbook_by_id(rb_id)


def update_runbook(runbook_id, data):
    """Update an existing runbook. Returns updated dict or None."""
    existing = get_runbook_by_id(runbook_id)
    if not existing:
        return None
    now = _now()
    steps = json.dumps(data.get("steps", existing["steps"]))
    nist_controls = json.dumps(data.get("nist_controls", existing["nist_controls"]))
    tags = json.dumps(data.get("tags", existing["tags"]))
    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE idc_runbooks SET
               title=%s, category=%s, trigger_condition=%s, severity=%s,
               description=%s, steps=%s, nist_controls=%s, tags=%s,
               owner=%s, estimated_duration_min=%s, classification=%s, updated_at=%s
               WHERE id=%s""",
            (
                data.get("title", existing["title"]),
                data.get("category", existing["category"]),
                data.get("trigger_condition", existing["trigger_condition"]),
                data.get("severity", existing["severity"]),
                data.get("description", existing["description"]),
                steps,
                nist_controls,
                tags,
                data.get("owner", existing["owner"]),
                int(data.get("estimated_duration_min", existing["estimated_duration_min"])),
                data.get("classification", existing["classification"]),
                now,
                runbook_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_runbook_by_id(runbook_id)


def delete_runbook(runbook_id):
    """Delete a runbook. Returns True if deleted, False if not found."""
    existing = get_runbook_by_id(runbook_id)
    if not existing:
        return False
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM idc_runbooks WHERE id=%s", (runbook_id,))
        conn.commit()
    finally:
        conn.close()
    return True


def record_execution(runbook_id):
    """Increment execution_count and set last_executed_at. Returns updated dict or None."""
    existing = get_runbook_by_id(runbook_id)
    if not existing:
        return None
    now = _now()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE idc_runbooks SET execution_count=execution_count+1, last_executed_at=%s, updated_at=%s WHERE id=%s",
            (now, now, runbook_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_runbook_by_id(runbook_id)


# ── Seeds ────────────────────────────────────────────────────────────────────

_SEED_RUNBOOKS = [
    {
        "id": "rb-idc-server-provisioning-failure",
        "title": "Server Provisioning Failure",
        "category": "provisioning",
        "trigger_condition": "IaC apply (Terraform/Ansible) exits non-zero OR new server unreachable within 10 minutes of provisioning",
        "severity": "critical",
        "description": (
            "Recovery runbook for failed server provisioning events — covers Terraform apply "
            "failures, Ansible playbook errors, AMI/image availability issues, IAM permission "
            "denials, and network misconfiguration. Goal: restore successful provisioning "
            "within one hour and capture a root-cause record to prevent recurrence."
        ),
        "steps": [
            {
                "action": "Capture the full error output from the IaC tool. For Terraform: terraform show -json tfplan.json > provision_error.json. For Ansible: check the failed task name and module error message in the play recap.",
                "expected_outcome": "Error log captured; root cause category identified (auth, image, network, quota, config).",
                "rollback_action": "",
            },
            {
                "action": "If Terraform state is corrupted or partially applied: run terraform state list to identify created resources. Destroy only the failed resources: terraform destroy -target=<resource_address>. Do NOT run terraform destroy without -target on a shared state.",
                "expected_outcome": "Partial resources removed; state file consistent with actual cloud state.",
                "rollback_action": "If destroy fails, manually remove the resource via cloud console and run terraform state rm <address> to de-register it.",
            },
            {
                "action": "Check IAM permissions: confirm the provisioning service account has the required roles (e.g., ec2:RunInstances, iam:PassRole for AWS). Run: aws iam simulate-principal-policy --policy-source-arn <role_arn> --action-names ec2:RunInstances ec2:CreateTags.",
                "expected_outcome": "Policy simulation returns 'allowed' for all required actions.",
                "rollback_action": "Attach missing IAM policy and re-run provisioning.",
            },
            {
                "action": "Verify the target AMI / machine image exists in the target region and account: aws ec2 describe-images --image-ids <ami_id> --region <region>. For cross-account AMIs, confirm sharing permissions.",
                "expected_outcome": "AMI found and accessible; ImageState = available.",
                "rollback_action": "Switch to a region-local copy of the AMI or update image ID in the IaC variable file.",
            },
            {
                "action": "Check resource quotas/limits: aws service-quotas list-service-quotas --service-code ec2 | grep Running. If quota is the blocker, request a quota increase via the cloud console or submit a support ticket.",
                "expected_outcome": "Available capacity confirmed OR quota increase request submitted.",
                "rollback_action": "Provision in a different availability zone or region while waiting for quota approval.",
            },
            {
                "action": "Re-run provisioning after fix: terraform apply -auto-approve (or ansible-playbook site.yml --limit <host>). Monitor the first 5 minutes for SSH/WinRM connectivity using: ssh -o ConnectTimeout=30 <user>@<ip> echo 'alive'.",
                "expected_outcome": "Server provisioned and reachable; configuration management completes successfully.",
                "rollback_action": "If second attempt fails, escalate to cloud engineering and open a POAM item for CM-2 (baseline configuration) if the failure is systemic.",
            },
            {
                "action": "Document the provisioning failure in the audit log: record the server name, IaC version, failure reason, resolution steps taken, and time-to-recovery. Update runbook with any new failure modes discovered.",
                "expected_outcome": "Audit record created; runbook updated if new failure mode found.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["CM-2", "CM-6", "SA-9", "SI-7", "AU-2"],
        "tags": ["provisioning", "terraform", "ansible", "iac", "server"],
        "owner": "Infrastructure Engineering",
        "estimated_duration_min": 60,
        "classification": "CUI",
    },
    {
        "id": "rb-idc-capacity-threshold-breach",
        "title": "Capacity Threshold Breach",
        "category": "capacity",
        "trigger_condition": "CPU >85% sustained 15 min, memory >90%, disk >80%, or auto-scaler fails to launch new instances within 5 min",
        "severity": "high",
        "description": (
            "Response runbook for capacity threshold breaches on compute, memory, storage, or "
            "network resources. Covers immediate mitigation (scale-out, request throttling) and "
            "root-cause analysis for unexpected load spikes or resource leaks. Aligns with "
            "NIST CP-2 (contingency planning) and SC-5 (resource availability)."
        ),
        "steps": [
            {
                "action": "Identify the saturated resource and affected hosts: check the monitoring dashboard (Grafana/CloudWatch/Datadog) for the resource metric breaching the threshold. Confirm scope: single instance, auto-scaling group, or entire cluster.",
                "expected_outcome": "Affected resource type and host(s) identified; breach start time recorded.",
                "rollback_action": "",
            },
            {
                "action": "For CPU/memory spike: check top-consuming processes — SSH to host and run: top -b -n1 | head -20 or ps aux --sort=-%cpu | head -15. For Kubernetes: kubectl top pods -A --sort-by=cpu | head -20.",
                "expected_outcome": "Top N processes consuming the resource identified by name, PID, and owner.",
                "rollback_action": "",
            },
            {
                "action": "If auto-scaling is configured and failed to trigger: check auto-scaling group activity log (AWS: aws autoscaling describe-scaling-activities --auto-scaling-group-name <asg>). Identify cooldown periods, launch config errors, or AZ capacity constraints.",
                "expected_outcome": "Auto-scaling failure reason identified (e.g., insufficient capacity in AZ, IAM error, launch template issue).",
                "rollback_action": "Manually launch a replacement instance in a different AZ: aws ec2 run-instances --launch-template LaunchTemplateId=<id> --count 1.",
            },
            {
                "action": "Apply immediate mitigation: (a) Scale-out manually if auto-scaler is stuck. (b) For disk: identify and archive or delete large temporary files (du -sh /tmp/* /var/log/* | sort -rh | head -10). (c) For memory leak: restart the offending process after capturing heap dump for analysis.",
                "expected_outcome": "Metric drops below threshold within 10 minutes of mitigation action.",
                "rollback_action": "If process restart causes service disruption, enable maintenance mode on the load balancer target group before restarting.",
            },
            {
                "action": "Enable request throttling if the spike is driven by inbound traffic: set rate limits on the API gateway or load balancer. For AWS ALB: update WAF rate-based rule to 2000 req/5min per IP. Monitor 5xx error rate during throttling.",
                "expected_outcome": "Inbound request rate capped; downstream services stabilize.",
                "rollback_action": "Remove throttling rule after capacity is restored and traffic returns to baseline.",
            },
            {
                "action": "Root-cause analysis: correlate the breach with recent deployments, traffic patterns (CDN logs), batch jobs, or cron tasks. Check if a new code release introduced a memory leak or inefficient query causing the spike.",
                "expected_outcome": "Root cause identified and categorized (traffic spike, memory leak, misconfigured job, infra regression).",
                "rollback_action": "If recent deployment is the cause, trigger rollback procedure or canary traffic shift back to previous version.",
            },
            {
                "action": "Update capacity planning baseline: record peak metrics, forecast 90-day growth, and submit a capacity increase request or right-sizing recommendation. Update POAM if SLA was breached.",
                "expected_outcome": "Capacity plan updated; SLA breach documented in POAM if applicable.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["CP-2", "SC-5", "SI-4", "AU-6", "CM-6"],
        "tags": ["capacity", "autoscaling", "cpu", "memory", "disk", "scaling"],
        "owner": "Infrastructure Engineering",
        "estimated_duration_min": 45,
        "classification": "CUI",
    },
    {
        "id": "rb-idc-cloud-drift-detected",
        "title": "Cloud Drift Detected",
        "category": "drift",
        "trigger_condition": "IaC drift detection reports >0 resource differences OR cloud config baseline check fails OR AWS Config/Azure Policy shows non-compliant resources",
        "severity": "high",
        "description": (
            "Remediation runbook for infrastructure drift — when the actual cloud resource "
            "configuration diverges from the desired state declared in Terraform/CloudFormation. "
            "Common causes: manual console changes, auto-remediation side effects, CSP-initiated "
            "updates, or stale IaC state. Aligns with NIST CM-3 (configuration change control) "
            "and CM-6 (configuration settings)."
        ),
        "steps": [
            {
                "action": "Run drift detection to enumerate all drifted resources: terraform plan -out=drift.tfplan 2>&1 | tee drift_report.txt. Review the plan output for unexpected changes (~ modify, - delete, + create). For AWS CloudFormation: aws cloudformation detect-stack-drift --stack-name <stack>.",
                "expected_outcome": "List of drifted resources with attribute-level diff captured in drift_report.txt.",
                "rollback_action": "",
            },
            {
                "action": "Classify drift by risk: (a) Security-impacting: security group rule added, IAM policy modified, encryption disabled — IMMEDIATE remediation required. (b) Config drift: instance type changed, tag missing — remediate within 24 hours. (c) Metadata drift: tags only — remediate in next sprint.",
                "expected_outcome": "Each drifted resource tagged with risk tier (critical/high/medium/low) and assigned owner.",
                "rollback_action": "",
            },
            {
                "action": "For security-impacting drift: immediately revert via IaC apply OR directly via CLI. Example — unauthorized security group rule added: aws ec2 revoke-security-group-ingress --group-id <sg_id> --ip-permissions <permissions_json>. Document the unauthorized change.",
                "expected_outcome": "Security configuration restored to IaC-declared state; audit record created.",
                "rollback_action": "If reverting breaks a running service, escalate to security team for risk acceptance before proceeding.",
            },
            {
                "action": "Investigate the source of drift: check cloud audit logs for who made the manual change (AWS CloudTrail: aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=<resource_id> --start-time <drift_start>).",
                "expected_outcome": "Actor (user/role/service) and timestamp of manual change identified.",
                "rollback_action": "",
            },
            {
                "action": "Reconcile IaC state: if the drift represents a legitimate change that was not applied through IaC (e.g., an emergency hotfix), update the Terraform/CloudFormation code to match the current state, commit to version control, and run terraform apply to confirm zero diff.",
                "expected_outcome": "IaC code updated and committed; terraform plan shows 'No changes' after apply.",
                "rollback_action": "If the manual change was unauthorized, revert and open a security incident ticket.",
            },
            {
                "action": "Harden against future drift: enable AWS Config Rule 'required-tags', enable CloudTrail + Config recording for all regions, configure Sentinel / OPA policy to block unauthorized changes, add drift detection to CI/CD pipeline as a scheduled job (every 6 hours).",
                "expected_outcome": "Preventive controls enabled; drift detection automated in CI/CD.",
                "rollback_action": "",
            },
            {
                "action": "If drift was caused by a CSP-initiated change (e.g., AWS deprecating an instance type): update the IaC variable file, apply the change through the normal change management process, and close any associated Config findings.",
                "expected_outcome": "IaC updated for CSP change; Config finding closed.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["CM-3", "CM-6", "CM-7", "AU-2", "AU-3", "SI-7"],
        "tags": ["drift", "terraform", "config", "cloudtrail", "compliance", "iac"],
        "owner": "Platform Engineering",
        "estimated_duration_min": 90,
        "classification": "CUI",
    },
    {
        "id": "rb-idc-patch-rollback",
        "title": "Patch Rollback",
        "category": "patch",
        "trigger_condition": "Post-patch health check fails, service error rate >5%, or application team declares patch-induced regression within 2 hours of patch window",
        "severity": "medium",
        "description": (
            "Rollback runbook for failed or regression-inducing OS/middleware patches. "
            "Covers rolling back OS patches via snapshot restore, package downgrade, or "
            "AMI swap. Ensures audit continuity (NIST SI-2 / RA-5) and minimizes "
            "unplanned downtime. Use within the patch window or emergency change window."
        ),
        "steps": [
            {
                "action": "Declare rollback intent: notify the patch change manager and affected application owners via the agreed communication channel (Slack/Teams/ServiceNow). Record rollback start time in the change ticket.",
                "expected_outcome": "Rollback declared; stakeholders notified; change ticket updated with rollback reason.",
                "rollback_action": "",
            },
            {
                "action": "Verify a pre-patch snapshot or AMI exists: AWS: aws ec2 describe-snapshots --filters Name=description,Values='pre-patch*' --owner-ids self. For VM-based: check vSphere/Hyper-V snapshot inventory. Confirm the snapshot was taken BEFORE the patch was applied.",
                "expected_outcome": "Pre-patch snapshot/AMI identified by ID and creation timestamp.",
                "rollback_action": "If no snapshot exists, proceed with package downgrade method (Step 4) instead of image restore.",
            },
            {
                "action": "For AMI/snapshot rollback: (a) Stop the affected instance. (b) Detach the root EBS volume. (c) Create a new volume from the pre-patch snapshot. (d) Attach the restored volume as root (/dev/xvda). (e) Start the instance. (f) Verify application health via health check endpoint.",
                "expected_outcome": "Instance restored to pre-patch state; application health check returns HTTP 200.",
                "rollback_action": "If instance fails to boot from restored volume, mount the volume on a rescue instance to investigate boot errors.",
            },
            {
                "action": "For package downgrade (no snapshot): identify the previously installed package version from the system package log. RHEL/CentOS: sudo yum history list | grep <package>; sudo yum history undo <transaction_id>. Ubuntu/Debian: sudo apt-get install <package>=<previous_version>.",
                "expected_outcome": "Previous package version reinstalled; package manager confirms downgrade.",
                "rollback_action": "If downgrade fails due to dependency conflicts, pin the package version and rebuild from a known-good base image.",
            },
            {
                "action": "Run post-rollback health checks: (a) Service is running: systemctl status <service>. (b) Application endpoints return expected responses: curl -sf http://localhost:<port>/health. (c) No new error spikes in application logs: tail -n 100 /var/log/app/error.log.",
                "expected_outcome": "All health checks pass; application error rate returns to pre-patch baseline.",
                "rollback_action": "",
            },
            {
                "action": "Capture the patch failure details for the vendor/security team: collect the patch log (/var/log/dnf.log or /var/log/dpkg.log), application error logs during the patch window, and any core dumps. Open a vendor support case if the patch is CVE-mandated.",
                "expected_outcome": "Patch failure evidence package created; vendor case opened if applicable.",
                "rollback_action": "",
            },
            {
                "action": "Update POAM: if the rolled-back patch addressed a known CVE, add a POAM entry with the vulnerability ID, risk acceptance rationale, and remediation target date (must be <= 30 days for critical CVEs per NIST RA-5). Schedule a re-patch test in a non-production environment before retry.",
                "expected_outcome": "POAM updated; re-patch test scheduled in non-prod within 7 days.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["SI-2", "RA-5", "CM-3", "CM-4", "AU-2", "CP-9"],
        "tags": ["patch", "rollback", "os", "cve", "snapshot", "downgrade"],
        "owner": "Platform Engineering",
        "estimated_duration_min": 60,
        "classification": "CUI",
    },
]


def seed_runbooks():
    """Insert seed runbooks if they don't already exist. Returns count added."""
    added = 0
    conn = _get_conn()
    try:
        for rb in _SEED_RUNBOOKS:
            existing = conn.execute(
                "SELECT id FROM idc_runbooks WHERE id=%s", (rb["id"],)
            ).fetchone()
            if existing:
                continue
            now = _now()
            conn.execute(
                """INSERT INTO idc_runbooks
                   (id, title, category, trigger_condition, severity, description,
                    steps, nist_controls, tags, owner, estimated_duration_min,
                    classification, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    rb["id"],
                    rb["title"],
                    rb["category"],
                    rb["trigger_condition"],
                    rb["severity"],
                    rb["description"],
                    json.dumps(rb["steps"]),
                    json.dumps(rb["nist_controls"]),
                    json.dumps(rb["tags"]),
                    rb["owner"],
                    rb["estimated_duration_min"],
                    rb["classification"],
                    now,
                    now,
                ),
            )
            added += 1
        if added:
            conn.commit()
    finally:
        conn.close()
    return added
