# Shared PS1 feature ETL — extracted verbatim from fleet notebook CELLS 6-8.
# Do not edit by hand; regenerate with tooling/build_ps1_features_module.py
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from pyspark.sql import functions as F
from pyspark.sql.window import Window
from pyspark.storagelevel import StorageLevel

TARGET_COL = "will_hardware_oos_3d"
SLA_TARGET_COL = "will_fail_3d"
LABEL_HORIZON_DAYS = 3
LABEL_DATA_LAG_DAYS = 0

# PS1_LABEL_MODE  state | onset   (default "state" -- preserves the 28-Jul decision)
#   state : will_hardware_oos_3d as built -- marks every day of an out-of-service spell.
#           95-98% of positives merely follow another positive (measured in sql/35).
#   onset : the TRANSITION into a spell only, with in-spell rows dropped from train
#           and score. This is the retraining sql/35 specifies; it is opt-in so that
#           nothing changes unless the run asks for it.
PS1_LABEL_MODE_DEFAULT = "state"
KEY_COLS = ["DEVICE_ID", "transit_day"]
JOIN_KEY_DEVICE = "DEVICE_KEY"
PS5_DATE_COLUMN = None
AUXILIARY_DUPLICATE_POLICY = "skip"
RUN_DEEP_GRAIN_AUDIT = False
IS_LOCAL_SPARK = False
shuffle_partitions = 200


def _notebook_flag(name: str, default: bool = False) -> bool:
    return bool(globals().get(name, default))


def _notebook_val(name: str, default):
    return globals().get(name, default)

STAGE_TIMINGS_SEC: dict[str, float] = {}
SOURCE_COLUMN_MAPPINGS: dict[str, dict] = {}
SOURCE_SKIPS: dict[str, dict] = {}
SOURCE_DERIVATIONS: dict[str, str] = {}
SOURCE_GRAIN_AUDITS: dict[str, dict] = {}


@contextmanager
def timed_stage(name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        STAGE_TIMINGS_SEC[name] = round(elapsed, 2)
        print(f"[{name}] {elapsed:,.1f} sec")


def _apply_s3a_hadoop_conf(_spark):
    """Override S3A duration defaults like '60s' that break Hadoop 3.3.x parsers."""
    _s3a_numeric = {
        "fs.s3a.connection.timeout": "200000",
        "fs.s3a.connection.establish.timeout": "30000",
        "fs.s3a.connection.request.timeout": "60000",
        "fs.s3a.connection.acquisition.timeout": "60000",
        "fs.s3a.connection.idle.time": "60000",
        "fs.s3a.connection.ttl": "300000",
        "fs.s3a.socket.timeout": "60000",
        "fs.s3a.threads.keepalivetime": "60",
        "fs.s3a.multipart.purge.age": "86400000",
    }
    for key, value in _s3a_numeric.items():
        try:
            _spark.conf.set(f"spark.hadoop.{key}", value)
        except Exception as exc:
            print(f"[S3A] spark.conf {key}: {type(exc).__name__}")
    try:
        _hconf = _spark.sparkContext._jsc.hadoopConfiguration()
        for key, value in _s3a_numeric.items():
            _hconf.set(key, value)
    except Exception as exc:
        print(f"[S3A] hadoopConfiguration override: {type(exc).__name__}")


@dataclass
class _FleetCfg:
    device_cat: str
    failure_features: list[str]
    ps1_tap_features: list[str]
    fleet_tap_features: list[str]
    gold_extra: list[str]
    incident_features: list[str]
    metric_features: list[str]
    chain_features: list[str]
    anomaly_features: list[str]
    lifetime_features: list[str]
    usage_features: list[str]
    mttr_features: list[str]
    usage_ext_features: list[str]
    spark_derived_features: list[str]
    oos_label_concurrent_features: list[str]
    all_candidate_features: list[str]
    required_ps1_features: list[str]
    tap_feature_cols: list[str]  # columns selected from df_read_tap join


def _resolve_end_day_expr(end_day: str | None, for_training: bool):
    if end_day is not None:
        return F.to_date(F.lit(end_day))
    return F.date_sub(
        F.current_date(),
        LABEL_HORIZON_DAYS + LABEL_DATA_LAG_DAYS,
    )


def _reset_source_audit():
    global SOURCE_COLUMN_MAPPINGS, SOURCE_SKIPS, SOURCE_DERIVATIONS, SOURCE_GRAIN_AUDITS
    SOURCE_COLUMN_MAPPINGS = {}
    SOURCE_SKIPS = {}
    SOURCE_DERIVATIONS = {}
    SOURCE_GRAIN_AUDITS = {}


def _fleet_key(fleet: str) -> str:
    key = fleet.strip().upper()
    if key not in FLEET_CONFIG:
        raise ValueError(f"Unknown fleet {fleet!r}; expected one of {sorted(FLEET_CONFIG)}")
    return key


def _paths(spark, fleet: str, s3_gold: str | None, s3_silver: str | None):
    if s3_gold and s3_silver:
        return s3_gold, s3_silver
    g = globals()
    sg = s3_gold or g.get("S3_GOLD_RUNTIME")
    ss = s3_silver or g.get("S3_SILVER_RUNTIME")
    if not sg or not ss:
        raise RuntimeError(
            f"[{fleet}] S3 paths required: pass s3_gold/s3_silver or define "
            "S3_GOLD_RUNTIME / S3_SILVER_RUNTIME in the notebook."
        )
    return sg, ss

FLEET_CONFIG: dict[str, _FleetCfg] = {}

FLEET_CONFIG['GATE'] = _FleetCfg(
    device_cat='GATE',
    failure_features=['roll_fail_7d', 'roll_fail_30d', 'roll_fail_90d', 'days_since_fail', 'fail_free_streak'],
    ps1_tap_features=['tap_count', 'tap_reject_rate_pct', 'peak_hour_tap_count', 'tap_timeout_rate_pct', 'tap_timeout_count'],
    fleet_tap_features=['gate_tap_count_prior_7d', 'gate_tap_count_prior_30d', 'gate_tap_count_prior_90d', 'gate_tap_reject_prior_7d', 'gate_tap_reject_prior_30d', 'gate_tap_reject_prior_90d', 'gate_tap_reject_trend_7v30d', 'gate_peak_hour_tap_prior_7d', 'gate_tap_timeout_prior_7d'],
    gold_extra=['gate_mech_events', 'csc_reader_events', 'comms_events', 'system_events', 'hardware_oos_count', 'chargeable_outage_count', 'chargeable_outage_min', 'events_7d', 'events_30d', 'oos_events_7d', 'hardware_oos_events_7d', 'outage_min_7d', 'availability_pct_7d', 'critical_events_7d', 'metric_p95_txn_ms', 'metric_slow_tap_count', 'metric_rolling_7d_avg_ms', 'metric_txn_delta', 'use_revenue_cents'],
    incident_features=['incident_count_7d_past', 'incident_count_30d_past', 'incident_count_90d_past', 'chargeable_count_7d_past', 'chargeable_count_30d_past', 'avg_mttr_7d_past', 'avg_mttr_30d_past', 'min_priority_30d_past', 'major_inc_count_30d_past', 'distinct_event_codes_30d', 'days_since_last_incident', 'incident_rate_trend'],
    metric_features=['met_avail_pct', 'met_downtime_min', 'met_event_count', 'met_p95_downtime'],
    chain_features=['chain_length', 'chain_failure_count', 'inter_failure_days_mean', 'inter_failure_days_min', 'failure_acceleration_rate', 'chain_event_diversity', 'max_chain_downtime_min', 'chain_component_diversity'],
    anomaly_features=['anomaly_days_7d', 'anomaly_days_30d', 'reject_spike_flag_7d', 'reject_spike_flag_30d', 'max_hourly_reject_rate_7d', 'stddev_hourly_reject_7d', 'anomaly_score_7d', 'anomaly_score_30d'],
    lifetime_features=['device_age_days', 'cumulative_taps_lifetime', 'cumulative_failures_lifetime', 'time_since_last_maintenance_days', 'mttr_mean_days', 'mttr_p90_days'],
    usage_features=['days_in_service', 'total_taps_lifetime', 'tap_rate_30d'],
    mttr_features=['mttr_avg_downtime_30d', 'mttr_avg_downtime_90d', 'mttr_failure_days_30d', 'mttr_failure_days_90d', 'mttr_days_since_prev_failure', 'mttr_max_downtime_30d'],
    usage_ext_features=['usage_days_in_service', 'usage_days_since_last_failure', 'usage_cumulative_tap_count', 'usage_cumulative_failure_count', 'usage_cumulative_outage_min', 'usage_daily_maint_events'],
    spark_derived_features=['gate_mech_events_prior_sum_7d', 'gate_mech_events_prior_sum_30d', 'csc_reader_events_prior_sum_7d', 'csc_reader_events_prior_sum_30d', 'comms_events_prior_sum_7d', 'comms_events_prior_sum_30d', 'chargeable_outage_count_prior_sum_7d', 'chargeable_outage_count_prior_sum_30d', 'hardware_oos_count_prior_sum_7d', 'array_peer_hw_oos_7d'],
    oos_label_concurrent_features=['oos_event_count', 'hardware_oos_count', 'oos_events_7d', 'hardware_oos_events_7d', 'event_count', 'critical_events', 'gate_mech_events', 'csc_reader_events', 'comms_events', 'system_events', 'chargeable_outage_count', 'chargeable_outage_min', 'events_7d', 'events_30d', 'outage_min_7d', 'critical_events_7d'],
    all_candidate_features=['roll_fail_7d', 'roll_fail_30d', 'roll_fail_90d', 'days_since_fail', 'fail_free_streak', 'tap_count', 'tap_reject_rate_pct', 'peak_hour_tap_count', 'tap_timeout_rate_pct', 'tap_timeout_count', 'gate_tap_count_prior_7d', 'gate_tap_count_prior_30d', 'gate_tap_count_prior_90d', 'gate_tap_reject_prior_7d', 'gate_tap_reject_prior_30d', 'gate_tap_reject_prior_90d', 'gate_tap_reject_trend_7v30d', 'gate_peak_hour_tap_prior_7d', 'gate_tap_timeout_prior_7d', 'availability_pct_7d', 'metric_p95_txn_ms', 'metric_slow_tap_count', 'metric_rolling_7d_avg_ms', 'metric_txn_delta', 'use_revenue_cents', 'incident_count_7d_past', 'incident_count_30d_past', 'incident_count_90d_past', 'chargeable_count_7d_past', 'chargeable_count_30d_past', 'avg_mttr_7d_past', 'avg_mttr_30d_past', 'min_priority_30d_past', 'major_inc_count_30d_past', 'distinct_event_codes_30d', 'days_since_last_incident', 'incident_rate_trend', 'met_avail_pct', 'met_downtime_min', 'met_event_count', 'met_p95_downtime', 'chain_length', 'chain_failure_count', 'inter_failure_days_mean', 'inter_failure_days_min', 'failure_acceleration_rate', 'chain_event_diversity', 'max_chain_downtime_min', 'chain_component_diversity', 'anomaly_days_7d', 'anomaly_days_30d', 'reject_spike_flag_7d', 'reject_spike_flag_30d', 'max_hourly_reject_rate_7d', 'stddev_hourly_reject_7d', 'anomaly_score_7d', 'anomaly_score_30d', 'device_age_days', 'cumulative_taps_lifetime', 'cumulative_failures_lifetime', 'time_since_last_maintenance_days', 'mttr_mean_days', 'mttr_p90_days', 'days_in_service', 'total_taps_lifetime', 'tap_rate_30d', 'mttr_avg_downtime_30d', 'mttr_avg_downtime_90d', 'mttr_failure_days_30d', 'mttr_failure_days_90d', 'mttr_days_since_prev_failure', 'mttr_max_downtime_30d', 'usage_days_in_service', 'usage_days_since_last_failure', 'usage_cumulative_tap_count', 'usage_cumulative_failure_count', 'usage_cumulative_outage_min', 'usage_daily_maint_events', 'gate_mech_events_prior_sum_7d', 'gate_mech_events_prior_sum_30d', 'csc_reader_events_prior_sum_7d', 'csc_reader_events_prior_sum_30d', 'comms_events_prior_sum_7d', 'comms_events_prior_sum_30d', 'chargeable_outage_count_prior_sum_7d', 'chargeable_outage_count_prior_sum_30d', 'hardware_oos_count_prior_sum_7d', 'array_peer_hw_oos_7d'],
    required_ps1_features=[],
    tap_feature_cols=['gate_tap_count_prior_7d', 'gate_tap_count_prior_30d', 'gate_tap_count_prior_90d', 'gate_tap_reject_prior_7d', 'gate_tap_reject_prior_30d', 'gate_tap_reject_prior_90d', 'gate_tap_reject_trend_7v30d', 'gate_peak_hour_tap_prior_7d', 'gate_tap_timeout_prior_7d'],
)

FLEET_CONFIG['TVM'] = _FleetCfg(
    device_cat='TVM',
    failure_features=['roll_fail_7d', 'roll_fail_30d', 'roll_fail_90d', 'days_since_fail', 'fail_free_streak'],
    ps1_tap_features=['tap_count', 'tap_reject_rate_pct', 'peak_hour_tap_count', 'tap_timeout_rate_pct', 'tap_timeout_count'],
    fleet_tap_features=['tvm_read_count_prior_7d', 'tvm_read_count_prior_30d', 'tvm_read_count_prior_90d', 'tvm_reject_rate_prior_7d', 'tvm_reject_rate_prior_30d', 'tvm_reject_rate_prior_90d', 'tvm_reject_rate_trend_7v30d', 'tvm_null_rate_prior_7d'],
    gold_extra=['printer_events', 'bankcard_events', 'bhu_events', 'chu_events', 'scrst_events', 'system_events', 'comms_events', 'csc_reader_events', 'hardware_oos_count', 'chargeable_outage_count', 'chargeable_outage_min', 'events_7d', 'events_30d', 'oos_events_7d', 'hardware_oos_events_7d', 'outage_min_7d', 'availability_pct_7d', 'critical_events_7d', 'metric_p95_txn_ms', 'metric_slow_tap_count', 'metric_rolling_7d_avg_ms', 'metric_txn_delta', 'use_revenue_cents', 'sales_7d_avg', 'kpi_count'],
    incident_features=['incident_count_7d_past', 'incident_count_30d_past', 'incident_count_90d_past', 'chargeable_count_7d_past', 'chargeable_count_30d_past', 'avg_mttr_7d_past', 'avg_mttr_30d_past', 'min_priority_30d_past', 'major_inc_count_30d_past', 'distinct_event_codes_30d', 'days_since_last_incident', 'incident_rate_trend'],
    metric_features=['met_avail_pct', 'met_downtime_min', 'met_event_count', 'met_p95_downtime'],
    chain_features=['chain_length', 'chain_failure_count', 'inter_failure_days_mean', 'inter_failure_days_min', 'failure_acceleration_rate', 'chain_event_diversity', 'max_chain_downtime_min', 'chain_component_diversity'],
    anomaly_features=['anomaly_days_7d', 'anomaly_days_30d', 'reject_spike_flag_7d', 'reject_spike_flag_30d', 'max_hourly_reject_rate_7d', 'stddev_hourly_reject_7d', 'anomaly_score_7d', 'anomaly_score_30d'],
    lifetime_features=['device_age_days', 'cumulative_taps_lifetime', 'cumulative_failures_lifetime', 'time_since_last_maintenance_days', 'mttr_mean_days', 'mttr_p90_days'],
    usage_features=['days_in_service', 'total_taps_lifetime', 'tap_rate_30d'],
    mttr_features=['mttr_avg_downtime_30d', 'mttr_avg_downtime_90d', 'mttr_failure_days_30d', 'mttr_failure_days_90d', 'mttr_days_since_prev_failure', 'mttr_max_downtime_30d'],
    usage_ext_features=['usage_days_in_service', 'usage_days_since_last_failure', 'usage_cumulative_tap_count', 'usage_cumulative_failure_count', 'usage_cumulative_outage_min', 'usage_daily_maint_events'],
    spark_derived_features=['printer_events_prior_sum_7d', 'printer_events_prior_sum_30d', 'bankcard_events_prior_sum_7d', 'bankcard_events_prior_sum_30d', 'bhu_events_prior_sum_7d', 'bhu_events_prior_sum_30d', 'chu_events_prior_sum_7d', 'chu_events_prior_sum_30d', 'scrst_events_prior_sum_7d', 'scrst_events_prior_sum_30d', 'comms_events_prior_sum_7d', 'comms_events_prior_sum_30d', 'chargeable_outage_count_prior_sum_7d', 'chargeable_outage_count_prior_sum_30d', 'hardware_oos_count_prior_sum_7d', 'facility_peer_hw_oos_7d'],
    oos_label_concurrent_features=['oos_event_count', 'hardware_oos_count', 'oos_events_7d', 'hardware_oos_events_7d', 'event_count', 'critical_events', 'printer_events', 'bankcard_events', 'bhu_events', 'chu_events', 'scrst_events', 'comms_events', 'system_events', 'csc_reader_events', 'chargeable_outage_count', 'chargeable_outage_min', 'events_7d', 'events_30d', 'outage_min_7d', 'critical_events_7d'],
    all_candidate_features=['roll_fail_7d', 'roll_fail_30d', 'roll_fail_90d', 'days_since_fail', 'fail_free_streak', 'tap_count', 'tap_reject_rate_pct', 'peak_hour_tap_count', 'tap_timeout_rate_pct', 'tap_timeout_count', 'tvm_read_count_prior_7d', 'tvm_read_count_prior_30d', 'tvm_read_count_prior_90d', 'tvm_reject_rate_prior_7d', 'tvm_reject_rate_prior_30d', 'tvm_reject_rate_prior_90d', 'tvm_reject_rate_trend_7v30d', 'tvm_null_rate_prior_7d', 'availability_pct_7d', 'metric_p95_txn_ms', 'metric_slow_tap_count', 'metric_rolling_7d_avg_ms', 'metric_txn_delta', 'use_revenue_cents', 'sales_7d_avg', 'kpi_count', 'incident_count_7d_past', 'incident_count_30d_past', 'incident_count_90d_past', 'chargeable_count_7d_past', 'chargeable_count_30d_past', 'avg_mttr_7d_past', 'avg_mttr_30d_past', 'min_priority_30d_past', 'major_inc_count_30d_past', 'distinct_event_codes_30d', 'days_since_last_incident', 'incident_rate_trend', 'met_avail_pct', 'met_downtime_min', 'met_event_count', 'met_p95_downtime', 'chain_length', 'chain_failure_count', 'inter_failure_days_mean', 'inter_failure_days_min', 'failure_acceleration_rate', 'chain_event_diversity', 'max_chain_downtime_min', 'chain_component_diversity', 'anomaly_days_7d', 'anomaly_days_30d', 'reject_spike_flag_7d', 'reject_spike_flag_30d', 'max_hourly_reject_rate_7d', 'stddev_hourly_reject_7d', 'anomaly_score_7d', 'anomaly_score_30d', 'device_age_days', 'cumulative_taps_lifetime', 'cumulative_failures_lifetime', 'time_since_last_maintenance_days', 'mttr_mean_days', 'mttr_p90_days', 'days_in_service', 'total_taps_lifetime', 'tap_rate_30d', 'mttr_avg_downtime_30d', 'mttr_avg_downtime_90d', 'mttr_failure_days_30d', 'mttr_failure_days_90d', 'mttr_days_since_prev_failure', 'mttr_max_downtime_30d', 'usage_days_in_service', 'usage_days_since_last_failure', 'usage_cumulative_tap_count', 'usage_cumulative_failure_count', 'usage_cumulative_outage_min', 'usage_daily_maint_events', 'printer_events_prior_sum_7d', 'printer_events_prior_sum_30d', 'bankcard_events_prior_sum_7d', 'bankcard_events_prior_sum_30d', 'bhu_events_prior_sum_7d', 'bhu_events_prior_sum_30d', 'chu_events_prior_sum_7d', 'chu_events_prior_sum_30d', 'scrst_events_prior_sum_7d', 'scrst_events_prior_sum_30d', 'comms_events_prior_sum_7d', 'comms_events_prior_sum_30d', 'chargeable_outage_count_prior_sum_7d', 'chargeable_outage_count_prior_sum_30d', 'hardware_oos_count_prior_sum_7d', 'facility_peer_hw_oos_7d'],
    required_ps1_features=[],
    tap_feature_cols=['tvm_read_count_prior_7d', 'tvm_read_count_prior_30d', 'tvm_read_count_prior_90d', 'tvm_reject_rate_prior_7d', 'tvm_reject_rate_prior_30d', 'tvm_reject_rate_prior_90d', 'tvm_reject_rate_trend_7v30d', 'tvm_null_rate_prior_7d'],
)

FLEET_CONFIG['VALIDATOR'] = _FleetCfg(
    device_cat='VALIDATOR',
    failure_features=['roll_fail_7d', 'roll_fail_30d', 'roll_fail_90d', 'days_since_fail', 'fail_free_streak'],
    ps1_tap_features=['tap_count', 'tap_reject_rate_pct', 'peak_hour_tap_count', 'tap_timeout_rate_pct', 'tap_timeout_count'],
    fleet_tap_features=['tvm_read_count_prior_7d', 'tvm_read_count_prior_30d', 'tvm_read_count_prior_90d', 'tvm_reject_rate_prior_7d', 'tvm_reject_rate_prior_30d', 'tvm_reject_rate_prior_90d', 'tvm_reject_rate_trend_7v30d', 'tvm_null_rate_prior_7d'],
    gold_extra=['printer_events', 'bankcard_events', 'bhu_events', 'chu_events', 'scrst_events', 'system_events', 'comms_events', 'csc_reader_events', 'hardware_oos_count', 'chargeable_outage_count', 'chargeable_outage_min', 'events_7d', 'events_30d', 'oos_events_7d', 'hardware_oos_events_7d', 'outage_min_7d', 'availability_pct_7d', 'critical_events_7d', 'metric_p95_txn_ms', 'metric_slow_tap_count', 'metric_rolling_7d_avg_ms', 'metric_txn_delta', 'use_revenue_cents', 'sales_7d_avg', 'kpi_count'],
    incident_features=[],
    metric_features=['met_avail_pct', 'met_downtime_min', 'met_event_count', 'met_p95_downtime'],
    chain_features=['chain_length', 'chain_failure_count', 'inter_failure_days_mean', 'inter_failure_days_min', 'failure_acceleration_rate', 'chain_event_diversity', 'max_chain_downtime_min', 'chain_component_diversity'],
    anomaly_features=['anomaly_days_7d', 'anomaly_days_30d', 'reject_spike_flag_7d', 'reject_spike_flag_30d', 'max_hourly_reject_rate_7d', 'stddev_hourly_reject_7d', 'anomaly_score_7d', 'anomaly_score_30d'],
    lifetime_features=['device_age_days', 'cumulative_taps_lifetime', 'cumulative_failures_lifetime', 'time_since_last_maintenance_days', 'mttr_mean_days', 'mttr_p90_days'],
    usage_features=['days_in_service', 'total_taps_lifetime', 'tap_rate_30d'],
    mttr_features=['mttr_avg_downtime_30d', 'mttr_avg_downtime_90d', 'mttr_failure_days_30d', 'mttr_failure_days_90d', 'mttr_days_since_prev_failure', 'mttr_max_downtime_30d'],
    usage_ext_features=['usage_days_in_service', 'usage_days_since_last_failure', 'usage_cumulative_tap_count', 'usage_cumulative_failure_count', 'usage_cumulative_outage_min', 'usage_daily_maint_events'],
    spark_derived_features=['printer_events_prior_sum_7d', 'printer_events_prior_sum_30d', 'bankcard_events_prior_sum_7d', 'bankcard_events_prior_sum_30d', 'bhu_events_prior_sum_7d', 'bhu_events_prior_sum_30d', 'chu_events_prior_sum_7d', 'chu_events_prior_sum_30d', 'scrst_events_prior_sum_7d', 'scrst_events_prior_sum_30d', 'comms_events_prior_sum_7d', 'comms_events_prior_sum_30d', 'chargeable_outage_count_prior_sum_7d', 'chargeable_outage_count_prior_sum_30d', 'hardware_oos_count_prior_sum_7d', 'facility_peer_hw_oos_7d'],
    oos_label_concurrent_features=['oos_event_count', 'hardware_oos_count', 'oos_events_7d', 'hardware_oos_events_7d', 'event_count', 'critical_events', 'printer_events', 'bankcard_events', 'bhu_events', 'chu_events', 'scrst_events', 'comms_events', 'system_events', 'csc_reader_events', 'chargeable_outage_count', 'chargeable_outage_min', 'events_7d', 'events_30d', 'outage_min_7d', 'critical_events_7d'],
    all_candidate_features=['roll_fail_7d', 'roll_fail_30d', 'roll_fail_90d', 'days_since_fail', 'fail_free_streak', 'tap_count', 'tap_reject_rate_pct', 'peak_hour_tap_count', 'tap_timeout_rate_pct', 'tap_timeout_count', 'tvm_read_count_prior_7d', 'tvm_read_count_prior_30d', 'tvm_read_count_prior_90d', 'tvm_reject_rate_prior_7d', 'tvm_reject_rate_prior_30d', 'tvm_reject_rate_prior_90d', 'tvm_reject_rate_trend_7v30d', 'tvm_null_rate_prior_7d', 'availability_pct_7d', 'metric_p95_txn_ms', 'metric_slow_tap_count', 'metric_rolling_7d_avg_ms', 'metric_txn_delta', 'use_revenue_cents', 'sales_7d_avg', 'kpi_count', 'met_avail_pct', 'met_downtime_min', 'met_event_count', 'met_p95_downtime', 'chain_length', 'chain_failure_count', 'inter_failure_days_mean', 'inter_failure_days_min', 'failure_acceleration_rate', 'chain_event_diversity', 'max_chain_downtime_min', 'chain_component_diversity', 'anomaly_days_7d', 'anomaly_days_30d', 'reject_spike_flag_7d', 'reject_spike_flag_30d', 'max_hourly_reject_rate_7d', 'stddev_hourly_reject_7d', 'anomaly_score_7d', 'anomaly_score_30d', 'device_age_days', 'cumulative_taps_lifetime', 'cumulative_failures_lifetime', 'time_since_last_maintenance_days', 'mttr_mean_days', 'mttr_p90_days', 'days_in_service', 'total_taps_lifetime', 'tap_rate_30d', 'mttr_avg_downtime_30d', 'mttr_avg_downtime_90d', 'mttr_failure_days_30d', 'mttr_failure_days_90d', 'mttr_days_since_prev_failure', 'mttr_max_downtime_30d', 'usage_days_in_service', 'usage_days_since_last_failure', 'usage_cumulative_tap_count', 'usage_cumulative_failure_count', 'usage_cumulative_outage_min', 'usage_daily_maint_events', 'printer_events_prior_sum_7d', 'printer_events_prior_sum_30d', 'bankcard_events_prior_sum_7d', 'bankcard_events_prior_sum_30d', 'bhu_events_prior_sum_7d', 'bhu_events_prior_sum_30d', 'chu_events_prior_sum_7d', 'chu_events_prior_sum_30d', 'scrst_events_prior_sum_7d', 'scrst_events_prior_sum_30d', 'comms_events_prior_sum_7d', 'comms_events_prior_sum_30d', 'chargeable_outage_count_prior_sum_7d', 'chargeable_outage_count_prior_sum_30d', 'hardware_oos_count_prior_sum_7d', 'facility_peer_hw_oos_7d'],
    required_ps1_features=[],
    tap_feature_cols=['tvm_read_count_prior_7d', 'tvm_read_count_prior_30d', 'tvm_read_count_prior_90d', 'tvm_reject_rate_prior_7d', 'tvm_reject_rate_prior_30d', 'tvm_reject_rate_prior_90d', 'tvm_reject_rate_trend_7v30d', 'tvm_null_rate_prior_7d'],
)

# --- CELL 6 shared helpers (hoisted for CELL 7) ---


def _ensure_date_column(frame, column="transit_day"):
    dtype = dict(frame.dtypes).get(column)
    if dtype == "date":
        return frame
    return frame.withColumn(column, F.to_date(F.col(column)))


def read_feature_source(
    spark,
    device_cat,
    source_name,
    path,
    feature_cols,
    start_day,
    end_day_expr,
    required_features=None,
    date_column=None,
    optional_source=False,
    optional_guidance=None,
):
    """Resolve columns case-insensitively, then filter and project them canonically."""
    required_features = required_features or []
    _apply_s3a_hadoop_conf(spark)
    raw = spark.read.parquet(path)
    raw_columns = list(raw.columns)
    lower_to_actual = {}
    ambiguous_case_columns = {}
    for actual_column in raw_columns:
        normalized = actual_column.casefold()
        if normalized in lower_to_actual and lower_to_actual[normalized] != actual_column:
            ambiguous_case_columns.setdefault(normalized, [lower_to_actual[normalized]])
            ambiguous_case_columns[normalized].append(actual_column)
        else:
            lower_to_actual[normalized] = actual_column
    if ambiguous_case_columns:
        raise ValueError(
            f"{source_name} has columns that differ only by letter case, which is ambiguous: "
            f"{ambiguous_case_columns}"
        )

    def resolve(requested_column):
        return lower_to_actual.get(requested_column.casefold())

    source_date_column = date_column or "transit_day"
    required_base = ["DEVICE_ID", source_date_column, "mars_device_category"]
    missing_base = sorted(c for c in required_base if resolve(c) is None)
    missing_required = sorted(c for c in required_features if resolve(c) is None)
    if missing_base or missing_required:
        if optional_source:
            def looks_date_like(column_name):
                lowered = column_name.lower()
                return (
                    lowered.endswith(("_date", "_day", "_time", "_timestamp", "_at"))
                    or lowered.startswith(("date_", "day_", "time_", "effective_", "snapshot_", "as_of_"))
                    or lowered in {"date", "day", "timestamp", "effective_date", "snapshot_date", "as_of_date"}
                )
            date_like_candidates = sorted(
                c for c in raw_columns if looks_date_like(c)
            )
            SOURCE_SKIPS[source_name] = {
                "missing_base": missing_base,
                "missing_required": missing_required,
                "date_like_candidates": date_like_candidates,
            }
            print(
                f"WARNING: {source_name} skipped. Missing base={missing_base}; "
                f"missing required features={missing_required}."
            )
            print(f"  Date-like columns found: {date_like_candidates or '(none)'}")
            if optional_guidance:
                print(f"  {optional_guidance}")
            return None
        raise ValueError(
            f"{source_name} schema is incomplete. Missing base={missing_base}; "
            f"missing required features={missing_required}. Rebuild/export the upstream table."
        )

    resolved_features = [(c, resolve(c)) for c in feature_cols if resolve(c) is not None]
    available_features = [canonical for canonical, _ in resolved_features]
    missing_optional = [c for c in feature_cols if resolve(c) is None]

    canonical_mapping = {
        "DEVICE_ID": resolve("DEVICE_ID"),
        "transit_day": resolve(source_date_column),
        "mars_device_category": resolve("mars_device_category"),
        **{canonical: actual for canonical, actual in resolved_features},
    }
    SOURCE_COLUMN_MAPPINGS[source_name] = canonical_mapping
    changed_case = {
        canonical: actual
        for canonical, actual in canonical_mapping.items()
        if canonical != actual
    }
    if changed_case:
        print(f"{source_name}: canonicalized source columns {changed_case}")

    if optional_source and not available_features:
        SOURCE_SKIPS[source_name] = {
            "reason": "no_requested_features_available",
            "missing_optional": missing_optional,
        }
        print(
            f"WARNING: {source_name} skipped because none of its "
            f"{len(feature_cols)} requested feature columns are available."
        )
        if optional_guidance:
            print(f"  {optional_guidance}")
        return None

    frame = raw.select(
        F.col(resolve("DEVICE_ID")).alias("DEVICE_ID"),
        F.col(resolve(source_date_column)).alias("transit_day"),
        F.col(resolve("mars_device_category")).alias("mars_device_category"),
        *[F.col(actual).alias(canonical) for canonical, actual in resolved_features],
    )
    frame = _ensure_date_column(frame)
    frame = (
        frame
        .where(F.col("mars_device_category") == device_cat)
        .where(F.col("transit_day") >= F.to_date(F.lit(start_day)))
        .where(F.col("transit_day") <= end_day_expr)
        .select(*KEY_COLS, *available_features)
    )
    if missing_optional:
        print(f"{source_name}: optional columns absent ({len(missing_optional)}): {missing_optional}")
    print(
        f"{source_name}: selected {len(available_features)} feature columns "
        f"(date source: {source_date_column})"
    )
    return frame



def _label_mode() -> str:
    mode = os.environ.get("PS1_LABEL_MODE", PS1_LABEL_MODE_DEFAULT).strip().lower()
    if mode not in ("state", "onset"):
        raise ValueError(
            f"PS1_LABEL_MODE must be 'state' or 'onset', got {mode!r}"
        )
    return mode


def _apply_onset_label(frame, target_col: str, key_col: str = "DEVICE_ID",
                       date_col: str = "transit_day"):
    """Turn a spell-STATE label into an ONSET label. Mirrors sql/35_ps1_label_onset.sql.

    The state label marks every day within, and the 3 days before, an out-of-service
    spell. Measured on 786,525 loaded rows, 95-98% of its positives merely follow
    another positive, so a model trained on it learns "is this device broken now" --
    hindsight rather than prediction.

      is_onset   1 on the FIRST day of a positive run. This is the event.
      in_spell   the previous day was already positive. Dropped from train and score,
                 because on those rows the failure has already happened.

    Grain note: this spine is device x day, so partitioning by DEVICE_ID is right.
    sql/35 also partitions by component_serial_nbr because the cross-wired table is
    device x component x day -- do not copy that partitioning here.
    """
    w = Window.partitionBy(key_col).orderBy(date_col)
    # coalesce to 0: without it the first row per device has a NULL prev, in_spell
    # evaluates to NULL, and `where(~in_spell)` would silently drop a real onset.
    prev = F.coalesce(F.lag(F.col(target_col)).over(w), F.lit(0)).cast("byte")
    tagged = (
        frame
        .withColumn("_prev_label", prev)
        .withColumn("_in_spell", (F.col(target_col) == 1) & (F.col("_prev_label") == 1))
        .withColumn("_is_onset",
                    ((F.col(target_col) == 1) & (F.col("_prev_label") == 0)).cast("byte"))
    )
    stats = tagged.agg(
        F.count(F.lit(1)).alias("n"),
        F.sum(F.col("_in_spell").cast("int")).alias("d"),
        F.sum(F.col("_is_onset").cast("int")).alias("o"),
    ).collect()[0]
    total = int(stats["n"] or 0)
    dropped = int(stats["d"] or 0)
    onsets = int(stats["o"] or 0)
    kept = total - dropped
    print(
        f"[label] PS1_LABEL_MODE=onset - dropped {dropped:,} in-spell rows of {total:,} "
        f"({100.0 * dropped / total if total else 0.0:.2f}%); {kept:,} retained, "
        f"{onsets:,} onsets ({100.0 * onsets / kept if kept else 0.0:.4f}% of retained)"
    )
    return (
        tagged.where(~F.col("_in_spell"))
        .withColumn(target_col, F.col("_is_onset"))
        .drop("_prev_label", "_in_spell", "_is_onset")
    )

def read_spine(
    spark,
    fleet: str,
    start_day: str,
    end_day: str | None = None,
    *,
    with_label: bool = True,
    s3_gold: str | None = None,
    s3_silver: str | None = None,
):
    """CELL 6 — gold/silver spine read + optional hardware OOS label."""
    _reset_source_audit()
    cfg = FLEET_CONFIG[_fleet_key(fleet)]
    s3_gold, s3_silver = _paths(spark, fleet, s3_gold, s3_silver)
    end_day_expr = _resolve_end_day_expr(end_day, for_training=with_label)
    DEVICE_CAT = cfg.device_cat
    FAILURE_FEATURES = cfg.failure_features
    PS1_TAP_FEATURES = cfg.ps1_tap_features
    GATE_GOLD_EXTRA = cfg.gold_extra
    INCIDENT_FEATURES = cfg.incident_features
    USAGE_FEATURES = cfg.usage_features
    REQUIRED_PS1_FEATURES = cfg.required_ps1_features

    PS1_PASS_THROUGH = [JOIN_KEY_DEVICE]
    PS1_REQUESTED = list(dict.fromkeys(
        PS1_PASS_THROUGH
        + FAILURE_FEATURES + PS1_TAP_FEATURES + GATE_GOLD_EXTRA + INCIDENT_FEATURES
        + USAGE_FEATURES + [SLA_TARGET_COL]
    ))


    def _silver_col_map(raw):
        return {column.casefold(): column for column in raw.columns}


    def _silver_col(raw, name, col_map=None):
        col_map = col_map or _silver_col_map(raw)
        actual = col_map.get(name.casefold())
        if actual is None:
            raise ValueError(
                f"Required silver column {name!r} is missing. "
                f"Available columns (first 25): {raw.columns[:25]}"
            )
        return actual


    def attach_hardware_oos_label(frame, device_category, horizon_days):
        """Build VALIDATOR-style hardware OOS Set label from S3 silver parquet (no UC catalog)."""
        base = frame.drop(TARGET_COL) if TARGET_COL in frame.columns else frame
        dim_path = f"{s3_silver}/dim_device/"
        dee_path = f"{s3_silver}/device_event_enriched/"
        dim_raw = spark.read.parquet(dim_path)
        dee_raw = spark.read.parquet(dee_path)
        dim_map = _silver_col_map(dim_raw)
        dee_map = _silver_col_map(dee_raw)
        dk_col = _silver_col(dim_raw, "DEVICE_KEY", dim_map)
        is_current_col = _silver_col(dim_raw, "is_current", dim_map)
        dee_dk = _silver_col(dee_raw, "DEVICE_KEY", dee_map)
        dee_dev = _silver_col(dee_raw, "DEVICE_ID", dee_map)
        dee_dtm = _silver_col(dee_raw, "EVENT_DTM", dee_map)
        dee_state = _silver_col(dee_raw, "EVENT_STATE_TYPE_NAME", dee_map)
        dee_hw_oos = _silver_col(dee_raw, "is_hardware_oos_event", dee_map)
        dee_cat = _silver_col(dee_raw, "mars_device_category", dee_map)
        current_devices = (
            dim_raw
            .where(F.col(is_current_col) == True)
            .select(F.col(dk_col).alias("DEVICE_KEY"))
            .distinct()
        )
        bounds = frame.agg(
            F.min("transit_day").alias("_min_day"),
            F.max("transit_day").alias("_max_day"),
        ).collect()[0]
        min_day = bounds["_min_day"]
        max_day = bounds["_max_day"]
        failure_days = (
            dee_raw.alias("dee")
            .join(
                F.broadcast(current_devices).alias("d_cur"),
                F.col(f"dee.{dee_dk}") == F.col("d_cur.DEVICE_KEY"),
            )
            .where(F.col(f"dee.{dee_hw_oos}") == True)
            .where(F.col(f"dee.{dee_state}") == "Set")
            .where(F.col(f"dee.{dee_cat}") == device_category)
            .where(
                F.to_date(F.col(f"dee.{dee_dtm}"))
                >= F.date_add(F.lit(min_day), 1)
            )
            .where(
                F.to_date(F.col(f"dee.{dee_dtm}"))
                <= F.date_add(F.lit(max_day), horizon_days)
            )
            .select(
                F.col(f"dee.{dee_dev}").alias("DEVICE_ID"),
                F.to_date(F.col(f"dee.{dee_dtm}")).alias("failure_date"),
            )
            .distinct()
        )
        seq = spark.range(1, horizon_days + 1).select(F.col("id").cast("int").alias("n"))
        label_days = (
            failure_days.crossJoin(seq)
            .withColumn("label_day", F.date_sub(F.col("failure_date"), F.col("n")))
            .select("DEVICE_ID", "label_day")
            .distinct()
        )
        return (
            base.alias("sp")
            .join(
                label_days.alias("ld"),
                (F.col("sp.DEVICE_ID") == F.col("ld.DEVICE_ID"))
                & (F.col("sp.transit_day") == F.col("ld.label_day")),
                "left",
            )
            .withColumn(
                TARGET_COL,
                F.when(F.col("ld.DEVICE_ID").isNotNull(), F.lit(1)).otherwise(F.lit(0)).cast("byte"),
            )
            .select("sp.*", TARGET_COL)
        )


    df_ps1 = read_feature_source(spark, cfg.device_cat, 
        "gold.device_ps1_daily",
        f"{s3_gold}/device_ps1_daily/",
        PS1_REQUESTED,
        start_day,
        end_day_expr,
        required_features=REQUIRED_PS1_FEATURES + [SLA_TARGET_COL],
    )
    print(
        f"Building {TARGET_COL} in-notebook from {s3_silver}/device_event_enriched/ "
        f"(hardware OOS Set, {LABEL_HORIZON_DAYS}-day lookahead)"
    )
    if with_label:
        df_ps1 = attach_hardware_oos_label(df_ps1, cfg.device_cat, LABEL_HORIZON_DAYS)
        df_ps1 = df_ps1.where(F.col(TARGET_COL).isin(0, 1))
        if _label_mode() == "onset":
            df_ps1 = _apply_onset_label(df_ps1, TARGET_COL)
        if SLA_TARGET_COL in df_ps1.columns:
            _sla = df_ps1.agg(F.avg(SLA_TARGET_COL).alias("r")).collect()[0]["r"]
            _oos = df_ps1.agg(F.avg(TARGET_COL).alias("r")).collect()[0]["r"]
            print(
                f"Label rates — {SLA_TARGET_COL}: {100 * float(_sla or 0):.4f}% | "
                f"{TARGET_COL}: {100 * float(_oos or 0):.4f}%"
            )

    return df_ps1

def add_auxiliary(
    spark,
    df_ps1,
    fleet: str,
    start_day: str,
    end_day: str | None = None,
    *,
    s3_gold: str | None = None,
    s3_silver: str | None = None,
):
    """CELL 7 — auxiliary reads + fleet tap rolling features."""
    cfg = FLEET_CONFIG[_fleet_key(fleet)]
    s3_gold, s3_silver = _paths(spark, fleet, s3_gold, s3_silver)
    end_day_expr = _resolve_end_day_expr(end_day, for_training=(end_day is None))
    CHAIN_FEATURES = cfg.chain_features
    ANOMALY_FEATURES = cfg.anomaly_features
    LIFETIME_FEATURES = cfg.lifetime_features

    df_ps2 = read_feature_source(spark, cfg.device_cat, 
        "gold.device_ps2_chains",
        f"{s3_gold}/device_ps2_chains/",
        cfg.chain_features,
        start_day,
        end_day_expr,
        optional_source=True,
        optional_guidance=(
            "PS2 is optional. It will be joined only when requested chain features exist "
            "and the filtered data has one row per DEVICE_ID/transit_day."
        ),
    )

    df_ps4 = read_feature_source(spark, cfg.device_cat, 
        "gold.device_ps4_hourly",
        f"{s3_gold}/device_ps4_hourly/",
        cfg.anomaly_features,
        start_day,
        end_day_expr,
        optional_source=True,
        optional_guidance=(
            "PS4 is an hourly source. It is omitted unless precomputed anomaly features exist "
            "at exactly one row per DEVICE_ID/transit_day."
        ),
    )

    df_ps5 = read_feature_source(spark, cfg.device_cat, 
        "gold.device_ps5_component",
        f"{s3_gold}/device_ps5_component/",
        cfg.lifetime_features,
        start_day,
        end_day_expr,  # v2.0 omitted this upper bound.
        date_column=PS5_DATE_COLUMN,
        optional_source=True,
        optional_guidance=(
            "Set PS5_DATE_COLUMN only after confirming a historical as-of/snapshot date. "
            "Joining a current snapshot or future failure date to historical rows would leak data."
        ),
    )

    metric_raw_cols = [
        "availability_pct", "total_downtime_min", "total_events", "p95_downtime_min"
    ]
    df_metric_raw = read_feature_source(spark, cfg.device_cat, 
        "silver.metric_daily",
        f"{s3_silver}/metric_daily/",
        metric_raw_cols,
        start_day,
        end_day_expr,
        required_features=metric_raw_cols,
        optional_source=True,
        optional_guidance=(
            "The expected metric fields are unavailable in this export, so metric_daily will be omitted. "
            "Inspect SOURCE_SKIPS and the source schema before defining any semantic aliases."
        ),
    )
    if df_metric_raw is None:
        df_metric = None
    else:
        df_metric = df_metric_raw.select(
            *KEY_COLS,
            F.col("availability_pct").alias("met_avail_pct"),
            F.col("total_downtime_min").alias("met_downtime_min"),
            F.col("total_events").alias("met_event_count"),
            F.col("p95_downtime_min").alias("met_p95_downtime"),
        )

    # ── GATE silver enrichments (S28 device_mttr, S20 usage lifecycle) — DEVICE_KEY grain ──
    mttr_cols = [
        "avg_downtime_30d", "avg_downtime_90d", "failure_days_30d", "failure_days_90d",
        "days_since_prev_failure", "max_downtime_30d",
    ]
    try:
        _mttr_raw = spark.read.parquet(f"{s3_silver}/device_mttr/")
        _mttr_lc = {c.casefold(): c for c in _mttr_raw.columns}
        _cat = _mttr_lc.get("device_category", "device_category")
        _dk = _mttr_lc.get("device_key", "DEVICE_KEY")
        _fd = _mttr_lc.get("failure_date", "failure_date")
        df_mttr = (
            _mttr_raw
            .where(F.col(_cat) == cfg.device_cat)
            .select(
                F.col(_dk).alias("DEVICE_KEY"),
                F.to_date(F.col(_fd)).alias("transit_day"),
                *[F.col(_mttr_lc[c.casefold()]).alias(f"mttr_{c}") for c in mttr_cols if c.casefold() in _mttr_lc],
            )
        )
        print(f"silver.device_mttr (S28): loaded {len(mttr_cols)} MTTR columns")
    except Exception as exc:
        df_mttr = None
        print(f"WARNING: silver.device_mttr skipped ({exc})")

    usage_ext_cols = [
        "days_in_service", "days_since_last_failure", "cumulative_tap_count",
        "cumulative_failure_count", "cumulative_outage_min", "daily_maint_events",
    ]
    try:
        _ul_raw = spark.read.parquet(f"{s3_silver}/usage_lifecycle_daily/")
        _ul_lc = {c.casefold(): c for c in _ul_raw.columns}
        _dk = _ul_lc.get("device_key", "DEVICE_KEY")
        _td = _ul_lc.get("transit_day", "transit_day")
        _cat = _ul_lc.get("mars_device_category", "mars_device_category")
        df_usage_ext = (
            _ul_raw
            .where(F.col(_cat) == cfg.device_cat)
            .where(F.col(_td) >= F.to_date(F.lit(start_day)))
            .where(F.col(_td) <= end_day_expr)
            .select(
                F.col(_dk).alias("DEVICE_KEY"),
                F.to_date(F.col(_td)).alias("transit_day"),
                *[F.col(_ul_lc[c.casefold()]).alias(f"usage_{c}") for c in usage_ext_cols if c.casefold() in _ul_lc],
            )
        )
        print(f"silver.usage_lifecycle_daily extended: {len(usage_ext_cols)} cols")
    except Exception as exc:
        df_usage_ext = None
        print(f"WARNING: usage_lifecycle_daily extended skipped ({exc})")







    def _build_tap_features(spark, df_ps1, cfg, start_day, end_day_expr, s3_silver):
        if cfg.device_cat == 'GATE':
            # ── GATE tap signal (gold tap_event_daily cols on PS1 spine — replaces read_tap S30) ──
            # VALIDATOR uses silver.read_tap_device_daily (~1% GATE coverage).
            # GATE uses gold tap_count × tap_reject_rate_pct (~27% coverage) with prior-only rolling windows.

            SECONDS_PER_DAY = 86_400


            def prior_window(days):
                return (
                    Window.partitionBy("DEVICE_ID")
                    .orderBy(F.col("_day_epoch"))
                    .rangeBetween(-days * SECONDS_PER_DAY, -1)
                )


            w7, w30, w90 = prior_window(7), prior_window(30), prior_window(90)

            TAP_RAW = ["tap_count", "tap_reject_rate_pct", "peak_hour_tap_count", "tap_timeout_rate_pct"]
            for col in TAP_RAW:
                if col not in df_ps1.columns:
                    df_ps1 = df_ps1.withColumn(col, F.lit(None).cast("double"))

            df_gate_tap_base = (
                df_ps1
                .select(*KEY_COLS, *TAP_RAW)
                .withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
                .withColumn("_daily_reads", F.col("tap_count").cast("double"))
                .withColumn(
                    "_daily_reject_equiv",
                    F.when(
                        F.col("tap_reject_rate_pct").isNotNull() & F.col("_daily_reads").isNotNull(),
                        F.col("_daily_reads") * F.col("tap_reject_rate_pct").cast("double") / 100.0,
                    ),
                )
                .withColumn("_daily_peak_reads", F.col("peak_hour_tap_count").cast("double"))
                .withColumn("_daily_timeout_pct", F.col("tap_timeout_rate_pct").cast("double"))
            )

            S30_REJECT_SOURCE = "tap_count × tap_reject_rate_pct / 100 (gold tap_event_daily)"
            SOURCE_DERIVATIONS["GATE_TAP_REJECT_SOURCE"] = S30_REJECT_SOURCE
            print(f"GATE tap rejection source: {S30_REJECT_SOURCE}")

            df_read_tap = (
                df_gate_tap_base
                .withColumn("_reads_7d", F.sum("_daily_reads").over(w7))
                .withColumn("_reads_30d", F.sum("_daily_reads").over(w30))
                .withColumn("_reads_90d", F.sum("_daily_reads").over(w90))
                .withColumn("_rejects_7d", F.sum("_daily_reject_equiv").over(w7))
                .withColumn("_rejects_30d", F.sum("_daily_reject_equiv").over(w30))
                .withColumn("_rejects_90d", F.sum("_daily_reject_equiv").over(w90))
                .withColumn("_peak_7d", F.sum("_daily_peak_reads").over(w7))
                .withColumn("_timeout_7d", F.avg("_daily_timeout_pct").over(w7))
                .withColumn("gate_tap_count_prior_7d", F.col("_reads_7d"))
                .withColumn("gate_tap_count_prior_30d", F.col("_reads_30d"))
                .withColumn("gate_tap_count_prior_90d", F.col("_reads_90d"))
                .withColumn(
                    "gate_tap_reject_prior_7d",
                    F.when(F.col("_reads_7d") > 0, 100.0 * F.col("_rejects_7d") / F.col("_reads_7d")),
                )
                .withColumn(
                    "gate_tap_reject_prior_30d",
                    F.when(F.col("_reads_30d") > 0, 100.0 * F.col("_rejects_30d") / F.col("_reads_30d")),
                )
                .withColumn(
                    "gate_tap_reject_prior_90d",
                    F.when(F.col("_reads_90d") > 0, 100.0 * F.col("_rejects_90d") / F.col("_reads_90d")),
                )
                .withColumn(
                    "gate_tap_reject_trend_7v30d",
                    F.col("gate_tap_reject_prior_7d") - F.col("gate_tap_reject_prior_30d"),
                )
                .withColumn("gate_peak_hour_tap_prior_7d", F.col("_peak_7d"))
                .withColumn("gate_tap_timeout_prior_7d", F.col("_timeout_7d"))
                .where(F.col("transit_day") >= F.to_date(F.lit(start_day)))
                .select(*KEY_COLS, *cfg.tap_feature_cols)
            )

            print("GATE tap prior-only rolling plan created (lazy; no Spark action yet).")

            return df_read_tap

        if cfg.device_cat == 'TVM':
            # ── TVM tap signal (silver read_tap_device_daily S30 — high TVM coverage) ──
            # GATE uses gold tap_count × tap_reject_rate_pct; TVM/VALIDATOR use S30 device-day collapse.

            SECONDS_PER_DAY = 86_400


            def prior_window(days):
                return (
                    Window.partitionBy("DEVICE_ID")
                    .orderBy(F.col("_day_epoch"))
                    .rangeBetween(-days * SECONDS_PER_DAY, -1)
                )


            w7, w30, w90 = prior_window(7), prior_window(30), prior_window(90)

            S30_REJECT_SOURCE = None
            df_s30_base = None

            try:
                _s30_raw = spark.read.parquet(f"{s3_silver}/read_tap_device_daily/")
                _s30_lc = {c.casefold(): c for c in _s30_raw.columns}
                _cat = _s30_lc.get("mars_device_category", "mars_device_category")
                _did = _s30_lc.get("device_id", "DEVICE_ID")
                _td = _s30_lc.get("transit_day", "transit_day")
                _drc = _s30_lc.get("daily_read_count", "daily_read_count")
                _rrc = _s30_lc.get("rejected_read_count", "rejected_read_count")
                _nsc = _s30_lc.get("null_status_read_count", "null_status_read_count")
                _rrp = _s30_lc.get("reject_rate_pct", "reject_rate_pct")

                df_s30_base = (
                    _s30_raw
                    .where(F.col(_cat) == cfg.device_cat)
                    .where(F.col(_td) >= F.to_date(F.lit(start_day)))
                    .where(F.col(_td) <= end_day_expr)
                    .select(
                        F.col(_did).alias("DEVICE_ID"),
                        F.to_date(F.col(_td)).alias("transit_day"),
                        F.col(_drc).cast("double").alias("_daily_reads"),
                        F.col(_rrc).cast("double").alias("_rejected_reads"),
                        F.col(_nsc).cast("double").alias("_null_reads"),
                        F.col(_rrp).cast("double").alias("_reject_rate_pct"),
                    )
                    .withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
                    .withColumn(
                        "_daily_reject_equiv",
                        F.coalesce(
                            F.col("_rejected_reads"),
                            F.when(
                                F.col("_reject_rate_pct").isNotNull() & F.col("_daily_reads").isNotNull(),
                                F.col("_daily_reads") * F.col("_reject_rate_pct").cast("double") / 100.0,
                            ),
                        ),
                    )
                    .withColumn(
                        "_null_rate_pct",
                        F.when(
                            F.col("_daily_reads") > 0,
                            100.0 * F.col("_null_reads").cast("double") / F.col("_daily_reads").cast("double"),
                        ),
                    )
                )
                S30_REJECT_SOURCE = "rejected_read_count (silver read_tap_device_daily S30)"
                print("TVM S30 source: read_tap_device_daily")
            except Exception as exc:
                print(f"WARNING: read_tap_device_daily unavailable ({exc}); trying read_tap_daily fallback")
                try:
                    _rt_raw = spark.read.parquet(f"{s3_silver}/read_tap_daily/")
                    _rt_lc = {c.casefold(): c for c in _rt_raw.columns}
                    _cat = _rt_lc.get("mars_device_category", "mars_device_category")
                    _did = _rt_lc.get("device_id", "DEVICE_ID")
                    _td = _rt_lc.get("transit_day", "transit_day")
                    _drc = _rt_lc.get("daily_read_count", "daily_read_count")
                    _rrc = _rt_lc.get("rejected_read_count", "rejected_read_count")
                    _nsc = _rt_lc.get("null_status_read_count", "null_status_read_count")
                    df_s30_base = (
                        _rt_raw
                        .where(F.col(_cat) == cfg.device_cat)
                        .where(F.col(_td) >= F.to_date(F.lit(start_day)))
                        .where(F.col(_td) <= end_day_expr)
                        .select(
                            F.col(_did).alias("DEVICE_ID"),
                            F.to_date(F.col(_td)).alias("transit_day"),
                            F.col(_drc).cast("double").alias("_daily_reads"),
                            F.col(_rrc).cast("double").alias("_rejected_reads"),
                            F.col(_nsc).cast("double").alias("_null_reads"),
                        )
                        .withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
                        .withColumn(
                            "_reject_rate_pct",
                            F.when(
                                F.col("_daily_reads") > 0,
                                100.0 * F.col("_rejected_reads") / F.col("_daily_reads"),
                            ),
                        )
                        .withColumn(
                            "_daily_reject_equiv",
                            F.col("_rejected_reads").cast("double"),
                        )
                        .withColumn(
                            "_null_rate_pct",
                            F.when(
                                F.col("_daily_reads") > 0,
                                100.0 * F.col("_null_reads").cast("double") / F.col("_daily_reads").cast("double"),
                            ),
                        )
                    )
                    S30_REJECT_SOURCE = "rejected_read_count (silver read_tap_daily fallback)"
                    print("TVM S30 fallback: read_tap_daily")
                except Exception as exc2:
                    raise RuntimeError(f"Neither read_tap_device_daily nor read_tap_daily available: {exc2}") from exc2

            SOURCE_DERIVATIONS["TVM_TAP_REJECT_SOURCE"] = S30_REJECT_SOURCE
            print(f"TVM tap rejection source: {S30_REJECT_SOURCE}")

            df_read_tap = (
                df_s30_base
                .withColumn("_reads_7d", F.sum("_daily_reads").over(w7))
                .withColumn("_reads_30d", F.sum("_daily_reads").over(w30))
                .withColumn("_reads_90d", F.sum("_daily_reads").over(w90))
                .withColumn("_rejects_7d", F.sum("_daily_reject_equiv").over(w7))
                .withColumn("_rejects_30d", F.sum("_daily_reject_equiv").over(w30))
                .withColumn("_rejects_90d", F.sum("_daily_reject_equiv").over(w90))
                .withColumn("tvm_read_count_prior_7d", F.col("_reads_7d"))
                .withColumn("tvm_read_count_prior_30d", F.col("_reads_30d"))
                .withColumn("tvm_read_count_prior_90d", F.col("_reads_90d"))
                .withColumn(
                    "tvm_reject_rate_prior_7d",
                    F.when(F.col("_reads_7d") > 0, 100.0 * F.col("_rejects_7d") / F.col("_reads_7d")),
                )
                .withColumn(
                    "tvm_reject_rate_prior_30d",
                    F.when(F.col("_reads_30d") > 0, 100.0 * F.col("_rejects_30d") / F.col("_reads_30d")),
                )
                .withColumn(
                    "tvm_reject_rate_prior_90d",
                    F.when(F.col("_reads_90d") > 0, 100.0 * F.col("_rejects_90d") / F.col("_reads_90d")),
                )
                .withColumn(
                    "tvm_reject_rate_trend_7v30d",
                    F.col("tvm_reject_rate_prior_7d") - F.col("tvm_reject_rate_prior_30d"),
                )
                .withColumn("tvm_null_rate_prior_7d", F.avg("_null_rate_pct").over(w7))
                .where(F.col("transit_day") >= F.to_date(F.lit(start_day)))
                .select(*KEY_COLS, *cfg.tap_feature_cols)
            )

            print("TVM S30 prior-only rolling plan created (lazy; no Spark action yet).")

            return df_read_tap

        if cfg.device_cat == 'VALIDATOR':
            # ── VALIDATOR tap signal (silver read_tap_device_daily S30 — BMV coverage) ──
            # GATE uses gold tap_count × tap_reject_rate_pct; VALIDATOR uses S30 device-day collapse.

            SECONDS_PER_DAY = 86_400


            def prior_window(days):
                return (
                    Window.partitionBy("DEVICE_ID")
                    .orderBy(F.col("_day_epoch"))
                    .rangeBetween(-days * SECONDS_PER_DAY, -1)
                )


            w7, w30, w90 = prior_window(7), prior_window(30), prior_window(90)

            S30_REJECT_SOURCE = None
            df_s30_base = None

            try:
                _s30_raw = spark.read.parquet(f"{s3_silver}/read_tap_device_daily/")
                _s30_lc = {c.casefold(): c for c in _s30_raw.columns}
                _cat = _s30_lc.get("mars_device_category", "mars_device_category")
                _did = _s30_lc.get("device_id", "DEVICE_ID")
                _td = _s30_lc.get("transit_day", "transit_day")
                _drc = _s30_lc.get("daily_read_count", "daily_read_count")
                _rrc = _s30_lc.get("rejected_read_count", "rejected_read_count")
                _nsc = _s30_lc.get("null_status_read_count", "null_status_read_count")
                _rrp = _s30_lc.get("reject_rate_pct", "reject_rate_pct")

                df_s30_base = (
                    _s30_raw
                    .where(F.col(_cat) == cfg.device_cat)
                    .where(F.col(_td) >= F.to_date(F.lit(start_day)))
                    .where(F.col(_td) <= end_day_expr)
                    .select(
                        F.col(_did).alias("DEVICE_ID"),
                        F.to_date(F.col(_td)).alias("transit_day"),
                        F.col(_drc).cast("double").alias("_daily_reads"),
                        F.col(_rrc).cast("double").alias("_rejected_reads"),
                        F.col(_nsc).cast("double").alias("_null_reads"),
                        F.col(_rrp).cast("double").alias("_reject_rate_pct"),
                    )
                    .withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
                    .withColumn(
                        "_daily_reject_equiv",
                        F.coalesce(
                            F.col("_rejected_reads"),
                            F.when(
                                F.col("_reject_rate_pct").isNotNull() & F.col("_daily_reads").isNotNull(),
                                F.col("_daily_reads") * F.col("_reject_rate_pct").cast("double") / 100.0,
                            ),
                        ),
                    )
                    .withColumn(
                        "_null_rate_pct",
                        F.when(
                            F.col("_daily_reads") > 0,
                            100.0 * F.col("_null_reads").cast("double") / F.col("_daily_reads").cast("double"),
                        ),
                    )
                )
                S30_REJECT_SOURCE = "rejected_read_count (silver read_tap_device_daily S30)"
                print("VALIDATOR S30 source: read_tap_device_daily")
            except Exception as exc:
                print(f"WARNING: read_tap_device_daily unavailable ({exc}); trying read_tap_daily fallback")
                try:
                    _rt_raw = spark.read.parquet(f"{s3_silver}/read_tap_daily/")
                    _rt_lc = {c.casefold(): c for c in _rt_raw.columns}
                    _cat = _rt_lc.get("mars_device_category", "mars_device_category")
                    _did = _rt_lc.get("device_id", "DEVICE_ID")
                    _td = _rt_lc.get("transit_day", "transit_day")
                    _drc = _rt_lc.get("daily_read_count", "daily_read_count")
                    _rrc = _rt_lc.get("rejected_read_count", "rejected_read_count")
                    _nsc = _rt_lc.get("null_status_read_count", "null_status_read_count")
                    df_s30_base = (
                        _rt_raw
                        .where(F.col(_cat) == cfg.device_cat)
                        .where(F.col(_td) >= F.to_date(F.lit(start_day)))
                        .where(F.col(_td) <= end_day_expr)
                        .select(
                            F.col(_did).alias("DEVICE_ID"),
                            F.to_date(F.col(_td)).alias("transit_day"),
                            F.col(_drc).cast("double").alias("_daily_reads"),
                            F.col(_rrc).cast("double").alias("_rejected_reads"),
                            F.col(_nsc).cast("double").alias("_null_reads"),
                        )
                        .withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
                        .withColumn(
                            "_reject_rate_pct",
                            F.when(
                                F.col("_daily_reads") > 0,
                                100.0 * F.col("_rejected_reads") / F.col("_daily_reads"),
                            ),
                        )
                        .withColumn(
                            "_daily_reject_equiv",
                            F.col("_rejected_reads").cast("double"),
                        )
                        .withColumn(
                            "_null_rate_pct",
                            F.when(
                                F.col("_daily_reads") > 0,
                                100.0 * F.col("_null_reads").cast("double") / F.col("_daily_reads").cast("double"),
                            ),
                        )
                    )
                    S30_REJECT_SOURCE = "rejected_read_count (silver read_tap_daily fallback)"
                    print("VALIDATOR S30 fallback: read_tap_daily")
                except Exception as exc2:
                    raise RuntimeError(f"Neither read_tap_device_daily nor read_tap_daily available: {exc2}") from exc2

            SOURCE_DERIVATIONS["VALIDATOR_TAP_REJECT_SOURCE"] = S30_REJECT_SOURCE
            print(f"VALIDATOR tap rejection source: {S30_REJECT_SOURCE}")

            df_read_tap = (
                df_s30_base
                .withColumn("_reads_7d", F.sum("_daily_reads").over(w7))
                .withColumn("_reads_30d", F.sum("_daily_reads").over(w30))
                .withColumn("_reads_90d", F.sum("_daily_reads").over(w90))
                .withColumn("_rejects_7d", F.sum("_daily_reject_equiv").over(w7))
                .withColumn("_rejects_30d", F.sum("_daily_reject_equiv").over(w30))
                .withColumn("_rejects_90d", F.sum("_daily_reject_equiv").over(w90))
                .withColumn("tvm_read_count_prior_7d", F.col("_reads_7d"))
                .withColumn("tvm_read_count_prior_30d", F.col("_reads_30d"))
                .withColumn("tvm_read_count_prior_90d", F.col("_reads_90d"))
                .withColumn(
                    "tvm_reject_rate_prior_7d",
                    F.when(F.col("_reads_7d") > 0, 100.0 * F.col("_rejects_7d") / F.col("_reads_7d")),
                )
                .withColumn(
                    "tvm_reject_rate_prior_30d",
                    F.when(F.col("_reads_30d") > 0, 100.0 * F.col("_rejects_30d") / F.col("_reads_30d")),
                )
                .withColumn(
                    "tvm_reject_rate_prior_90d",
                    F.when(F.col("_reads_90d") > 0, 100.0 * F.col("_rejects_90d") / F.col("_reads_90d")),
                )
                .withColumn(
                    "tvm_reject_rate_trend_7v30d",
                    F.col("tvm_reject_rate_prior_7d") - F.col("tvm_reject_rate_prior_30d"),
                )
                .withColumn("tvm_null_rate_prior_7d", F.avg("_null_rate_pct").over(w7))
                .where(F.col("transit_day") >= F.to_date(F.lit(start_day)))
                .select(*KEY_COLS, *cfg.tap_feature_cols)
            )

            print("VALIDATOR S30 prior-only rolling plan created (lazy; no Spark action yet).")

            return df_read_tap
        raise RuntimeError(f"No tap builder for fleet {cfg.device_cat}")

    df_read_tap = _build_tap_features(spark, df_ps1, cfg, start_day, end_day_expr, s3_silver)

    return {
        'df_ps1': df_ps1,
        'df_ps2': df_ps2,
        'df_ps4': df_ps4,
        'df_ps5': df_ps5,
        'df_metric': df_metric,
        'df_mttr': df_mttr,
        'df_usage_ext': df_usage_ext,
        'df_read_tap': df_read_tap,
    }

def join_and_materialise(
    spark,
    aux: dict,
    fleet: str,
    *,
    with_label: bool = True,
    materialize: bool = True,
):
    """CELL 8 — joins, grain audit, feature materialization."""
    cfg = FLEET_CONFIG[_fleet_key(fleet)]
    df_ps1 = aux['df_ps1']
    df_ps2 = aux['df_ps2']
    df_ps4 = aux['df_ps4']
    df_ps5 = aux['df_ps5']
    df_metric = aux['df_metric']
    df_mttr = aux['df_mttr']
    df_usage_ext = aux['df_usage_ext']
    df_read_tap = aux['df_read_tap']
    FEATURE_COLS: list[str] = []
    df_features = None

    if cfg.device_cat == 'GATE':
        def enforce_auxiliary_daily_grain(auxiliary, source_name):
            """Return a daily-unique source, or safely skip/error on duplicate join keys."""
            feature_columns = [c for c in auxiliary.columns if c not in KEY_COLS]
            if not feature_columns:
                SOURCE_SKIPS[source_name] = {"reason": "no_usable_feature_columns"}
                print(f"{source_name}: not joined (no usable feature columns)")
                return None

            # Null keys can never match a Spark equality join, so remove them lazily.
            usable = auxiliary.where(
                F.col("DEVICE_ID").isNotNull() & F.col("transit_day").isNotNull()
            )
            stage_name = "grain_audit_" + source_name.rsplit(".", 1)[-1]
            with timed_stage(stage_name):
                duplicate_row = (
                    usable.select(*KEY_COLS)
                    .groupBy(*KEY_COLS)
                    .agg(F.count(F.lit(1)).alias("_rows_per_key"))
                    .where(F.col("_rows_per_key") > 1)
                    .agg(
                        F.count(F.lit(1)).cast("long").alias("duplicate_key_count"),
                        F.coalesce(
                            F.sum(F.col("_rows_per_key") - F.lit(1)), F.lit(0)
                        ).cast("long").alias("extra_source_rows"),
                        F.coalesce(F.max("_rows_per_key"), F.lit(0))
                        .cast("long").alias("max_rows_per_key"),
                    )
                    .first()
                )

            stats = {key: int(value) for key, value in duplicate_row.asDict().items()}
            stats["feature_columns"] = feature_columns
            SOURCE_GRAIN_AUDITS[source_name] = stats

            if stats["duplicate_key_count"]:
                message = (
                    f"{source_name} has {stats['duplicate_key_count']:} duplicate daily keys, "
                    f"{stats['extra_source_rows']:} extra source rows, and up to "
                    f"{stats['max_rows_per_key']:} rows per key."
                )
                if AUXILIARY_DUPLICATE_POLICY == "skip":
                    skip_record = dict(SOURCE_SKIPS.get(source_name, {}))
                    skip_record.update({"reason": "duplicate_daily_keys", **stats})
                    SOURCE_SKIPS[source_name] = skip_record
                    print(f"WARNING: {message} Source skipped; rebuild it at daily grain to restore its features.")
                    return None
                raise RuntimeError(
                    message
                    + " Rebuild the upstream export at one row per DEVICE_ID/transit_day. "
                    + "Do not use dropDuplicates() unless an authoritative row-selection rule exists."
                )

            print(f"{source_name}: daily grain OK; {len(feature_columns)} feature columns eligible")
            return usable


        def left_join_new_features(base, auxiliary, source_name):
            duplicate_names = sorted(
                (set(base.columns) & set(auxiliary.columns)) - set(KEY_COLS)
            )
            if duplicate_names:
                print(f"{source_name}: skipping already-present columns {duplicate_names}")
            new_features = [
                c for c in auxiliary.columns if c not in KEY_COLS and c not in base.columns
            ]
            return base.join(
                auxiliary.select(*KEY_COLS, *new_features),
                on=KEY_COLS,
                how="left",
            )


        df_joined = df_ps1
        JOINED_SOURCES = []
        for source_name, auxiliary in [
            ("gold.device_ps2_chains", df_ps2),
            ("gold.device_ps4_hourly", df_ps4),
            ("gold.device_ps5_component", df_ps5),
            ("gold.tap_event_daily_rolling", df_read_tap),
            ("silver.metric_daily", df_metric),
        ]:
            if auxiliary is None:
                print(f"{source_name}: not joined (source was safely skipped)")
                continue
            auxiliary = enforce_auxiliary_daily_grain(auxiliary, source_name)
            if auxiliary is None:
                continue
            df_joined = left_join_new_features(df_joined, auxiliary, source_name)
            JOINED_SOURCES.append(source_name)


        # --- Silver MTTR / usage joins + Spark prior-window GATE features ---
        if df_mttr is not None and JOIN_KEY_DEVICE in df_joined.columns:
            df_joined = df_joined.join(df_mttr, on=[JOIN_KEY_DEVICE, "transit_day"], how="left")
            print("Joined silver.device_mttr (S28) on DEVICE_KEY")
        elif df_mttr is not None:
            print("WARNING: DEVICE_KEY missing on spine — skipped device_mttr join")

        if df_usage_ext is not None and JOIN_KEY_DEVICE in df_joined.columns:
            df_joined = df_joined.join(df_usage_ext, on=[JOIN_KEY_DEVICE, "transit_day"], how="left")
            print("Joined silver.usage_lifecycle_daily extended on DEVICE_KEY")
        elif df_usage_ext is not None:
            print("WARNING: DEVICE_KEY missing on spine — skipped usage_lifecycle extended join")

        _SPARK_PRIOR_COLS = [
            "gate_mech_events", "csc_reader_events", "comms_events", "system_events",
            "chargeable_outage_count", "chargeable_outage_min", "hardware_oos_count",
        ]
        if "_day_epoch" not in df_joined.columns:
            df_joined = df_joined.withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
        for _pcol in _SPARK_PRIOR_COLS:
            if _pcol in df_joined.columns:
                _w7 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-7 * 86_400, -1)
                _w30 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-30 * 86_400, -1)
                df_joined = (
                    df_joined
                    .withColumn(f"{_pcol}_prior_sum_7d", F.sum(F.col(_pcol).cast("double")).over(_w7))
                    .withColumn(f"{_pcol}_prior_sum_30d", F.sum(F.col(_pcol).cast("double")).over(_w30))
                )
        if "TRANSIT_ARRAY_ID" in df_joined.columns and "hardware_oos_events_7d" in df_joined.columns:
            _w_arr = Window.partitionBy("TRANSIT_ARRAY_ID", "transit_day")
            df_joined = df_joined.withColumn(
                "array_peer_hw_oos_7d", F.avg(F.col("hardware_oos_events_7d").cast("double")).over(_w_arr)
            )
        print("Spark prior-window GATE features materialized (lazy)")


        available = set(df_joined.columns)
        FEATURE_COLS = [c for c in cfg.all_candidate_features if c in available]
        MISSING_FEATURE_COLS = [c for c in cfg.all_candidate_features if c not in available]
        missing_required_after_join = sorted(set(cfg.required_ps1_features) - available)
        if missing_required_after_join:
            raise ValueError(f"Required features are absent after joins: {missing_required_after_join}")

        if MISSING_FEATURE_COLS:
            print(f"Optional features absent ({len(MISSING_FEATURE_COLS)}): {MISSING_FEATURE_COLS}")
        print(f"Features selected: {len(FEATURE_COLS)}")

        # Float32 halves the dense pandas feature memory versus the v2.0 float64 conversion.
        _MLFLOW_ID_COLS = [
            c for c in ["DEVICE_KEY", "mars_device_category", "FACILITY_ID", "TRANSIT_ARRAY_ID"]
            if c in df_joined.columns
        ]
        _select_cols = ["DEVICE_ID", *_MLFLOW_ID_COLS, "transit_day"]
        if with_label and TARGET_COL in df_joined.columns:
            _select_cols.append(F.col(TARGET_COL).cast("byte").alias(TARGET_COL))
        df_features = df_joined.select(
            *_select_cols,
            *[F.col(c).cast("float").alias(c) for c in FEATURE_COLS],
        )

        if materialize:
            with timed_stage("spark_spine_count"):
                SPINE_ROW_COUNT = df_ps1.select(*KEY_COLS).count()

            if _notebook_flag("RUN_DEEP_GRAIN_AUDIT"):
                duplicate_spine_keys = (
                    df_ps1.groupBy(*KEY_COLS)
                    .count()
                    .where(F.col("count") > 1)
                    .limit(10)
                    .collect()
                )
                if duplicate_spine_keys:
                    raise RuntimeError(
                        "PS1 itself contains duplicate (DEVICE_ID, transit_day) keys. "
                        f"Examples: {duplicate_spine_keys}"
                    )

            if _notebook_flag("IS_LOCAL_SPARK"):
                # Small disk-backed blocks avoid the MemoryStore heap failure seen with local[16].
                df_features = df_features.repartition(_notebook_val("shuffle_partitions", shuffle_partitions), "DEVICE_ID")

            if materialize and _notebook_flag("CACHE_FEATURE_FRAME"):
                FEATURE_CACHE_LEVEL = (
                    StorageLevel.DISK_ONLY if IS_LOCAL_SPARK else StorageLevel.MEMORY_AND_DISK
                )
                FEATURE_CACHE_LEVEL_NAME = "DISK_ONLY" if IS_LOCAL_SPARK else "MEMORY_AND_DISK"
                print(f"Feature persistence  : {FEATURE_CACHE_LEVEL_NAME}")
                df_features = df_features.persist(FEATURE_CACHE_LEVEL)
            else:
                FEATURE_CACHE_LEVEL_NAME = "disabled"

            with timed_stage("spark_materialize_and_audit"):
                audit = df_features.agg(
                    F.count("*").alias("row_count"),
                    F.approx_count_distinct("DEVICE_ID", rsd=0.02).alias("approx_device_count"),
                    F.sum(F.col(TARGET_COL).cast("long")).alias("positive_count"),
                    F.min("transit_day").alias("min_day"),
                    F.max("transit_day").alias("max_day"),
                ).first().asDict()

            audit["spine_row_count"] = int(SPINE_ROW_COUNT)
            audit["join_row_delta"] = int(audit["row_count"] - SPINE_ROW_COUNT)

            if audit["row_count"] != SPINE_ROW_COUNT:
                raise RuntimeError(
                    "Backstop join expansion detected after the per-source grain gates. "
                    f"final={audit['row_count']:}, spine={SPINE_ROW_COUNT:}, "
                    f"delta={audit['join_row_delta']:}. Inspect SOURCE_GRAIN_AUDITS before training."
                )
            if not audit["row_count"]:
                raise RuntimeError("No eligible rows remain after filters.")
            if not audit["positive_count"]:
                raise RuntimeError("No positive labels remain after filters.")

            print(json.dumps(audit, indent=2, default=str))
            print(f"Positive rate: {100.0 * audit['positive_count'] / audit['row_count']:.4f}%")

        # MTTR/usage joined above on df_joined before feature selection

    elif cfg.device_cat == 'TVM':
        def enforce_auxiliary_daily_grain(auxiliary, source_name):
            """Return a daily-unique source, or safely skip/error on duplicate join keys."""
            feature_columns = [c for c in auxiliary.columns if c not in KEY_COLS]
            if not feature_columns:
                SOURCE_SKIPS[source_name] = {"reason": "no_usable_feature_columns"}
                print(f"{source_name}: not joined (no usable feature columns)")
                return None

            # Null keys can never match a Spark equality join, so remove them lazily.
            usable = auxiliary.where(
                F.col("DEVICE_ID").isNotNull() & F.col("transit_day").isNotNull()
            )
            stage_name = "grain_audit_" + source_name.rsplit(".", 1)[-1]
            with timed_stage(stage_name):
                duplicate_row = (
                    usable.select(*KEY_COLS)
                    .groupBy(*KEY_COLS)
                    .agg(F.count(F.lit(1)).alias("_rows_per_key"))
                    .where(F.col("_rows_per_key") > 1)
                    .agg(
                        F.count(F.lit(1)).cast("long").alias("duplicate_key_count"),
                        F.coalesce(
                            F.sum(F.col("_rows_per_key") - F.lit(1)), F.lit(0)
                        ).cast("long").alias("extra_source_rows"),
                        F.coalesce(F.max("_rows_per_key"), F.lit(0))
                        .cast("long").alias("max_rows_per_key"),
                    )
                    .first()
                )

            stats = {key: int(value) for key, value in duplicate_row.asDict().items()}
            stats["feature_columns"] = feature_columns
            SOURCE_GRAIN_AUDITS[source_name] = stats

            if stats["duplicate_key_count"]:
                message = (
                    f"{source_name} has {stats['duplicate_key_count']:} duplicate daily keys, "
                    f"{stats['extra_source_rows']:} extra source rows, and up to "
                    f"{stats['max_rows_per_key']:} rows per key."
                )
                if AUXILIARY_DUPLICATE_POLICY == "skip":
                    skip_record = dict(SOURCE_SKIPS.get(source_name, {}))
                    skip_record.update({"reason": "duplicate_daily_keys", **stats})
                    SOURCE_SKIPS[source_name] = skip_record
                    print(f"WARNING: {message} Source skipped; rebuild it at daily grain to restore its features.")
                    return None
                raise RuntimeError(
                    message
                    + " Rebuild the upstream export at one row per DEVICE_ID/transit_day. "
                    + "Do not use dropDuplicates() unless an authoritative row-selection rule exists."
                )

            print(f"{source_name}: daily grain OK; {len(feature_columns)} feature columns eligible")
            return usable


        def left_join_new_features(base, auxiliary, source_name):
            duplicate_names = sorted(
                (set(base.columns) & set(auxiliary.columns)) - set(KEY_COLS)
            )
            if duplicate_names:
                print(f"{source_name}: skipping already-present columns {duplicate_names}")
            new_features = [
                c for c in auxiliary.columns if c not in KEY_COLS and c not in base.columns
            ]
            return base.join(
                auxiliary.select(*KEY_COLS, *new_features),
                on=KEY_COLS,
                how="left",
            )


        df_joined = df_ps1
        JOINED_SOURCES = []
        for source_name, auxiliary in [
            ("gold.device_ps2_chains", df_ps2),
            ("gold.device_ps4_hourly", df_ps4),
            ("gold.device_ps5_component", df_ps5),
            ("gold.tap_event_daily_rolling", df_read_tap),
            ("silver.metric_daily", df_metric),
        ]:
            if auxiliary is None:
                print(f"{source_name}: not joined (source was safely skipped)")
                continue
            auxiliary = enforce_auxiliary_daily_grain(auxiliary, source_name)
            if auxiliary is None:
                continue
            df_joined = left_join_new_features(df_joined, auxiliary, source_name)
            JOINED_SOURCES.append(source_name)


        # --- Silver MTTR / usage joins + Spark prior-window TVM features ---
        if df_mttr is not None and JOIN_KEY_DEVICE in df_joined.columns:
            df_joined = df_joined.join(df_mttr, on=[JOIN_KEY_DEVICE, "transit_day"], how="left")
            print("Joined silver.device_mttr (S28) on DEVICE_KEY")
        elif df_mttr is not None:
            print("WARNING: DEVICE_KEY missing on spine — skipped device_mttr join")

        if df_usage_ext is not None and JOIN_KEY_DEVICE in df_joined.columns:
            df_joined = df_joined.join(df_usage_ext, on=[JOIN_KEY_DEVICE, "transit_day"], how="left")
            print("Joined silver.usage_lifecycle_daily extended on DEVICE_KEY")
        elif df_usage_ext is not None:
            print("WARNING: DEVICE_KEY missing on spine — skipped usage_lifecycle extended join")

        _SPARK_PRIOR_COLS = [
            "printer_events", "bankcard_events", "bhu_events", "chu_events", "scrst_events",
            "system_events", "comms_events", "csc_reader_events",
            "chargeable_outage_count", "hardware_oos_count",
        ]
        if "_day_epoch" not in df_joined.columns:
            df_joined = df_joined.withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
        for _pcol in _SPARK_PRIOR_COLS:
            if _pcol in df_joined.columns:
                _w7 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-7 * 86_400, -1)
                _w30 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-30 * 86_400, -1)
                df_joined = (
                    df_joined
                    .withColumn(f"{_pcol}_prior_sum_7d", F.sum(F.col(_pcol).cast("double")).over(_w7))
                    .withColumn(f"{_pcol}_prior_sum_30d", F.sum(F.col(_pcol).cast("double")).over(_w30))
                )
        if "FACILITY_ID" in df_joined.columns and "hardware_oos_events_7d" in df_joined.columns:
            _w_fac = Window.partitionBy("FACILITY_ID", "transit_day")
            df_joined = df_joined.withColumn(
                "facility_peer_hw_oos_7d", F.avg(F.col("hardware_oos_events_7d").cast("double")).over(_w_fac)
            )
        print("Spark prior-window TVM features materialized (lazy)")


        available = set(df_joined.columns)
        FEATURE_COLS = [c for c in cfg.all_candidate_features if c in available]
        MISSING_FEATURE_COLS = [c for c in cfg.all_candidate_features if c not in available]
        missing_required_after_join = sorted(set(cfg.required_ps1_features) - available)
        if missing_required_after_join:
            raise ValueError(f"Required features are absent after joins: {missing_required_after_join}")

        if MISSING_FEATURE_COLS:
            print(f"Optional features absent ({len(MISSING_FEATURE_COLS)}): {MISSING_FEATURE_COLS}")
        print(f"Features selected: {len(FEATURE_COLS)}")

        # Float32 halves the dense pandas feature memory versus the v2.0 float64 conversion.
        _MLFLOW_ID_COLS = [
            c for c in ["DEVICE_KEY", "mars_device_category", "FACILITY_ID", "TRANSIT_ARRAY_ID"]
            if c in df_joined.columns
        ]
        _select_cols = ["DEVICE_ID", *_MLFLOW_ID_COLS, "transit_day"]
        if with_label and TARGET_COL in df_joined.columns:
            _select_cols.append(F.col(TARGET_COL).cast("byte").alias(TARGET_COL))
        df_features = df_joined.select(
            *_select_cols,
            *[F.col(c).cast("float").alias(c) for c in FEATURE_COLS],
        )

        if materialize:
            with timed_stage("spark_spine_count"):
                SPINE_ROW_COUNT = df_ps1.select(*KEY_COLS).count()

            if _notebook_flag("RUN_DEEP_GRAIN_AUDIT"):
                duplicate_spine_keys = (
                    df_ps1.groupBy(*KEY_COLS)
                    .count()
                    .where(F.col("count") > 1)
                    .limit(10)
                    .collect()
                )
                if duplicate_spine_keys:
                    raise RuntimeError(
                        "PS1 itself contains duplicate (DEVICE_ID, transit_day) keys. "
                        f"Examples: {duplicate_spine_keys}"
                    )

            if _notebook_flag("IS_LOCAL_SPARK"):
                # Small disk-backed blocks avoid the MemoryStore heap failure seen with local[16].
                df_features = df_features.repartition(_notebook_val("shuffle_partitions", shuffle_partitions), "DEVICE_ID")

            if materialize and _notebook_flag("CACHE_FEATURE_FRAME"):
                FEATURE_CACHE_LEVEL = (
                    StorageLevel.DISK_ONLY if IS_LOCAL_SPARK else StorageLevel.MEMORY_AND_DISK
                )
                FEATURE_CACHE_LEVEL_NAME = "DISK_ONLY" if IS_LOCAL_SPARK else "MEMORY_AND_DISK"
                print(f"Feature persistence  : {FEATURE_CACHE_LEVEL_NAME}")
                df_features = df_features.persist(FEATURE_CACHE_LEVEL)
            else:
                FEATURE_CACHE_LEVEL_NAME = "disabled"

            with timed_stage("spark_materialize_and_audit"):
                audit = df_features.agg(
                    F.count("*").alias("row_count"),
                    F.approx_count_distinct("DEVICE_ID", rsd=0.02).alias("approx_device_count"),
                    F.sum(F.col(TARGET_COL).cast("long")).alias("positive_count"),
                    F.min("transit_day").alias("min_day"),
                    F.max("transit_day").alias("max_day"),
                ).first().asDict()

            audit["spine_row_count"] = int(SPINE_ROW_COUNT)
            audit["join_row_delta"] = int(audit["row_count"] - SPINE_ROW_COUNT)

            if audit["row_count"] != SPINE_ROW_COUNT:
                raise RuntimeError(
                    "Backstop join expansion detected after the per-source grain gates. "
                    f"final={audit['row_count']:}, spine={SPINE_ROW_COUNT:}, "
                    f"delta={audit['join_row_delta']:}. Inspect SOURCE_GRAIN_AUDITS before training."
                )
            if not audit["row_count"]:
                raise RuntimeError("No eligible rows remain after filters.")
            if not audit["positive_count"]:
                raise RuntimeError("No positive labels remain after filters.")

            print(json.dumps(audit, indent=2, default=str))
            print(f"Positive rate: {100.0 * audit['positive_count'] / audit['row_count']:.4f}%")

        # MTTR/usage joined above on df_joined before feature selection

    elif cfg.device_cat == 'VALIDATOR':
        def enforce_auxiliary_daily_grain(auxiliary, source_name):
            """Return a daily-unique source, or safely skip/error on duplicate join keys."""
            feature_columns = [c for c in auxiliary.columns if c not in KEY_COLS]
            if not feature_columns:
                SOURCE_SKIPS[source_name] = {"reason": "no_usable_feature_columns"}
                print(f"{source_name}: not joined (no usable feature columns)")
                return None

            # Null keys can never match a Spark equality join, so remove them lazily.
            usable = auxiliary.where(
                F.col("DEVICE_ID").isNotNull() & F.col("transit_day").isNotNull()
            )
            stage_name = "grain_audit_" + source_name.rsplit(".", 1)[-1]
            with timed_stage(stage_name):
                duplicate_row = (
                    usable.select(*KEY_COLS)
                    .groupBy(*KEY_COLS)
                    .agg(F.count(F.lit(1)).alias("_rows_per_key"))
                    .where(F.col("_rows_per_key") > 1)
                    .agg(
                        F.count(F.lit(1)).cast("long").alias("duplicate_key_count"),
                        F.coalesce(
                            F.sum(F.col("_rows_per_key") - F.lit(1)), F.lit(0)
                        ).cast("long").alias("extra_source_rows"),
                        F.coalesce(F.max("_rows_per_key"), F.lit(0))
                        .cast("long").alias("max_rows_per_key"),
                    )
                    .first()
                )

            stats = {key: int(value) for key, value in duplicate_row.asDict().items()}
            stats["feature_columns"] = feature_columns
            SOURCE_GRAIN_AUDITS[source_name] = stats

            if stats["duplicate_key_count"]:
                message = (
                    f"{source_name} has {stats['duplicate_key_count']:,} duplicate daily keys, "
                    f"{stats['extra_source_rows']:,} extra source rows, and up to "
                    f"{stats['max_rows_per_key']:,} rows per key."
                )
                if AUXILIARY_DUPLICATE_POLICY == "skip":
                    skip_record = dict(SOURCE_SKIPS.get(source_name, {}))
                    skip_record.update({"reason": "duplicate_daily_keys", **stats})
                    SOURCE_SKIPS[source_name] = skip_record
                    print(f"WARNING: {message} Source skipped; rebuild it at daily grain to restore its features.")
                    return None
                raise RuntimeError(
                    message
                    + " Rebuild the upstream export at one row per DEVICE_ID/transit_day. "
                    + "Do not use dropDuplicates() unless an authoritative row-selection rule exists."
                )

            print(f"{source_name}: daily grain OK; {len(feature_columns)} feature columns eligible")
            return usable


        def left_join_new_features(base, auxiliary, source_name):
            duplicate_names = sorted(
                (set(base.columns) & set(auxiliary.columns)) - set(KEY_COLS)
            )
            if duplicate_names:
                print(f"{source_name}: skipping already-present columns {duplicate_names}")
            new_features = [
                c for c in auxiliary.columns if c not in KEY_COLS and c not in base.columns
            ]
            return base.join(
                auxiliary.select(*KEY_COLS, *new_features),
                on=KEY_COLS,
                how="left",
            )


        df_joined = df_ps1
        JOINED_SOURCES = []
        for source_name, auxiliary in [
            ("gold.device_ps2_chains", df_ps2),
            ("gold.device_ps4_hourly", df_ps4),
            ("gold.device_ps5_component", df_ps5),
            ("gold.tap_event_daily_rolling", df_read_tap),
            ("silver.metric_daily", df_metric),
        ]:
            if auxiliary is None:
                print(f"{source_name}: not joined (source was safely skipped)")
                continue
            auxiliary = enforce_auxiliary_daily_grain(auxiliary, source_name)
            if auxiliary is None:
                continue
            df_joined = left_join_new_features(df_joined, auxiliary, source_name)
            JOINED_SOURCES.append(source_name)


        # --- Silver MTTR / usage joins + Spark prior-window VALIDATOR features ---
        if df_mttr is not None and JOIN_KEY_DEVICE in df_joined.columns:
            df_joined = df_joined.join(df_mttr, on=[JOIN_KEY_DEVICE, "transit_day"], how="left")
            print("Joined silver.device_mttr (S28) on DEVICE_KEY")
        elif df_mttr is not None:
            print("WARNING: DEVICE_KEY missing on spine — skipped device_mttr join")

        if df_usage_ext is not None and JOIN_KEY_DEVICE in df_joined.columns:
            df_joined = df_joined.join(df_usage_ext, on=[JOIN_KEY_DEVICE, "transit_day"], how="left")
            print("Joined silver.usage_lifecycle_daily extended on DEVICE_KEY")
        elif df_usage_ext is not None:
            print("WARNING: DEVICE_KEY missing on spine — skipped usage_lifecycle extended join")

        _SPARK_PRIOR_COLS = [
            "printer_events", "bankcard_events", "bhu_events", "chu_events", "scrst_events",
            "system_events", "comms_events", "csc_reader_events",
            "chargeable_outage_count", "hardware_oos_count",
        ]
        if "_day_epoch" not in df_joined.columns:
            df_joined = df_joined.withColumn("_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
        for _pcol in _SPARK_PRIOR_COLS:
            if _pcol in df_joined.columns:
                _w7 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-7 * 86_400, -1)
                _w30 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-30 * 86_400, -1)
                df_joined = (
                    df_joined
                    .withColumn(f"{_pcol}_prior_sum_7d", F.sum(F.col(_pcol).cast("double")).over(_w7))
                    .withColumn(f"{_pcol}_prior_sum_30d", F.sum(F.col(_pcol).cast("double")).over(_w30))
                )
        if "FACILITY_ID" in df_joined.columns and "hardware_oos_events_7d" in df_joined.columns:
            _w_fac = Window.partitionBy("FACILITY_ID", "transit_day")
            df_joined = df_joined.withColumn(
                "facility_peer_hw_oos_7d", F.avg(F.col("hardware_oos_events_7d").cast("double")).over(_w_fac)
            )

        # --- VALIDATOR station-cascade features from PS2 (shift-before-rolling; leakage-safe) ---
        if "is_coordinated_station_failure" in df_joined.columns:
            if "_day_epoch" not in df_joined.columns:
                df_joined = df_joined.withColumn(
                    "_day_epoch", F.col("transit_day").cast("timestamp").cast("long")
                )
            _w_dev = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
            _w30 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-30 * 86_400, -1)
            df_joined = (
                df_joined
                .withColumn("_cascade_flag", F.lit(1))
                .withColumn("_coord_flag", F.col("is_coordinated_station_failure").cast("int"))
                .withColumn("_cascade_flag_lag", F.coalesce(F.lag("_cascade_flag", 1).over(_w_dev), F.lit(0)))
                .withColumn("_coord_flag_lag", F.coalesce(F.lag("_coord_flag", 1).over(_w_dev), F.lit(0)))
                .withColumn(
                    "station_cascade_days_30d",
                    F.sum(F.col("_cascade_flag_lag").cast("double")).over(_w30),
                )
                .withColumn(
                    "station_coordinated_fail_days_30d",
                    F.sum(F.col("_coord_flag_lag").cast("double")).over(_w30),
                )
                .withColumn(
                    "_last_cascade_date",
                    F.last(
                        F.when(F.col("_cascade_flag_lag") == 1, F.col("transit_day")),
                        ignorenulls=True,
                    ).over(_w_dev.rowsBetween(Window.unboundedPreceding, -1)),
                )
                .withColumn(
                    "days_since_station_cascade",
                    F.when(
                        F.col("_last_cascade_date").isNotNull(),
                        F.least(
                            F.datediff(F.col("transit_day"), F.col("_last_cascade_date")),
                            F.lit(999),
                        ).cast("double"),
                    ).otherwise(F.lit(999.0)),
                )
                .drop("_cascade_flag", "_coord_flag", "_cascade_flag_lag", "_coord_flag_lag", "_last_cascade_date")
            )
            print("VALIDATOR station-cascade features materialized from PS2 (lazy)")


        print("Spark prior-window VALIDATOR features materialized (lazy)")


        available = set(df_joined.columns)
        FEATURE_COLS = [c for c in cfg.all_candidate_features if c in available]
        MISSING_FEATURE_COLS = [c for c in cfg.all_candidate_features if c not in available]
        missing_required_after_join = sorted(set(cfg.required_ps1_features) - available)
        if missing_required_after_join:
            raise ValueError(f"Required features are absent after joins: {missing_required_after_join}")

        if MISSING_FEATURE_COLS:
            print(f"Optional features absent ({len(MISSING_FEATURE_COLS)}): {MISSING_FEATURE_COLS}")
        print(f"Features selected: {len(FEATURE_COLS)}")

        # Float32 halves the dense pandas feature memory versus the v2.0 float64 conversion.
        _MLFLOW_ID_COLS = [
            c for c in ["DEVICE_KEY", "mars_device_category", "FACILITY_ID", "TRANSIT_ARRAY_ID"]
            if c in df_joined.columns
        ]
        _select_cols = ["DEVICE_ID", *_MLFLOW_ID_COLS, "transit_day"]
        if with_label and TARGET_COL in df_joined.columns:
            _select_cols.append(F.col(TARGET_COL).cast("byte").alias(TARGET_COL))
        df_features = df_joined.select(
            *_select_cols,
            *[F.col(c).cast("float").alias(c) for c in FEATURE_COLS],
        )

        if materialize:
            with timed_stage("spark_spine_count"):
                SPINE_ROW_COUNT = df_ps1.select(*KEY_COLS).count()

            if _notebook_flag("RUN_DEEP_GRAIN_AUDIT"):
                duplicate_spine_keys = (
                    df_ps1.groupBy(*KEY_COLS)
                    .count()
                    .where(F.col("count") > 1)
                    .limit(10)
                    .collect()
                )
                if duplicate_spine_keys:
                    raise RuntimeError(
                        "PS1 itself contains duplicate (DEVICE_ID, transit_day) keys. "
                        f"Examples: {duplicate_spine_keys}"
                    )

            if _notebook_flag("IS_LOCAL_SPARK"):
                # Small disk-backed blocks avoid the MemoryStore heap failure seen with local[16].
                df_features = df_features.repartition(_notebook_val("shuffle_partitions", shuffle_partitions), "DEVICE_ID")

            if materialize and _notebook_flag("CACHE_FEATURE_FRAME"):
                FEATURE_CACHE_LEVEL = (
                    StorageLevel.DISK_ONLY if IS_LOCAL_SPARK else StorageLevel.MEMORY_AND_DISK
                )
                FEATURE_CACHE_LEVEL_NAME = "DISK_ONLY" if IS_LOCAL_SPARK else "MEMORY_AND_DISK"
                print(f"Feature persistence  : {FEATURE_CACHE_LEVEL_NAME}")
                df_features = df_features.persist(FEATURE_CACHE_LEVEL)
            else:
                FEATURE_CACHE_LEVEL_NAME = "disabled"

            with timed_stage("spark_materialize_and_audit"):
                audit = df_features.agg(
                    F.count("*").alias("row_count"),
                    F.approx_count_distinct("DEVICE_ID", rsd=0.02).alias("approx_device_count"),
                    F.sum(F.col(TARGET_COL).cast("long")).alias("positive_count"),
                    F.min("transit_day").alias("min_day"),
                    F.max("transit_day").alias("max_day"),
                ).first().asDict()

            audit["spine_row_count"] = int(SPINE_ROW_COUNT)
            audit["join_row_delta"] = int(audit["row_count"] - SPINE_ROW_COUNT)

            if audit["row_count"] != SPINE_ROW_COUNT:
                raise RuntimeError(
                    "Backstop join expansion detected after the per-source grain gates. "
                    f"final={audit['row_count']:,}, spine={SPINE_ROW_COUNT:,}, "
                    f"delta={audit['join_row_delta']:,}. Inspect SOURCE_GRAIN_AUDITS before training."
                )
            if not audit["row_count"]:
                raise RuntimeError("No eligible rows remain after filters.")
            if not audit["positive_count"]:
                raise RuntimeError("No positive labels remain after filters.")

            print(json.dumps(audit, indent=2, default=str))
            print(f"Positive rate: {100.0 * audit['positive_count'] / audit['row_count']:.4f}%")

        # MTTR/usage joined above on df_joined before feature selection

    else:
        raise RuntimeError(f"No CELL 8 join path for fleet {cfg.device_cat}")

    if df_features is None:
        raise RuntimeError("join_and_materialise did not build df_features")
    return df_features, FEATURE_COLS

