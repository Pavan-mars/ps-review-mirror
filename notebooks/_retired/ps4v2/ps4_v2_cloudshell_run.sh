#!/usr/bin/env bash
# ps4_v2_cloudshell_run.sh -- rewritten 29-Jul: DB instance + secret already
# confirmed from your CloudShell output, S3 base path already confirmed live.
# You only need to set ASOF_DATE below (defaults to the latest one we saw:
# 2026-07-28).

set -euo pipefail

DB_INSTANCE_ID="cubic-mars-rds-aurora-1-dev"
SECRET_NAME="cubic-mars-secret-rds-dev"
ASOF_DATE="${1:-2026-07-28}"     # override: bash ps4_v2_cloudshell_run.sh 2026-07-29
DEVICE_TYPES="tvm,gate,validator"

echo "== Step 1: deps (idempotent) =="
command -v psql >/dev/null 2>&1 || sudo dnf install -y postgresql15 || sudo yum install -y postgresql15
pip3 install -q pyarrow psycopg2-binary

echo "== Step 2: resolve endpoint + credentials =="
ENDPOINT=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_ID" \
  --query "DBInstances[0].Endpoint.Address" --output text)
PORT=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_ID" \
  --query "DBInstances[0].Endpoint.Port" --output text)
SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id "$SECRET_NAME" --query SecretString --output text)
DB_USER=$(echo "$SECRET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['username'])")
DB_PASS=$(echo "$SECRET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
DB_NAME=$(echo "$SECRET_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('dbname', d.get('database','appdb')))")
export PGPASSWORD="$DB_PASS"
echo "Resolved $ENDPOINT:$PORT / db=$DB_NAME"

echo "== Step 3: run the additive migration (safe to re-run) =="
psql -h "$ENDPOINT" -p "$PORT" -U "$DB_USER" -d "$DB_NAME" -f ~/ps4_v2_additive_migration.sql

echo "== Step 4: dry-run the loader for asof=$ASOF_DATE, all 3 device types =="
python3 ~/ps4_v2_backfill_loader.py --asof-date "$ASOF_DATE" --device-types "$DEVICE_TYPES" --dry-run

echo ""
read -p "Counts above look correct (PROMOTE=True for all 3)? Type 'yes' to load for real: " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
  echo "Stopped -- nothing written to RDS."
  exit 0
fi

echo "== Step 5: load for real =="
export PS4_RDS_DSN="host=$ENDPOINT port=$PORT dbname=$DB_NAME user=$DB_USER password=$DB_PASS sslmode=require"
python3 ~/ps4_v2_backfill_loader.py --asof-date "$ASOF_DATE" --device-types "$DEVICE_TYPES"

echo "== Step 6: sanity check =="
psql -h "$ENDPOINT" -p "$PORT" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT device_type, count(*) FROM ps4v2_clustering_assignments WHERE asof_date = '$ASOF_DATE' GROUP BY device_type;"
psql -h "$ENDPOINT" -p "$PORT" -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT * FROM ps4v2_load_audit WHERE asof_date = '$ASOF_DATE';"

echo "Done."
