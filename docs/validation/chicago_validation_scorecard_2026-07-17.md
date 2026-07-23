# Chicago Validation Scorecard

Snapshot 2026-07-17. Live: `SELECT * FROM mars_dev.audit.catalog_validation`.
**Counts: 132 PASS · 1 WARN · 1 FAIL · 8 INFO.** (Row-count PASS for every table is in `catalog_facts.md`; below are the meaningful grain / cross-layer / model checks.)

## FAIL (1)
```
[FAIL] silver.incident_root_cause :: grain_unique :: 670 dup rows on (availability_event_id)
```
The silver table is not unique on `availability_event_id`. PS3 gold dedups via `QUALIFY ROW_NUMBER()` (PS3 = 0 dups), but S17 should apply the same dedup so the silver keystone is unique.

## WARN (1)
```
[WARN] bronze.edw_late_transaction_detail :: row_count :: 0 rows   (empty source; not used downstream)
```

## Grain-uniqueness (all PASS)
```
[PASS] gold.device_ps1_daily            0 dup on (DEVICE_ID, transit_day)
[PASS] gold.device_ps2_chains           0 dup on (DEVICE_ID, transit_day)
[PASS] gold.device_ps3_incident         0 dup on (availability_event_id)
[PASS] gold.device_ps4_hourly           0 dup on (DEVICE_ID, DEVICE_KEY, hour_bucket, transit_day)
[PASS] gold.device_ps5_component        0 dup on (DEVICE_ID, COMPONENT_SERIAL_NBR)
[PASS] silver.device_event_enriched     0 dup on (DW_DEVICE_EVENT_ID)
[PASS] silver.device_failures           0 dup on (DEVICE_KEY, device_category, failure_date)
[PASS] silver.device_mttr               0 dup on (DEVICE_KEY, failure_date)
[PASS] silver.device_survival_intervals 0 dup on (DEVICE_KEY, interval_start_date)
[PASS] silver.hw_config_current         0 dup on (DEVICE_KEY, COMPONENT_SERIAL_NBR)
[PASS] silver.incident_history          0 dup on (incident_number)
[PASS] silver.metric_daily              0 dup on (DEVICE_KEY, transit_day)
[PASS] silver.station_network_daily     0 dup on (FACILITY_ID, device_category, transit_day)
```

## Cross-layer reconciliation + model metrics (INFO)
```
[INFO] recon.dim_device.category_dist                 GATE=1382  OTHER=11997  VALIDATOR=4218  TVM=1019
[INFO] recon.device_failures.devices_by_cat           GATE=944   VALIDATOR=1535  TVM=469
[PASS] recon.ps1.avm_ghosts_in_spine                  0
[INFO] recon.ps1.label_positive_rate_3d               20.266%
[INFO] recon.ps3.rows_and_levels                      rows=34612  lvl2=18777
[INFO] recon.ps3.kpi_rule_id_fill                     0.0%
[INFO] recon.ps4.ensemble_rate                        1.05%
[INFO] recon.ps5.censoring_rate                       100.0%
[INFO] recon.silver.device_event_enriched_vs_bronze   silver=190874858  bronze=190874858
```
