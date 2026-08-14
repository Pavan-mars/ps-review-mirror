"""Generate gold table source lineage Excel workbook."""
from pathlib import Path

import pandas as pd

OUT = Path(__file__).with_name("chicago_gold_table_sources.xlsx")

SUMMARY = pd.DataFrame(
    [
        {
            "gold_table": "device_ps1_daily",
            "problem_statement": "PS1 — Predictive failure",
            "grain": "(DEVICE_ID, transit_day)",
            "device_types": "TVM, GATE, VALIDATOR",
            "primary_targets": "will_fail_3d, will_fail_7d, will_fail_14d",
            "ddl_file": "sql/gold/device_ps1_daily__create.sql",
            "in_s3_export": "Yes",
            "notes": "Daily feature + label table; training window transit_day >= 2023-07-01",
        },
        {
            "gold_table": "device_ps2_chains",
            "problem_statement": "PS2 — Cascade / fault chains",
            "grain": "(DEVICE_ID, transit_day) where >=2 fault onsets",
            "device_types": "TVM, GATE, VALIDATOR",
            "primary_targets": "subsystem_chain, cascade features",
            "ddl_file": "sql/gold/device_ps2_chains__create.sql",
            "in_s3_export": "Yes",
            "notes": "Event-sequence chains; station co-failure from S27",
        },
        {
            "gold_table": "device_ps3_incident",
            "problem_statement": "PS3 — Root cause (ServiceNow availability spine)",
            "grain": "(availability_event_id)",
            "device_types": "TVM, GATE, VALIDATOR (VALIDATOR=0 incidents)",
            "primary_targets": "failure_level, derived component classification",
            "ddl_file": "sql/gold/device_ps3_incident__create.sql",
            "in_s3_export": "Yes",
            "notes": "Spine = silver.incident_root_cause (S17); sparse for GATE, zero for VALIDATOR",
        },
        {
            "gold_table": "device_ps3_oos_component",
            "problem_statement": "PS3 — Root cause (hardware OOS event spine)",
            "grain": "(DEVICE_ID, DW_DEVICE_EVENT_ID)",
            "device_types": "TVM, GATE, VALIDATOR",
            "primary_targets": "target_component_subsystem",
            "ddl_file": "sql/gold/device_ps3_oos_component__create.sql",
            "in_s3_export": "No",
            "notes": "Alternate dense PS3 spine from device_event_enriched; not in export_gold_to_s3.py",
        },
        {
            "gold_table": "device_ps4_hourly",
            "problem_statement": "PS4 — Anomaly detection",
            "grain": "(DEVICE_ID, DEVICE_KEY, hour_bucket, transit_day)",
            "device_types": "TVM, GATE, VALIDATOR",
            "primary_targets": "ensemble_anomaly_flag",
            "ddl_file": "sql/gold/device_ps4_hourly__create.sql",
            "in_s3_export": "Yes",
            "notes": "3-signal ensemble: event rate, M401 metric, tap reject rate",
        },
        {
            "gold_table": "device_ps5_component",
            "problem_statement": "PS5 — Survival / component lifetime",
            "grain": "(DEVICE_ID, COMPONENT_SERIAL_NBR)",
            "device_types": "TVM, GATE, VALIDATOR",
            "primary_targets": "days_to_failure, is_censored",
            "ddl_file": "sql/gold/device_ps5_component__create.sql",
            "in_s3_export": "Yes",
            "notes": "Component-level survival; hw_config_current is the component spine",
        },
    ]
)

SOURCES = pd.DataFrame(
    [
        # PS1
        ("device_ps1_daily", "silver", "dim_device", "S06", "Device spine, array position, category filter"),
        ("device_ps1_daily", "silver", "device_event_enriched", "S16", "Event counts and OOS-related signals"),
        ("device_ps1_daily", "silver", "device_outage", "S18", "Outage duration, failure level, chargeable flags"),
        ("device_ps1_daily", "silver", "device_failures", "S26", "Hardware OOS failure events for labels"),
        ("device_ps1_daily", "silver", "metric_daily", "S10", "M401 tap timing, comms event counts"),
        ("device_ps1_daily", "silver", "kpi_daily", "S12", "KPI aggregates (pre-aggregated per device-day)"),
        ("device_ps1_daily", "silver", "tap_event_daily", "S13", "Tap volume / reject (VALIDATOR+GATE)"),
        ("device_ps1_daily", "silver", "tvm_sale_daily", "S14", "TVM sales features"),
        ("device_ps1_daily", "silver", "use_revenue_daily", "S21", "Revenue (FARE_DUE in cents)"),
        ("device_ps1_daily", "silver", "device_incident_features_daily", "S24", "Rolling ServiceNow incident features (TVM/GATE)"),
        # PS2
        ("device_ps2_chains", "silver", "dim_device", "S06", "Device metadata"),
        ("device_ps2_chains", "silver", "device_event_enriched", "S16", "Fault-onset events (Set + hardware OOS)"),
        ("device_ps2_chains", "silver", "device_incident_features_daily", "S24", "Incident history features"),
        ("device_ps2_chains", "silver", "station_network_daily", "S27", "Station co-failure / cascade context"),
        ("device_ps2_chains", "silver", "device_survival_intervals", "S29", "Days healthy before fault chain"),
        ("device_ps2_chains", "bronze", "ncs_stage_cashbox_tracking", "", "TVM cashbox tracking events"),
        ("device_ps2_chains", "bronze", "ncs_stage_cashbox_type", "", "Cashbox type dimension"),
        # PS3 incident
        ("device_ps3_incident", "silver", "incident_root_cause", "S17", "Primary spine — ServiceNow availability events"),
        ("device_ps3_incident", "silver", "device_event_enriched", "S16", "Pre-incident event counts (24h / 7d)"),
        ("device_ps3_incident", "silver", "hw_config_current", "S09", "Component match for affected_component"),
        ("device_ps3_incident", "silver", "dim_event_type", "", "Event type enrichment"),
        ("device_ps3_incident", "bronze", "edw_kpi_rules", "", "KPI rule metadata"),
        ("device_ps3_incident", "bronze", "edw_kpi", "", "KPI dimension for rules join"),
        # PS3 OOS component
        ("device_ps3_oos_component", "silver", "device_event_enriched", "S16", "Primary spine — hardware OOS onsets + prior-window aggregates"),
        ("device_ps3_oos_component", "silver", "hw_config_current", "S09", "Component serial enrichment"),
        # PS4
        ("device_ps4_hourly", "silver", "dim_device", "S06", "Device context"),
        ("device_ps4_hourly", "silver", "device_event_enriched", "S16", "Hourly event rates and subsystem counts"),
        ("device_ps4_hourly", "silver", "metric_hourly", "S05", "M401 hourly tap timing"),
        ("device_ps4_hourly", "silver", "tap_event_daily", "S13", "Daily reject rate broadcast to hourly rows"),
        ("device_ps4_hourly", "silver", "use_revenue_daily", "S21", "Daily revenue broadcast to hourly rows"),
        ("device_ps4_hourly", "silver", "device_incident_features_daily", "S24", "Incident features broadcast to hourly rows"),
        # PS5
        ("device_ps5_component", "silver", "hw_config_current", "S09", "Component spine — installed hardware"),
        ("device_ps5_component", "silver", "device_outage", "S18", "Failure / outage proxy"),
        ("device_ps5_component", "silver", "incident_history", "S15", "Lifetime ServiceNow stats (TVM/GATE)"),
        ("device_ps5_component", "silver", "tap_event_daily", "S13", "Lifetime usage intensity"),
        ("device_ps5_component", "silver", "device_mttr", "S28", "Lifetime MTTR (includes VALIDATOR)"),
        ("device_ps5_component", "bronze", "ncs_stage_cashbox_tracking", "", "Cashbox wear signal (TVM/VALIDATOR)"),
    ],
    columns=["gold_table", "source_layer", "source_table", "silver_table_id", "role"],
)

PIPELINE = pd.DataFrame(
    [
        ("Build gold tables", "notebooks/run_layer_gold.py", "Runs sql/gold/*__create.sql in Databricks"),
        ("Validate gold", "notebooks/validation/validate_gold.py", "Grain / coverage checks after build"),
        ("Export to S3", "notebooks/export_gold_to_s3.py", "Parquet to s3://.../chicago/gold/<table>/ for SageMaker"),
        ("Build silver (upstream)", "notebooks/run_layer_silver.py", "Silver tables built from bronze via sql/silver/"),
        ("Schema reference", "skills/chicago-data-catalog/references/gold_schemas.md", "Column counts and targets snapshot"),
    ],
    columns=["step", "path", "description"],
)

with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
    SUMMARY.to_excel(writer, sheet_name="gold_tables_summary", index=False)
    SOURCES.to_excel(writer, sheet_name="source_lineage", index=False)
    PIPELINE.to_excel(writer, sheet_name="pipeline", index=False)

print(f"Wrote {OUT}")
