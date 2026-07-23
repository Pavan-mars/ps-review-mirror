# PS5 v5.1 — device + serial RUL, Lambda scorer, RDS migration: what's built + how to ship it

**Event = any hardware OOS 'Set'** (`fault_state=Set` + `is_device_fault` + counted-as-OOS; commanded/maintenance OOS
excluded; chargeable gating removed). `event_def_version = 2026-07-23.v1`.

---

## 1. What was built (all verified in the sandbox)

| # | Artifact | What it does | Verified |
|---|---|---|---|
| 1 | `notebooks/PS5_Reliability_Survival_v5_1.ipynb` + `engine/ps5_reliability_engine_v51.py` | **Train step.** v5 gate **+** device-grain RUL, serial-grain reliability, slim param persistence, device state feed, RDS manifest — on the OOS-Set event | synthetic run: `[leak-check] PASS` ×3, device+serial feeds + params + state emitted, `[manifest] device feeds=3 serial feeds=3` |
| 2 | `lambda/ps5_daily_scorer.py` | **Serve step (Lambda).** Loads slim params + daily state, re-scores per-device **and** per-serial RUL **params-only (numpy, no sksurv)**, upserts to RDS | self-test: numpy-serve == sksurv-train, **corr 1.00000**, worst |Δ| 0.1 d (device), 0.0 d (serial) |
| 3 | `rds/06_phase1d_ps5_oos_set_reliability.sql` | **RDS migration.** Additive OOS-Set columns on the 05 device/serial/scoring-run tables + `ps5_event_definition` reference + `ps5_feature_alignment_audit` + latest-per-device/serial views | `sqlglot` postgres parse OK (22 stmts); every Lambda-written column exists in 05+06 |

**Train → serve split (answers "can it run in Lambda?"):** the notebook is the *train* step — full-history purged-CV
across Cox/Coxnet/RSF/GBSA/WeibullAFT — run it on **SageMaker or Databricks**, weekly. The Lambda is the *serve* step —
milliseconds, params-only. Lambda's 15-min timeout + 250 MB package limit rule out running the notebook there; you never
needed to.

---

## 2. Step-by-step to make it live

### Step 1 — run the notebook (SageMaker or Databricks), `SMOKE_TEST = False`
Open `PS5_Reliability_Survival_v5_1.ipynb`, run all. On the real `mars_dev.silver.device_failures` confirm, per type:
- the printed **filter line** matches your columns: `[event=hw_oos_set] device_failures N -> M episodes … filters: fault_state='Set', is_device_fault=TRUE, is_oos_event=TRUE, NOT is_commanded_oos, NOT is_maintenance_oos`
- **`[leak-check] … PASS`** (0 mismatches)
- **`[device] RUL scored …`** with a plausible median RUL (tens–hundreds of days, not decades) and **`[serial] … components …`**
- **`[manifest] device feeds=3 serial feeds=3`**

Outputs land under `PS5_reliability_v5_outputs/{tvm,gates,validators}/`. For **true per-serial event counts**, point
`CONFIG['COMPONENT_TABLE']` at a `gold.device_ps5_component` rebuilt on the OOS-Set event; otherwise the serial grain
attributes each device's OOS-Set count to its components from `hw_config_current` (the notebook prints which it used).

### Step 2 — upload params + state + feeds to S3
```
s3://<gold-bucket>/chicago/ps5/params/<type>_device_survival_params.json
s3://<gold-bucket>/chicago/ps5/params/<type>_serial_params.json
s3://<gold-bucket>/chicago/ps5/state/<type>_device_state.parquet
s3://<gold-bucket>/chicago/ps5/state/<type>_serial_reliability.parquet   # roster for the Lambda's serial re-score
```
(The notebook writes these locally + lists them in `ps5_rds_load_manifest.json`; upload that tree.)

### Step 3 — apply the RDS migration (additive, idempotent)
```
psql "$RDS_URL" -f rds/06_phase1d_ps5_oos_set_reliability.sql
# post-check:
psql "$RDS_URL" -c "\d ps5_reliability_estimates"          # expect days_since_hw_oos, roll_fail_30d, event_def_version, feature_asof_date
psql "$RDS_URL" -c "SELECT * FROM ps5_event_definition;"   # expect the 2026-07-23.v1 row
```
Runs after `05_phase1c`. Safe to re-run.

### Step 4 — deploy the Lambda `cubic-mars-ps5-daily-scorer`
- **Runtime:** Python 3.11. **Deps:** `numpy`, `pandas`, `pyarrow` (state parquet), `psycopg2-binary` — ship as a Lambda
  layer or container image (numpy+pandas+pyarrow exceed the 250 MB zip, so a **container image** is simplest). `boto3` is
  in the runtime. **No scikit-survival / scipy / sklearn** — that's the point.
- **Env vars:**
  ```
  PS5_PARAMS_BUCKET=<gold-bucket>   PS5_PARAMS_PREFIX=chicago/ps5/params   PS5_STATE_PREFIX=chicago/ps5/state
  PS5_CITY=CHI   PS5_DEVICE_TYPES=tvm,gates,validators   PS5_RDS_SECRET=cubic/rds/dashboard   PS5_WRITE_RDS=true
  ```
- **IAM:** `s3:GetObject` on the params/state prefixes, `secretsmanager:GetSecretValue` on the RDS secret, VPC access to
  Aurora. Stay within the project guardrails (dev acct **170202974600**, region **us-east-1**, SSO/OIDC, encrypt-by-default).
- **Backfill (day-0):** invoke once — it reads the uploaded state + params and upserts the first `as_of_date` rows.
  ```
  aws lambda invoke --function-name cubic-mars-ps5-daily-scorer --payload '{"asof":"2026-04-11"}' out.json
  ```
- **Schedule:** EventBridge daily (e.g. `cron(0 7 * * ? *)`). Each run appends one `as_of_date` per device/serial
  (the `05` tables are a time series; the latest-per-device views surface the newest).
- **Dry-run first:** set `PS5_WRITE_RDS=false` to score without writing; the return payload shows counts + median RUL.

### Step 5 — API route `/ps5/reliability` (dashboard-api Lambda)
Add a route that reads the new views and returns the shape the dashboard already expects
(`{event_definition, event_def_version, window, floor, devices:[…], serials:[…]}`):
```sql
SELECT * FROM v_ps5_reliability_oos_latest WHERE city_id = :city;   -- device grain
SELECT * FROM v_ps5_serial_oos_latest      WHERE city_id = :city;   -- serial grain
SELECT * FROM ps5_event_definition WHERE event_def_version = :v;    -- the chip
```
The dashboard's `apiPS5Reliability()` already targets `/ps5/reliability` with a mock fallback — once this route returns
200, the tab flips from SAMPLE to live automatically (the amber ribbon disappears).

### Step 6 — dashboard device + serial subtabs
With the route live, wire the **Device RUL** table (sortable: serial? no — device_id, type, facility, age, RUL median,
P10–P90, risk band, days-since-HW-OOS, fails/30d, overdue) and the **Serial** table (device, serial, component type,
age, risk tier, component RUL, overdue). This is the last piece; do it after Step 5 returns real rows (or now against
the labelled SAMPLE feed).

---

## 3. Config knobs (engine `CONFIG`)
- `HW_OOS_SET.require_chargeable=False` — any hardware OOS (True = chargeable subset only)
- `HW_OOS_SET.min_outage_min` — >0 drops transient self-recovering blips
- `EMIT_DEVICE_SERIAL=True` — score + persist device/serial grains
- `RUL_CAP_DAYS=3650` — display cap (guards degenerate Weibull scale; the handful of no-signal devices pin here)
- `RISK_TIERS` — serial risk_score (OOS-fails / age-day) → CRITICAL/HIGH/MEDIUM/LOW
- `COMPONENT_TABLE` / `SERIAL_SOURCE` — per-serial roster source
- `WINDOW_MODE=telemetry_era`, `TELEMETRY_START=2024-01-01`, `CINDEX_FLOOR=0.65`

## 4. Honest caveats
- **Serial event counts** are device-OOS-Set attributed to components unless `gold.device_ps5_component` is rebuilt on
  the OOS-Set event — a documented follow-up for true per-component counts.
- **RUL cap:** a few no-signal (healthy) devices display at the 3650-day cap; that's the intended "no near-term concern"
  ceiling, not the old #88 decade-RUL bug (which was ~36,000 days — gone via the telemetry-era window + hazard-based MRL).
- **Gate:** on synthetic the C-index passes easily (strong planted signal); the **real** run's C-index decides
  `gate_pass` / `data_quality_gate_passed`. Until it clears 0.65, the dashboard keeps the honest "below floor / SAMPLE"
  framing.
