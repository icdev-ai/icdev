# Disaster Recovery Runbook
<!-- CUI // SP-CTI -->

**Classification:** CUI // SP-CTI  
**Owner:** Platform Engineering  
**Review Cadence:** Quarterly (aligned to DR test schedule)  
**Last Reviewed:** 2026-05-11

---

## Recovery Objectives

| Objective | Target | Mechanism |
|-----------|--------|-----------|
| RTO (Recovery Time Objective) | **4 hours** | Multi-AZ auto-failover + read replica promotion |
| RPO (Recovery Point Objective) | **15 minutes** | S3-RTC cross-region replication + RDS automated backups every 5 min (PITR) |
| DR Region | **us-east-2** | Read replica + S3 DR buckets pre-provisioned |
| Backup Isolation | Separate AWS account | Cross-account RDS snapshot copy daily |

---

## Architecture Overview

```
PRIMARY (us-gov-west-1)                 DR (us-east-2)
┌──────────────────────┐               ┌──────────────────────┐
│  RDS Multi-AZ        │──async──────▶│  RDS Read Replica    │
│  (Primary + Standby) │               │  (Promote on DR)     │
└──────────────────────┘               └──────────────────────┘
         │ daily snapshot
         ▼
BACKUP ACCOUNT (separate)              S3 DR Buckets (us-east-2)
┌──────────────────────┐               ┌──────────────────────┐
│  Cross-account RDS   │               │  S3-RTC replication  │
│  Snapshot copies     │               │  ≤ 15 min guaranteed │
└──────────────────────┘               └──────────────────────┘
         │
         ▼
Lambda Verifier (daily 06:00 UTC)
  ✓ Snapshot exists + available + encrypted
  ✓ S3 replication lag < 15 min
  ✓ Cross-account copy complete
  → SNS alert on any failure
```

---

## Disaster Scenarios

### Scenario 1: AZ Failure (Most Common)

**What happens automatically:**
- RDS Multi-AZ promotes the standby replica in the healthy AZ (60–120 seconds).
- DNS endpoint updated automatically — no application change required.
- Estimated auto-recovery time: **< 5 minutes** (well within RTO 4h).

**Operator actions:** Monitor. No intervention required unless the failover does not complete.

---

### Scenario 2: Regional Failure (us-gov-west-1 unavailable)

**Target: Operational in us-east-2 within 4 hours.**

#### Step 1 — Declare DR (0:00)

```bash
# Notify team and open incident
python tools/infra/dr_failover.py status --json
```

Confirm: replica lag was < 15 minutes before the outage (check CloudWatch history).  
If lag exceeded 15 minutes → RPO may be breached. Document and escalate.

#### Step 2 — Promote RDS Read Replica (0:05)

```bash
python tools/infra/dr_failover.py failover \
  --db-identifier icdev-prod \
  --confirm
```

Monitor promotion (AWS Console → RDS → `icdev-prod-dr-replica` → Status: `available`).  
Expected promotion time: **15–30 minutes**.

Manual alternative:
```bash
aws rds promote-read-replica \
  --db-instance-identifier icdev-prod-dr-replica \
  --region us-east-2 \
  --backup-retention-period 35 \
  --preferred-backup-window "02:00-03:00"
```

#### Step 3 — Update Application Config (0:35)

Update the database connection string in your secrets manager / environment config to point to the new DR endpoint:

```bash
# Get new endpoint after promotion
aws rds describe-db-instances \
  --db-instance-identifier icdev-prod-dr-replica \
  --region us-east-2 \
  --query 'DBInstances[0].Endpoint.Address'
```

Update `DB_HOST` in AWS Secrets Manager / Parameter Store in us-east-2.  
Restart application pods:

```bash
kubectl rollout restart deployment/icdev-api -n icdev-prod
kubectl rollout restart deployment/icdev-dashboard -n icdev-prod
```

#### Step 4 — Verify Application Health (1:00)

```bash
# Health check
curl -s https://app-dr.icdev.internal/health | jq .

# Smoke test
python tools/testing/health_check.py --json
```

Confirm: all critical paths return 200. Check dashboard at `/`.

#### Step 5 — Enable Multi-AZ on Promoted Instance (1:30)

Once the promoted instance is stable, enable Multi-AZ for HA in the DR region:

```bash
aws rds modify-db-instance \
  --db-instance-identifier icdev-prod-dr-replica \
  --multi-az \
  --region us-east-2 \
  --apply-immediately
```

#### Step 6 — RTO Checkpoint (≤ 4:00)

By the 4-hour mark, confirm:
- [ ] Application serving traffic from us-east-2
- [ ] Database writes succeeding
- [ ] Monitoring/alerting operational
- [ ] Incident timeline documented

---

### Scenario 3: Data Corruption / Accidental Deletion

Use point-in-time recovery (PITR) — supports any 5-minute window within the 35-day retention period.

```bash
# Restore to specific point in time
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier icdev-prod \
  --target-db-instance-identifier icdev-prod-pitr-recovery \
  --restore-time 2026-05-10T14:00:00Z \
  --db-instance-class db.r6g.large \
  --multi-az \
  --region us-gov-west-1

# Or restore from a named snapshot
python tools/infra/dr_failover.py restore \
  --snapshot-id icdev-dr-daily-20260510 \
  --target-instance icdev-prod-recovery-test
```

Verify data integrity before promoting the recovered instance.

---

### Scenario 4: Ransomware / Backup Account Compromise

Cross-account snapshots in the isolated backup account are the last resort.

```bash
# List available cross-account snapshots
aws rds describe-db-snapshots \
  --snapshot-type shared \
  --region us-gov-west-1 \
  --query 'DBSnapshots[?contains(DBSnapshotIdentifier, `dr-copy-`)]'

# Restore from cross-account snapshot
aws rds copy-db-snapshot \
  --source-db-snapshot-identifier arn:aws:rds:us-gov-west-1:<backup-account>:snapshot:dr-copy-... \
  --target-db-snapshot-identifier icdev-prod-from-backup-account \
  --region us-gov-west-1

aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier icdev-prod-ransomware-recovery \
  --db-snapshot-identifier icdev-prod-from-backup-account \
  --db-instance-class db.r6g.large \
  --region us-gov-west-1
```

---

## Quarterly DR Test Procedure

Tests are automated and run non-destructively against test resources (never production).  
Schedule: **January, April, July, October** (first week).

### Automated Test

```bash
# Dry run (no AWS calls)
python tools/infra/dr_failover.py test --dry-run --json

# Full automated test (requires AWS credentials + --db-identifier)
python tools/infra/dr_failover.py test \
  --db-identifier icdev-prod \
  --json
```

The automated test:
1. Finds the latest automated RDS snapshot.
2. Restores it to a temporary test instance (`icdev-dr-test-YYYYMMDDHHMM`).
3. Verifies the restore completes within the RTO window.
4. Checks S3 replication lag metrics.
5. Confirms RTO and RPO targets were met.
6. Deletes the test instance.

### Manual Tabletop Checklist

Run in parallel with the automated test each quarter:

- [ ] Review DR config: `cat args/dr_config.yaml`
- [ ] Verify replica lag trending: CloudWatch → `ReplicaLag` metric for `icdev-prod-dr-replica`
- [ ] Verify S3 replication: CloudWatch → `ReplicationLatency` for each production S3 bucket
- [ ] Verify Lambda verifier ran successfully: CloudWatch Logs → `/aws/lambda/icdev-dr-backup-verifier`
- [ ] Verify cross-account snapshot exists: AWS Console → backup account → RDS Snapshots
- [ ] Walk through Scenario 2 steps above with the on-call team (tabletop, no live changes)
- [ ] Update this runbook if any step is unclear or outdated
- [ ] Record test results and RTO/RPO measurements in the DR test log (below)

### DR Test Log

| Quarter | Date | RTO Achieved | RPO Achieved | Notes |
|---------|------|-------------|-------------|-------|
| Q1 2026 | TBD | — | — | Baseline test |
| Q2 2026 | TBD | — | — | |
| Q3 2026 | TBD | — | — | |
| Q4 2026 | TBD | — | — | |

---

## Monitoring & Alerts

| Alert | Threshold | Action |
|-------|-----------|--------|
| `icdev-rds-replica-lag-rpo-guard` | Lag > 10 min | Investigate replication; DR may not meet RPO |
| `icdev-s3-replication-lag-*` | Latency > 10 min | Check S3-RTC health |
| Lambda verifier `FAIL` result | Any failure | Check SNS notification; review logs |
| `icdev-rto-breach-warning` | 3h elapsed in failover | Escalate — 1h buffer remaining |

CloudWatch dashboard: `icdev-dr-monitoring` (us-gov-west-1 and us-east-2).

---

## Infrastructure Reference

| Component | File |
|-----------|------|
| DR configuration | `args/dr_config.yaml` |
| Terraform generator | `tools/infra/dr_generator.py` |
| Failover/test automation | `tools/infra/dr_failover.py` |
| Generated Terraform | `terraform/dr/` (per project) |

Generate fresh Terraform for a project:
```bash
python tools/infra/dr_generator.py \
  --project-path /path/to/project \
  --project-name icdev-prod \
  --environment prod \
  --json
```

---

## Contacts

| Role | Responsibility |
|------|----------------|
| Platform Engineering On-Call | First responder for all DR events |
| Data Engineer | Verifies data integrity post-restore |
| Security Officer | Approves cross-account operations |
| Program Manager | Stakeholder communications, RTO tracking |

---

*CUI // SP-CTI — Handle per CUI policy. Do not distribute outside authorized channels.*
