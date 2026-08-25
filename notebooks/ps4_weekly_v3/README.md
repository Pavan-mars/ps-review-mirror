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

## Environment contract (both PS4 producers)

Lifted 25-Aug-2026 so a Databricks job or the Boston port can retarget either
producer without editing code. Every default is the Chicago dev value that was
previously hard-coded: a run with no env set behaves exactly as before.
Covers this notebook and the daily feed
`notebooks/ps4_anomaly_detection/PS4_SageMaker_MLflow_FeatureStore_PySpark.ipynb`.

| Variable | Default | Consumed by |
|---|---|---|
| `PS4_ARTIFACT_BUCKET` | `cubic-mars-pm-s3-datalake-dev-artifacts-170202974600` | both producers |
| `PS4_GOLD_BUCKET` | `cubic-mars-pm-s3-datalake-dev-gold-170202974600` | daily feed only |
| `PS4_S3_PREFIX` | `chicago/ps4` | daily feed only |
| `PS4_ENABLE_S3_EXPORT` | `true` (accepts `true`/`false`) | daily feed only - gates its CELL 19 S3 export |
| `PS4_AS_OF_DATE` | daily feed: blank = derive from data; weekly v3: `2026-04-11` | both producers - the daily feed uses a set value verbatim as its `asof=` partition (validated as YYYY-MM-DD); the weekly v3 already read it |
| `PS4_CITY_CODE` | `CHI` | weekly v3 only |
| `PS4_CITY_NAME` | `chicago` | weekly v3 only |
| `PS4_GOLD_PREFIX` | `chicago/gold/device_ps4_hourly` | weekly v3 only |

Deliberately NOT env-configurable: `PIPELINE_VERSION` (`v3`) in this notebook.
It names the output family under `OUTPUT_ROOT`, and letting a job environment
change it would silently fork the dataset the loader and dashboards read -
bump it in code, in review. This notebook's `GOLD_BUCKET` also stays a
constant; only its prefix is lifted.
