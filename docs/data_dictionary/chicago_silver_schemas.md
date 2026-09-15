# Chicago Silver Schemas (snapshot 2026-07-17)

Columns for the silver **feature/label/reference** tables captured in this snapshot.
Full column lists for ALL bronze/silver/gold tables (incl. `device_*` survival/failure tables and the
dims) are live in `mars_dev.audit.catalog_columns` / `audit.catalog_docs` (see `live_queries.md`).

### silver.dim_failure_level (7)
failure_level|INT · description|STRING · metric_category|INT · metric_category_name|STRING · is_device_fault|BOOLEAN · is_operational|BOOLEAN · severity_ordinal|INT

### silver.hw_config_current (17)
DEVICE_ID · COMPONENT_DESCRIPTION · COMPONENT_SERIAL_NBR · LAST_REPORTED_DTM · REPORTED_CHANGED_DTM · OPERATOR_ID · FACILITY_ID · hw_source · component_age_days|INT · DEVICE_KEY|DECIMAL · DEVICE_NAME · DEVICE_TYPE_NAME · mars_device_category · FACILITY_NAME · OPERATOR_NAME · device_serial_number · city_id

### silver.incident_history (68)
incident_number · incident_state · maintenance_type · opened_dtm · resolved_dtm · closed_dtm · incident_date|DATE · duration_seconds|LONG · time_to_resolve_minutes · time_to_close_minutes · priority|INT · priority_label · severity|INT · severity_label · category · subcategory · u_category · cause · short_description · description · resolution_notes · close_code · wm_asset · cmdb_ci_name · ncs_device_id · location_name · assignment_group · assigned_to · caller · event_code_raw · event_code_id|INT · event_code_name · event_cleared_dtm · is_chargeable|INT · chargeable_level · is_chargeable_override|INT · reopen_count|INT · is_major_incident|INT · reason_code · business_impact · contact_type · estimated_time_hours · ci_asset_tag · ci_operational_status · ci_install_status · ci_install_date|DATE · ci_serial_number · ci_ncs_device_id · ci_ncs_device_name · ci_component_id · ci_component_position · ci_manufacturer · ci_warranty_expiration|DATE · ci_class_name · ci_fault_count|INT · cmdb_model_name · cmdb_model_type · cmdb_model_life_expectancy · cmdb_model_is_repairable|INT · cmdb_model_is_rotable|INT · cmdb_model_category · cmdb_model_parent_category · DEVICE_KEY · mars_device_category · FACILITY_NAME · FACILITY_ID · OPERATOR_ID · city_id

### silver.incident_root_cause (62)  [grain-FAIL: 670 dup on availability_event_id]
SN_U_EVENT_ID · SN_SYS_ID · SN_U_DEVICE_TYPE · SN_U_DEVICE_ID · device_id · transit_day_key|DECIMAL · transit_day|DATE · availability_event_id · AE_FAULT_STATE · AE_FAILURE_LEVEL · failure_level_label · is_chargeable|BOOLEAN · is_device_fault|BOOLEAN · incident_duration_min · AE_FAULT_DESCRIPTION · AE_SYMPTOM · AE_PROBLEM · AE_RESOLUTION · affected_component · wot_state · request_type · root_cause_category · AE_DEVICE_TYPE_NAME · AE_OPERATOR_ID · AE_OPERATOR_NAME · AE_FACILITY_ID · AE_FACILITY_NAME · AE_START_DTM · AE_END_DTM · EDW_INSERTED_DTM · EDW_UPDATED_DTM · sn_category · sn_maintenance_type · sn_priority|INT · sn_priority_label · sn_chargeable_level · sn_event_code_id|INT · sn_event_code_name · jb_* (servicenow_cta_chargability as of 2026-09-15, formerly jumpbox: wot_number/device_id/wot_state/fault_state/request_type/fault_description/resolution/affected_component/failure_level/start_dtm/resolved_at/opened_at/caller/facility_name) · DEVICE_KEY · DEVICE_NAME · DEVICE_TYPE_NAME · DEVICE_CONTROL_GROUP_TYPE_NAME · mars_device_category · FACILITY_NAME · OPERATOR_NAME · DEVICE_SERIAL_NUMBER · from_cta_sn_mirror|BOOLEAN · from_svn_stage|BOOLEAN

### silver.incident_task_ci_link (38)
task_ci_sys_id · linked_incident_sys_id · linked_incident_number · ci_item · extracted_bus_number · constructed_device_id · DEVICE_KEY · DEVICE_ID · mars_device_category · FACILITY_NAME · FACILITY_ID · OPERATOR_ID · incident_sys_id · incident_number · incident_created_on · incident_updated_on · opened_at · resolved_at · closed_at · incident_date|DATE · time_to_resolve_minutes · is_chargeable|BOOLEAN · chargeable_level · event_code · category · subcategory · cause · short_description · close_notes · close_code · priority|INT · severity|INT · incident_state|INT · reopen_count|INT · is_major_incident|BOOLEAN · maintenance_type · u_outage_start_time · u_outage_end_time

### silver.kpi_avail_enriched (64)
TRANSIT_DAY_KEY · transit_day · EVENT_ID · RMA · DEVICE_ID · DEVICE_TYPE_ID · DEVICE_KEY · FAULT_STATE · FAILURE_LEVEL · START_DTM · END_DTM · outage_duration_min · FAULT_DESCRIPTION · SYMPTOM · PROBLEM · RESOLUTION · EXCLUDED · ARRAY_ID · ARRAY_SIZE · ARRAY_POSITION · UPDATED_DTM · ae_device_type_name · OPERATOR_ID · OPERATOR_NAME · FACILITY_ID · FACILITY_NAME · DEVICE_NAME · DEVICE_TYPE_NAME · DEVICE_CONTROL_GROUP_TYPE_NAME · mars_device_category · DEVICE_SERIAL_NUMBER · RELIEF_ID · relief_failure_level · relief_start_dtm · relief_end_dtm · relief_notes · svn_data_available|BOOLEAN · SN_U_EVENT_ID · SN_SYS_ID · sn_* (device_type/device_id/fault_state/failure_level/fault_description/symptom/problem/resolution/affected_component/wot_state/request_type/edw_inserted_dtm/edw_updated_dtm) · jb_* (servicenow_cta_chargability as of 2026-09-15, formerly jumpbox: wot_number/wot_state/fault_state/fault_description/resolution/affected_component/failure_level/start_dtm/resolved_at/opened_at/caller/facility_name)

### silver.kpi_daily (45)
KPI_ID · DEVICE_ID · DEVICE_KEY · TRANSIT_DAY_KEY · transit_day · OPERATOR_ID · FACILITY_ID · KPI_VALUE · FAILURE_LEVEL · FAULT_DESCRIPTION · EXCLUDED · RELIEF_VALUE · ARRAY_ID · ARRAY_SIZE · ARRAY_POSITION · START_DTM · END_DTM · DEVICE_TYPE_ID · DEVICE_TYPE_NAME · KPI_NAME · KPI_SYSTEM · KPI_TYPE · kpi_units · KPI_DESC · KPI_CRITICALITY · kpi_rule_count · rule_min_failure_level · rule_max_failure_level · kpi_target_band · KPI_DEDUCTION_TYPE · KPI_DEDUCTION_VALUE · meets_target|BOOLEAN · summary_kpi_value · summary_kpi_quantity · DEVICE_NAME · dim_device_type_name · mars_device_category · FACILITY_NAME · OPERATOR_NAME · sldc_kpi_value · sldc_band · sldc_percent · sldc_adjusted_pct · KPI_AGENCY_ID · KPI_AGENCY_NAME

### silver.maintenance_ledger (23)
DEVICE_ID · DEVICE_KEY · mars_device_category · FACILITY_ID · FACILITY_NAME · OPERATOR_ID · OPERATOR_NAME · ledger_date|DATE · event_dtm · event_end_dtm · duration_min · ledger_type · failure_level · component_subsystem · event_type_id|LONG · event_type_name · fault_state · fault_description · source_event_id · source_table · is_commanded_oos|BOOLEAN · is_maintenance_oos|BOOLEAN · _silver_load_ts

### silver.metric_daily (28)  [M401 tap-timing + reader/comms events]
DEVICE_KEY · TRANSIT_DAY_KEY · transit_day · FACILITY_ID · DEVICE_ID · DEVICE_NAME · DEVICE_TYPE_NAME · mars_device_category · FACILITY_NAME · OPERATOR_ID · OPERATOR_NAME · m401_daily_txn_count|LONG · m401_avg_txn_time_ms · m401_max_txn_time_ms · m401_p95_txn_time_ms · m401_p99_txn_time_ms · m401_slow_tap_count|LONG · m401_slow_tap_pct · m401_txn_count_delta|LONG · m401_avg_time_delta_ms · m401_rolling_7d_avg_ms · m401_z_score_vs_28d|DOUBLE · volume_drop_flag|BOOLEAN · comms_csc_read_err_count|LONG · comms_host_comm_lost_count|LONG · comms_device_comms_lost_count|LONG · comms_total_count|LONG · comms_event_flag|BOOLEAN

### silver.metric_hourly (6)
DEVICE_KEY · hour_bucket · transit_day · metric_401_tap_count_hour|LONG · metric_401_avg_ms_hour · metric_401_max_ms_hour

### silver.read_tap_daily (24)
DEVICE_ID · transit_day · TRANSIT_DAY_KEY · OPERATOR_ID · FACILITY_ID · BUS_ID · daily_read_count|LONG · unique_tokens|LONG · approved_read_count|LONG · rejected_read_count|LONG · null_status_read_count|LONG · reject_rate_pct|DOUBLE · entry_count|LONG · exit_count|LONG · transfer_tap_count|LONG · read_active_hours|LONG · first_read_dtm · last_read_dtm · DEVICE_KEY · DEVICE_NAME · mars_device_category · FACILITY_NAME · OPERATOR_NAME · TRANSIT_MODE_NAME

### silver.station_network_daily (15)
FACILITY_ID · FACILITY_NAME · OPERATOR_ID · OPERATOR_NAME · device_category · transit_day · devices_failed|LONG · total_failure_events|LONG · total_downtime_minutes · avg_downtime_minutes · max_downtime_minutes · is_coordinated_failure|BOOLEAN · is_major_station_event|BOOLEAN · first_failure_dtm · last_failure_dtm

### silver.tap_event_daily (22)
DEVICE_ID · transit_day · OPERATOR_ID · BUS_ID · tap_count|LONG · unique_cards|LONG · tap_approved_count|LONG · tap_reject_count|LONG · tap_reject_rate_pct|DOUBLE · tap_timeout_count|LONG · tap_timeout_rate_pct|DOUBLE · total_fare · avg_fare · peak_hour_tap_count|LONG · first_tap_dtm · last_tap_dtm · FACILITY_ID · DEVICE_NAME · DEVICE_TYPE_NAME · mars_device_category · FACILITY_NAME · OPERATOR_NAME

### silver.tvm_sale_daily (22)
DEVICE_ID · transit_day · OPERATOR_ID · FACILITY_ID · daily_sales_count|LONG · error_txn_count|LONG · error_txn_rate_pct|DOUBLE · cash_sales_count|LONG · card_sales_count|LONG · cash_sales_pct|DOUBLE · total_revenue_cents · avg_sale_value_cents · total_cash_cents · total_card_cents · sales_active_hours|LONG · first_sale_dtm · last_sale_dtm · DEVICE_KEY · DEVICE_NAME · FACILITY_NAME · OPERATOR_NAME · mars_device_category

### silver.usage_lifecycle_daily (25)
DEVICE_KEY · DEVICE_ID · transit_day · mars_device_category · FACILITY_ID · FACILITY_NAME · OPERATOR_ID · OPERATOR_NAME · m401_daily_txn_count|LONG · daily_failure_count|LONG · daily_outage_min · daily_maint_events|LONG · daily_tech_logins|LONG · daily_maint_mode_events|LONG · daily_maint_duration_min · days_in_service|INT · cumulative_tap_count|LONG · cumulative_failure_count|LONG · cumulative_outage_min · cumulative_maint_events|LONG · days_since_last_failure|INT · days_since_last_maintenance|INT · failure_count_30d|LONG · tap_count_30d|LONG · maint_events_30d|LONG

### silver.use_revenue_daily (24)
DEVICE_ID · transit_day · TRANSIT_DAY_KEY · daily_txn_count|LONG · priced_txn_count|LONG · priced_txn_pct|DOUBLE · daily_fare_due_cents · daily_calc_fare_cents · daily_extra_fare_cents · daily_uncollectible_cents · daily_net_revenue_cents · daily_fare_due_dollars|DOUBLE · daily_net_revenue_dollars|DOUBLE · FIRST_TXN_DTM · LAST_TXN_DTM · revenue_active_hours · DEVICE_KEY · DEVICE_NAME · mars_device_category · FACILITY_ID · FACILITY_NAME · OPERATOR_ID · OPERATOR_NAME · TRANSIT_MODE_NAME

### silver.dim_stop_point (21) · silver.kpi_monthly_benchmark (22)
(reference/dim tables - see audit.catalog_columns for columns)

---
**Not inlined here** (pull from `audit.catalog_columns`): silver `device_event_enriched`, `device_failures`,
`device_mttr`, `device_survival_intervals`, `device_outage`, `device_uptime_intervals`,
`device_incident_features_daily`, `dim_device`, `dim_event_matrix`, `dim_event_type`, `dim_facility`;
all 85 bronze; all 5 gold. The generator wrote complete per-layer schema docs to `audit.catalog_docs`.
