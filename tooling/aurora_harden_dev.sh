#!/usr/bin/env bash
# =====================================================================
# aurora_harden_dev.sh -- the dev serving database, made safe without
# touching performance.
#
# Decision 13-Sep-2026 (PK): apply whatever does not affect dashboards,
# loader output or latency. That rules IN deletion protection and a
# 7-day backup window (both metadata changes, applied immediately, no
# restart, no extra instance) and rules OUT Multi-AZ for dev (a second
# instance doubles compute cost and adds nothing a 6-user dashboard with
# nightly loaders can feel; Aurora storage is already replicated across
# three AZs; Multi-AZ goes in at UAT with the ALB).
#
# Also takes a manual cluster snapshot first, so the migrate() hazard
# (sql/08/11/18/28/57/59 mutate data on every deploy) has a restore point.
# =====================================================================
set -euo pipefail
export AWS_PAGER=""
REGION=${REGION:-us-east-1}
CLUSTER=${CLUSTER:-cubic-mars-rds-aurora-dev}
DRY_RUN=${DRY_RUN:-1}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

echo "== before"
aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER" --region "$REGION" \
  --query 'DBClusters[0].{DB:DatabaseName,Engine:EngineVersion,MultiAZ:MultiAZ,Backup:BackupRetentionPeriod,DelProt:DeletionProtection,Status:Status}' --output table

if [ "$DRY_RUN" = "0" ]; then
  echo "== manual snapshot (restore point before any further migrate)"
  aws rds create-db-cluster-snapshot --db-cluster-identifier "$CLUSTER" \
    --db-cluster-snapshot-identifier "${CLUSTER}-manual-${STAMP}" --region "$REGION" --output table
  echo "== deletion protection on, backups 7 days (apply-immediately; no restart)"
  aws rds modify-db-cluster --db-cluster-identifier "$CLUSTER" --deletion-protection \
    --backup-retention-period 7 --apply-immediately --region "$REGION" \
    --query 'DBCluster.{Backup:BackupRetentionPeriod,DelProt:DeletionProtection,Status:Status}' --output table
  echo "== after"
  aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER" --region "$REGION" \
    --query 'DBClusters[0].{MultiAZ:MultiAZ,Backup:BackupRetentionPeriod,DelProt:DeletionProtection,Status:Status}' --output table
else
  echo "DRY_RUN: would (1) snapshot ${CLUSTER}-manual-${STAMP}, (2) set DeletionProtection=true and BackupRetentionPeriod=7 with --apply-immediately. Re-run with DRY_RUN=0."
fi
echo "Multi-AZ: deliberately NOT changed in dev. Add at UAT: aws rds create-db-instance --db-cluster-identifier $CLUSTER --db-instance-class <same> --engine aurora-postgresql --availability-zone <other AZ>"
