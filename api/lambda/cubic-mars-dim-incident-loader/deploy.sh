#!/usr/bin/env bash
# Deploy cubic-mars-dim-incident-loader by cloning cubic-mars-dim-loader's
# runtime config (role, VPC, layers) and reusing its vendored deps from ~/dimz.
#
# Prereqs in CloudShell:
#   ~/dimz          = the unzipped cubic-mars-dim-loader bundle (pg8000/scramp/
#                     asn1crypto/six/dateutil already vendored)
#   this directory  = handler.py + deploy.sh (uploaded together)
#
# Idempotent: create on first run, update-function-code on re-runs.
set -euo pipefail
REGION=us-east-1
FN=cubic-mars-dim-incident-loader
SRC=cubic-mars-dim-loader

[ -f handler.py ] || { echo "ERROR: handler.py not found next to deploy.sh"; exit 1; }
[ -d ~/dimz/pg8000 ] || { echo "ERROR: ~/dimz missing/incomplete -- re-download the $SRC zip first"; exit 1; }

echo "[1/5] read $SRC runtime config"
CFG=$(aws lambda get-function-configuration --function-name $SRC --region $REGION)
ROLE=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['Role'])" "$CFG")
LAYERS=$(python3 -c "import json,sys;print(' '.join(l['Arn'] for l in json.loads(sys.argv[1]).get('Layers',[])))" "$CFG")
SUBNETS=$(python3 -c "import json,sys;print(','.join(json.loads(sys.argv[1])['VpcConfig']['SubnetIds']))" "$CFG")
SGS=$(python3 -c "import json,sys;print(','.join(json.loads(sys.argv[1])['VpcConfig']['SecurityGroupIds']))" "$CFG")
echo "      role=$ROLE"
echo "      layers=${LAYERS:-none}  subnets=$SUBNETS  sg=$SGS"

echo "[2/5] assemble package (deps from ~/dimz + new handler.py)"
BUILD=~/dic_build
rm -rf "$BUILD" && cp -r ~/dimz "$BUILD"
rm -f "$BUILD"/f.zip "$BUILD"/handler.py
cp handler.py "$BUILD"/handler.py

echo "[3/5] import gate (the lesson from the 25-Aug pg8000 incident)"
cd "$BUILD"
python3 - <<'EOF'
import ast, sys
ast.parse(open('handler.py').read())
sys.path.insert(0, '.')
import handler
assert hasattr(handler, 'lambda_handler'), 'lambda_handler missing from module'
print('      handler imports clean; lambda_handler present')
EOF

echo "[4/5] zip"
rm -f ~/dic.zip
zip -qr ~/dic.zip . -x '__pycache__/*' '*.pyc'
echo "      $(du -h ~/dic.zip | cut -f1) ~/dic.zip"

echo "[5/5] create/update $FN"
ENVVARS="Variables={GOLD_BUCKET=cubic-mars-pm-s3-datalake-dev-gold-170202974600,DIC_PREFIX=chicago/dim/device_incident_cmdb,RDS_SECRET_ID=cubic-mars-secret-rds-dev,CITY_ID=CHI,KEEP_SNAPSHOTS=3}"
cd ~
if aws lambda get-function --function-name $FN --region $REGION >/dev/null 2>&1; then
  aws lambda update-function-code --function-name $FN --zip-file fileb://dic.zip --region $REGION >/dev/null
  echo "      updated existing $FN"
else
  aws lambda create-function --function-name $FN --runtime python3.12 \
    --handler handler.lambda_handler --role "$ROLE" --zip-file fileb://dic.zip \
    --timeout 300 --memory-size 512 ${LAYERS:+--layers $LAYERS} \
    --vpc-config "SubnetIds=$SUBNETS,SecurityGroupIds=$SGS" \
    --environment "$ENVVARS" --region $REGION >/dev/null
  echo "      created $FN"
fi
aws lambda wait function-active-v2 --function-name $FN --region $REGION

cat <<'NEXT'

DONE. Next steps (in order):
  1. Run the Databricks notebook once (export on) so a manifest exists.
  2. Dry run:
     aws lambda invoke --function-name cubic-mars-dim-incident-loader \
       --cli-binary-format raw-in-base64-out --payload '{"dry_run":true}' \
       /tmp/dic.json --region us-east-1 && cat /tmp/dic.json
  3. Real load: same invoke with payload '{}'; expect status=committed,
     rows_loaded ~6.6K, rows_after equal.
  4. Verify via the dashboard-api catalog action (table should now appear
     with rows).
NEXT
