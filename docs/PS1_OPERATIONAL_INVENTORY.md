# PS1 Operational Inventory

**CUBIC MARS Chicago (CTA-Ventra) · Problem Statement 1 — 3-day hardware failure prediction**
**Compiled 2026-08-11 · Updated 2026-08-15 with the measured serving contract · Evidence-marked throughout**

> **STATUS OF THE TEN QUESTIONS**
>
> | # | Question | State |
> |---|---|---|
> | 1 | SageMaker notebooks | **§1 — complete** |
> | 2 | S3 output locations | **§2 — complete**; live object freshness needs `ps1_aws_inventory.sh` |
> | 3 | Loaders / dispatchers / Lambdas / handlers | **§3 — complete** |
> | 4 | How data reaches RDS | **§4 — complete** |
> | 5 | RDS tables + schema + **row counts** | **§5 — schema complete; ROW COUNTS STILL NEED `tooling/sql/ps1_inventory_counts.sql`** |
> | 6 | Inference endpoints | **§6 + §14 — MEASURED 2026-08-15, complete** |
> | 7 | EventBridge rules | **§7 — from repo; live states need `ps1_aws_inventory.sh`** |
> | 8 | How ECR + EventBridge are used | **§8 — complete, DECISION-1 now settled** |
> | 9 | Issues since yesterday | **§9 — complete, 15 entries** |
> | 10 | Cleanup performed | **§10 — complete** |
>
> **Only two gaps remain, and both need one command each. See §12.**
>
> ---
>
> **THIS DOCUMENT IS THE PRIMARY PS1 REFERENCE.** Standing instruction from PK,
> 2026-08-15: consult it before answering any PS1 question, cite it, and **update
> it in the same session whenever anything it describes changes** — a reference
> that silently goes stale is worse than none, because people trust it.
>
> The maintenance protocol — what triggers an update, which section to change, and
> the rules for the edit — is in **`local-notes.md` §3A** at the repo root, which loads
> automatically at the start of every session. In short:
>
> - a script is run → replace the `[AWS-PENDING]` marker with the measured value
>   **and its date**, and commit the transcript to `tooling/out/`
> - a defect is found **or withdrawn** → §9 gets an entry either way; strike
>   withdrawn claims in place rather than deleting them
> - anything structural changes → mirror it into `skills/chicago-ps1/SKILL.md`
> - every claim keeps its evidence tag; never promote `[UNVERIFIED]` to
>   `[MEASURED]` without a transcript
> - add a dated subsection rather than rewriting history (§13 and §14 are the
>   pattern)

---

## 0. How to read this, and the two findings that matter most

Every claim carries a marker. Nothing is asserted without one.

| Marker | Meaning |
|---|---|
| **[READ]** | Read out of a file in this repository on 2026-08-11. File and line named. |
| **[MEASURED]** | Observed in live AWS via CloudShell during this programme. |
| **[AWS-PENDING]** | Only live AWS can answer. `tooling/ps1_aws_inventory.sh` and `tooling/sql/ps1_inventory_counts.sql` fill these. |
| **[UNVERIFIED]** | Not checked. Stated as hypothesis, never as fact. |

### The two things to read first

**Finding A — the dashboard reads two paths, and one of them is frozen.**
Path A (`ps1_cross_wired_daily`) is live and feeds `/ps1/predictions` plus the fourteen `/ps1/xw-*` routes. Path B (`ps1_failure_predictions`, `ps1_serial_predictions`, `ps1_inference_runs`) was switched off on 2026-08-10 at 11:55Z — but its **tables were not dropped, and six routes still read them** **[READ]**. Those panels have been serving data frozen at the shutdown ever since, and nothing on the screen says so. This is the single most important thing to know about the serving tier.

**Finding B — nothing produces a PS1 score for a date the notebooks did not train on.**
Established in `docs/PS1_DAILY_INFERENCE_DESIGN.md` §1 and summarised in §9 below. Cell 20 stores the held-out **test split**; Cell 24 exports exactly that. The prefix named `device_ps1_cross_wired_daily` holds one-off scores on training data — "daily" describes the grain, not the cadence **[READ]**.

---

## 1. SageMaker notebooks

All under `notebooks/ps1_failure_prediction/`. **[READ]**

### 1.1 Current — production

| Notebook | Bytes | Cells | Fleet | Role |
|---|---|---|---|---|
| `PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb` | 212,945 | 25 | GATE | **Production.** ETL → Spark ML → MLflow → endpoint → monitor → cross-wire |
| `PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb` | 217,170 | 25 | TVM | Production, same structure |
| `PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb` | 222,718 | 27 | VALIDATOR | Production. **Cell indices shift +2** — two extra leading markdown cells |
| `PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb` | 255,789 | 29 | GATE | Superseded pure-sklearn predecessor. **Only PS1 notebook that reads PS4 scored artefacts** |
| `PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb` | 14,688 | 8 | VALIDATOR | Utility — builds DEVICE_ID ↔ BUS_ID ↔ serial lookup. Writes local files only |
| `Chicago_PS1_Predictive_Failure.ipynb` | 26,604 | 19 | all three | Local demo. **7-day target `will_fail_7d`, not the production 3-day OOS label.** Reads a hardcoded Windows path. Not part of the pipeline |

The three production notebooks are near-identical ports. Shared config, all string literals **[READ, CELL 3]**:

```python
TARGET_COL          = "will_hardware_oos_3d"
SLA_TARGET_COL      = "will_fail_3d"          # chargeable SLA reference only
LABEL_HORIZON_DAYS  = 3
GOLD_VERSION        = 6
TRAIN_START         = "2023-07-01"
MAX_LOOKBACK_DAYS   = 90
ENABLE_FEATURE_STORE = True
MLFLOW_SERVER_ARN   = "arn:aws:sagemaker:us-east-1:170202974600:mlflow-tracking-server/cubic-mars-mlflow-server-dev"
```

Per-fleet differences:

| | GATE | TVM | VALIDATOR |
|---|---|---|---|
| `OUT_DIR` | `ps1_gate_oos_outputs` | `ps1_tvm_oos_outputs` | `ps1_validator_oos_outputs` |
| `FS_GROUP_NAME` | `ps1-chicago-device-features-dev` | `ps1-chicago-device-features-dev` | `chicago-ps1-3d-validator-failure-features` |
| Tap signal | gold `tap_event_daily` on the PS1 spine | silver `read_tap_device_daily` + `read_tap_daily` | same as TVM |

GATE's divergence is documented in its own code **[READ, CELL 7 L126]**: `# VALIDATOR uses silver.read_tap_device_daily (~1% GATE coverage).`

### 1.2 Cell map (GATE/TVM indices; **add +2 for VALIDATOR** from CELL 4 on)

| CELL | What it does | Class |
|---|---|---|
| 0–2 | Output capture, pip installs, imports | setup |
| **3** | **Configuration — every bucket, prefix, MLflow ARN, target** | config |
| 4 | Spark checkpoint load/save; lets you skip 6–12 | I/O |
| 5 | Spark session, feature lists, `spark_path()` s3→s3a | setup |
| 6 | Gold/silver reads; **builds the OOS label in-notebook** from `device_event_enriched` | ETL |
| 7 | Auxiliary reads (PS2/PS4/PS5/metric/mttr/lifecycle) + tap rolling | ETL |
| 8 | Joins, grain audit, materialise feature frame. **`FEATURE_COLS` computed here from available columns** | ETL |
| 9–10 | Temporal splits, feature pruning, Spark EDA | ETL |
| **11** | Spark ML baselines — LR, RF, GBT, XGB, LGB, CatBoost | **TRAIN** |
| **12** | Optuna HPT — Spark XGB / LGB / CatBoost | **TRAIN** |
| 13 | Threshold + quality gate + eval plots | eval |
| 14 | Legacy clean-label stub — **disabled** | dead |
| 15–16 | Feature Store create + seed online store, verify | feature store |
| 17–18 | Helpers; MLflow leaderboard via `search_runs` ranked by `test_ap` | eval |
| 19 | SHAP on the Spark tree champion | explain |
| **20** | Ranking metrics **and stores `_res["predictions"] = _test_pdf`** | score (test split) |
| **21** | MLflow model registration, `create_model_package` | **REGISTER** |
| **22** | **Deploy endpoint — builds `model.tar.gz` and uploads it** | **DEPLOY** |
| 23 | SageMaker Model Quality Monitor baseline + schedule | monitor |
| **24** | **Cross-wired output at device + component grain → S3 parquet** | **EXPORT** |

### 1.3 Archive — 13 notebooks, `notebooks/ps1_failure_prediction/archive/` **[READ]**

Six Spark `_v3` / `Improved_Baseline` ports (GATE/TVM/VALIDATOR × OOS-label and SLA-label variants), three Databricks-native `*_Failure_Prediction` notebooks, `PS1_Evaluation_Fix.ipynb`, `PS1_3d_TVM_Improved_Baseline.ipynb`, `PS1_SageMaker_MLflow_FeatureStore.ipynb` (the original 7-day single-notebook version the three production ones were split from), plus `PS1_GATE_Runbook.md` and `archive/scripts/` (MLflow purge/restore, teardown).

None of the six `_v3` notebooks writes to S3 — each writes to a local `model_artifacts/ps1_<fleet>_<label>_v3` directory **[READ]**.

> **[UNVERIFIED]** `archive/PS1_Evaluation_Fix.ipynb` — its own header says *"Best models saved to S3 via boto3"* and cell 35 is `# CELL 19 — Quality gate check then save best models to S3`. Its S3 writes were not enumerated before the bridge dropped. It is archive, so low risk, but it is not "clean" — it is unchecked.

### 1.4 Databricks jobs that PS1 depends on

| File | Role |
|---|---|
| `notebooks/cross_wired_daily_job.py` (456 lines) | **The daily assembler.** Builds device × component × day rows from Gold, joins PS1 scores, writes `chicago/cross_wired/daily/asof=<date>` + a completion manifest |
| `notebooks/run_layer_gold.py`, `export_gold_to_s3.py`, `export_silver_to_s3.py`, `run_layer_silver.py`, `validate_tables.py` | Medallion build and export. Upstream of everything above |

---

## 2. S3 output locations

Resolved values, all from CELL 3 **[READ]**:

| Variable | Value |
|---|---|
| `ARTIFACT_BUCKET` | `cubic-mars-pm-s3-datalake-dev-artifacts-170202974600` |
| `GOLD_BUCKET` / `BUCKET` | `cubic-mars-pm-s3-datalake-dev-gold-170202974600` |
| `SILVER_PREFIX` | `chicago/silver` |
| `GOLD_BASE` | `chicago/gold` |
| `PS1_XW_S3_PREFIX` | `chicago/device_ps1_cross_wired_daily` |

> **Note, because it looks like a mistake and is not:** silver *and* gold both live in the bucket named `...-gold-...`. `S3_SILVER` is constructed from `GOLD_BUCKET` **[READ, CELL 3 L59]**. That is what the code says.

### 2.1 WRITES

| # | S3 URI | Written by | Format | What it is |
|---|---|---|---|---|
| **W1** | `s3://…-artifacts-…/chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}/` | CELL 24 | parquet | **The dashboard feed.** Per-fleet, device × component grain. Consumed by `cubic-mars-ps1-xw-loader` |
| **W2** | `s3://…-artifacts-…/sagemaker/ps1-3d/{tag}/spark-model-v{MLFLOW_VERSION}/model.tar.gz` | CELL 22 | tar.gz | **What the endpoints load.** Contains the native booster, `ps1_{tag}_meta.joblib`, `ps1_{tag}_threshold.joblib`, generated `inference.py` |
| **W3** | `s3://…-artifacts-…/feature-store/{FS_GROUP_NAME}` | CELL 15 | Iceberg/parquet | Feature Store offline store. Two groups — GATE+TVM share one, VALIDATOR has its own |
| **W4** | `s3://…-artifacts-…/sagemaker/ps1-3d/{tag}/mq-baseline/`, `…/mq-baseline/output/`, `…/mq-output/` | CELL 23 | csv + json | Model Quality Monitor baseline and results |
| **W5** | `s3://…-gold-…/chicago/cross_wired/daily/asof=<date>/` | `cross_wired_daily_job.py` L408–414 | parquet | The assembled cross-wire |
| **W6** | `s3://…-gold-…/chicago/cross_wired/manifest/asof=<date>/manifest.json` | same, L434–438 | json | **Completion manifest** — already a usable event trigger |

CELL 24's write **[READ, L159–169]**:
```python
ps1_xw_base = PS1_XW_S3
for _cat in sorted(comp_preds_df["device_category"].dropna().unique()):
    _slug = str(_cat).strip().lower()
    _cat_df.to_parquet(f"{ps1_xw_base}/{_slug}/", index=False, storage_options=S3_OPTS)
```

### 2.2 READS

Spark, via `S3_SILVER_RUNTIME` / `S3_GOLD_RUNTIME` — all parquet **[READ, CELLS 6–7]**:

`chicago/silver/`: `dim_device`, `device_event_enriched`, `metric_daily`, `device_mttr`, `usage_lifecycle_daily`, `hw_config_current`, and (TVM + VALIDATOR only) `read_tap_device_daily`, `read_tap_daily`
`chicago/gold/`: `device_ps1_daily`, `device_ps2_chains`, `device_ps4_hourly`, `device_ps5_component`

CELL 24 additionally re-reads four of these through **pandas**: `hw_config_current`, `dim_device`, `device_ps2_chains`, `device_ps5_component`.

`PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb` uniquely reads `s3://…-artifacts-…/chicago/ps4/scored/asof=*/{device_daily,anomalies,outliers}/`.

**Existence and freshness of every prefix: [AWS-PENDING]** — item 2 of `ps1_aws_inventory.sh`.

> **[UNVERIFIED]** `tag` and `MLFLOW_VERSION` in W2/W4 are runtime values from the CELL 21 registry response. The literal deployed key is `spark-model-v1/model.tar.gz` **[MEASURED]**, so `MLFLOW_VERSION` resolved to `1` for the deployed models — but I have not read the assignment.

---

## 3. Loaders, dispatchers, Lambdas and handlers

Fourteen Lambda directories under `api/lambda/`; three are PS1 **[READ]**.

> **There is no dispatcher Lambda.** "Dispatcher" appears only as a design-doc name — `docs/architecture/Cross_Wire_Databricks_Daily_Job_Column_Manifest.md:10`, `→ Dispatcher (Lambda / EventBridge)`. The implemented equivalent of that role is `cubic-mars-ps1-xw-loader`.

### 3.1 `cubic-mars-ps1-xw-loader` — **PATH A, LIVE**

| | |
|---|---|
| **Handler** | `handler.py`, `lambda_handler(event, context)` (L656) |
| **Reads** | `s3://{ARTIFACT_BUCKET}/{PS1_XW_PREFIX}/{gate,tvm,validator}` — extensionless keys (L225–226). Also Aurora `ml_batch_load_audit` for prior ETags (L286) |
| **Writes** | Aurora `ps1_cross_wired_daily` (DELETE by city+device_type then insert, L759), `ml_batch_load_audit` (L354, **outside the transaction** so failed loads still leave lineage) |
| **Trigger** | EventBridge `cubic-mars-ps1-xw-daily-load`, `cron(40 6 * * ? *)`, `--state ENABLED` (deploy.sh L196–197) |
| **Kill-switch** | **None.** Safety is behavioural instead |
| **Actions** | `load` (default), plus read-only `freshness`, `verify`, `audit`, `dry_run` |
| **Expected volume** | `EXPECTED = {"gate":107110, "tvm":184483, "validator":494932, "total":786525}` (L63) |

Its safety property is worth stating: `raise ValueError("0 rows shaped -- refusing to delete existing rows for an empty load")` **[READ, ~L753]**. An empty export cannot wipe the table. That is the right shape — the loader refuses rather than silently succeeding on nothing.

### 3.2 `cubic-mars-ps1-rds-push` — **PATH B, FROZEN**

| | |
|---|---|
| **Handler** | `handler.py`, `lambda_handler` (L401) |
| **Reads** | `s3://{GOLD_BUCKET}/chicago/gold/device_ps1_cross_wired_daily` — **different bucket and prefix from Path A** |
| **Writes** | `ps1_inference_runs`, `ps1_failure_predictions`, `ps1_serial_predictions`, `ml_batch_load_audit` |
| **Trigger** | Three wired, all off: EventBridge `cubic-mars-ps1-daily-push` `cron(15 6 * * ? *)` `--state DISABLED`; S3 ObjectCreated **not armed**; manual invoke |

**Four independent locks** **[READ, deploy.sh]**:

1. `PATHB_REVIVE != 1` → the deploy script `exit 2`s before any AWS call (L32–51)
2. `RULE_STATE=DISABLED` unless `PATHB_ENABLE_SCHEDULE=1` (L218–219)
3. `PATHB_ARM_S3_TRIGGER` gates the S3 notification (L253)
4. Reserved concurrency 0, set out-of-band and **deliberately not managed by the script** (L272–278)

The reason is recorded in the script itself (L13–15): *"On 10-Aug-2026 at 11:55Z the PS1 legacy path was deliberately switched off"*, and (L48–49) *"Path B has never once held all three fleets and holds zero GATE rows."*

> Lock 4 is asserted **only in a comment**. `deploy.sh` neither sets nor reads it. Whether reserved concurrency is actually 0 right now is **[AWS-PENDING]**.

### 3.3 `cubic-mars-dashboard-api`

Single VPC Lambda behind one HTTP API Gateway (`$default` catch-all), serving every PS1–PS5 route plus admin actions (`migrate`, `apply_sql`, `purge`, `catalog`, `inspect`, `recreate`, `load_run`). Route bodies also come from `ps2_v25_routes.py` and `ps3_v25_routes.py`; DDL from `sql/` **[READ]**.

Guards: `MUTATION_TOKEN`, `MAX_PAYLOAD_BYTES` (16384), `STAGE_QUOTA_PER_HR` (200), `D360_PARALLEL` (off by default), `D360_POOL_SIZE` (6).

### 3.4 The 36 `/ps1*` routes

Count verified exactly: `grep -oE '"/ps1[a-zA-Z0-9/_-]*"' handler.py | sort -u | wc -l` → **36** **[READ]**.

> **Only one route is method-gated.** `route()` tests `method` for just three paths repo-wide, one of which is PS1: `/ps1/servicenow-stage` (POST). The other 35 answer on **any verb** — there is no method check. That is not a defect today (API Gateway is a catch-all and the handlers are read-only), but it means a `DELETE /ps1/summary` returns 200 with a scorecard.

**Path A — live data (15 routes):** `/ps1/predictions`, `/ps1/xw-summary`, `/ps1/xw-performance`, `/ps1/xw-tiers`, `/ps1/xw-drivers`, `/ps1/xw-causation`, `/ps1/xw-facility`, `/ps1/xw-device-drivers`, `/ps1/xw-base-rate`, `/ps1/xw-performance-onset`, `/ps1/xw-chronic`, `/ps1/xw-flag-reason`, `/ps1/xw-state-mix`, `/ps1/xw-act-now`, `/ps1/table-status`

**Path B — frozen since 10-Aug (6 routes):** `/ps1/serial-predictions`, `/ps1/runs`, `/ps1/crosstab`, `/ps1/coverage`, `/ps1/model-performance` (partly), `/ps1/load-audit`

**sql/load 26-Jul seed data (7 routes):** `/ps1/summary`, `/ps1/leaderboard`, `/ps1/features`, `/ps1/feature-importance`, `/ps1/confusion`, `/ps1/risk-bands`, `/ps1/threshold-sweep`, `/ps1/calibration`

**Dimension / other (8):** `/ps1/facilities`, `/ps1/station-summary`, `/ps1/explainability`, `/ps1/risk-trend`, `/ps1/device-360`, `/ps1/servicenow-stage`, `/ps1/servicenow-staged`

### 3.5 A registry defect worth fixing

`docs/reference/loader_to_target_map.json` **[READ]**:

```json
"cubic-mars-ps1-xw-loader": {
  "bucket": ["cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"],
  "prefix": ["chicago/device_ps1_cross_wired_daily"],
  "targets": [],          <-- ZERO TARGETS
  "n": 0
}
```

The live Path-A loader is registered as writing **nothing**, while the retired Path B lists three targets. The code demonstrably writes `ps1_cross_wired_daily` (L759) and `ml_batch_load_audit` (L354). Anyone treating this JSON as authoritative would conclude Path B is the only PS1 writer — the exact inverse of the truth. `docs/reference/endpoint_to_table_xref.json` is likewise incomplete: 32 of the 36 routes, missing `/ps1/coverage`, `/ps1/facilities`, `/ps1/device-360`, `/ps1/servicenow-stage`.

**Treat both registries as advisory. The source is authoritative.**

---

## 4. How data reaches RDS

### Path A — LIVE

```
SageMaker PS1 notebooks (CELL 24, gate / tvm / validator)
  └─> s3://…-artifacts-…/chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}   [parquet]
        └─> EventBridge  cubic-mars-ps1-xw-daily-load   cron(40 6 * * ? *)   ENABLED
              └─> Lambda  cubic-mars-ps1-xw-loader   (in the RDS VPC)
                    ├─> Aurora  ps1_cross_wired_daily     DELETE by (city, device_type) + insert
                    └─> Aurora  ml_batch_load_audit       lineage, written outside the txn
                          └─> views v_ps1_predictions_xw + v_ps1_xw_*
                                └─> cubic-mars-dashboard-api  →  15 routes
```

IAM is scoped to exactly this prefix **[READ, deploy.sh L53–58]**: `"Resource":"arn:aws:s3:::$ARTIFACT_BUCKET/$PS1_XW_PREFIX/*"`.

### Path B — FROZEN

```
(gold export)
  └─> s3://…-gold-…/chicago/gold/device_ps1_cross_wired_daily   [parquet]
        ├─ EventBridge cubic-mars-ps1-daily-push  cron(15 6 * * ? *)   DISABLED
        ├─ S3 ObjectCreated on chicago/gold/device_ps1_cross_wired     NOT ARMED
        └─ Lambda cubic-mars-ps1-rds-push   reserved concurrency 0
              └─> ps1_inference_runs / ps1_failure_predictions /
                  ps1_serial_predictions / ml_batch_load_audit
```

### Associated services

| Service | Role |
|---|---|
| **Aurora PostgreSQL 16.4** | The serving database. Reachable only through CloudShell via VPC Lambdas |
| **Secrets Manager** | `RDS_SECRET_ID` / `SECRET_ARN` supply DB credentials to every VPC Lambda |
| **API Gateway (HTTP API)** | `$default` catch-all → `cubic-mars-dashboard-api` |
| **EventBridge** | Both loader schedules |
| **CloudWatch** | Lambda + endpoint metrics; the only place invocation counts exist |
| **SageMaker Feature Store** | Two groups; online store seeded at CELL 15 |
| **SageMaker Model Registry** | `create_model_package` at CELL 21 |
| **MLflow (SageMaker managed)** | `cubic-mars-mlflow-server-dev` |
| **S3** | Two buckets — artifacts and gold |

**This is the bit that matters:** the dashboard reads **both** paths. The `xw-*` routes and `/ps1/predictions` are live; the scorecard, serial, runs, crosstab and coverage routes read tables frozen at the 10-Aug shutdown. **A user cannot tell the two apart from the screen.**

---

## 5. Aurora tables and schemas

Reconstructed by applying each `CREATE TABLE` then every subsequent `ALTER` in file-number order, from migration text **[READ]**. **Row counts are [AWS-PENDING]** — run `tooling/sql/ps1_inventory_counts.sql`.

Prerequisite: `CREATE TYPE city_code AS ENUM ('CHI','BOS','LAX','TOC');` (`sql/01:45`).

### 5.1 `ps1_cross_wired_daily` — the live table (40 columns, `sql/34:78`)

```sql
xw_id BIGSERIAL PRIMARY KEY,
city_id city_code NOT NULL REFERENCES cities(id),
device_type VARCHAR(12) NOT NULL,        -- GATE | TVM | VALIDATOR
device_key VARCHAR(64) NOT NULL,         -- SCD2 surrogate
device_id VARCHAR(40),                   -- stable business id; JOIN ON THIS
component_serial_nbr VARCHAR(64),        -- NULL for validators (63% of rows)
component_type VARCHAR(80), device_category VARCHAR(40),
facility_id VARCHAR(40), operator_id VARCHAR(40),
transit_day DATE NOT NULL, event_date DATE,
ps1_fail_prob NUMERIC(9,6), ps1_predicted SMALLINT,
threshold_used NUMERIC(9,6), ps1_risk_tier VARCHAR(12),
score NUMERIC(14,8), ps1_p95_hist NUMERIC(9,6), is_prob_anomaly BOOLEAN,
will_hardware_oos_3d SMALLINT,           -- THE LABEL
is_coordinated_station_failure BOOLEAN, is_major_station_event BOOLEAN,
station_devices_failed INT, days_healthy_before_chain NUMERIC(10,2),
avg_rolling_mttr_30d_min NUMERIC(12,3), avg_rolling_mttr_90d_min NUMERIC(12,3),
max_downtime_ever_min NUMERIC(14,3), total_failure_days_s28 INT,
last_failure_date_s28 DATE, component_age_days NUMERIC(10,2),
no_prior_failure_in_window BOOLEAN,
shap_feat1 VARCHAR(120), shap_val1 NUMERIC(14,8),
shap_feat2 VARCHAR(120), shap_val2 NUMERIC(14,8),
shap_feat3 VARCHAR(120), shap_val3 NUMERIC(14,8),
extra JSONB, asof_date DATE NOT NULL, run_id VARCHAR(64)
```

Seven indexes from `sql/34`, plus `sql/49`:
```sql
CREATE UNIQUE INDEX ux_ps1_cross_wired_daily_grain
  ON ps1_cross_wired_daily
  (city_id, device_type, device_key, component_serial_nbr, transit_day)
  NULLS NOT DISTINCT;
```
`NULLS NOT DISTINCT` is load-bearing — without it a plain UNIQUE constrains nothing for the 63% of rows where `component_serial_nbr` is NULL. sql/49 guards the create with a `DO $$` pre-flight that RAISEs if `v_ps1_xw_grain` finds duplicates.

### 5.2 `ps1_model_performance` — the scorecard (27 columns)

`sql/11:30` creates 22; `sql/16:71–75` adds five. `sql/11:15` first does `DROP TABLE IF EXISTS ps1_model_performance, ps1_feature_importance CASCADE` — sql/11 deliberately re-owns these two.

```sql
-- sql/11 (22)
city_id city_code NOT NULL REFERENCES cities(id),
device_category VARCHAR(12) NOT NULL, model_name VARCHAR(60), algorithm VARCHAR(40),
train_auc/train_ap/train_f1, val_auc/val_ap/val_f1,
test_auc/test_ap/test_f1/test_prec/test_rec   NUMERIC(7,4),
decision_threshold NUMERIC(7,5), mlflow_version VARCHAR(16),
endpoint_name VARCHAR(80), n_features INT, quality_gate VARCHAR(8),
promoted BOOLEAN, computed_date DATE NOT NULL,
PRIMARY KEY (city_id, device_category, computed_date)

-- sql/16 (+5) -- the five that caused migrations 52, 53 and 54
target_col VARCHAR(32), label_revision VARCHAR(16),
recall_floor NUMERIC(6,4), base_rate_pct NUMERIC(6,2), run_id VARCHAR(48)
```

### 5.3 `ps1_inference_runs` (19 columns, `sql/16:30`)

```sql
city_id city_code NOT NULL, run_id VARCHAR(48) NOT NULL, run_ts TIMESTAMPTZ NOT NULL,
run_kind VARCHAR(16) NOT NULL DEFAULT 'batch_score',   -- train | batch_score
device_category VARCHAR(12) NOT NULL, scoring_date DATE,
endpoint_name VARCHAR(80), serving_image VARCHAR(200),
model_version VARCHAR(24), mlflow_version VARCHAR(16),
target_col VARCHAR(32), decision_threshold NUMERIC(7,5),
n_devices_scored INT, n_flagged INT, gold_snapshot_s3 VARCHAR(300),
status VARCHAR(16) NOT NULL DEFAULT 'running', error_text TEXT,
duration_s NUMERIC(10,2), computed_date DATE NOT NULL,
PRIMARY KEY (city_id, run_id, device_category)
```

> **There is no `label_revision` column here.** sql/16 added it to `ps1_model_performance` and `ps1_failure_summary` only. `sql/52` selected it from this table and failed live with `42703`. See §9.

### 5.4 The remaining tables

| Table | Migration | Columns | Holds |
|---|---|---|---|
| `ps1_failure_predictions` | `sql/11:18` + 5 ALTERs `sql/16:59–63` | 16 | Per-device scored snapshot (Path B) |
| `ps1_serial_predictions` | `sql/16:82` | 13 | Component-grain risk attribution (Path B) |
| `ps1_confusion` | `sql/11:103` | 7 | TP/FP/TN/FN per fleet |
| `ps1_feature_importance` | `sql/11:51` | 7 | Aggregate SHAP per fleet |
| `ps1_explainability` | `sql/11:59` | 6 | Per-prediction SHAP |
| `ps1_station_summary` | `sql/11:67` | 10 | Per-facility rollup |
| `ps1_risk_trend` | `sql/11:43` | 7 | Daily mean risk per fleet |
| `ps1_risk_bands` | `sql/11:83` | 6 | Tier histogram |
| `ps1_threshold_sweep` | `sql/11:89` | 8 | Precision/recall vs threshold |
| `ps1_calibration` | `sql/11:96` | 8 | Reliability bins |
| `ps1_leaderboard` | `sql/04:60` | 12 | Every algorithm tried per fleet |
| `ps1_features` | `sql/04:77` | 8 | Top SHAP drivers (13-Jul seed) |
| `ps1_failure_summary` | `sql/04:15` + `sql/16:77` | 39 | **RETIRED** — see below |
| `ml_batch_load_audit` | `sql/16:106` | 13 | Shared PS1/PS3 loader audit |
| `servicenow_staging` | `sql/13:2` | 8 | Staged incidents (never posted live) |

`ps1_failure_summary` was retired by `sql/51`, which **does not drop it, delete rows, or ALTER it** — because `migrate()` uses `CREATE TABLE IF NOT EXISTS` and would recreate it empty on the next deploy, and `/ps1/summary` still falls back to it. It carries a COMMENT instead: *"RETIRED 2026-08-10 … target='will_fail_3d', which is not a column in this system (the real label is will_hardware_oos_3d)."*

### 5.5 Views — 25 total

| View | Migration | Note |
|---|---|---|
| `v_ps1_xw_causation` | `34:219` → **DROP+CREATE `50`** | 13 cols; `sufficient_data` + `min_cell` appended. sql/34 dropped a fleet's row entirely when a cell was <30, making insufficiency indistinguishable from absence |
| `v_ps1_table_status` | `36:152` → **DROP+CREATE `51`** | 5 cols; `retired` appended; three supersession mappings corrected |
| `v_ps1_provenance_gaps` | `52:89` → **DROP+CREATE `53`** | 8 cols; `assessment` rewritten so `label_revision` reads as *derived*, not missing |
| `v_ps1_serving_gap` | `54:81` | **E-1 in the data layer.** Its own COMMENT says `serving_run_id` is a **PROXY** |
| `v_ps1_xw_device_state` / `_state_mix` / `_act_now` | `36` → **DROP+CREATE `48`** | Dropped in dependency order |
| `v_ps1_xw_summary` | `34:272` | 17 cols. A **row-count reconciliation**, not a scorecard |
| `v_ps1_predictions_xw` | `37:32` | Prediction feed; `DISTINCT ON (city_id, device_id)` |
| `v_ps1_xw_onset` / `_base_rate` / `_performance_onset` / `_spells` / `_chronic_devices` | `35` | Onset/spell framing |
| `v_ps1_shap_importance`, `v_ps1_device_drivers`, `v_ps1_xw_grain`, `v_ps1_xw_performance`, `v_ps1_xw_tier_calibration`, `v_ps1_xw_facility` | `34` | |
| `v_ps1_xw_flag_reason` | `36:31` | |
| `v_ps1_label_frame` | `48:105` | `evaluable_day = MAX(transit_day) - 3`, derived from data, never `CURRENT_DATE` |
| `v_ml_batch_freshness` | `16:128` | 36-hour staleness banner |

`sql/48`'s header records why the DROPs were needed **[READ]**: `CREATE OR REPLACE VIEW` raised **`42P16 cannot drop columns from view`**, and separately `MAX(varchar)` returns TEXT, giving `42P16 cannot change data type of view column "device_type" from text to character varying(12)` — fixed with `(array_agg(o.device_type))[1]::varchar(12)`.

### 5.6 Six views nothing reads

**[READ, verified by grep over `handler.py`]** — `v_ps1_serving_gap`, `v_ps1_provenance_gaps`, `v_ps1_label_frame`, `v_ps1_xw_onset`, `v_ps1_xw_grain`, `v_ps1_xw_spells`, `v_ps1_predictions_xw_coverage`, `v_ml_batch_freshness` appear in **no route**.

For most that is fine — they are intermediate. But `v_ps1_serving_gap` is the E-1 disclosure, deliberately put in the data layer by sql/54 *"where it cannot be lost to a handler bug"* — and today **nothing surfaces it**. A disclosure nobody reads is not a disclosure.

### 5.7 Migration ledger

**PS1 migrations:** `04` (failure_summary, leaderboard, features), `11` (10 serving tables), `13` (servicenow_staging), `14` (dim_station), `16` (batch lineage + 11 ADD COLUMNs), `34` (cross_wired + 8 views), `35` (onset framing), `36` (state framing), `37` (predictions_xw), `48` (evaluable day), `49` (unique index), `50` (causation all fleets), `51` (failure_summary retire), `52`/`53`/`54` (the provenance chain).

**Numbering gaps: 10, 12, 43 do not exist.** `sql/11`'s header references `sql/12_ps1_serving_backfill.sql` as its populating file — **that file is absent from the tree**. That is why ten serving tables were created and several were never populated.

### 5.8 The two `ps1`-named tables that are **not** PS1 tables **[READ, closed 2026-08-11]**

`sql/44_ps2_v25.sql` creates two tables whose names contain `ps1`. Both belong to **PS2 v2.5**, which evaluates its rebuilt label against PS1's. They are *about* PS1; they are not *of* PS1, and no PS1 route reads either.

| Table | Columns | PK | Holds |
|---|---|---|---|
| `ps2_v25_ps1_label_parity` (`:199`) | 28 | `(city_id, device_category)` | Agreement between PS2's rebuilt label and PS1's: `eligible/comparable/matching/mismatching_device_days`, three positive rates (`source_`, `rebuilt_`, `legacy_sla_`), `parity_rate`, `parity_status` |
| `ps2_v25_ps1_model_performance` (`:228`) | 32 | `(city_id, device_category)` | PS2's independent scoring of PS1 predictions: full confusion matrix, `precision`, `recall`, `specificity`, `f1_score`, `balanced_accuracy`, `brier_score`, `roc_auc`, `pr_auc`, `prediction_coverage` |

Both carry the v2.5 governance block (`run_id`, `run_ts_utc`, `notebook_version`, `quality_status`, `run_mode`, `run_disposition`, `output_scope`, `is_production`) — a provenance discipline PS1's own tables do not have.

> **Worth noting for later:** `ps2_v25_ps1_model_performance` is a **second, independent scoring of PS1** computed by a different team's notebook. If its `roc_auc` disagrees with `ps1_model_performance.test_auc`, that is a signal worth chasing rather than a duplicate to reconcile away. Nobody has compared them.

---

## 6. Inference endpoints

**The question asked about "ECR inference endpoints". The premise needs correcting: PS1's endpoints are not ECR-backed.**

| | |
|---|---|
| Endpoints | `chicago-ps1-3d-gate-failure-v1`, `chicago-ps1-3d-tvm-failure-v1`, `chicago-ps1-3d-validator-failure-v1` |
| Container | **AWS-managed SageMaker DLC** — not a custom image **[MEASURED]** |
| Model data | `s3://…-artifacts-…/sagemaker/ps1-3d/{tag}/spark-model-v1/model.tar.gz` **[MEASURED]** |
| Invocations, 14 days | **0** **[MEASURED]** |
| Created by | CELL 22 |

The archive is self-contained **[READ, CELL 22]** — native booster file, `ps1_{tag}_meta.joblib` (carrying `feature_cols` and `medians`), `ps1_{tag}_threshold.joblib`, and an `inference.py` generated inline from `_INF_TEMPLATE`.

**ECR status:** `cubic-pdm/mars-ps1` holds 2.38 GB and is referenced by **0 of 28 model package groups and 0 endpoints** **[MEASURED]**. `docker/Dockerfile.ps1` + `docker/inference_ps1.py` in the repo build a Flask `/ping` + `/invocations` BYOC image that **nothing deploys** — its `model_fn` globs `*_champion.joblib`, a filename the deployed bundle does not contain.

> `cubic-pdm/mars-ps3` (778 MB) **is** live — model package 14 references it, pinning the **mutable `:latest` tag** **[MEASURED]**. An image push silently changes what a registered model package resolves to. Real defect, PS3 scope.

**Zero invocations is expected** — the daily Chicago data has not arrived, so nothing has had cause to call them. But note precisely what `InService` proves: the model **loaded** at container start. `input_fn` and `predict_fn` run only on invocation, and there have been none. **Whether these endpoints can answer is untested in production.**

**Full live detail is no longer pending — it was measured on 2026-08-15. See §14.**

---

## 7. EventBridge rules

| Rule | Schedule | State | Target | Purpose |
|---|---|---|---|---|
| `cubic-mars-ps1-xw-daily-load` | `cron(40 6 * * ? *)` | **ENABLED** | `cubic-mars-ps1-xw-loader` | Path A daily load |
| `cubic-mars-ps1-daily-push` | `cron(15 6 * * ? *)` | **DISABLED** | `cubic-mars-ps1-rds-push` | Path B, retired 10-Aug |
| `cubic-mars-sfn-training-pipeline-dev` | `cron(0 2 * * ? *)` | ENABLED **[MEASURED]** | Step Functions | **Succeeds daily having launched zero training jobs** |

The wider schedule chain, measured and currently collision-free **[MEASURED]**:

```
05:45  dim loader
06:40  PS1-A  cubic-mars-ps1-xw-daily-load        <-- the live PS1 trigger
07:10  PS2
07:20  PS5
07:35  PS4
08:00  PS4-v3 (Mondays)
```

Path A's 06:40 slot is deliberate **[READ, deploy.sh L194]**: *"daily schedule (06:40 UTC, after the PS1 notebooks, before PS4 at 07:10)"*.

Live rule states: **[AWS-PENDING]** — item 7 of `ps1_aws_inventory.sh`.

---

## 8. How ECR and EventBridge are used, and what to expect

**ECR — used by nothing in PS1.** Expected result: none. `cubic-pdm/mars-ps1` is 2.38 GB of storage cost with no consumer. The managed DLC covers everything PS1 needs because the model bundle carries its own handler and its own feature contract. **Recommendation: retire, by lifecycle policy and a 30-day re-audit, not by delete.**

**EventBridge — used only as a clock.** Both PS1 rules are `cron(...)`. Neither is triggered by an event. Expected result today: at 06:40 UTC the loader reads three S3 objects and replaces `ps1_cross_wired_daily`.

**What a clock cannot express.** Cron encodes a *guess* about when upstream finished. If Chicago's data is late, the rule fires anyway, over stale S3, and succeeds. There is no rule anywhere that fires on completion, and no rule that fires on **absence** — so a run that never happens produces no signal at all. That gap, and the four rules that close it, are specified in `docs/PS1_DAILY_INFERENCE_DESIGN.md` §Q6–Q7 and built (created DISABLED) by `tooling/ps1_eventbridge_build.sh`.

---

## 9. Issues found since yesterday, and how each was resolved

| # | Issue | Root cause | Resolution | State |
|---|---|---|---|---|
| 1 | `/ps1/summary` returned `target=None`; `/ps1/model-performance` returned `serving_matches_scorecard=None` | The `sql/load` INSERT named 22 columns and omitted the five `sql/16` provenance columns. NULL since 26-Jul; only surfaced when routes first read them | `sql/52` backfills from `ps1_inference_runs` + policy constants | **Partly applied — 5 of 6 statements** |
| 2 | `sql/52` statement 1 failed live: `42703 column "label_revision" does not exist` | It selected `label_revision FROM ps1_inference_runs`. That column exists on `ps1_model_performance`, never on `ps1_inference_runs`. **The local test passed because the fixture was hand-written from what the table was assumed to contain.** A fixture built from an assumption cannot falsify it | `sql/53` — same goal, real schema; `label_revision` derived from `target_col` instead. **Rule adopted: build fixtures by extracting `CREATE TABLE` from the migration text** | **Applied 6/6** |
| 3 | `sql/53` made the API assert a **falsehood** — `serving_matches_scorecard = true` | It backfilled `run_id` from "the latest train-kind run per endpoint". Two training runs landed on 26-Jul (`ps1_sklearn_20260726` 12:00Z, `ps1_20260726` 16:40Z), so "latest" picked the Spark run — while the rows are the sklearn run. **A confident wrong value is worse than the honest NULL it replaced** | `sql/54` — discriminate on `mlflow_version`, which each load stamped into the row itself. Plus `v_ps1_serving_gap` so the disclosure lives in the data layer | **Applied 5/5** |
| 4 | The `partition_cols` finding was **wrong** | I grepped for a string, found it absent, inferred a cause without looking, then cited a code comment as evidence for the cause it asserted | **Refuted and struck through in place** in both documents | Corrected |
| 5 | Freshness verdict outvoted `UNREADABLE` | A majority of healthy fleets outweighed one unreadable source | `UNREADABLE` now **dominates** the verdict | Fixed |
| 6 | E-1 scenario C returned a silent `None` | An absent record produced no caveat — the same silence-vs-insufficiency failure as sql/50, eight hours later | Explicit caveat added | Fixed |
| 7 | `ps1_verify_state.sh` `[U9]` manufactured a false alarm | It windowed "last 3 days" and asserted `Invocations=0` for a function disabled 10-Aug 12:00Z. The window **spanned the disable**, so it reported a violation every time it ran correctly | Window now splits at the disable timestamp — BEFORE is history, only SINCE is an assertion | **Fixed today** |
| 8 | **Cell 24 exports the test split, not daily scores** | Cell 20 stores `_res["predictions"] = _test_pdf`. `cross_wired_daily_job.py` then filters `transit_day IN (target_days)` — on a new day, zero rows, no exception, no message, all-NULL predictions, job reports success | Documented; patch specified in `tooling/patches/cross_wired_daily_job_ps1_source.md` (raise on all-fleets-missing, `ps1_partial` in the manifest, loader refuses without `--allow-partial`) | **Found today — patch NOT yet applied** |
| 9 | **I asserted the wrong serving contract** | I read `docker/inference_ps1.py`, found a handler, and concluded it was *the* handler — without checking what builds the archive. Cell 22 generates its own inline. Two answers were wrong: the artefact type, and the reason for rejecting Batch Transform | Corrected in place, wrong reasoning left visible. **The tell each time: evidence something *could* be true, treated as evidence it *was*** | **Corrected today** |
| 10 | **C-3: `reindex(fill_value=0.0)`** | `_predict_proba` reindexes to `feature_cols` filling absent columns with `0.0`, while `_impute` only fills NaN for columns *already present*. A Gold column that fails to materialise is scored as **zero** — an ordinary value for a count or a rate — returning a confident, plausible, wrong probability | Specified as `DECISION-8`: assert column presence **before** serialising, and refuse | **Found today — not yet implemented** |
| 11 | Registry lists **zero targets** for the live loader | `loader_to_target_map.json` is stale | Documented §3.5 | **Open** |
| 12 | 871-byte Lambda drift called "worse than a skipped deploy" | The diff was entirely a docstring. Inference from a byte delta | Retracted | Corrected |

**The pattern running through 2, 3, 4, 6, 8, 9 and 10 is one thing: absence rendered as a value.** An empty `WHERE`, a missing column, a NULL replaced by a guess, a `fill_value=0.0`. Each time the system produced something plausible instead of admitting it had nothing.

---

## 10. Cleanup performed and pending

### Done

| # | Action | Reversible |
|---|---|---|
| 1 | Path B fully frozen — rule DISABLED, concurrency 0, S3 trigger unarmed, deploy script refuses without `PATHB_REVIVE=1` | yes |
| 2 | `ps1_failure_summary` retired via COMMENT — **not** dropped, because `migrate()` would recreate it empty and `/ps1/summary` still falls back to it | yes |
| 3 | `v_ps1_table_status` corrected — three supersession mappings were wrong; `v_ps1_xw_summary` is a row-count reconciliation, not a scorecard | yes |
| 4 | `v_ps1_xw_causation` publishes **all** fleets with `sufficient_data`, instead of dropping small-cell fleets silently | yes |
| 5 | Unique grain index with `NULLS NOT DISTINCT`, guarded by a duplicate pre-flight | yes |
| 6 | Provenance chain 52→53→54; scorecard now states its own run | yes |
| 7 | `[U9]` false alarm fixed | yes |
| 8 | Inventory snapshot moved to `tooling/out/`, tracked as evidence; only `*.log` ignored | yes |
| 9 | Schedule chain verified collision-free | n/a |
| 10 | Drift sweep: 9 MATCH, 2 benign DRIFT, 1 NO-REPO-SRC (`cubic-mars-ps2-rds-push`) | n/a |

### Pending — specified, not executed

| # | Action | Gate |
|---|---|---|
| 11 | Retire ECR `cubic-pdm/mars-ps1` | Lifecycle policy + 30-day re-audit. `ps1_cleanup.sh` blocks if a policy already exists or is unreadable |
| 12 | Delete 6 empty ECR repos | **List first.** Several belong to unfinished PS2–PS5 work. Behind `--delete-empty-repos` |
| 13 | Disable the 02:00 no-op training pipeline | Script refuses if the name pattern matches more than one rule |
| 14 | Delete the three endpoints | **Blocked until `DescribeEndpointConfig` is captured and committed.** It is the only artefact that can close E-1 |
| 15 | Apply the `cross_wired_daily_job.py` patch | Runs in Databricks; must be tested against a past date first |
| 16 | Implement the C-3 column assertion | Before any daily scoring runs |
| 17 | Fix `loader_to_target_map.json` | Trivial; open |
| 18 | Surface `v_ps1_serving_gap` in a route | Open — the disclosure exists and nothing reads it |
| 19 | Decide `VALIDATOR.recall_floor` | **Outstanding since 26-Jul. VALIDATOR is currently gated on nothing** |

---

## 11. What this document does not claim

- No row count here is live. Every count is `[AWS-PENDING]` or a figure quoted from a migration comment.
- It does not claim Path B's reserved concurrency is 0 — that is asserted only in a deploy-script comment; `deploy.sh` neither sets nor reads it.
- It does not claim the endpoints can answer an inference request. Zero invocations means the contract is untested in production.
- ~~It does not claim `sql/load/ps1_sklearn_20260726.sql`'s 22-column INSERT is fixed.~~ **[CLOSED — see §13.1. It is fixed on disk, and still unpushed.]**
- ~~It does not claim the archive notebooks are clean — `PS1_Evaluation_Fix.ipynb` writes to S3 and was not enumerated.~~ **[CLOSED — see §13.2. It writes to a third model location.]**
- The 954/429 device-population gap remains open and, per the standing constraint, does not go on the dashboard.

---

## 12. To fill the gaps

```bash
# 1. AWS — read-only, changes nothing. Fills items 2, 6, 7 and live Lambda config.
cd <repo-root>
bash tooling/ps1_aws_inventory.sh

# 2. Aurora — read-only, every statement a SELECT. Fills item 5.
#    Run it the same way sql/50 through sql/54 were run today.
#    tooling/sql/ps1_inventory_counts.sql
```

Paste both transcripts back and every `[AWS-PENDING]` marker gets replaced with a measured value.

---

## 13. Addendum — four gaps closed, 2026-08-11 **[READ]**

The first pass of this inventory left four items unread because the device bridge dropped. All four are now closed. Two were routine. Two changed something.

### 13.1 The upstream `sql/load` fix **is applied on disk** — and is still unpushed

The five-column omission that caused migrations 52, 53 and 54 has been fixed in the working tree. Both loaders now name the provenance columns:

```sql
INSERT INTO ps1_model_performance (
  city_id, device_category, model_name, algorithm,
  train_auc, …, test_rec, decision_threshold, mlflow_version, endpoint_name,
  n_features, quality_gate, promoted, computed_date,
  run_id, target_col, label_revision, recall_floor        -- <-- the fix
) VALUES
  ('CHI','TVM', …, '2026-07-26','ps1_sklearn_20260726','will_hardware_oos_3d','R7-1', 0.80),
  ('CHI','GATE',…, '2026-07-26','ps1_sklearn_20260726','will_hardware_oos_3d','R7-1', 0.70),
  ('CHI','VALIDATOR',…,'2026-07-26','ps1_sklearn_20260726','will_hardware_oos_3d','R7-1', NULL);
```

Three things this confirms:

1. **`base_rate_pct` is still deliberately not named** — correct. `/ps1/summary` derives it from `ps1_confusion`'s own TP/FP/TN/FN, and a derived figure from the run's real confusion matrix beats a second copy that can drift.
2. **`VALIDATOR.recall_floor` is `NULL` in the load itself**, not merely un-backfilled. It is a decision that has never been taken, now visible in the source rather than inferred from an empty column. Outstanding since 26-Jul; VALIDATOR is gated on nothing.
3. **`run_id` is now stamped by the load** — so a re-run reproduces the correct value and `sql/52`→`53`→`54` never needs to happen again. That is what makes the chain a one-off rather than a recurring repair.

`n_features` is `NULL` in the sklearn load but `67 / 67 / 43` in the Spark load — a real difference between the two runs, not an omission.

**This sits in commit `8fd0d8c`, which is still not pushed.** `git log --oneline -2` on the device confirms it is HEAD.

### 13.2 `PS1_Evaluation_Fix.ipynb` writes to a **third** model location

The archive notebook does write models to S3, and not where the other two write **[READ, cells 4 and 35]**:

```python
S3_BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"      # the GOLD bucket
save_to_s3(model_tvm, S3_BUCKET, f"{S3_MODEL_PREFIX}/tvm_lgb_fixed_{ts}.pkl",  "TVM-LGB-Fixed")
save_to_s3(lgb_gate,  S3_BUCKET, f"{S3_MODEL_PREFIX}/gate_lgb_fixed_{ts}.pkl", "GATE-LGB-Fixed")
s3.put_object(Bucket=S3_BUCKET, …)   # thresholds_{ts}.json
```

So PS1 model artefacts exist in **three** places, in two buckets and three formats:

| Location | Format | Written by | Status |
|---|---|---|---|
| `…-artifacts-…/sagemaker/ps1-3d/{tag}/spark-model-v1/model.tar.gz` | tar.gz bundle | CELL 22, production | **live — what the endpoints load** |
| `…-gold-…/{S3_MODEL_PREFIX}/{tvm,gate}_lgb_fixed_{ts}.pkl` + `thresholds_{ts}.json` | pickle + json | `PS1_Evaluation_Fix.ipynb`, archive | orphaned |
| local `model_artifacts/ps1_<fleet>_<label>_v3` | joblib | six `_v3` archive notebooks | never left the instance |

The pickles are timestamped, so there may be several generations. They are in the **gold** bucket, where nothing else about PS1 lives. Nothing references them. Add to the cleanup list — but **inventory before deleting**: these are the only surviving artefacts of the LGB evaluation line, and a `.pkl` written by an archive notebook is exactly the kind of thing someone turns out to have been depending on.

### 13.3 Gold layer producers — how `device_ps1_daily` gets made

**[READ]** `notebooks/run_layer_gold.py` builds five gold tables, `device_ps1_daily` first among them, and runs `device_ps1_daily__label_compare.sql` as part of the build. Its header records the ordering constraint: *"gold reads rebuilt silver tables (especially S17/S18/S20)"*.

**[READ]** `notebooks/export_gold_to_s3.py` then exports each table to `s3://<gold-bucket>/<prefix>/<table>/`, with `device_ps1_daily` explicit in its table list (L52).

So the full upstream chain is: `run_layer_silver` → `run_layer_gold` (builds `device_ps1_daily`) → `export_gold_to_s3` (lands it in S3) → the PS1 notebooks read it as their spine. **That is the chain whose completion should fire the daily scoring trigger** — see `docs/PS1_DAILY_INFERENCE_DESIGN.md` §Q6, `DECISION-3`.

### 13.4 What remained unread at the 11-Aug pass

Only one item, and it is low-value: per-file one-line descriptions for the ~30 non-PS1 migrations (`02, 05–09, 15, 17–20, 23–33, 38–42, 45–47`). Their filenames are known; only `26` and `46` contain `ALTER TABLE` statements. Nothing in PS1 depends on them.

---

## 14. The measured serving contract — 2026-08-15 **[MEASURED]**

Two read-only CloudShell runs (`ps1_read_docker_and_model.sh`, then `ps1_feature_contract.sh`) resolved item 6 completely and produced the first non-proxy evidence for E-1. Raw artefacts are committed at `tooling/out/`.

### 14.1 The endpoints — item 6 answered

All three, identical shape:

| | value |
|---|---|
| Image | `683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3` |
| Image owner | **AWS account 683313688378** — the managed DLC, not a CUBIC image |
| Status | `InService`, created 2026-07-24 |
| `SAGEMAKER_PROGRAM` | `inference.py` |
| `SAGEMAKER_SUBMIT_DIRECTORY` | `s3://sagemaker-us-east-1-170202974600/sagemaker-scikit-learn-2026-07-24-*/sourcedir.tar.gz` |
| Role | `arn:aws:iam::170202974600:role/cubic-mars-role-sagemaker-exe` |
| `SAGEMAKER_MODEL_SERVER_TIMEOUT` | 3600 |

**The question asked about "ECR inference endpoints". There are none for PS1.** The image is AWS's own managed container. `cubic-pdm/mars-ps1` (2.38 GB) is referenced by nothing. **`DECISION-1` is settled: ECR is not required for PS1 and that repository can be retired.**

**How xgboost gets into a scikit-learn container.** `sourcedir.tar.gz` carries a `requirements.txt`:

```
numpy
pandas
xgboost>=2.0
```

> **New risk, logged today:** `xgboost>=2.0` is unbounded. A container restart can install a newer xgboost against the same booster JSON. XGBoost does not guarantee identical output across major versions for a serialised model. **Pin it.**

**A question settled cleanly:** `inference.py` is **byte-identical** in `model.tar.gz` and in `sourcedir.tar.gz`, for all three fleets. For managed framework containers `SAGEMAKER_PROGRAM` resolves out of the submit directory, so there was a real possibility the handler being analysed was not the handler being run. It is the same file. No ambiguity.

### 14.2 The model bundle — what each archive contains

```
ps1_<fleet>_xgb.json          the booster
ps1_<fleet>_meta.joblib       feature_cols, medians, model_type, model_file, classifier
ps1_<fleet>_threshold.joblib  the operating threshold
requirements.txt
inference.py
```

| fleet | archive | features | medians | threshold | classifier |
|---|---|---|---|---|---|
| GATE | 788 KB | **47** | 47/47 | **0.12284049** | `SparkXGBClassifierModel` |
| TVM | 68 KB | **40** | 40/40 | **0.02648935** | `SparkXGBClassifierModel` |
| VALIDATOR | 124 KB | **40** | 40/40 | **0.39651793** | `SparkXGBClassifierModel` |

Machine-readable copies: `tooling/out/ps1_{gate,tvm,validator}_feature_contract.json`.

Feature-set structure: **14 features shared by all three fleets**; 13 unique to GATE (`gate_tap_*`, `gate_mech_events_*`, `csc_reader_events_*`), 16 unique to TVM (`printer_*`, `bankcard_*`, `bhu_*`, `chu_*`, `scrst_*`, `avg_mttr_*`), 14 unique to VALIDATOR (`tvm_read_*`, `mttr_*`). The fleets are genuinely different models, not one model applied three times.

### 14.3 E-1, quantified for the first time

`v_ps1_serving_gap` has only ever used a **proxy** — the latest train-kind row in `ps1_inference_runs` — and says so in its own `COMMENT`. This is the first direct observation of the deployed artefact.

| fleet | **deployed threshold** | scorecard `v2` (Spark) | scorecard `v3-sklearn` | **deployed n_features** | scorecard `v2` |
|---|---|---|---|---|---|
| GATE | **0.122840** | 0.1349 | 0.648467 | **47** | 67 |
| TVM | **0.026489** | 0.0255 | 0.494842 | **40** | 67 |
| VALIDATOR | **0.396518** | 0.4513 | 0.604953 | **40** | 43 |

**The deployed artefact matches neither scorecard row, for any fleet, on either axis.** `classifier = SparkXGBClassifierModel` confirms the lineage is Spark — exactly as `sql/load/ps1_sklearn_20260726.sql` warned in its `error_text`: *"the live endpoint still serves the Spark champion"*. But the threshold is merely *close* to the Spark row, and the feature count matches nothing at all.

Three readings are possible and this document does not choose between them **[UNVERIFIED]**:

1. The deployed models come from a Spark run whose numbers were never loaded into `ps1_model_performance`.
2. The loaded `v2` figures were transcribed from a different checkpoint of the same run than the one exported at CELL 22.
3. `n_features` in the scorecard counts candidate features while the bundle counts selected ones.

Whichever it is, the operational consequence is fixed and immediate:

> **GATE's endpoint fires at 0.1228. `/ps1/threshold-sweep` publishes 0.65.** Any count of "devices that will be flagged", any alert-rate estimate, any capacity plan derived from the published threshold is wrong — and wrong in the unsafe direction, since a much lower threshold flags far more devices.

**What still cannot be claimed:** that these endpoints have ever answered an inference request. `InService` proves `model_fn` ran at container start. With 0 invocations in 14 days, `input_fn` and `predict_fn` remain unexercised in production.

### 14.4 C-3 corrected, and reduced to one line

Medians are **complete** — 47/47, 40/40, 40/40. The earlier framing ("features with no median") described an empty set and is withdrawn.

The defect survives in a sharper form. In the deployed handler:

```python
def _impute(df, cols, medians):
    for c in cols:
        if c in out.columns and c in medians:      # ABSENT columns skipped
            out[c] = out[c].astype(float).fillna(medians[c])
...
X = _impute(df, cols, medians).reindex(columns=cols, fill_value=0.0)
```

A column *present* with NaN receives its median. A column **absent** is skipped by `_impute`, then `reindex` sets it to `0.0` — **while the correct median sits unused in the same bundle**. The cause is ordering, and the fix is one line:

```python
X = _impute(df.reindex(columns=cols), cols, medians).astype(float).values
```

Reindex first, so absent columns arrive as `NaN` and `_impute` fills them properly. `DECISION-8`'s presence assertion still applies on top — silently substituting a median for an entire missing feature is its own failure mode, just a less severe one than substituting zero.

`notebooks/ps1_batch_score_daily.py` implements both the fix and the assertion.

### 14.5 A bug in the audit tool itself

`ps1_read_docker_and_model.sh` reported `VERDICT: NO ps1_*_meta.joblib in the archive` for GATE and VALIDATOR — printed two lines below a member listing containing `ps1_gate_meta.joblib`.

Cause: `set -o pipefail` combined with `tar -tzf … | grep -q`. `grep -q` exits at the first match, `tar` takes SIGPIPE and returns 141, `pipefail` propagates it, and the `&& HAS_META=1` never runs. A race — TVM's 68 KB archive finished listing before `grep` quit; GATE's 788 KB did not. Same archive shape, three different verdicts.

**It reported absence when it meant "I stopped looking" — in the script written to detect exactly that.** Fixed in `tooling/ps1_feature_contract.sh` by listing once into a variable and testing the variable: no pipe, no early exit, no race.

