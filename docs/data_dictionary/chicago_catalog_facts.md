# Chicago Data Catalog - Facts (all tables)

Snapshot 2026-07-17. Format: `layer.table | rows | cols | MB | files | [partitions] | date_col | min..max | stale`.
Live equivalent: `SELECT * FROM mars_dev.audit.catalog_facts ORDER BY layer, table_name`.

## bronze (85 tables)
```
_backfill_log | 12,089 | 15 | 0.1 | last_run_ts | 2026-06-08..2026-06-08 | stale=False
_ingest_control | 45 | 8 | 0.0 | last_run_ts | 2026-06-06..2026-06-08 | stale=False
_raw_load_log | 35 | 7 | 0.0 | last_run_ts | 2026-06-10..2026-06-10 | stale=False
_tunnel_ping_log | 1,372 | 6 | 0.0 | ping_ts | 2026-06-07..2026-06-11 | stale=False
cta_abp_use_tran_timing_data | 89,369,392 | 21 | 2214.3 | DW_INSERTED_DTM | 2015-12-15..2022-05-01 | stale=True
cta_availability_levels | 41 | 14 | INSERTED_DTM | 2012-11-04..2015-04-18 | stale=True
cta_kpi_agency_map | 98 | 14 | UPDATED_DTM | 2015-04-18..2023-06-23 | stale=True
cta_kpi_monthly_summary | 851 | 24 | MONTH_DTM | 2013-05-01..2015-03-01 | stale=True
cta_kpi_tvm_date_table | 19,864,800 | 15 | 57.7 | _raw_ingest_ts | 2026-06-15 | stale=False
cta_kpi_tvm_table | 445 | 14 | _ingest_ts | 2026-06-10 | stale=False
cta_kpi_variable_fee | 721 | 15 | MONTH_DTM | 2014-01-01..2026-03-01 | stale=True
cta_metrix_facility_map | 33 | 15 | _ingest_ts | 2026-06-10 | stale=False
cta_mm_daily_transaction_timing | 531,414 | 19 | 6.9 | POSTING_DAY_KEY | 2015-04-01..2015-04-30 | stale=True
cta_servicenow_availability_events | 375,578 | 67 | 37.5 | SN_U_END_DTM | 2017-02-15..2026-04-11 | stale=True
cta_servicenow_cmdb_ci | 42,959 | 108 | 2.5 | (no date) | stale=None
cta_servicenow_cmdb_model | 97,550 | 95 | 2.0 | (no date) | stale=None
cta_servicenow_cmdb_model_category | 426 | 21 | (no date) | stale=None
cta_servicenow_data_from_jumpbox | 604 | 37 | U_START_DTM | 2025-12-12..2026-04-11 | stale=True
cta_servicenow_incident | 278,001 | 44 | 74.9 | (no date) | stale=None
cta_sldc_monthly_summary | 13,376 | 26 | MONTH_DTM | 2013-09-01..2026-03-01 | stale=True
edw_abp_tap | 573,456,782 | 55 | 42347.3 | 477 files | TRANSACTION_DTM | 2024-01-01..2026-04-11 | stale=True
edw_activity_code_dimension | 189 | 8 | _ingest_ts | 2026-06-06 | stale=False
edw_aj_metric_fact | 1,032,003 | 24 | 29.9 | TRANSIT_DAY_KEY | 2015-04-16..2015-05-01 | stale=True
edw_availability_events | 752,510 | 28 | 27.2 | TRANSIT_DAY_KEY | 2013-02-28..2026-04-11 | stale=True
edw_availability_periods | 17 | 14 | START_DTM | 2013-08-01 | stale=True
edw_availability_relief | 2,117 | 10 | START_DTM | 0018-12-07..2026-04-08 | stale=True
edw_cashbox_event_dimension | 20 | 14 | _ingest_ts | 2026-06-10 | stale=False
edw_cashbox_type_dimension | 15 | 14 | _ingest_ts | 2026-06-10 | stale=False
edw_date_dimension | 72,814 | 32 | 3.9 | TRANSIT_DAY_BEGIN_DTM | 1900-01-02..2099-05-11 | stale=False
edw_device_current_hw_config | 11,736 | 9 | LAST_REPORTED_DTM | 2013-02-27..2026-04-11 | stale=True
edw_device_current_sw_config | 59,968 | 15 | 0.7 | LAST_REPORTED_DTM | 2013-01-24..2026-04-11 | stale=True
edw_device_current_tables | 103,924 | 16 | 5.4 | MSG_EFFECTIVE_DTM | 2013-02-22..2026-04-11 | stale=True
edw_device_dimension | 189,767 | 69 | 8.0 | INSERTED_DTM | 2012-09-21..2026-04-11 | stale=True
edw_device_event | 190,874,858 | 52 | 8962.1 | 230 files | EVENT_DAY_KEY | 2024-01-01..2026-04-11 | stale=True
edw_device_last_set_event | 300,411 | 12 | 10.9 | EVENT_DTM | 2024-01-01..2026-04-11 | stale=True
edw_device_last_state | 90,689 | 10 | 2.6 | EVENT_DTM | 2015-09-25..2026-04-11 | stale=True
edw_device_location_history | 253,438 | 19 | 4.0 | CHANGE_DTM | 2024-01-01..2026-04-11 | stale=True
edw_device_message_type_dimension | 55 | 20 | _ingest_ts | 2026-06-10 | stale=False
edw_device_metric | 643,679,282 | 22 | 15384.1 | 293 files | TRANSIT_DAY_KEY | 2017-10-29..2028-05-13 | stale=False
edw_event_type_dimension | 441 | 9 | _ingest_ts | 2026-06-06 | stale=False
edw_kpi | 60 | 16 | _ingest_ts | 2026-06-07 | stale=False
edw_kpi_detail_events_by_day | 148,661 | 28 | 2.3 | TRANSIT_DAY_KEY | 2024-01-01..2026-04-11 | stale=True
edw_kpi_rules | 123 | 17 | INSERTED_DTM | 2012-11-04..2025-02-06 | stale=True
edw_kpi_summary_by_day | 254,885 | 8 | 1.1 | TRANSIT_DAY_KEY | 2013-02-01..2026-04-19 | stale=True
edw_kpi_target | 236 | 9 | _ingest_ts | 2026-06-06 | stale=False
edw_late_transaction_detail | 0 | 22 | POSTING_DAY_KEY | (empty) | stale=None
edw_metric_dimension | 62 | 18 | _ingest_ts | 2026-06-10 | stale=False
edw_metric_summary_by_day | 17,863,366 | 10 | 214.6 | TRANSIT_DAY_KEY | 2023-12-31..2026-04-11 | stale=True
edw_read_transaction | 12,443,504 | 118 | 1106.3 | TRANSIT_DAY_KEY | 2013-08-06..2032-12-14 | stale=False
edw_stop_point_dimension | 12,888 | 35 | 0.7 | EDW_UPDATED_DTM | 2017-12-12..2024-06-17 | stale=True
edw_use_transaction_daily | 2,191,869 | 11 | 60.1 | TRANSIT_DAY_KEY | 2024-01-01..2026-05-09 | stale=True
ncs_stage_activity_code | 189 | 13 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_cashbox_event | 20 | 20 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_cashbox_inventory | 9,794 | 40 | EVENT_DTM | 2013-04-17..2033-04-03 | stale=False
ncs_stage_cashbox_manual_counts | 449,226 | 79 | 16.4 | TRANSIT_DAY_KEY | 1969-12-31..2026-04-09 | stale=True
ncs_stage_cashbox_tracking | 29,111,098 | 107 | 778.6 | EVENT_DTM | 2021-05-24..2026-04-11 | stale=True
ncs_stage_cashbox_type | 15 | 32 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_component_type | 44 | 13 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_daily_cashbox_detail | 43,884 | 77 | EVENT_DTM | 2001-12-31..2017-07-25 | stale=True
ncs_stage_device | 7,859 | 35 | UPDATED_DTM | 2013-02-18..2026-04-11 | stale=True
ncs_stage_device_control_group | 992 | 17 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_device_control_group_type | 5 | 13 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_device_current_hw_config | 147,600 | 18 | 2.9 | LAST_REPORTED_DTM | 2013-02-27..2026-04-11 | stale=True
ncs_stage_device_end_of_day | 2,960,842 | 19 | 82.8 | TRANSIT_DAY_KEY | 2015-09-14..2034-03-03 | stale=False
ncs_stage_device_end_of_day_msg_count | 42,369,945 | 12 | 136.7 | TRANSIT_DAY_KEY | 2013-01-05..2034-03-03 | stale=False
ncs_stage_device_event_history | 1,248,822,281 | 43 | 46702.5 | 1057 files | EVENT_DTM | 1949-11-25..2032-05-25 | stale=False
ncs_stage_device_type | 84 | 26 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_event | 422 | 16 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_sale_transaction | 17,180,705 | 96 | 1075.4 | TRANSACTION_DTM | 2024-01-01..2026-04-11 | stale=True
ncs_stage_sale_transaction_device_msg | 366,094 | 21 | 12.7 | TRANSACTION_DTM | 2013-11-11..2026-04-11 | stale=True
ncs_stage_stop_point | 12,887 | 28 | UPDATED_DTM | 2013-01-28..2024-06-17 | stale=True
ncs_stage_stop_point_type | 8 | 14 | _ingest_ts | 2026-06-10 | stale=False
ncs_stage_transit_facility | 1,986 | 32 | _ingest_ts | 2026-06-10 | stale=False
servicenow_cmdb_ci_acc | 1,828 | 142 | (no date) | stale=None
servicenow_cmdb_ci_card_handling | 2,215 | 142 | (no date) | stale=None
servicenow_cmdb_ci_netgear | 12,036 | 184 | 0.9 | (no date) | stale=None
servicenow_cmdb_ci_onboard_card_interface | 5,655 | 142 | (no date) | stale=None
servicenow_cmdb_ci_pos_device | 2,564 | 141 | (no date) | stale=None
servicenow_cmdb_hardware_product_model | 8,056 | 125 | (no date) | stale=None
servicenow_cmdb_model_category | 426 | 27 | (no date) | stale=None
servicenow_cmdb_software_component_model | 76,175 | 123 | 1.7 | (no date) | stale=None
servicenow_incident | 257,246 | 321 | 56.2 | (no date) | stale=None
servicenow_incident_bkp | 257,246 | 321 | 32.3 | (no date) | stale=None
servicenow_task_ci | 198,521 | 27 | (no date) | stale=None
servicenow_task_ci_bkp | 198,521 | 27 | (no date) | stale=None
```

## silver (29 tables)
```
device_event_enriched | 190,874,858 | 76 | 16637.8 | 200 files | EVENT_DAY_KEY | 2024-01-01..2026-04-11 | stale=True
device_failures | 668,418 | 9 | 16.4 | failure_date | 2014-03-13..2026-04-11 | stale=True
device_incident_features_daily | 103,485 | 14 | 9.3 | [DEVICE_KEY] | transit_day | 2024-01-01..2026-05-30 | stale=True
device_mttr | 668,418 | 12 | 11.3 | failure_date | 2014-03-13..2026-04-11 | stale=True
device_outage | 33,780,199 | 37 | 1039.8 | transit_day | 2024-01-01..2026-04-11 | stale=True
device_survival_intervals | 175,447 | 10 | 1.4 | preceding_failure_date | 2014-03-13..2026-04-11 | stale=True
device_uptime_intervals | 2,955,738 | 27 | 21.8 | last_event_dtm | 2016-04-06..2026-04-11 | stale=True
dim_device | 188,538 | 36 | 2.7 | (no date) | stale=None
dim_event_matrix | 185 | 15 | (no date) | stale=None
dim_event_type | 441 | 23 | (no date) | stale=None
dim_facility | 1,986 | 16 | (no date) | stale=None
dim_failure_level | 31 | 7 | (no date) | stale=None
dim_stop_point | 12,887 | 21 | UPDATED_DTM | 2013-01-28..2024-06-17 | stale=True
hw_config_current | 24,901 | 17 | [city_id] | LAST_REPORTED_DTM | 2013-02-27..2026-04-11 | stale=True
incident_history | 277,996 | 68 | 54.1 | [city_id] | opened_dtm | 2024-01-01..2026-05-30 | stale=True
incident_root_cause | 375,533 | 62 | 26.2 | transit_day_key | 2017-03-17..2026-04-11 | stale=True
incident_task_ci_link | 198,521 | 38 | 32.7 | (no date) | stale=None
kpi_avail_enriched | 753,425 | 64 | 58.6 | TRANSIT_DAY_KEY | 2013-02-28..2026-04-11 | stale=True
kpi_daily | 148,640 | 45 | 3.4 | TRANSIT_DAY_KEY | 2024-01-01..2026-04-11 | stale=True
kpi_monthly_benchmark | 851 | 22 | (no date) | stale=None
maintenance_ledger | 927,223 | 23 | 36.2 | event_dtm | 2013-02-28..2026-04-11 | stale=True
metric_daily | 3,426,592 | 28 | 95.4 | TRANSIT_DAY_KEY | 2017-10-29..2026-04-11 | stale=True
metric_hourly | 28,884,876 | 6 | 320.1 | hour_bucket | 2024-01-01..2026-04-11 | stale=True
read_tap_daily | 375,789 | 24 | 14.5 | transit_day | 2024-01-01..2026-04-11 | stale=True
station_network_daily | 133,159 | 15 | 4.4 | transit_day | 2014-03-13..2026-04-11 | stale=True
tap_event_daily | 2,195,426 | 22 | 60.6 | transit_day | 2024-01-01..2026-04-11 | stale=True
tvm_sale_daily | 431,862 | 22 | 15.9 | transit_day | 2024-01-01..2026-04-11 | stale=True
usage_lifecycle_daily | 3,426,592 | 25 | 58.2 | transit_day | 2017-10-29..2026-04-11 | stale=True
use_revenue_daily | 2,191,869 | 24 | 90.3 | transit_day | 2024-01-01..2026-05-09 | stale=True
```

## gold (5 tables)
```
device_ps1_daily | 2,894,573 | 107 | 231.0 | 19 files | transit_day | 2024-01-01..2026-04-11 | stale=True
device_ps2_chains | 2,191,785 | 57 | 111.2 | 13 files | transit_day | 2024-01-01..2026-04-11 | stale=True
device_ps3_incident | 34,612 | 62 | 3.3 | 10 files | transit_day | 2024-01-01..2026-04-11 | stale=True
device_ps4_hourly | 30,411,781 | 49 | 1135.2 | 50 files | hour_bucket | 2024-01-01..2026-04-11 | stale=True
device_ps5_component | 11,718 | 56 | 1.6 | 36 files | LAST_REPORTED_DTM | 2013-02-27..2026-04-11 | stale=True
```
