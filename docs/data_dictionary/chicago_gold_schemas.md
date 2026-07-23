# Chicago Gold Schemas (snapshot 2026-07-17)

The 5 gold PS feature tables. Full column lists are in `mars_dev.audit.catalog_columns`
(`WHERE layer='gold'`) / `audit.catalog_docs` (`doc_name='chicago_catalog_gold.md'`).

| Gold table | Rows | Cols | Grain | Target / purpose |
|---|---|---|---|---|
| `device_ps1_daily` | 2,894,573 | 107 | (DEVICE_ID, transit_day) | `will_fail_{3,7,14}d` failure labels + rolling features; TVM/GATE/VALIDATOR |
| `device_ps2_chains` | 2,191,785 | 57 | (DEVICE_ID, transit_day) | fault-chain / cascade features + S27 station co-failure + S29 survival age |
| `device_ps3_incident` | 34,612 | 62 | (availability_event_id) | `failure_level` severity classification (TVM+GATE; VALIDATOR=0 incidents) |
| `device_ps4_hourly` | 30,411,781 | 49 | (DEVICE_ID, DEVICE_KEY, hour_bucket, transit_day) | `ensemble_anomaly_flag` (3-signal, adaptive 2sigma) |
| `device_ps5_component` | 11,718 | 56 | (DEVICE_ID, COMPONENT_SERIAL_NBR) | `days_to_failure` + `is_censored` (currently 100% censored - move to device-level S29) |

Key label/target columns: PS1 `will_fail_3d/7d/14d`, `is_chargeable`; PS3 `failure_level`, `failure_level_label`,
`derived_component_type`, `kpi_rule_id` (0% fill); PS4 `event_rate_anomaly`/`metric_anomaly`/`reject_rate_anomaly`,
`ensemble_anomaly_flag`; PS5 `days_to_failure`, `is_censored`, `component_age_days`.
Full column detail: `SELECT * FROM mars_dev.audit.catalog_columns WHERE layer='gold' AND table_name='device_ps1_daily' ORDER BY ordinal;`
