# PS3 retired notebooks

Moved here 2026-08-18 — not part of the live daily pipeline. Nothing deleted; reversible via `git mv`.

## Still live (in `notebooks/ps3_root_cause_analysis/`)

### Path A — daily go-live (V26 dashboard)

| File | Role |
|---|---|
| `PS3_RootCause_Severity_SageMaker_Source_First_V26.ipynb` | Production two-head train/deploy |
| `PS3_V26_PRODUCTION.ipynb` | Production run wrapper |
| `databricks_daily/ps3_daily_incremental.py` | Daily gold MERGE |
| `databricks_daily/export_gold_ps3_incremental_to_s3.py` | S3 export for scorer |
| `databricks_daily/ps3_rds_writer.py` | RDS upsert after scoring |
| `databricks_daily/job_ps3_daily_workflow.json` | Reference job JSON |

### Path B — OOS redesign core

| File | Role |
|---|---|
| `PS3_01_OOS_Spine_Build.ipynb` | OOS spine build |
| `PS3_v2_OOS_Serial_SageMaker.ipynb` | OOS serial-grain training |
| `ps3_oos_spine.py`, `ps3_oos_engine_v2.py` | OOS spine engine (generated; builders retired) |
| `ps3_rc_features.py`, `ps3_rc_daily_score.py` | Daily root-cause scorer |
| `ps3_serial_grain.py` | Serial-grain helpers |

## Daily pipeline (elsewhere in repo)

| File | Role |
|---|---|
| `notebooks/ps3_gold_complete_event.py` | PutEvents trigger |
| `sagemaker/ps3/batch_transform_daily.py` | V26 batch scorer (Path A) |
| `databricks.yml` | `medallion_ps3_daily` job |

## Retired here — superseded versions (Step 1)

| File | Why |
|---|---|
| `PS3_RootCause_Severity_SageMaker_Source_First_V22–V25.ipynb` | Superseded by V26 |
| `PS3_RootCause_Severity_SageMaker.ipynb` | Early unversioned notebook |
| `PS3_RootCause_Severity_SageMaker_RDS_Dashboard_Ready.ipynb` | Pre-V26 dashboard path |
| `PS3_03_RootCause_Models.ipynb` | Superseded by `_v2` |
| `Chicago_PS3_Root_Cause.ipynb` | Early exploratory demo |
| `PS3_Q5_Q6_append_cells.py`, `PS3_Q6b_append_cell.py` | One-off notebook patch scripts |
| `PS3_reconcile_diagnostic.py`, `PS3_reconcile_nonblocking.py` | Ad-hoc reconcile utilities |
| `ps3_schema_dump.py` | One-off schema dump |

## Retired here — reference / analysis / builders (Step 2)

| File | Why |
|---|---|
| `PS3_02_Taxonomy_And_Gaps.ipynb` | Coverage analysis — not daily ops |
| `PS3_03_RootCause_Models_v2.ipynb` | Model exploration — not daily ops |
| `PS3_Calibration_Native_vs_ServiceNow.ipynb` | Calibration study |
| `PS3_SageMaker_MLflow_FeatureStore.ipynb` | Alternate MLflow training path (V26 is production) |
| `make_ps3_v2_engine.py`, `build_ps3_v2_notebook.py` | Regenerate v2 notebooks/engine; `ps3_oos_engine_v2.py` already materialised |
| `gold_device_ps3_incident_incremental.sql` | Human reference; driver uses `sql/gold/device_ps3_incident__create.sql` |

See [docs/PS3_GO_LIVE_GUIDE.md](../../docs/PS3_GO_LIVE_GUIDE.md).

## eventbridge_schedule_cron_alternative.json (retired 25-Aug-2026)
A 26-Jul "belt-and-suspenders" CRON trigger design for the PS3 daily chain. Retired
because (a) it references databricks/job_ps3_daily_workflow.json, which does not exist
on any branch - the companion never landed; (b) it creates the rule ENABLED, against
the DISABLED-first convention; and (c) the event-driven path it was a fallback for is
now DEPLOYED: state machine cubic-mars-ps3-daily-scoring + three DISABLED rules
(cubic-mars-ps3-gold-export-complete / -sfn-failed / -batch-failed), stood up 25-Aug.
Do not resurrect the cron path; the gold-complete event is the trigger of record.
