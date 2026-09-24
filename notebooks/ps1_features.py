# Shared PS1 feature ETL — extracted verbatim from fleet notebook CELLS 6-8.
# Do not edit by hand; regenerate with tooling/build_ps1_features_module.py
from __future__ import annotations

import datetime as _dt
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
LABEL_HORIZON_DAYS = int(os.environ.get("PS1_LABEL_HORIZON_DAYS", "3"))
LABEL_DATA_LAG_DAYS = 0

# PS1_EVENT_DEFINITION  hardware_oos | ventra_kpi   (default preserves current runs)
#   hardware_oos : every is_hardware_oos_event Set. 34 of 185 matrix codes qualify,
#                  and the resulting label runs 62-91% positive.
#   ventra_kpi   : only codes Cubic counts toward that fleet's Ventra availability
#                  KPI -- oos_counted_gate_kpi / _bus_kpi / _fmvd_kpi, already
#                  carried on device_event_enriched (S16). 13 / 12 / 21 codes.
# PS1_EXCLUDE_RELIEVED  true drops failure days covered by a relief record or an
#   EXCLUDED flag in kpi_avail_enriched -- downtime Cubic itself excused.
PS1_EVENT_DEFINITION_DEFAULT = "hardware_oos"
EVENT_DEFINITIONS = ("hardware_oos", "ventra_kpi")
KPI_FLAG_BY_FLEET = {"GATE": "oos_counted_gate_kpi",
                     "TVM": "oos_counted_fmvd_kpi",
                     "VALIDATOR": "oos_counted_bus_kpi"}

# PS1_LABEL_MODE  state | onset   (default "state" -- preserves the 28-Jul decision)
#   state : will_hardware_oos_3d as built -- marks every day of an out-of-service spell.
#           95-98% of positives merely follow another positive (measured in sql/35).
#   onset : the TRANSITION into a spell only, with in-spell rows dropped from train
#           and score. This is the retraining sql/35 specifies; it is opt-in so that
#           nothing changes unless the run asks for it.
PS1_LABEL_MODE_DEFAULT = "state"


def _event_definition() -> str:
    d = os.environ.get("PS1_EVENT_DEFINITION", PS1_EVENT_DEFINITION_DEFAULT).strip().lower()
    if d not in EVENT_DEFINITIONS:
        raise ValueError(f"PS1_EVENT_DEFINITION must be one of {EVENT_DEFINITIONS}, got {d!r}")
    return d


def _exclude_relieved() -> bool:
    return os.environ.get("PS1_EXCLUDE_RELIEVED", "false").strip().lower() == "true"


def _exclude_failure_history() -> bool:
    """Withhold EVERY feature derived from the device's own OOS failure history.
    Default OFF. This is a DIAGNOSTIC, not a recommended configuration.

    Why it exists: the 23-Sep guard run showed the model had been predicting the FEED
    GAP rather than the device. Suppressing 2026-04's manufactured starts made that
    month HARDER -- 0.8259 -> 0.7096 -- while every other month moved less than 0.004,
    which can only happen if those starts were being predicted better than chance.
    `roll_fail_90d` sits at SHAP #2 (0.499). During a coverage outage every device's
    failure counters fall together, so the family identifies that the FLEET is inside a
    gap, and the start that follows a gap is then trivially predictable.

    PS1_EXCLUDE_GAP_FEATURES removes only the features that encode the 3-day
    sessionisation window. This removes the whole family, so the question it answers is:
    how much of the remaining AUC is device-level risk, and how much is the model
    noticing an outage?

    Read the MONTHLY table, not the headline. If only 2026-04 and 2025-03 fall, the
    family was carrying gap detection. If every month falls together, it was carrying
    genuine signal and should be kept.
    """
    return os.environ.get("PS1_EXCLUDE_FAILURE_HISTORY", "false").strip().lower() == "true"


def _exclude_gap_features() -> bool:
    """Withhold features that encode the sessionisation gap rather than predict it.

    An episode start requires more than 3 days since the last failure day, so a
    time-since-last-failure feature determines whether one is possible. On
    VALIDATOR this produces AUC 0.9973 with lift flat at 1/base_rate.
    """
    return os.environ.get("PS1_EXCLUDE_GAP_FEATURES", "false").strip().lower() == "true"


def _evq_prior_only() -> bool:
    """Drop the event-quality same-day counts, keeping only their prior windows.

    evq_oos_sets and evq_comp_serials are same-day, and the label is sessionised
    with a 3-day gap -- a device with failure days today cannot start an episode
    for three days, so these partly determine the label mechanically. Set this to
    measure how much of the gain is real device behaviour.
    """
    return os.environ.get("PS1_EVQ_PRIOR_ONLY", "false").strip().lower() == "true"


def _enable_kpi_features() -> bool:
    """silver.kpi_daily contract-performance features. Default OFF.

    Measured coverage: TVM 7.7% of device-days, GATE 0.3%, VALIDATOR 0. Expect it
    to help TVM and do nothing elsewhere.
    """
    return os.environ.get("PS1_ENABLE_KPI_FEATURES", "false").strip().lower() == "true"


def _enable_event_quality_features() -> bool:
    """device_event_enriched duration + component attribution. Default OFF.

    Adds a second scan of a 259M-row table; expect a slower ETL cell.
    """
    return os.environ.get("PS1_ENABLE_EVENT_QUALITY_FEATURES", "false").strip().lower() == "true"


def _enable_m401_features() -> bool:
    """silver.metric_daily real M401/comms columns. Default OFF until measured."""
    return os.environ.get("PS1_ENABLE_M401_FEATURES", "false").strip().lower() == "true"


def _enable_usage_ext_features() -> bool:
    """The rest of silver.usage_lifecycle_daily. Default OFF until measured."""
    return os.environ.get("PS1_ENABLE_USAGE_EXT_FEATURES", "false").strip().lower() == "true"


def _enable_avail_features() -> bool:
    """silver.kpi_avail_enriched outage rollups. Default OFF until measured."""
    return os.environ.get("PS1_ENABLE_AVAIL_FEATURES", "false").strip().lower() == "true"


def _enable_warning_features() -> bool:
    """silver.warnings_daily -- degradation precursors. Default OFF until measured."""
    return os.environ.get("PS1_ENABLE_WARNING_FEATURES", "false").strip().lower() == "true"


def _enable_station_features() -> bool:
    """silver.station_network_daily -- station co-failure context. Default OFF until measured."""
    return os.environ.get("PS1_ENABLE_STATION_FEATURES", "false").strip().lower() == "true"


def _enable_uptime_features() -> bool:
    """silver.device_uptime_intervals -- end-of-day message volume. Default OFF until measured."""
    return os.environ.get("PS1_ENABLE_UPTIME_FEATURES", "false").strip().lower() == "true"


def _completeness_guard() -> bool:
    """Count OBSERVED days rather than calendar days when sessionising. Default OFF.

    The gap rule cannot tell a device recovering from a gap in the DATA FEED, so when
    ingestion drops days the first failure after the feed resumes scores as a new
    episode start. Measured over 38 months of TVM: 2024-03 (2.10 starts/device),
    2025-11 (1.43), 2026-04 (1.08) and 2025-03 (1.05) against 0.11-0.34 in normal
    months -- together ~28% of all 9,337 starts, and 2026-04 is inside the test window.

    The guard is a PURE SUPPRESSOR by construction: obs_idx advances at most once per
    calendar day, so the observed-day delta is always <= datediff and the new predicate
    accepts a strict subset of what the calendar rule accepts. It can never mint a start.

    Consequence for evaluation: guard-on and guard-off runs are scored against DIFFERENT
    labels with different positive counts, and manufactured starts are known to dilute
    discrimination toward 0.5. A guard-on AUC rise is therefore expected for reasons
    unrelated to model skill. NEVER report a guard-on-vs-guard-off AUC delta as evidence
    the guard works; compare only runs sharing one fixed guard-on label.
    """
    return os.environ.get("PS1_COMPLETENESS_GUARD", "false").strip().lower() == "true"


def _coverage_signal() -> str:
    """Which quantity decides whether a day was observed. "fleet" | "device".

    fleet  -- distinct devices reporting ANY event that day, against PS1_COVERAGE_FLOOR
              times the median. Cheap, but it measures NETWORK PRESENCE, and a device
              still emitting some codes stays present even when the codes the label
              reads have stopped. It may be inert against a code-selective loss.
    device -- per-device days-present. Needs no threshold at all, and catches a subset
              of devices going dark, which a fleet average hides.

    Neither is validated. Run notebooks/ps1_failure_prediction/ps1_coverage_probe.py
    first; it measures both and reports what each would suppress per month.
    """
    v = os.environ.get("PS1_COVERAGE_SIGNAL", "fleet").strip().lower()
    if v not in ("fleet", "device"):
        raise ValueError(f"PS1_COVERAGE_SIGNAL must be 'fleet' or 'device', got {v!r}")
    return v


def _coverage_floor() -> float:
    """Fraction of the median device count below which a day counts as unobserved.

    Only used by the "fleet" signal. Raising this is NOT free: because obs_idx is a
    cumulative sum, a single day wrongly marked unobserved inside a gap stops the index
    advancing, and since the minimum qualifying gap is exactly 4 calendar days, that
    collapses the delta to 3 and silently MERGES two genuine episodes. A lost true
    positive is worse than a manufactured start, which is merely orthogonal noise.

    MEASURED 23-Sep-2026 on TVM, 38 months (ps1_coverage_probe.py). Median 442 devices
    per day. The distribution is BIMODAL and 0.5 sits in an empty band: no day in the
    whole series has a device count between 221 (0.50x) and 309 (0.70x), so those two
    floors give identical results, and the lowest monthly minimum outside a known
    outage is 332. A threshold in an empty band cannot be tripped by ordinary variance,
    which is what makes the merge hazard above tolerable here.

    The acceptance test at each floor, against 9,337 calendar-rule starts:
        0.90  2,311 suppressed, but only 68.3% in the four known-bad months --
              31.7% COLLATERAL, including 245 of 2024-04's 344 starts. Fails.
        0.50    934 suppressed, 99.0% of it in two months, 9 starts elsewhere. Passes.
    Do not raise this above 0.5 without re-running the probe.
    """
    return float(os.environ.get("PS1_COVERAGE_FLOOR", "0.5"))


def _coverage_lookback_days() -> int:
    """Calendar days read BEFORE the frame's min_day so the first in-window failure day
    has a real predecessor instead of being minted as a start by the window boundary.

    Measured: 2023-07, the first month of the series, carries 557 starts for this reason
    alone. The requirement is _gap + 1 OBSERVED days, and a calendar span only delivers
    that when every day in it is observed -- which is what the guard exists to doubt. So
    this is set generously and the run reports how many observed days it actually bought.
    """
    return max(0, int(os.environ.get("PS1_COVERAGE_LOOKBACK_DAYS", "30")))


def _coverage_control_month() -> str:
    """Optional 'YYYY-MM' known to have full coverage. If set, the guard RAISES when it
    suppresses more than 2% of that month's starts -- the negative control that stops a
    mis-set floor from passing the acceptance test by cutting real episodes everywhere
    while the four known-bad months still dominate the absolute counts.
    """
    return os.environ.get("PS1_COVERAGE_CONTROL_MONTH", "").strip()


def _session_gap_days() -> int:
    """Consecutive failure days within this gap are ONE episode; only the first
    is kept. 0 disables sessionisation (the historical behaviour).

    The PS3 episode fact uses 3 -- its oos_fact_definition reads "PS1
    hardware-OOS SET session; a new session starts after > 3 days". Counting raw
    event-days instead inflates the label enormously: GATE carries a KPI-counted
    OOS Set on roughly a third of all device-days.
    """
    return max(0, int(os.environ.get("PS1_EVENT_SESSION_GAP_DAYS", "0")))
KEY_COLS = ["DEVICE_ID", "transit_day"]
JOIN_KEY_DEVICE = "DEVICE_KEY"
PS5_DATE_COLUMN = None
AUXILIARY_DUPLICATE_POLICY = "skip"
RUN_DEEP_GRAIN_AUDIT = False
IS_LOCAL_SPARK = False

# Optional auxiliary frames handed from add_auxiliary to join_and_materialise.
# They cannot travel in the `aux` dict: CELL 8 builds that from an explicit literal
# in each fleet notebook, so a new key would need three notebook edits to arrive.
_PS1_DF_WARN = None
_PS1_DF_AVAIL = None
_PS1_DF_EVQ = None
_PS1_DF_KPI = None
_PS1_DF_STATION = None
_PS1_DF_UPTIME = None
# Last day each of those two sources actually observed. Beyond it the source is silent,
# which is not the same as a measured zero -- see the join sites.
_PS1_STN_MAX_DAY = None
_PS1_UPT_MAX_DAY = None
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


# -- silver.warnings_daily: degradation precursors ---------------------------
# Same-day counts plus the 7d/30d prior sums that _SPARK_PRIOR_COLS derives.
WARNING_BASE_COLS = [
    "warn_events", "warn_service_calls", "warn_nearfull", "warn_low",
    "warn_chu", "warn_bhu", "warn_printer", "warn_comms", "warn_reader",
]
WARNING_FEATURE_COLS = WARNING_BASE_COLS + [
    f"{c}_prior_sum_{w}d" for c in WARNING_BASE_COLS for w in (7, 30)
]
if _enable_warning_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in WARNING_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] warnings_daily enabled: +{len(WARNING_FEATURE_COLS)} candidate features")


# -- silver.kpi_avail_enriched: device-day outage rollups --------------------
# Replaces the metric_features family, whose four source columns
# (availability_pct / total_downtime_min / total_events / p95_downtime_min) exist
# nowhere in this repo -- silver.metric_daily is a METRIC_401 timing table.
# Only the prior-window sums are registered as features: an outage's duration is
# not known until it ends, so the same-day value could reach past the label edge.
AVAIL_BASE_COLS = [
    "kae_outage_count", "kae_outage_min_sum", "kae_outage_min_max",
    "kae_failure_level_max", "kae_excluded_count", "kae_relieved_count",
]
AVAIL_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in AVAIL_BASE_COLS for w in (7, 30)]
if _enable_avail_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in AVAIL_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] kpi_avail_enriched enabled: +{len(AVAIL_FEATURE_COLS)} candidate features")


# -- silver.station_network_daily: station co-failure context ----------------
# Facility-keyed, not device-keyed: the table has no DEVICE_ID at all, its grain is
# (FACILITY_ID, device_category, transit_day), and it is SPARSE -- a station-day with
# no failure carries no row, so a left join must coalesce to 0 rather than leave a
# null that a tree will read as its own category.
#
# Only the strictly-prior sums are registered. station_network_daily is built from
# silver.device_failures, whose VALIDATOR branch is byte-identical to PS1's own default
# label construction -- so on VALIDATOR a same-day value would hand the model its own
# answer outright. GATE and TVM take a different branch of S26, so the overlap there is
# strong rather than exact. Prior-only is the safe rule for all three.
STATION_BASE_COLS = [
    "stn_devices_failed", "stn_failure_events", "stn_downtime_sum",
    "stn_coordinated", "stn_major",
]
STATION_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in STATION_BASE_COLS for w in (7, 30)]
if _enable_station_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in STATION_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] station_network_daily enabled: +{len(STATION_FEATURE_COLS)} candidate features")


# -- silver.device_uptime_intervals: end-of-day message volume ---------------
# THREE columns, not the six the PS3 engine asks for. The other three are unusable:
#   uptime_pct, downtime_hours  -- CAST(NULL AS DOUBLE) in sql/silver/08, because
#       bronze ncs_stage_device_end_of_day carries no UPTIME_SECONDS/TOTAL_SECONDS
#       (schema probe, 2026-06-18). They are all-null columns, not features.
#   hours_since_last_hb, is_silent_device -- computed in the last_state CTE from
#       bronze.edw_device_last_state, which is one row per device joined with NO
#       date predicate. Every device-day row for a device therefore carries the
#       SAME value, fixed at build time. On a 2024 training row that encodes 2026
#       state: a device-identity constant with no temporal content, which is the
#       shape that already produced degenerate lift on days_since_fail.
# What remains genuinely varies by device-day and is final at end of day D.
UPTIME_BASE_COLS = ["upt_msg_count", "upt_msg_types", "upt_complete_flag"]
UPTIME_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in UPTIME_BASE_COLS for w in (7, 30)]
if _enable_uptime_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in UPTIME_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] device_uptime_intervals enabled: +{len(UPTIME_FEATURE_COLS)} candidate features")


# -- silver.metric_daily: the columns it actually has ------------------------
# Excludes m401_p95_txn_time_ms / m401_slow_tap_count / m401_rolling_7d_avg_ms /
# m401_txn_count_delta, which gold_extra already supplies as metric_p95_txn_ms,
# metric_slow_tap_count, metric_rolling_7d_avg_ms and metric_txn_delta.
M401_RAW_COLS = [
    "m401_daily_txn_count", "m401_avg_txn_time_ms", "m401_max_txn_time_ms",
    "m401_p99_txn_time_ms", "m401_slow_tap_pct", "m401_avg_time_delta_ms",
    "m401_z_score_vs_28d", "volume_drop_flag",
    "comms_csc_read_err_count", "comms_host_comm_lost_count",
    "comms_device_comms_lost_count", "comms_total_count", "comms_event_flag",
]
M401_FEATURE_COLS = [f"met_{c}" for c in M401_RAW_COLS]

# -- silver.usage_lifecycle_daily: the 8 unused columns worth having ---------
USAGE_EXT_EXTRA_COLS = [
    "daily_failure_count", "daily_tech_logins", "daily_maint_mode_events",
    "cumulative_maint_events", "days_since_last_maintenance",
    "failure_count_30d", "tap_count_30d", "maint_events_30d",
]
USAGE_EXT_EXTRA_FEATURES = [f"usage_{c}" for c in USAGE_EXT_EXTRA_COLS]

# The four phantom metric_features never resolve; drop them so the "absent" list
# reports real gaps only.
_PHANTOM_METRIC_FEATURES = ["met_avail_pct", "met_downtime_min",
                            "met_event_count", "met_p95_downtime"]
if _enable_m401_features():
    for _cfg in FLEET_CONFIG.values():
        _cfg.all_candidate_features[:] = [
            c for c in _cfg.all_candidate_features if c not in _PHANTOM_METRIC_FEATURES
        ]
        for _c in M401_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] metric_daily M401/comms enabled: +{len(M401_FEATURE_COLS)} "
          f"candidate features (4 phantom names removed)")
if _enable_usage_ext_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in USAGE_EXT_EXTRA_FEATURES:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] usage_lifecycle_daily extended: +{len(USAGE_EXT_EXTRA_FEATURES)} "
          f"candidate features")


# -- device_event_enriched: duration + component attribution ----------------
# Same-day counts are safe (known by end of day D; the label covers D+1..D+H).
# The duration columns are prior-window ONLY -- a duration is not known until the
# clear, which may land inside the label window.
EVQ_SAFE_COLS = ["evq_oos_sets", "evq_comp_types", "evq_comp_serials"]
EVQ_DURATION_COLS = ["evq_dur_sum", "evq_dur_max", "evq_dur_mean"]
EVQ_BASE_COLS = EVQ_SAFE_COLS + EVQ_DURATION_COLS
EVQ_PRIOR_SUM_COLS = [
    f"{c}_prior_sum_{w}d" for c in EVQ_BASE_COLS for w in (7, 30)
]
# The same-day counts are registered only when PS1_EVQ_PRIOR_ONLY is off.
EVQ_FEATURE_COLS = ([] if _evq_prior_only() else EVQ_SAFE_COLS) + EVQ_PRIOR_SUM_COLS
if _enable_event_quality_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in EVQ_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    _evq_same = 0 if _evq_prior_only() else len(EVQ_SAFE_COLS)
    print(f"[features] event-quality enabled: +{len(EVQ_FEATURE_COLS)} candidate features "
          f"({_evq_same} same-day, {len(EVQ_PRIOR_SUM_COLS)} prior-window)")
    if _evq_prior_only():
        print("[features] PS1_EVQ_PRIOR_ONLY=true -- same-day counts withheld; "
              "this measures the gain net of the sessionisation rule")


# -- features that encode the sessionisation gap ----------------------------
# Applied last, after every source block above has contributed its candidates.
GAP_ENCODING_FEATURES = [
    "days_since_fail",               # the gap quantity itself
    "fail_free_streak",              # the same quantity
    "usage_days_since_last_failure",  # the same quantity, from usage_lifecycle_daily
    "mttr_days_since_prev_failure",   # the same quantity, from device_mttr
    "roll_fail_7d",                  # a 7-day window overlapping the 3-day gap
    "usage_daily_failure_count",     # failures today => no episode start for 3 days
]
# Retained deliberately -- failure history beyond the gap window was assumed to be a
# legitimate predictor: roll_fail_30d, roll_fail_90d, usage_cumulative_failure_count,
# mttr_failure_days_30d, mttr_failure_days_90d. (An earlier version of this comment also
# named usage_failure_count_30d, which is not a feature in any fleet's candidate list.)
# That assumption is what PS1_EXCLUDE_FAILURE_HISTORY now tests -- see below.
# Everything derived from the device's own OOS failure history. These are precisely the
# features that go quiet for EVERY device at once during a coverage outage, which is how
# the model learned to recognise a feed gap. Verified present in the candidate lists.
FAILURE_HISTORY_FEATURES = [
    # counts -- these fall to zero across the fleet during an outage
    "roll_fail_30d", "roll_fail_90d",
    "usage_cumulative_failure_count", "cumulative_failures_lifetime",
    "mttr_failure_days_30d", "mttr_failure_days_90d",
    "chain_length", "chain_failure_count",
    # shape -- derived from the same failure sequences
    "inter_failure_days_mean", "inter_failure_days_min", "failure_acceleration_rate",
    "max_chain_downtime_min", "chain_event_diversity", "chain_component_diversity",
]
# The station family is failure history too: station_network_daily is built from
# silver.device_failures, and its prior-window sums include days on which THIS device
# contributed to its own station's count. Appended rather than inlined because the names
# are generated. Without this, PS1_EXCLUDE_FAILURE_HISTORY would report an ablation it
# had not performed.
FAILURE_HISTORY_FEATURES = FAILURE_HISTORY_FEATURES + STATION_FEATURE_COLS

# DELIBERATELY RETAINED: hardware_oos_count_prior_sum_7d and
# chargeable_outage_count_prior_sum_{7,30}d. These are prior-window sums of the OOS event
# itself, so they also fall to zero in an outage and can also signal a gap -- but they are
# the most legitimate predictor the model has, and removing them would gut it for reasons
# that have nothing to do with coverage. There is no clean separation here: the features
# that detect a gap ARE the features that predict a failure.
#
# That is why the diagnostic is the MONTHLY table rather than the headline AUC. A family
# carrying genuine device risk costs every month roughly equally; a family carrying gap
# detection costs 2026-04 and 2025-03 and almost nothing else. The 23-Sep guard run showed
# exactly that shape in reverse -- one month moved 0.116, every other under 0.004.
if _exclude_failure_history():
    _dropped = set()
    for _cfg in FLEET_CONFIG.values():
        for _lst in (_cfg.all_candidate_features, _cfg.failure_features,
                     _cfg.chain_features, _cfg.mttr_features):
            _dropped |= {c for c in _lst if c in FAILURE_HISTORY_FEATURES}
            _lst[:] = [c for c in _lst if c not in FAILURE_HISTORY_FEATURES]
    print(f"[features] PS1_EXCLUDE_FAILURE_HISTORY=true -- withheld {len(_dropped)} "
          f"failure-history features. DIAGNOSTIC RUN: read the MONTHLY drift table. "
          f"If only the coverage-gap months fall, this family was carrying gap "
          f"detection rather than device risk.")

if _exclude_gap_features():
    for _cfg in FLEET_CONFIG.values():
        _before = len(_cfg.all_candidate_features)
        _cfg.all_candidate_features[:] = [
            c for c in _cfg.all_candidate_features if c not in GAP_ENCODING_FEATURES
        ]
        _cfg.failure_features[:] = [
            c for c in _cfg.failure_features if c not in GAP_ENCODING_FEATURES
        ]
    print(f"[features] PS1_EXCLUDE_GAP_FEATURES=true -- withheld "
          f"{len(GAP_ENCODING_FEATURES)} gap-encoding features; what survives is the "
          f"model's real predictive skill")


# -- silver.kpi_daily: contract performance, gap-aware windows ---------------
# Windows start at day 4. Measured lift of a KPI miss preceding an episode start:
# TVM 0.24x at lag 1d, 0.76x at 3d, 2.92x at 7d -- below 1.0 at short lags because
# a miss on D-1 makes an episode start on D impossible under the >3-day gap rule.
# Days 1-3 are mechanically anti-correlated, so they are excluded.
KPI_BASE_COLS = [
    "kpi_rows",           # KPI rows recorded that day
    "kpi_miss",           # rows failing meets_target
    "kpi_core_miss",      # rows failing meets_target on a Core KPI
    "kpi_value_min",      # worst KPI value that day
    "kpi_deduction_max",  # largest contractual deduction that day
]
KPI_WINDOWS = ((14, 4), (30, 4))     # (days back, days excluded at the near end)
KPI_FEATURE_COLS = [f"{c}_{lo}to{hi}d" for c in KPI_BASE_COLS for hi, lo in KPI_WINDOWS]
if _enable_kpi_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in KPI_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] kpi_daily enabled: +{len(KPI_FEATURE_COLS)} candidate features "
          f"(windows start at day 4; same-day never used)")


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
    if _enable_station_features():
        # The spine projection is built from PS1_REQUESTED and has never carried
        # FACILITY_ID. That is also why facility_peer_hw_oos_7d has reported "absent"
        # in every run to date -- its `if "FACILITY_ID" in df_joined.columns` guard has
        # never once been satisfied, so PS1 has had a facility feature written and dead
        # the whole time. Requested only under the station flag, so a default run's
        # feature matrix is unchanged; note that enabling the flag therefore revives
        # facility_peer_hw_oos_7d as well, and a first measurement covers both.
        PS1_PASS_THROUGH = PS1_PASS_THROUGH + ["FACILITY_ID"]
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
        """Build VALIDATOR-style hardware OOS Set label from S3 silver parquet (no UC catalog).

        The event definition is chosen by PS1_EVENT_DEFINITION; see the module header.
        Note that TARGET_COL keeps its "_3d" name whatever the horizon, because that
        literal is a published serving-tier column name.
        """
        _defn = _event_definition()
        _relief = _exclude_relieved()
        _gap = _session_gap_days()
        print(f"[label] event definition : {_defn}")
        print(f"[label] horizon          : {horizon_days} day(s)")
        print(f"[label] exclude relieved : {_relief}")
        print(f"[label] session gap days : {_gap}" + ("  (0 = count every event-day)" if not _gap else ""))
        _guard = _completeness_guard()
        if _guard:
            print(f"[label] completeness grd : ON  signal={_coverage_signal()} "
                  f"floor={_coverage_floor()} lookback={_coverage_lookback_days()}d")
            if not _gap:
                print("[label] !! GUARD ARMED BUT SESSIONISATION IS OFF "
                      "(PS1_EVENT_SESSION_GAP_DAYS=0). No gap rule runs, so the guard "
                      "has nothing to modify and will report zeros. That is NOT a clean "
                      "feed -- it is a guard that never fired.")
        if _defn != PS1_EVENT_DEFINITION_DEFAULT or horizon_days != 3 or _relief or _gap or _guard:
            print(f"[label] !! NON-DEFAULT LABEL. {TARGET_COL} holds a "
                  f"{_defn}/{horizon_days}-day label, NOT the published 3-day one. "
                  "Do not publish this run to Aurora or the dashboard.")

        def _apply_event_definition(fd):
            if _defn == "hardware_oos":
                return fd
            flag_name = KPI_FLAG_BY_FLEET.get(device_category.strip().upper())
            if flag_name is None:
                raise ValueError(f"no Ventra KPI flag mapped for fleet {device_category!r}")
            actual = _silver_col(dee_raw, flag_name, dee_map)
            return fd.where(F.col(f"dee.{actual}") == True)

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
        # Under the guard the read starts EARLIER than the first labellable day, purely
        # so the lag has a real predecessor; those pre-window rows are filtered back out
        # after sessionisation. Without the guard this is date_add(min_day, 1) exactly as
        # before -- the earliest failure date that can label a day on the spine.
        _lookback = _coverage_lookback_days() if (_guard and _gap > 0) else 0
        _read_start = (min_day - _dt.timedelta(days=_lookback)) if _lookback \
            else (min_day + _dt.timedelta(days=1))
        failure_days = (
            dee_raw.alias("dee")
            .join(
                F.broadcast(current_devices).alias("d_cur"),
                F.col(f"dee.{dee_dk}") == F.col("d_cur.DEVICE_KEY"),
            )
            .where(F.col(f"dee.{dee_hw_oos}") == True)
            .where(F.col(f"dee.{dee_state}") == "Set")
            .where(F.col(f"dee.{dee_cat}") == device_category)
            .transform(_apply_event_definition)
            .where(
                F.to_date(F.col(f"dee.{dee_dtm}")) >= F.lit(_read_start)
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
        if _relief:
            kae_raw = spark.read.parquet(f"{s3_silver}/kpi_avail_enriched/")
            kae_map = _silver_col_map(kae_raw)
            kae_dev = _silver_col(kae_raw, "DEVICE_ID", kae_map)
            kae_day = _silver_col(kae_raw, "transit_day", kae_map)
            _rel = F.lit(False)
            if "relief_id" in kae_map:
                _rel = _rel | F.col(kae_map["relief_id"]).isNotNull()
            if "excluded" in kae_map:
                _rel = _rel | (F.col(kae_map["excluded"]).cast("double") > 0)
            relieved = (
                kae_raw.where(_rel)
                .select(F.col(kae_dev).alias("DEVICE_ID"),
                        F.to_date(F.col(kae_day)).alias("failure_date"))
                .distinct()
            )
            failure_days = failure_days.persist(StorageLevel.MEMORY_AND_DISK)
            _before = failure_days.count()
            failure_days = (failure_days
                            .join(relieved, ["DEVICE_ID", "failure_date"], "left_anti")
                            .persist(StorageLevel.MEMORY_AND_DISK))
            _after = failure_days.count()
            print(f"[label] relief excluded  : {_before - _after:,} of {_before:,} failure days")

        if _gap > 0:
            failure_days = failure_days.persist(StorageLevel.MEMORY_AND_DISK)
            _pre = failure_days.count()
            _wf = Window.partitionBy("DEVICE_ID").orderBy("failure_date")

            if not _guard:
                failure_days = (
                    failure_days
                    .withColumn("_prev_fail", F.lag("failure_date").over(_wf))
                    .where(F.col("_prev_fail").isNull()
                           | (F.datediff(F.col("failure_date"), F.col("_prev_fail")) > _gap))
                    .drop("_prev_fail")
                    .persist(StorageLevel.MEMORY_AND_DISK)
                )
                _post = failure_days.count()
                print(f"[label] sessionised      : {_pre:,} failure days -> {_post:,} episode starts "
                      f"(gap > {_gap}d)")
            else:
                _signal = _coverage_signal()
                _floor = _coverage_floor()

                # The calendar MUST span the whole failure_days read range on both sides.
                # A failure day outside it joins to nothing, obs_idx is NULL, and -- unlike
                # today's NULL on _prev_fail, which isNull() catches and KEEPS -- a NULL in
                # the arithmetic makes the predicate NULL and the row is silently DROPPED.
                _cal_start = _read_start
                _cal_end = max_day + _dt.timedelta(days=horizon_days)
                _n_cal = (_cal_end - _cal_start).days + 1
                _calendar = (
                    spark.range(_n_cal)
                    .select(F.date_add(F.lit(_cal_start), F.col("id").cast("int")).alias("cal_day"))
                )

                # Coverage is measured on to_date(EVENT_DTM) -- the SAME expression the
                # label uses. transit_day is a different column (it rolls at the service
                # boundary, not midnight), and keying the calendar on it would put the
                # observed/unobserved boundary one day off at exactly the gap edges, which
                # is the only place any of this matters.
                # No OOS filter, no Set filter, no current-device filter: the question is
                # whether the FEED delivered anything, not what it said.
                _cov = (
                    dee_raw
                    .where(F.col(dee_cat) == device_category)
                    .where(F.to_date(F.col(dee_dtm)).between(F.lit(_cal_start), F.lit(_cal_end)))
                    .select(F.col(dee_dev).cast("string").alias("DEVICE_ID"),
                            F.to_date(F.col(dee_dtm)).alias("cal_day"))
                )

                if _signal == "fleet":
                    # Exact countDistinct, not approx. At ~465 devices over ~1,200 days the
                    # exact count is free next to the scan, and an approximation that flips
                    # one day across the floor inside a gap MERGES two genuine episodes.
                    # Persist: approxQuantile and the later count() are two separate
                    # actions, and without this each one re-scans the whole TVM slice of
                    # device_event_enriched -- an entirely wasted ~190M-row pass.
                    _daily = _cov.groupBy("cal_day").agg(
                        F.countDistinct("DEVICE_ID").alias("dev_cnt")
                    ).persist(StorageLevel.MEMORY_AND_DISK)
                    _med = _daily.approxQuantile("dev_cnt", [0.5], 0.001)[0]
                    _thresh = _floor * _med
                    _flags = (
                        _calendar.join(_daily, "cal_day", "left")
                        .withColumn("_obs", F.when(
                            F.coalesce(F.col("dev_cnt"), F.lit(0)) >= F.lit(_thresh), 1).otherwise(0))
                    )
                    _wc = (Window.orderBy("cal_day")
                           .rowsBetween(Window.unboundedPreceding, Window.currentRow))
                    _idx = (_flags.withColumn("obs_idx", F.sum("_obs").over(_wc))
                                  .select("cal_day", "obs_idx", "_obs", "dev_cnt")
                                  .persist(StorageLevel.MEMORY_AND_DISK))
                    _n_unobs = _idx.where(F.col("_obs") == 0).count()
                    print(f"[label] coverage (fleet) : median {_med:,.0f} devices/day, "
                          f"floor {_thresh:,.0f} ({_floor:.0%})")
                    _join_idx = _idx.select(F.col("cal_day").alias("_ix_day"), "obs_idx")
                    failure_days = (
                        failure_days
                        .join(F.broadcast(_join_idx),
                              F.col("failure_date") == F.col("_ix_day"), "left")
                        .drop("_ix_day")
                    )
                else:
                    # Per-device days-present. No threshold at all: a day is observed for a
                    # device iff that device emitted something. Catches a subset going dark,
                    # which a fleet average averages away.
                    _dev_days = _cov.distinct().withColumn("_obs_raw", F.lit(1))
                    _devs = failure_days.select("DEVICE_ID").distinct()
                    _grid = (
                        _devs.crossJoin(_calendar)
                        .join(_dev_days, ["DEVICE_ID", "cal_day"], "left")
                        .withColumn("_obs", F.coalesce(F.col("_obs_raw"), F.lit(0)))
                        .drop("_obs_raw")
                    )
                    _wc = (Window.partitionBy("DEVICE_ID").orderBy("cal_day")
                           .rowsBetween(Window.unboundedPreceding, Window.currentRow))
                    _idx = (_grid.withColumn("obs_idx", F.sum("_obs").over(_wc))
                                 .select("DEVICE_ID", "cal_day", "obs_idx", "_obs")
                                 .persist(StorageLevel.MEMORY_AND_DISK))
                    _n_unobs = _idx.where(F.col("_obs") == 0).count()
                    print(f"[label] coverage (device): per-device days-present, no threshold")
                    _join_idx = _idx.select(F.col("DEVICE_ID").alias("_ix_dev"),
                                            F.col("cal_day").alias("_ix_day"), "obs_idx")
                    failure_days = (
                        failure_days
                        .join(_join_idx,
                              (F.col("DEVICE_ID") == F.col("_ix_dev"))
                              & (F.col("failure_date") == F.col("_ix_day")), "left")
                        .drop("_ix_dev", "_ix_day")
                    )

                print(f"[label] calendar         : {_n_cal:,} days evaluated "
                      f"[{_cal_start} .. {_cal_end}], {_n_unobs:,} marked UNOBSERVED")
                if _n_unobs == 0:
                    print("[label] !! THE FLOOR FIRED AND FOUND NOTHING. Every day is observed, "
                          "so obs_idx advances exactly like datediff and this run is "
                          "BIT-IDENTICAL to the guard being off. Zero suppression below is "
                          "NOT evidence the feed is clean -- it means this signal cannot see "
                          "the defect. Run ps1_coverage_probe.py and try PS1_COVERAGE_SIGNAL=device.")

                # obs_idx must never be NULL here: a NULL would make the predicate NULL and
                # drop a real start without trace. Fail loudly instead.
                _orphans = failure_days.where(F.col("obs_idx").isNull()).count()
                if _orphans:
                    raise RuntimeError(
                        f"[label] completeness guard: {_orphans:,} failure days fell outside "
                        f"the observed calendar [{_cal_start} .. {_cal_end}]. The calendar must "
                        f"span the full failure_days read range or real episode starts are "
                        f"silently dropped.")

                # Did the lookback actually buy _gap + 1 OBSERVED days? A calendar span
                # delivers that only when every day in it is observed, which is exactly the
                # assumption this guard abandons.
                if _lookback:
                    _pre_obs = (_idx.where((F.col("cal_day") < F.lit(min_day)) & (F.col("_obs") == 1))
                                    .select("cal_day").distinct().count())
                    # Distinguish the two reasons the lookback can come up short. If the
                    # fleet has NO events at all before min_day then the window starts at
                    # the data floor and no lookback can ever help -- that is a fact about
                    # the extract, not a misconfiguration, and raising would make the guard
                    # unusable on any full-history run. If events DO exist earlier and we
                    # simply did not read far enough, that IS a misconfiguration.
                    _has_earlier = _cov.where(F.col("cal_day") < F.lit(min_day)).limit(1).count() > 0
                    print(f"[label] lookback         : {_lookback}d calendar bought "
                          f"{_pre_obs:,} observed days before {min_day} (need > {_gap})")
                    if _pre_obs <= _gap:
                        if not _has_earlier:
                            print(f"[label] !! LEFT-CENSORED. The fleet has no events at all before "
                                  f"{min_day}, so the window starts at the data floor and no lookback "
                                  f"can supply a predecessor. Each device's FIRST failure day in the "
                                  f"window is therefore counted as an episode start whether or not it "
                                  f"really begins one. Measured on TVM that is 557 starts in 2023-07. "
                                  f"Treat the first month as warm-up: exclude or down-weight it, and "
                                  f"do not read its base rate as comparable to later months.")
                        else:
                            raise RuntimeError(
                                f"[label] completeness guard: the {_lookback}-day lookback yielded only "
                                f"{_pre_obs} observed days before {min_day}, but the gap rule needs more "
                                f"than {_gap} -- and earlier events DO exist, so the read simply did not "
                                f"reach them. Raise PS1_COVERAGE_LOOKBACK_DAYS. Leaving it would mint one "
                                f"spurious start per device at the window boundary.")

                # The escape stays attached to PREDECESSOR EXISTENCE, never to the
                # arithmetic -- the same shape as the calendar rule it replaces.
                failure_days = (
                    failure_days
                    .withColumn("_prev_fail", F.lag("failure_date").over(_wf))
                    .withColumn("_prev_idx", F.lag("obs_idx").over(_wf))
                    .withColumn("_kept", F.col("_prev_idx").isNull()
                                | ((F.col("obs_idx") - F.col("_prev_idx")) > _gap))
                    .withColumn("_was_start", F.col("_prev_fail").isNull()
                                | (F.datediff(F.col("failure_date"), F.col("_prev_fail")) > _gap))
                    .persist(StorageLevel.MEMORY_AND_DISK)
                )

                _in_win = F.col("failure_date") >= F.lit(min_day + _dt.timedelta(days=1))
                _rep = (
                    failure_days.where(_in_win)
                    .withColumn("_m", F.date_format(F.col("failure_date"), "yyyy-MM"))
                    .groupBy("_m")
                    .agg(F.sum(F.when(F.col("_was_start"), 1).otherwise(0)).alias("before"),
                         F.sum(F.when(F.col("_kept"), 1).otherwise(0)).alias("after"))
                    .orderBy("_m").collect()
                )
                _tb = sum(r["before"] for r in _rep)
                _ta = sum(r["after"] for r in _rep)
                print(f"[label] guard suppressed : {_tb - _ta:,} of {_tb:,} episode starts "
                      f"({(_tb - _ta) / _tb if _tb else 0:.1%})")
                _hits = [r for r in _rep if r["before"] > r["after"]]
                for r in sorted(_hits, key=lambda x: -(x["before"] - x["after"]))[:12]:
                    print(f"[label]   {r['_m']}  {r['before'] - r['after']:6,} of "
                          f"{r['before']:6,} ({(r['before'] - r['after']) / r['before']:5.1%})")
                if len(_hits) > 12:
                    print(f"[label]   ... and {len(_hits) - 12} more months with suppression")
                print(f"[label]   months with suppression: {len(_hits)} of {len(_rep)} "
                      "-- CONCENTRATED is the pass condition; spread evenly means the floor "
                      "is cutting real episodes")

                # Suppression at a preceding gap of exactly _gap + 1 is the dangerous case:
                # one day wrongly unobserved there merges two genuine episodes.
                _tight = (failure_days.where(_in_win & F.col("_was_start") & ~F.col("_kept"))
                          .withColumn("_calgap", F.datediff(F.col("failure_date"), F.col("_prev_fail")))
                          .groupBy("_calgap").count().orderBy("_calgap").limit(6).collect())
                if _tight:
                    print("[label]   suppressed by preceding calendar gap: "
                          + ", ".join(f"{r['_calgap']}d={r['count']:,}" for r in _tight)
                          + f"  (a {_gap + 1}d gap is one unobserved day from being merged)")

                _ctrl = _coverage_control_month()
                if _ctrl:
                    _cr = [r for r in _rep if r["_m"] == _ctrl]
                    if not _cr:
                        raise RuntimeError(f"[label] control month {_ctrl!r} is not in this window")
                    _b, _a = _cr[0]["before"], _cr[0]["after"]
                    _frac = (_b - _a) / _b if _b else 0.0
                    print(f"[label] control {_ctrl}  : {_b - _a:,} of {_b:,} suppressed ({_frac:.2%})")
                    if _frac > 0.02:
                        raise RuntimeError(
                            f"[label] completeness guard: control month {_ctrl} lost {_frac:.1%} of its "
                            f"episode starts, above the 2% tolerance. The floor is cutting genuine "
                            f"episodes, not manufactured ones. Lower PS1_COVERAGE_FLOOR.")

                failure_days = (
                    failure_days
                    .where(F.col("_kept") & _in_win)
                    .drop("_prev_fail", "_prev_idx", "_kept", "_was_start", "obs_idx")
                    .persist(StorageLevel.MEMORY_AND_DISK)
                )
                _post = failure_days.count()
                print(f"[label] sessionised      : {_pre:,} failure days -> {_post:,} episode starts "
                      f"(observed-day gap > {_gap}, signal={_signal})")

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

    # These four names exist nowhere in this repo -- silver.metric_daily (S10) is a
    # METRIC_401 timing table producing m401_* / comms_* columns and never had
    # availability fields, so this request has failed in every run ever made. The
    # intent is served by gold_extra (availability_pct_7d, outage_min_7d, events_7d)
    # and, when enabled, by the kpi_avail_enriched rollups above.
    metric_raw_cols = [
        "availability_pct", "total_downtime_min", "total_events", "p95_downtime_min"
    ]
    # Only attempted when the M401 replacement is off. Left unguarded it prints
    # "skipped ... missing required features" for four columns that exist nowhere
    # in this repo, immediately before the real read succeeds.
    df_metric_raw = None
    if not _enable_m401_features():
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
    if _enable_m401_features():
        # Direct read: the four names above never resolve, so read_feature_source
        # would return None before we ever reach the real columns.
        try:
            _md_raw = spark.read.parquet(f"{s3_silver}/metric_daily/")
            _md_lc = {c.casefold(): c for c in _md_raw.columns}
            _md_dev = _md_lc.get("device_id", "DEVICE_ID")
            _md_day = _md_lc.get("transit_day", "transit_day")
            _md_cat = _md_lc.get("mars_device_category")
            _md_present = [c for c in M401_RAW_COLS if c.casefold() in _md_lc]
            if not _md_present:
                raise ValueError(f"no M401 columns; available: {_md_raw.columns[:20]}")
            _md = _md_raw
            if _md_cat:
                _md = _md.where(F.col(_md_cat) == cfg.device_cat)
            df_metric = (
                _md
                .where(F.to_date(F.col(_md_day)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_md_day)) <= end_day_expr)
                .select(
                    F.col(_md_dev).cast("string").alias("DEVICE_ID"),
                    F.to_date(F.col(_md_day)).alias("transit_day"),
                    *[F.col(_md_lc[c.casefold()]).cast("double").alias(f"met_{c}")
                      for c in _md_present],
                )
                .dropDuplicates(["DEVICE_ID", "transit_day"])
            )
            print(f"silver.metric_daily: loaded {len(_md_present)} real M401/comms columns")
        except Exception as exc:
            df_metric = None
            print(f"WARNING: silver.metric_daily M401 read skipped ({exc})")
    elif df_metric_raw is None:
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
    if _enable_usage_ext_features():
        usage_ext_cols = usage_ext_cols + USAGE_EXT_EXTRA_COLS
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

    # -- silver.warnings_daily (degradation precursors) - DEVICE_ID x day grain --
    df_warn = None
    if _enable_warning_features():
        _warn_src = {
            "warn_events": "warn_events", "warn_service_calls": "service_call_events",
            "warn_nearfull": "nearfull_events", "warn_low": "low_events",
            "warn_chu": "warn_chu", "warn_bhu": "warn_bhu",
            "warn_printer": "warn_printer", "warn_comms": "warn_comms",
            "warn_reader": "warn_reader",
        }
        try:
            _wn_raw = spark.read.parquet(f"{s3_silver}/warnings_daily/")
            _wn_lc = {c.casefold(): c for c in _wn_raw.columns}
            _wd = _wn_lc.get("device_id", "DEVICE_ID")
            _we = _wn_lc.get("event_date", "event_date")
            _picked = [(a, _wn_lc[s.casefold()]) for a, s in _warn_src.items()
                       if s.casefold() in _wn_lc]
            if not _picked:
                raise ValueError(f"no warning columns; available: {_wn_raw.columns[:15]}")
            df_warn = (
                _wn_raw
                .where(F.to_date(F.col(_we)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_we)) <= end_day_expr)
                .select(
                    F.col(_wd).cast("string").alias("DEVICE_ID"),
                    F.to_date(F.col(_we)).alias("transit_day"),
                    *[F.col(s).cast("double").alias(a) for a, s in _picked],
                )
                .groupBy("DEVICE_ID", "transit_day")
                .agg(*[F.sum(a).alias(a) for a, _ in _picked])
            )
            print(f"silver.warnings_daily: loaded {len(_picked)} precursor columns")
        except Exception as exc:
            df_warn = None
            print(f"WARNING: silver.warnings_daily skipped ({exc})")

    # -- silver.kpi_avail_enriched -> device-day outage rollup -------------------
    df_avail = None
    if _enable_avail_features():
        try:
            _ka_raw = spark.read.parquet(f"{s3_silver}/kpi_avail_enriched/")
            _ka_lc = {c.casefold(): c for c in _ka_raw.columns}
            _kd = _ka_lc.get("device_id", "DEVICE_ID")
            _kt = _ka_lc.get("transit_day", "transit_day")
            _kdur = _ka_lc.get("outage_duration_min")
            _kfl = _ka_lc.get("failure_level")
            _kex = _ka_lc.get("excluded")
            _krel = _ka_lc.get("relief_id")
            _kcat = _ka_lc.get("mars_device_category")
            _base = _ka_raw
            if _kcat:
                _base = _base.where(F.col(_kcat) == cfg.device_cat)
            _aggs = [F.count(F.lit(1)).alias("kae_outage_count")]
            if _kdur:
                _aggs += [F.sum(F.col(_kdur).cast("double")).alias("kae_outage_min_sum"),
                          F.max(F.col(_kdur).cast("double")).alias("kae_outage_min_max")]
            if _kfl:
                _aggs.append(F.max(F.col(_kfl).cast("double")).alias("kae_failure_level_max"))
            if _kex:
                _aggs.append(F.sum(F.when(F.col(_kex).cast("double") > 0, 1.0)
                                    .otherwise(0.0)).alias("kae_excluded_count"))
            if _krel:
                _aggs.append(F.sum(F.when(F.col(_krel).isNotNull(), 1.0)
                                    .otherwise(0.0)).alias("kae_relieved_count"))
            df_avail = (
                _base
                .where(F.to_date(F.col(_kt)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_kt)) <= end_day_expr)
                .groupBy(F.col(_kd).cast("string").alias("DEVICE_ID"),
                         F.to_date(F.col(_kt)).alias("transit_day"))
                .agg(*_aggs)
            )
            print(f"silver.kpi_avail_enriched: rolled up to {len(_aggs)} device-day columns")
        except Exception as exc:
            df_avail = None
            print(f"WARNING: silver.kpi_avail_enriched skipped ({exc})")

    # -- device_event_enriched -> device-day duration + component rollup --------
    df_evq = None
    if _enable_event_quality_features():
        try:
            _ev_raw = spark.read.parquet(f"{s3_silver}/device_event_enriched/")
            _ev_lc = {c.casefold(): c for c in _ev_raw.columns}

            def _ev(name):
                return _ev_lc.get(name.casefold())

            _e_dev, _e_day = _ev("DEVICE_ID"), _ev("transit_day")
            _e_state, _e_oos = _ev("EVENT_STATE_TYPE_NAME"), _ev("is_oos_event")
            _e_cat = _ev("mars_device_category")
            _e_dur = _ev("duration_to_clear_min")
            _e_ctype, _e_cser = _ev("COMPONENT_TYPE_NAME"), _ev("COMPONENT_SERIAL_NBR")
            if not (_e_dev and _e_day):
                raise ValueError(f"no DEVICE_ID/transit_day; columns: {_ev_raw.columns[:20]}")

            _ev_f = _ev_raw
            if _e_cat:
                _ev_f = _ev_f.where(F.col(_e_cat) == cfg.device_cat)
            if _e_oos:
                _ev_f = _ev_f.where(F.col(_e_oos).eqNullSafe(True))
            if _e_state:
                _ev_f = _ev_f.where(F.col(_e_state) == "Set")
            _ev_f = (_ev_f
                     .where(F.to_date(F.col(_e_day)) >= F.to_date(F.lit(start_day)))
                     .where(F.to_date(F.col(_e_day)) <= end_day_expr))

            _ev_aggs = [F.count(F.lit(1)).alias("evq_oos_sets")]
            if _e_ctype:
                _ev_aggs.append(F.countDistinct(F.col(_e_ctype)).alias("evq_comp_types"))
            if _e_cser:
                _ev_aggs.append(F.countDistinct(F.col(_e_cser)).alias("evq_comp_serials"))
            if _e_dur:
                _d = F.col(_e_dur).cast("double")
                _ev_aggs += [F.sum(_d).alias("evq_dur_sum"),
                             F.max(_d).alias("evq_dur_max"),
                             F.avg(_d).alias("evq_dur_mean")]
            df_evq = _ev_f.groupBy(
                F.col(_e_dev).cast("string").alias("DEVICE_ID"),
                F.to_date(F.col(_e_day)).alias("transit_day"),
            ).agg(*_ev_aggs)
            print(f"silver.device_event_enriched: rolled up to {len(_ev_aggs)} "
                  f"device-day quality columns")
        except Exception as exc:
            df_evq = None
            print(f"WARNING: device_event_enriched quality rollup skipped ({exc})")

    # -- silver.kpi_daily -> device-day contract-performance rollup -------------
    df_kpi = None
    if _enable_kpi_features():
        try:
            _kp_raw = spark.read.parquet(f"{s3_silver}/kpi_daily/")
            _kp_lc = {c.casefold(): c for c in _kp_raw.columns}
            _p_dev, _p_day = _kp_lc.get("device_id"), _kp_lc.get("transit_day")
            _p_cat = _kp_lc.get("mars_device_category")
            _p_mt = _kp_lc.get("meets_target")
            _p_crit = _kp_lc.get("kpi_criticality")
            _p_val = _kp_lc.get("kpi_value")
            _p_ded = _kp_lc.get("kpi_deduction_value")
            if not (_p_dev and _p_day):
                raise ValueError(f"no DEVICE_ID/transit_day; columns: {_kp_raw.columns[:20]}")
            _kp = _kp_raw
            if _p_cat:
                _kp = _kp.where(F.col(_p_cat) == cfg.device_cat)
            _kp = (_kp
                   .where(F.to_date(F.col(_p_day)) >= F.to_date(F.lit(start_day)))
                   .where(F.to_date(F.col(_p_day)) <= end_day_expr))
            _miss = (~F.col(_p_mt).eqNullSafe(True)) if _p_mt else F.lit(False)
            _core = (F.upper(F.col(_p_crit).cast("string")).startswith("CORE")
                     if _p_crit else F.lit(False))
            _aggs = [F.count(F.lit(1)).cast("double").alias("kpi_rows"),
                     F.sum(F.when(_miss, 1.0).otherwise(0.0)).alias("kpi_miss"),
                     F.sum(F.when(_miss & _core, 1.0).otherwise(0.0)).alias("kpi_core_miss")]
            if _p_val:
                _aggs.append(F.min(F.col(_p_val).cast("double")).alias("kpi_value_min"))
            if _p_ded:
                _aggs.append(F.max(F.col(_p_ded).cast("double")).alias("kpi_deduction_max"))
            df_kpi = _kp.groupBy(
                F.col(_p_dev).cast("string").alias("DEVICE_ID"),
                F.to_date(F.col(_p_day)).alias("transit_day"),
            ).agg(*_aggs)
            print(f"silver.kpi_daily: rolled up to {len(_aggs)} device-day columns "
                  f"(windows will start at day 4)")
        except Exception as exc:
            df_kpi = None
            print(f"WARNING: silver.kpi_daily skipped ({exc})")

    # -- silver.station_network_daily -> facility-day co-failure rollup ---------
    # Normalise FACILITY_ID on BOTH sides. It arrives as a float on one side
    # ("1704.0") and as text on the other ("1704"); pandas raises a MergeError on
    # that, but Spark does not -- it returns a silent zero-row match that looks
    # exactly like sparse coverage.
    df_station = None
    _stn_max_day = None
    if _enable_station_features():
        try:
            _sn_raw = spark.read.parquet(f"{s3_silver}/station_network_daily/")
            _sn_lc = {c.casefold(): c for c in _sn_raw.columns}
            _sf = _sn_lc.get("facility_id")
            _st = _sn_lc.get("transit_day")
            if not _sf or not _st:
                raise KeyError(f"FACILITY_ID/transit_day absent: {sorted(_sn_raw.columns)}")
            _base = _sn_raw
            _scat = _sn_lc.get("device_category")
            if _scat:
                _base = _base.where(F.upper(F.col(_scat).cast("string")) == cfg.device_cat.upper())
            _aggs = []
            for _src, _dst, _kind in (("devices_failed", "stn_devices_failed", "num"),
                                      ("total_failure_events", "stn_failure_events", "num"),
                                      ("total_downtime_minutes", "stn_downtime_sum", "num"),
                                      ("is_coordinated_failure", "stn_coordinated", "bool"),
                                      ("is_major_station_event", "stn_major", "bool")):
                _c = _sn_lc.get(_src)
                if not _c:
                    continue
                _e = (F.col(_c).cast("boolean").cast("double") if _kind == "bool"
                      else F.col(_c).cast("double"))
                _aggs.append(F.max(_e).alias(_dst))
            if not _aggs:
                raise KeyError("no station measure columns present")
            # Group even though the DDL grain is already unique once a category is
            # fixed: a duplicate here would fan out the spine, and the row-count
            # backstop would fail the whole run rather than just this source.
            df_station = (
                _base
                .where(F.to_date(F.col(_st)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_st)) <= end_day_expr)
                .groupBy(
                    F.regexp_replace(F.col(_sf).cast("string"), r"\.0$", "").alias("_fid_norm"),
                    F.to_date(F.col(_st)).alias("transit_day"))
                .agg(*_aggs)
            )
            _row = df_station.agg(F.max("transit_day")).collect()
            _stn_max_day = _row[0][0] if _row else None
            print(f"silver.station_network_daily: rolled up to {len(_aggs)} facility-day "
                  f"columns, last observed day {_stn_max_day}")
        except Exception as exc:
            df_station = None
            print(f"WARNING: silver.station_network_daily skipped ({exc})")

    # -- silver.device_uptime_intervals -> device-day message volume ------------
    # The day column is eod_date. This table has NO transit_day, which has already
    # broken two consumers with KeyError: ['transit_day'] not in index.
    df_uptime = None
    _upt_max_day = None
    if _enable_uptime_features():
        try:
            _up_raw = spark.read.parquet(f"{s3_silver}/device_uptime_intervals/")
            _up_lc = {c.casefold(): c for c in _up_raw.columns}
            _ud = _up_lc.get("device_id")
            _uday = _up_lc.get("eod_date")
            if not _ud or not _uday:
                raise KeyError(f"DEVICE_ID/eod_date absent: {sorted(_up_raw.columns)}")
            _base = _up_raw
            _ucat = _up_lc.get("mars_device_category")
            if _ucat:
                _base = _base.where(F.col(_ucat) == cfg.device_cat)
            _aggs = []
            for _src, _dst in (("total_msg_count", "upt_msg_count"),
                               ("distinct_message_types", "upt_msg_types"),
                               ("COMPLETE_FLAG", "upt_complete_flag")):
                _c = _up_lc.get(_src)
                if _c:
                    _aggs.append(F.max(F.col(_c).cast("double")).alias(_dst))
            if not _aggs:
                raise KeyError("no usable uptime measure column present")
            # max, not sum: the table's QUALIFY dedup is recent and the live table
            # may still carry the duplicate (DEVICE_KEY, eod_date) rows the DQ check
            # found. max is idempotent under duplication; sum is not.
            df_uptime = (
                _base
                .where(F.col(_ud).isNotNull())
                .where(F.to_date(F.col(_uday)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_uday)) <= end_day_expr)
                .groupBy(F.col(_ud).cast("string").alias("DEVICE_ID"),
                         F.to_date(F.col(_uday)).alias("transit_day"))
                .agg(*_aggs)
            )
            _row = df_uptime.agg(F.max("transit_day")).collect()
            _upt_max_day = _row[0][0] if _row else None
            print(f"silver.device_uptime_intervals: rolled up to {len(_aggs)} device-day "
                  f"columns, last observed day {_upt_max_day}")
        except Exception as exc:
            df_uptime = None
            print(f"WARNING: silver.device_uptime_intervals skipped ({exc})")

    global _PS1_DF_WARN, _PS1_DF_AVAIL, _PS1_DF_EVQ, _PS1_DF_KPI
    global _PS1_DF_STATION, _PS1_DF_UPTIME
    global _PS1_STN_MAX_DAY, _PS1_UPT_MAX_DAY
    _PS1_DF_WARN, _PS1_DF_AVAIL, _PS1_DF_EVQ = df_warn, df_avail, df_evq
    _PS1_DF_KPI = df_kpi
    _PS1_DF_STATION, _PS1_DF_UPTIME = df_station, df_uptime
    _PS1_STN_MAX_DAY, _PS1_UPT_MAX_DAY = _stn_max_day, _upt_max_day







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
    df_warn = globals().get("_PS1_DF_WARN")
    df_avail = globals().get("_PS1_DF_AVAIL")
    df_evq = globals().get("_PS1_DF_EVQ")
    df_kpi = globals().get("_PS1_DF_KPI")
    df_station = globals().get("_PS1_DF_STATION")
    df_uptime = globals().get("_PS1_DF_UPTIME")
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

        if df_warn is not None:
            df_joined = df_joined.join(df_warn, on=["DEVICE_ID", "transit_day"], how="left")
            for _wc in WARNING_BASE_COLS:
                if _wc in df_joined.columns:
                    df_joined = df_joined.withColumn(_wc, F.coalesce(F.col(_wc), F.lit(0.0)))
            print("Joined silver.warnings_daily on DEVICE_ID + transit_day")

        if df_avail is not None:
            df_joined = df_joined.join(df_avail, on=["DEVICE_ID", "transit_day"], how="left")
            for _ac in AVAIL_BASE_COLS:
                if _ac in df_joined.columns:
                    df_joined = df_joined.withColumn(_ac, F.coalesce(F.col(_ac), F.lit(0.0)))
            print("Joined silver.kpi_avail_enriched on DEVICE_ID + transit_day")

        if df_station is not None and "FACILITY_ID" in df_joined.columns:
            df_joined = (df_joined
                .withColumn("_fid_norm",
                            F.regexp_replace(F.col("FACILITY_ID").cast("string"), r"\.0$", ""))
                .join(df_station, on=["_fid_norm", "transit_day"], how="left")
                .drop("_fid_norm"))
            # Sparse WITHIN its coverage: no row means no failure at that station that
            # day, which is a real zero. PAST its last observed day it means the source
            # stopped, which is not a zero -- filling it would tell the model that every
            # station went quiet on the day ingestion did.
            _stn_max = globals().get("_PS1_STN_MAX_DAY")
            for _sc in STATION_BASE_COLS:
                if _sc in df_joined.columns:
                    _filled = F.coalesce(F.col(_sc), F.lit(0.0))
                    df_joined = df_joined.withColumn(
                        _sc,
                        _filled if _stn_max is None
                        else F.when(F.col("transit_day") <= F.lit(_stn_max), _filled))
            if "stn_devices_failed" in df_joined.columns:
                # Costs a scan only when there is nothing to find, which is exactly the
                # case worth paying for: a FACILITY_ID dtype mismatch does not raise in
                # Spark, it returns a silent zero-row match.
                if df_joined.where(F.col("stn_devices_failed") > 0).limit(1).count() == 0:
                    print("  WARNING: station_network_daily matched NOTHING. Check the "
                          "FACILITY_ID dtype on both sides, or this fleet has no facility "
                          "coverage. Every stn_* feature will be constant zero.")
            print("Joined silver.station_network_daily on FACILITY_ID + transit_day")
        elif df_station is not None:
            print("Skipped silver.station_network_daily: no FACILITY_ID on the spine")

        if df_uptime is not None:
            df_joined = df_joined.join(df_uptime, on=["DEVICE_ID", "transit_day"], how="left")
            # Same rule as the station family: zero-fill only inside the window the
            # source actually covers. device_uptime_intervals is frozen -- both of its
            # bronze feeders are on a deliberate hold list -- so an unconditional fill
            # would assert "this device sent zero messages" for every day since.
            _upt_max = globals().get("_PS1_UPT_MAX_DAY")
            for _uc in UPTIME_BASE_COLS:
                if _uc in df_joined.columns:
                    _filled = F.coalesce(F.col(_uc), F.lit(0.0))
                    df_joined = df_joined.withColumn(
                        _uc,
                        _filled if _upt_max is None
                        else F.when(F.col("transit_day") <= F.lit(_upt_max), _filled))
            print("Joined silver.device_uptime_intervals on DEVICE_ID + transit_day")

        if df_evq is not None:
            df_joined = df_joined.join(df_evq, on=["DEVICE_ID", "transit_day"], how="left")
            for _ec in EVQ_BASE_COLS:
                if _ec in df_joined.columns:
                    df_joined = df_joined.withColumn(_ec, F.coalesce(F.col(_ec), F.lit(0.0)))
            print("Joined device_event_enriched quality rollup on DEVICE_ID + transit_day")

        if df_kpi is not None:
            df_joined = df_joined.join(df_kpi, on=["DEVICE_ID", "transit_day"], how="left")
            for _kc in KPI_BASE_COLS:
                if _kc in df_joined.columns:
                    df_joined = df_joined.withColumn(_kc, F.coalesce(F.col(_kc), F.lit(0.0)))
            if "_day_epoch" not in df_joined.columns:
                df_joined = df_joined.withColumn(
                    "_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
            # Windows START AT DAY 4: days 1-3 are anti-correlated with the label
            # because a failure then makes an episode start impossible under the
            # >3-day sessionisation gap. Measured, not assumed.
            for _back, _near in KPI_WINDOWS:
                _w = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                            .rangeBetween(-_back * 86_400, -_near * 86_400))
                for _kc in KPI_BASE_COLS:
                    if _kc not in df_joined.columns:
                        continue
                    _agg = (F.min(F.col(_kc)) if _kc.endswith("_min")
                            else F.max(F.col(_kc)) if _kc.endswith("_max")
                            else F.sum(F.col(_kc)))
                    df_joined = df_joined.withColumn(
                        f"{_kc}_{_near}to{_back}d", _agg.over(_w))
            df_joined = df_joined.drop(*[c for c in KPI_BASE_COLS if c in df_joined.columns])
            print("Joined silver.kpi_daily; windows [4,14] and [4,30], same-day dropped")

        _SPARK_PRIOR_COLS = [
            "gate_mech_events", "csc_reader_events", "comms_events", "system_events",
            "chargeable_outage_count", "chargeable_outage_min", "hardware_oos_count",
        ]
        if df_warn is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in WARNING_BASE_COLS if c in df_joined.columns
            ]

        if df_avail is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in AVAIL_BASE_COLS if c in df_joined.columns
            ]

        if df_station is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in STATION_BASE_COLS if c in df_joined.columns
            ]

        if df_uptime is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in UPTIME_BASE_COLS if c in df_joined.columns
            ]

        if df_evq is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in EVQ_BASE_COLS if c in df_joined.columns
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

        if df_warn is not None:
            df_joined = df_joined.join(df_warn, on=["DEVICE_ID", "transit_day"], how="left")
            for _wc in WARNING_BASE_COLS:
                if _wc in df_joined.columns:
                    df_joined = df_joined.withColumn(_wc, F.coalesce(F.col(_wc), F.lit(0.0)))
            print("Joined silver.warnings_daily on DEVICE_ID + transit_day")

        if df_avail is not None:
            df_joined = df_joined.join(df_avail, on=["DEVICE_ID", "transit_day"], how="left")
            for _ac in AVAIL_BASE_COLS:
                if _ac in df_joined.columns:
                    df_joined = df_joined.withColumn(_ac, F.coalesce(F.col(_ac), F.lit(0.0)))
            print("Joined silver.kpi_avail_enriched on DEVICE_ID + transit_day")

        if df_station is not None and "FACILITY_ID" in df_joined.columns:
            df_joined = (df_joined
                .withColumn("_fid_norm",
                            F.regexp_replace(F.col("FACILITY_ID").cast("string"), r"\.0$", ""))
                .join(df_station, on=["_fid_norm", "transit_day"], how="left")
                .drop("_fid_norm"))
            # Sparse WITHIN its coverage: no row means no failure at that station that
            # day, which is a real zero. PAST its last observed day it means the source
            # stopped, which is not a zero -- filling it would tell the model that every
            # station went quiet on the day ingestion did.
            _stn_max = globals().get("_PS1_STN_MAX_DAY")
            for _sc in STATION_BASE_COLS:
                if _sc in df_joined.columns:
                    _filled = F.coalesce(F.col(_sc), F.lit(0.0))
                    df_joined = df_joined.withColumn(
                        _sc,
                        _filled if _stn_max is None
                        else F.when(F.col("transit_day") <= F.lit(_stn_max), _filled))
            if "stn_devices_failed" in df_joined.columns:
                # Costs a scan only when there is nothing to find, which is exactly the
                # case worth paying for: a FACILITY_ID dtype mismatch does not raise in
                # Spark, it returns a silent zero-row match.
                if df_joined.where(F.col("stn_devices_failed") > 0).limit(1).count() == 0:
                    print("  WARNING: station_network_daily matched NOTHING. Check the "
                          "FACILITY_ID dtype on both sides, or this fleet has no facility "
                          "coverage. Every stn_* feature will be constant zero.")
            print("Joined silver.station_network_daily on FACILITY_ID + transit_day")
        elif df_station is not None:
            print("Skipped silver.station_network_daily: no FACILITY_ID on the spine")

        if df_uptime is not None:
            df_joined = df_joined.join(df_uptime, on=["DEVICE_ID", "transit_day"], how="left")
            # Same rule as the station family: zero-fill only inside the window the
            # source actually covers. device_uptime_intervals is frozen -- both of its
            # bronze feeders are on a deliberate hold list -- so an unconditional fill
            # would assert "this device sent zero messages" for every day since.
            _upt_max = globals().get("_PS1_UPT_MAX_DAY")
            for _uc in UPTIME_BASE_COLS:
                if _uc in df_joined.columns:
                    _filled = F.coalesce(F.col(_uc), F.lit(0.0))
                    df_joined = df_joined.withColumn(
                        _uc,
                        _filled if _upt_max is None
                        else F.when(F.col("transit_day") <= F.lit(_upt_max), _filled))
            print("Joined silver.device_uptime_intervals on DEVICE_ID + transit_day")

        if df_evq is not None:
            df_joined = df_joined.join(df_evq, on=["DEVICE_ID", "transit_day"], how="left")
            for _ec in EVQ_BASE_COLS:
                if _ec in df_joined.columns:
                    df_joined = df_joined.withColumn(_ec, F.coalesce(F.col(_ec), F.lit(0.0)))
            print("Joined device_event_enriched quality rollup on DEVICE_ID + transit_day")

        if df_kpi is not None:
            df_joined = df_joined.join(df_kpi, on=["DEVICE_ID", "transit_day"], how="left")
            for _kc in KPI_BASE_COLS:
                if _kc in df_joined.columns:
                    df_joined = df_joined.withColumn(_kc, F.coalesce(F.col(_kc), F.lit(0.0)))
            if "_day_epoch" not in df_joined.columns:
                df_joined = df_joined.withColumn(
                    "_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
            # Windows START AT DAY 4: days 1-3 are anti-correlated with the label
            # because a failure then makes an episode start impossible under the
            # >3-day sessionisation gap. Measured, not assumed.
            for _back, _near in KPI_WINDOWS:
                _w = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                            .rangeBetween(-_back * 86_400, -_near * 86_400))
                for _kc in KPI_BASE_COLS:
                    if _kc not in df_joined.columns:
                        continue
                    _agg = (F.min(F.col(_kc)) if _kc.endswith("_min")
                            else F.max(F.col(_kc)) if _kc.endswith("_max")
                            else F.sum(F.col(_kc)))
                    df_joined = df_joined.withColumn(
                        f"{_kc}_{_near}to{_back}d", _agg.over(_w))
            df_joined = df_joined.drop(*[c for c in KPI_BASE_COLS if c in df_joined.columns])
            print("Joined silver.kpi_daily; windows [4,14] and [4,30], same-day dropped")

        _SPARK_PRIOR_COLS = [
            "printer_events", "bankcard_events", "bhu_events", "chu_events", "scrst_events",
            "system_events", "comms_events", "csc_reader_events",
            "chargeable_outage_count", "hardware_oos_count",
        ]
        if df_warn is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in WARNING_BASE_COLS if c in df_joined.columns
            ]

        if df_avail is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in AVAIL_BASE_COLS if c in df_joined.columns
            ]

        if df_station is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in STATION_BASE_COLS if c in df_joined.columns
            ]

        if df_uptime is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in UPTIME_BASE_COLS if c in df_joined.columns
            ]

        if df_evq is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in EVQ_BASE_COLS if c in df_joined.columns
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

        if df_warn is not None:
            df_joined = df_joined.join(df_warn, on=["DEVICE_ID", "transit_day"], how="left")
            for _wc in WARNING_BASE_COLS:
                if _wc in df_joined.columns:
                    df_joined = df_joined.withColumn(_wc, F.coalesce(F.col(_wc), F.lit(0.0)))
            print("Joined silver.warnings_daily on DEVICE_ID + transit_day")

        if df_avail is not None:
            df_joined = df_joined.join(df_avail, on=["DEVICE_ID", "transit_day"], how="left")
            for _ac in AVAIL_BASE_COLS:
                if _ac in df_joined.columns:
                    df_joined = df_joined.withColumn(_ac, F.coalesce(F.col(_ac), F.lit(0.0)))
            print("Joined silver.kpi_avail_enriched on DEVICE_ID + transit_day")

        if df_station is not None and "FACILITY_ID" in df_joined.columns:
            df_joined = (df_joined
                .withColumn("_fid_norm",
                            F.regexp_replace(F.col("FACILITY_ID").cast("string"), r"\.0$", ""))
                .join(df_station, on=["_fid_norm", "transit_day"], how="left")
                .drop("_fid_norm"))
            # Sparse WITHIN its coverage: no row means no failure at that station that
            # day, which is a real zero. PAST its last observed day it means the source
            # stopped, which is not a zero -- filling it would tell the model that every
            # station went quiet on the day ingestion did.
            _stn_max = globals().get("_PS1_STN_MAX_DAY")
            for _sc in STATION_BASE_COLS:
                if _sc in df_joined.columns:
                    _filled = F.coalesce(F.col(_sc), F.lit(0.0))
                    df_joined = df_joined.withColumn(
                        _sc,
                        _filled if _stn_max is None
                        else F.when(F.col("transit_day") <= F.lit(_stn_max), _filled))
            if "stn_devices_failed" in df_joined.columns:
                # Costs a scan only when there is nothing to find, which is exactly the
                # case worth paying for: a FACILITY_ID dtype mismatch does not raise in
                # Spark, it returns a silent zero-row match.
                if df_joined.where(F.col("stn_devices_failed") > 0).limit(1).count() == 0:
                    print("  WARNING: station_network_daily matched NOTHING. Check the "
                          "FACILITY_ID dtype on both sides, or this fleet has no facility "
                          "coverage. Every stn_* feature will be constant zero.")
            print("Joined silver.station_network_daily on FACILITY_ID + transit_day")
        elif df_station is not None:
            print("Skipped silver.station_network_daily: no FACILITY_ID on the spine")

        if df_uptime is not None:
            df_joined = df_joined.join(df_uptime, on=["DEVICE_ID", "transit_day"], how="left")
            # Same rule as the station family: zero-fill only inside the window the
            # source actually covers. device_uptime_intervals is frozen -- both of its
            # bronze feeders are on a deliberate hold list -- so an unconditional fill
            # would assert "this device sent zero messages" for every day since.
            _upt_max = globals().get("_PS1_UPT_MAX_DAY")
            for _uc in UPTIME_BASE_COLS:
                if _uc in df_joined.columns:
                    _filled = F.coalesce(F.col(_uc), F.lit(0.0))
                    df_joined = df_joined.withColumn(
                        _uc,
                        _filled if _upt_max is None
                        else F.when(F.col("transit_day") <= F.lit(_upt_max), _filled))
            print("Joined silver.device_uptime_intervals on DEVICE_ID + transit_day")

        if df_evq is not None:
            df_joined = df_joined.join(df_evq, on=["DEVICE_ID", "transit_day"], how="left")
            for _ec in EVQ_BASE_COLS:
                if _ec in df_joined.columns:
                    df_joined = df_joined.withColumn(_ec, F.coalesce(F.col(_ec), F.lit(0.0)))
            print("Joined device_event_enriched quality rollup on DEVICE_ID + transit_day")

        if df_kpi is not None:
            df_joined = df_joined.join(df_kpi, on=["DEVICE_ID", "transit_day"], how="left")
            for _kc in KPI_BASE_COLS:
                if _kc in df_joined.columns:
                    df_joined = df_joined.withColumn(_kc, F.coalesce(F.col(_kc), F.lit(0.0)))
            if "_day_epoch" not in df_joined.columns:
                df_joined = df_joined.withColumn(
                    "_day_epoch", F.col("transit_day").cast("timestamp").cast("long"))
            # Windows START AT DAY 4: days 1-3 are anti-correlated with the label
            # because a failure then makes an episode start impossible under the
            # >3-day sessionisation gap. Measured, not assumed.
            for _back, _near in KPI_WINDOWS:
                _w = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                            .rangeBetween(-_back * 86_400, -_near * 86_400))
                for _kc in KPI_BASE_COLS:
                    if _kc not in df_joined.columns:
                        continue
                    _agg = (F.min(F.col(_kc)) if _kc.endswith("_min")
                            else F.max(F.col(_kc)) if _kc.endswith("_max")
                            else F.sum(F.col(_kc)))
                    df_joined = df_joined.withColumn(
                        f"{_kc}_{_near}to{_back}d", _agg.over(_w))
            df_joined = df_joined.drop(*[c for c in KPI_BASE_COLS if c in df_joined.columns])
            print("Joined silver.kpi_daily; windows [4,14] and [4,30], same-day dropped")

        _SPARK_PRIOR_COLS = [
            "printer_events", "bankcard_events", "bhu_events", "chu_events", "scrst_events",
            "system_events", "comms_events", "csc_reader_events",
            "chargeable_outage_count", "hardware_oos_count",
        ]
        if df_warn is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in WARNING_BASE_COLS if c in df_joined.columns
            ]

        if df_avail is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in AVAIL_BASE_COLS if c in df_joined.columns
            ]

        if df_station is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in STATION_BASE_COLS if c in df_joined.columns
            ]

        if df_uptime is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in UPTIME_BASE_COLS if c in df_joined.columns
            ]

        if df_evq is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in EVQ_BASE_COLS if c in df_joined.columns
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

