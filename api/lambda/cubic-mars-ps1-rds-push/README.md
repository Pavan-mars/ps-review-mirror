# cubic-mars-ps1-rds-push

**Notebook -> S3 -> Lambda -> RDS -> Lambda -> Dashboard**, the S3->RDS leg for PS1.

```
PS1_3d_{GATE,TVM,VALIDATOR}_SageMaker_MLflow_FeatureStore.ipynb  (CELL 24)
      writes s3://<gold>/chicago/gold/device_ps1_cross_wired_daily
                  |
                  |  EventBridge cron(15 6 * * ? *)   OR   S3 ObjectCreated
                  v
      cubic-mars-ps1-rds-push          <- this Lambda
                  |
                  v
      Aurora: ps1_inference_runs / ps1_failure_predictions /
              ps1_serial_predictions / ml_batch_load_audit
                  |
                  v
      cubic-mars-dashboard-api  ->  17 live /ps1/* routes  ->  React dashboard
```

## Deploy (AWS CloudShell, us-east-1)

```bash
cd api/lambda/cubic-mars-ps1-rds-push && bash deploy.sh
```

Creates the IAM role, reuses the dashboard-api security group and 3 private subnets,
attaches the **AWS SDK for pandas managed layer** for pyarrow (do not bundle the wheel —
it is ~90 MB), creates the function, the daily EventBridge rule and the S3 trigger.

## Invoke

```bash
# dry run - reports what WOULD load, writes nothing
aws lambda invoke --function-name cubic-mars-ps1-rds-push \
  --cli-binary-format raw-in-base64-out --payload '{"dry_run":true}' /tmp/o.json && cat /tmp/o.json

# real load, explicit date
aws lambda invoke --function-name cubic-mars-ps1-rds-push \
  --cli-binary-format raw-in-base64-out \
  --payload '{"computed_date":"2026-07-26"}' /tmp/o.json && cat /tmp/o.json
```

## Behaviour

- **Idempotent.** Each target is `DELETE`d for this (city, run_id / computed_date) then
  re-`INSERT`ed, so a re-delivered S3 event or a manual re-run is safe.
- **Never creates or alters tables.** They come from `sql/04`, `sql/11`, `sql/16`, applied
  by the dashboard-api's `action=migrate`. If a table is missing, this fails loudly rather
  than inventing a schema.
- **Grain.** Source is device x component x day. Device rows are deduped on
  (device_id, prediction_date) — the device probability is constant across a device's
  component rows, so the dedup is lossless.
- **Audit.** One `ml_batch_load_audit` row per target table, `ps_id='PS1'`, carrying every
  warning the run emitted.

## Two things it will tell you, loudly

1. **Label semantics.** PS1 trains on `will_hardware_oos_3d`. Chargeable events are a
   *subset* of OOS, assigned by contract rules **after** the physical event — so OOS is the
   correct physical target for maintenance, and the dashboard must say "hardware OOS risk",
   not "failure" or "SLA". `target_col` and `label_revision` are written on every row.
2. **Single-category source.** If the parquet contains only one `device_category`, the run
   warns. `CELL 24` uses `CATEGORY_RESULTS = {CATEGORY: {...}}` and `to_parquet` without
   `partition_cols`, so each notebook run **overwrites** the previous category's output.
   Fix in the notebooks with `partition_cols=["device_category"]` or one key per category.

## attribution_weight is a placeholder

`ps1_serial_predictions.attribution_weight` is an **equal split** (1/n components on that
device-day) because the notebooks compute no learned attribution. `serial_risk_score` is
therefore device risk re-expressed at finer grain. Do not present it as learned
per-component risk.
