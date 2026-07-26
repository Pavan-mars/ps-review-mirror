CONSOLIDATED DEPLOY - Model Performance fix + Phase 1 (Device-360 / Analyse / cross-PS) + staged ServiceNow
===========================================================================================================

WHAT'S IN THIS BUNDLE
  handler.py            Lambda for cubic-mars-dashboard-api. Adds/updates:
                          - /ps1/model-performance : now returns TOP-LEVEL test_auc/model_version/category/
                            accuracy + s3_metrics.test_acc  -> fixes the rich tab's "AVG AUC-ROC = NaN" and
                            the broken Model Performance sub-tab.
                          - /ps1/device-360?device_id=..&city=CHI : cross-PS aggregation for one device
                            (PS1 prediction+SHAP device-level; PS2 cascade+event-codes device-level;
                             PS3 severity + PS5 reliability category-level; PS4 anomaly device-level) +
                             a grounded recommendation + a staged ServiceNow payload.
                          - POST /ps1/servicenow-stage : stages a scheduled-maintenance incident (NO live post).
                          - /ps1/servicenow-staged : lists staged incidents.
  sql/01..09,11,13      Schema + backfills. NEW sql/13 = servicenow_staging table.
  deploy.sh             CloudShell deploy (update-function-code + in-VPC migrate).

RE-DEPLOY SAFETY (validated on real Postgres, double-migrate):
  - sql/11 no longer drops the scored PS1 tables (predictions/explainability/station/...); they are PRESERVED.
  - sql/09 no longer drops ps2_device_cascades; it is PRESERVED.
  - BEGIN/COMMIT wrappers stripped from all sql so one benign "already exists" cannot abort a whole file.
  - Proven: migrate #1 = 0 fail, migrate #2 (re-run) = 0 fail, seeded PS1 + ps2_device_cascades markers survive.
  Your existing 500 live predictions and PS2 device data will NOT be wiped by this re-deploy.

DEPLOY (CloudShell, us-east-1, signed in):
  rm -f ~/CUBIC_MARS_consolidated_deploy.zip           # only if a prior upload exists
  # Actions -> Upload file -> CUBIC_MARS_consolidated_deploy.zip
  unzip -o CUBIC_MARS_consolidated_deploy.zip -d cons && cd cons && bash deploy.sh
  # expect: sql/13 applied, others applied/tolerated, 0 real failures
SMOKE TEST:
  API=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
  curl "$API/ps1/model-performance?city=CHI"    # rows carry top-level test_auc now
  curl "$API/ps1/device-360?device_id=<one from /ps1/predictions>&city=CHI"

FRONTEND (already applied live to your running dashboard at D:\...\cubic-dashboards):
  - NEW  src/components/tabs/Device360Modal.jsx   (also copied here in dashboard_patch/ for git)
  - EDIT src/components/tabs/PS1FailurePredictionTab.jsx : Analyse button per predictions row -> Device-360 modal
  Just run `npm run dev` (the .env already has VITE_API_BASE_URL). No copy needed for the running app;
  the dashboard_patch/ copy is only if you also want to push these to the git repo.

-----------------------------------------------------------------------
PHASE-3 FULL STATION COVERAGE (dim_station)
-----------------------------------------------------------------------
The predictions table resolves station names via dim_station (by facility_id), falling
back to the PS2 device catalog (by device_id). dim_station ships seeded with 17 real
facilities from the PS2 run (45 North Park, 94 Midway, 40 Forest Glen, ...). To cover
EVERY facility, drop the authoritative Databricks export in and re-deploy:

1) In Databricks (mars_dev), run and export to CSV:
     SELECT DISTINCT CAST(FACILITY_ID AS STRING) AS facility_id,
            FACILITY_NAME AS station_name, OPERATOR_NAME AS operator
     FROM   mars_dev.silver.dim_device
     WHERE  FACILITY_ID IS NOT NULL AND FACILITY_NAME IS NOT NULL;
   (silver.dim_facility -> facid, facility_name works too.)
2) Save it as  sql/dim_station_seed.csv  inside this bundle (replace the header-only template).
   Accepted headers (case-insensitive): facility_id|facid, station_name|facility_name, operator|operator_name.
3) Re-run bash deploy.sh. The migrate step upserts every row into dim_station
   (ON CONFLICT DO UPDATE, source='databricks'); the migrate report shows dim_station_seed.csv {loaded: N}.
Result: all 500 predictions resolve to a real station name. No code change, no frontend change.
