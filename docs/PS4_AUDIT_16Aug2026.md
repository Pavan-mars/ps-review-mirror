# PS4 — Complete Audit vs the Target Daily-Run Architecture

**16-Aug-2026.** Same framework as the PS1/PS2 audits. Tags: `[M]` measured live,
`[R]` repo, `[D]` decision, `[U]` unverified.

**Target (PK):** medallion-complete trigger -> daily scheduled notebook run once
Gold updates -> S3 -> Lambda -> RDS -> dashboard. PS4 (fault clustering / anomaly)
does not need per-event inference.

**Headline verdict:** loaders are the most automated of the five (two ENABLED
schedules including a weekly v3 cadence), the manifest discipline is exemplary —
but every notebook run is manual, no trigger exists, and the 29-Jul v2 additive
migration was delivered and never executed.

## (a) PS4 SageMaker notebooks

`notebooks/ps4*` `[R]`:

| Notebook / job | Role | Status |
|---|---|---|
| `ps4_fault_clustering/PS4_FaultClustering_{GATE,TVM,VALIDATOR}.ipynb` (+ `_PySpark` variants) | clustering per fleet, 6 notebooks | current; manual |
| `ps4_anomaly_detection/PS4_SageMaker_MLflow_FeatureStore.ipynb` (+ `_PySpark`) | SageMaker/MLflow family | kept |
| `ps4_anomaly_detection/Chicago_PS4_Anomaly_Detection.ipynb` | early analysis | kept |
| `ps4/ps4_device_daily_export.py`, `ps4/ps4_export_to_s3.py`, `ps4_anomaly/ps4_export_to_s3.py`, `ps4_fault_clustering/ps4_cluster_s3_export.py` | S3 export jobs | current |

## (b) S3 locations for PS4 outputs

Gold bucket `cubic-mars-pm-s3-datalake-dev-gold-170202974600` `[M 29-Jul CloudShell,
confirmed live — trust this over any notebook grep]`:

| Path | What |
|---|---|
| `chicago/ps4/clustering/<device_type>/asof=<date>/{assignments,cluster_summary}/` | daily as-of snapshots per device_type (tvm, gate, validator) |
| `chicago/ps4/clustering/manifest/asof=<date>/<device_type>_manifest.json` | run_id, champion_pipeline/run/silhouette, n_devices, n_clusters, and the EXACT paths — **always read paths from the manifest, never construct them** |
| `chicago/ps4` root | additional PS4 outputs |
| `chicago/ps4/v3` | v3 family — loader gates on `READY.json` |

Assignments columns include `will_fail_3d` joined from PS1. There is NO
anomaly_score, NO week_start/week_end, NO facility_id in what is actually written —
an earlier weekly_* schema assumption was wrong; do not resurrect it `[M 29-Jul]`.

## (c) EventBridge for PS4

| Rule | Schedule (UTC) | State |
|---|---|---|
| `ps4-rds-loader` daily load | cron(10 7 * * ? *) — 07:10 | ENABLED `[M 09-Aug]` |
| `ps4-v3-loader` weekly load | cron(0 8 ? * MON *) — Mon 08:00 | ENABLED `[M 09-Aug]` |
| Medallion-complete trigger / scheduled notebook run | — | DOES NOT EXIST |

## (d) Loaders, handlers, routes for RDS

| Component | Key facts |
|---|---|
| `cubic-mars-ps4-rds-loader` | manifest-driven; recomputes n_devices/n_clusters independently rather than trusting the manifest's own counts `[R]` |
| `cubic-mars-ps4-v3-loader` | reads `READY.json` before loading; weekly cadence |

Aurora tables + routes `[R, lineage doc]`: `ps4_cluster_assignments`,
`ps4_cluster_summary`, `ps4_cluster_profile`, `ps4_cluster_quality`,
`ps4_weekly_device_summary`, `ps4_weekly_alerts`, `ps4_weekly_timeline`,
`ps4_anomaly_alerts`, `ps4_anomaly_timeline`, `ps4_v3_runs`, `v_ps4_v3_current`,
`v_ps4_v3_table_status` — served by 12 `/ps4/*` routes (`weekly`, `weekly-alerts`,
`weekly-facility`, `weekly-timeline`, `weekly-persistent`, `cluster-profile`,
`cluster-quality`, `v3-status`, ...).

Open items specific to PS4:
- The 29-Jul **v2 additive migration** (`ps4_v2_additive_migration.sql` +
  `ps4_v2_backfill_loader.py` + CloudShell runner, targeting `ps4v2_*` tables) was
  delivered but **never run against live RDS** `[M 29-Jul memory — recheck before
  running]`; old `ps4_*` tables were left untouched by design;
  `cluster_profile_out` schema was incomplete at delivery.
- Tracker #52: verify the PS4 thresholds present in loaded results — still open.

## (e) PS4 elements on the dashboard

`V4PS4Overview.jsx` + `V4PS4Clusters.jsx` -> `/ps4/weekly*`, `/ps4/cluster-*`,
`/ps4/v3-status` `[R, 16-Aug grep]`. Location tab feeds from
`/ps4/weekly-facility`.

## Verdict vs target

| Target step | Reality | Verdict |
|---|---|---|
| Trigger on medallion-complete | none | MISSING |
| Daily scheduled notebook run | manual only; scheduling mechanism undecided (same decision as PS2/PS5) | MISSING + decision needed |
| Outputs to S3 | manifest-disciplined as-of snapshots, live-confirmed | RIGHT |
| Lambda -> RDS | two loaders, daily + weekly, ENABLED | RIGHT |
| Dashboard refresh | both PS4 tabs wired | RIGHT (content static until runs are scheduled) |
| Extra | ps4v2_* migration parked since 29-Jul; #52 threshold verification open | decide: run, revise, or retire |
