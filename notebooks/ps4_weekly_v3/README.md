# PS4 weekly v3 pipeline - producer

`PS4_SageMaker_Anomaly_Clustering_Weekly_RDS_v3_gp.ipynb` is the SOLE producer of the
`chicago/ps4/v3` S3 family that the weekly dashboard tables are built from.

## Provenance
Recovered 19-Aug-2026. It existed only as untracked copies: `~/PS4/` in SageMaker Studio
space `default` (which has no git clone at all), and an untracked copy in the local
`cubic-aws/mars-pm-platform` clone under `data-engineering/Sage_maker/PS4/`. A repo-wide
grep of this repository on 19-Aug found no tracked code writing `chicago/ps4/v3`.
The committed file is the 219,346-byte executed copy, kept WITH outputs because those
outputs are the provenance: last run `ps4-20260728T213153Z-bf4609d4`, 28-Jul-2026.
A smaller 50,716-byte variant (same day, 03:01) remains in the platform clone.

## Contract
- `CITY_NAME=chicago`, `PIPELINE_VERSION=v3`
- Reads GOLD_BUCKET, writes ARTIFACT_BUCKET
  `cubic-mars-pm-s3-datalake-dev-artifacts-170202974600` under `chicago/ps4/v3/`
- `runs/run_id=<RUN_ID>/READY.json` is written LAST - it is the loader's gate
- `manifests/latest.json`
- `OUTPUT_DATASETS = [weekly_device_summary, weekly_alerts, weekly_timeline, cluster_profile]`

## Consumer
`api/lambda/cubic-mars-ps4-v3-loader/handler.py` (EventBridge, Mon 08:00 UTC), DDL at
`api/lambda/cubic-mars-dashboard-api/sql/38_ps4_weekly_v3.sql`. The Aurora table names
(`ps4_weekly_*`, `ps4_cluster_profile`) are applied loader-side; this notebook never
names them. The loader DATASETS map is a 1:1 match with OUTPUT_DATASETS above.

## Known gap - ps4_cluster_quality
This notebook does NOT produce `cluster_quality`. The loader derives it from
`find_silhouette(manifest)`; when the manifest publishes no silhouette it falls back to
hardcoded `SILHOUETTE_RUN_LOG` values stamped `quality_source='run_log'`, and emits its
own warning that those numbers are not the served run's. Fix at source: publish
silhouette into the manifest from this notebook.

## Scheduling
Not scheduled. Manual Studio runs only. Last run 28-Jul-2026, so the Monday loader has
been re-serving that snapshot ever since.
