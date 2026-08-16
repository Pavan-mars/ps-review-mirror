#!/usr/bin/env bash
# =====================================================================
# ps1_repo_cleanup.sh
#   Retire every PS1 asset not associated with the three production
#   notebooks. RUN FROM THE REPO ROOT.
#
#   bash tooling/ps1_repo_cleanup.sh            # DRY RUN (default)
#   bash tooling/ps1_repo_cleanup.sh --apply    # perform the git mv's
#
# PK's decision, 2026-08-15: the production set is exactly
#   PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb
#   PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb
#   PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb
#
# NOTHING IS DELETED. Everything moves to notebooks/_retired/ps1/ and stays
# in git history either way. Retirement is a statement of intent, not an
# act of destruction -- and it is reversible with a second git mv.
#
# TWO NOTEBOOKS ARE **NOT** RETIRED DESPITE NOT BEING IN THE THREE.
# Both are load-bearing in ways their filenames do not advertise. Read
# section 1 before overriding.
# =====================================================================
set -uo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

NB="notebooks/ps1_failure_prediction"
RET="notebooks/_retired/ps1"

if [ ! -d "$NB" ]; then
  echo "Run this from the repo root -- $NB not found. pwd=$(pwd)"; exit 2
fi

if [ "$APPLY" -eq 1 ]; then
  echo "########## APPLY -- git mv will be executed ##########"
else
  echo "########## DRY RUN -- nothing will move ##########"
  echo "########## re-run with --apply             ##########"
fi

# Everything here is a `git mv`, so it must run in PK's PowerShell / a real
# shell with git -- NOT through the remote file bridge, which leaves
# .git/*.lock files it cannot unlink. See local-notes.md section 0.5.
mv_it() {  # mv_it <src> <dest-dir> <reason>
  local src="$1" dest="$2" why="$3"
  if [ ! -e "$src" ]; then
    echo "    SKIP (absent): $src"; return
  fi
  echo
  echo "    $src"
  echo "      -> $dest/"
  echo "      why: $why"
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$dest"
    git mv "$src" "$dest/" 2>&1 | sed 's/^/        /' || {
      echo "        git mv failed -- falling back to plain mv"
      mv "$src" "$dest/"; }
  fi
}

step() { printf '\n\n===== %s =====\n' "$*"; }

# =====================================================================
step "1  KEEP -- not in the three, but DO NOT RETIRE"
# =====================================================================
cat <<'KEEP'
  PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb          **KEEP**

      It is not a model notebook -- it is the PRODUCER of
      validator_device_bus_serial_map.xlsx, which is the stated source of
      sql/load/bus_map_20260729.sql:

          "Source: validator_device_bus_serial_map.xlsx (4,218 devices,
           1,832 buses)."   -- sql/41_dim_device_bus.sql

      That file populates dim_device_bus, which is read by handler.py (a
      live dashboard route) and joined by cross_wired_daily_job.py for
      BUS_ID / bus_device_flag. Operations dispatch crews to a BUS, not a
      device id -- sql/41's own header says the lookup was manual before it.

      Retiring this notebook would freeze dim_device_bus permanently: the
      4,218 rows would survive, but nobody could regenerate them when the
      fleet changes. Reclassify it as a UTILITY, do not retire it.

  notebooks/cross_wired_daily_job.py                 **KEEP**
  notebooks/run_layer_gold.py                        **KEEP**
  notebooks/run_layer_silver.py                      **KEEP**
  notebooks/export_gold_to_s3.py                     **KEEP**
  notebooks/export_silver_to_s3.py                   **KEEP**

      Upstream and downstream plumbing, not PS1 model notebooks.
      run_layer_gold.py BUILDS device_ps1_daily -- the spine the three
      production notebooks read -- and is SHARED with PS2-PS5. Touching
      any of these breaks other problem statements.
KEEP

# =====================================================================
step "2  RETIRE -- superseded PS1 model notebooks"
# =====================================================================
mv_it "$NB/PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb" "$RET" \
  "superseded sklearn predecessor of the GATE production notebook. NOTE: it is the ONLY PS1 notebook that reads PS4 scored artefacts (chicago/ps4/scored/asof=*/). If a PS4->PS1 feature path is ever wanted, this file is the only record of how it was done -- which is why it is moved, not deleted."

mv_it "$NB/Chicago_PS1_Predictive_Failure.ipynb" "$RET" \
  "local demo. 7-day target will_fail_7d, NOT the production 3-day will_hardware_oos_3d, and reads a hardcoded Windows path. Never part of the pipeline."

# =====================================================================
step "3  RETIRE -- the whole archive tree (13 notebooks, 1.6 MB)"
# =====================================================================
echo "  Moving notebooks/ps1_failure_prediction/archive/ -> $RET/archive/"
echo
echo "  BEFORE YOU APPLY, one item in there has an external side effect:"
cat <<'EVAL'
      archive/PS1_Evaluation_Fix.ipynb writes MODELS TO S3 -- to the GOLD
      bucket, where nothing else about PS1 lives:

          s3://cubic-mars-pm-s3-datalake-dev-gold-.../{S3_MODEL_PREFIX}/
              tvm_lgb_fixed_<ts>.pkl
              gate_lgb_fixed_<ts>.pkl
              thresholds_<ts>.json

      They are timestamped, so there may be several generations, and nothing
      references them. Retiring the notebook is fine. But it is the only
      record of WHAT WROTE those objects -- so inventory them BEFORE the S3
      cleanup, or they become unattributable files nobody dares delete:

          aws s3 ls s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/ \
              --recursive | grep -E "_lgb_fixed_|thresholds_"

      archive/scripts/ps1_teardown_all.py TEARS DOWN PS1 (endpoints, MPGs,
      MLflow experiments). It stays in the archive, but never run it to
      "clean up" -- it is a full teardown, not a tidy.
EVAL
mv_it "$NB/archive" "$RET" \
  "13 superseded notebooks + the MLflow purge/restore/teardown scripts. History, not pipeline."

# =====================================================================
step "4  RETIRE -- the BYOC container that nothing deploys"
# =====================================================================
cat <<'DOCKER'
  docker/Dockerfile.ps1
  docker/inference_ps1.py
  docker/requirements.txt

      MEASURED 2026-08-15: all three endpoints run the AWS-managed
      sagemaker-scikit-learn:1.2-1-cpu-py3 DLC and load a self-contained
      model.tar.gz built by CELL 22. This docker/ tree builds a Flask
      /ping + /invocations BYOC image that NOTHING deploys -- its model_fn
      globs '*_champion.joblib', a filename the real bundle does not contain.

      It is the parallel path that was built and abandoned.
DOCKER
mv_it "docker/Dockerfile.ps1"    "_retired/docker" "BYOC image, never deployed"
mv_it "docker/inference_ps1.py"  "_retired/docker" "BYOC handler, never deployed"
mv_it "docker/requirements.txt"  "_retired/docker" "pins for the BYOC image only -- NOT in the serving path"

# =====================================================================
step "5  NOT A FILE MOVE -- the AWS-side retirements"
# =====================================================================
cat <<'AWS'
  These are in tooling/ps1_cleanup.sh (dry-run default). Run that separately.

    ECR cubic-pdm/mars-ps1        2.38 GB, referenced by 0/28 MPGs and 0
                                  endpoints. Lifecycle policy + 30-day
                                  re-audit, THEN delete.
                                  DO NOT touch cubic-pdm/mars-ps3 -- it is
                                  LIVE (model package 14) and pins :latest.

    3 real-time endpoints         Capture-gated. tooling/out/endpoint_capture/
                                  is now committed, so deletion is permitted.
                                  Delete the ENDPOINT only; keep the
                                  endpoint-config and model -- they are the
                                  record that closes E-1.

    02:00 training pipeline       Succeeds daily having launched zero
                                  training jobs. Disable the rule (reversible).

    Path B cubic-mars-ps1-rds-push
                                  Already frozen: rule DISABLED, reserved
                                  concurrency 0, S3 trigger unarmed.
                                  LEAVE AS IS until the six dashboard routes
                                  that still read its tables are repointed.
                                  Deleting the Lambda while routes read the
                                  tables changes nothing visible -- and that
                                  is exactly why it is easy to get wrong.

    Legacy S3 prefix              gold/chicago/gold/device_ps1_cross_wired_daily
                                  Still the third fallback source in
                                  cross_wired_daily_job.py. Keep until batch
                                  scoring has run clean for 14 days.

    Orphaned .pkl models          See section 3. Inventory first.
AWS

# =====================================================================
step "6  WHAT THE THREE NOTEBOOKS DEPEND ON -- do not tidy any of it"
# =====================================================================
cat <<'DEPS'
  silver: dim_device, device_event_enriched, metric_daily, device_mttr,
          usage_lifecycle_daily, hw_config_current,
          read_tap_device_daily + read_tap_daily (TVM/VALIDATOR only)
  gold:   device_ps1_daily, device_ps2_chains, device_ps4_hourly,
          device_ps5_component

  Every one is shared with PS2-PS5. The three production notebooks are
  CONSUMERS of the medallion layers, not owners of them.

  GATE reads gold tap_event_daily rather than silver read_tap_device_daily,
  deliberately -- CELL 7 L126: "VALIDATOR uses silver.read_tap_device_daily
  (~1% GATE coverage)." Do not "harmonise" that.
DEPS

echo
echo
echo "===== SUMMARY ====="
if [ "$APPLY" -eq 1 ]; then
  echo "Moves applied. Review with:  git status --short"
  echo "Then commit -- clear .git\\*.lock first if a bridge session touched git."
else
  echo "DRY RUN -- nothing moved."
  echo "Review the list, then: bash tooling/ps1_repo_cleanup.sh --apply"
fi
echo
echo "Retired to:  $RET/  and  _retired/docker/"
echo "Nothing deleted. Every move is reversible with a second git mv, and"
echo "everything remains in git history regardless."
