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


def _enable_cleared_duration_features() -> bool:
    """Outages that had CLEARED before day D, split short (<60 min) vs long. Default OFF.

    The honest replacement for the withheld evq_dur_* (which summed durations of outages
    that may still have been open at D). Measured 25-Sep on in-service days: any outage
    cleared in the prior 7 days lifts a new episode 3.53x on GATE (1.64x VALIDATOR), but one
    lasting >=60 min lifts it only 0.88x (1.14x) and >=24 h 0.73x (0.91x) -- short blips come
    back, long outages were repairs that held. Rows are keyed by CLEAR date, so the prior
    windows (ending D-1) only ever see outages fully known at scoring time.
    """
    return os.environ.get("PS1_ENABLE_CLEARED_DURATION_FEATURES", "false").strip().lower() == "true"


def _enable_tvm_event_features() -> bool:
    """Prior-window counts of TVM precursor event families. Default OFF.

    From the 25-Sep TVM precursor scan (episode start within 7 days, in-service days):
    CSC bin near full 1.25x, CSC transport jam 1.23x, bill retract 1.21x, bill error 1.19x,
    SCT mag feed/capacity 1.17x ... against a ceiling of ~1.36x at TVM's 73.5% in-service
    base rate. The subsystem counts already carry part of this; these add event-level detail.
    """
    return os.environ.get("PS1_ENABLE_TVM_EVENT_FEATURES", "false").strip().lower() == "true"


def _enable_recency_trend_features() -> bool:
    """Days since each warning family last fired, and its 7-day vs 30-day rate. Default OFF.

    Applies to the DOPP, TVM-event and warnings_daily families only -- never to failure
    counts, whose recency is the sessionisation gap itself. Strictly prior days; capped at 90.
    """
    return os.environ.get("PS1_ENABLE_RECENCY_TREND_FEATURES", "false").strip().lower() == "true"


def _enable_tvm_sale_features() -> bool:
    """Prior-window TVM sale volume, error and cash/card mix from silver.tvm_sale_daily.

    Coin (CHU) and bill (BHU) units are 40% of TVM KPI OOS device-days (25-Sep). A falling
    cash share or rising transaction errors should lead those failures. Gold already
    computed error_txn_rate_pct and cash_sales_pct, but only sales_7d_avg ever reached the
    model. Prior windows only; TVM only; default OFF.
    """
    return os.environ.get("PS1_ENABLE_TVM_SALE_FEATURES", "false").strip().lower() == "true"


def _enable_dopp_features() -> bool:
    """Prior-window counts of DOPP (fare-processing module) warning events. Default OFF.

    Measured 25-Sep: DOPP is 98.2% of GATE's and 72% of VALIDATOR's KPI OOS device-days,
    yet no PS1 feature counted a DOPP event. In-service gates with a DOPP warning in the
    prior 7 days went out of service within 7 days at 30.2% vs 17.6% without (1.72x).
    Prior windows only (D-7..D-1, D-30..D-1): nothing from day D reaches the model.
    """
    return os.environ.get("PS1_ENABLE_DOPP_FEATURES", "false").strip().lower() == "true"


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


def _label_observed_edge() -> bool:
    """Drop rows whose label lookahead runs past the last day the event feed observed.

    A row on day D is labelled from events on D+1..D+horizon. When the feed stops before
    D+horizon the missing days count as "no failure", so the label is biased toward 0 --
    silently, because a stopped feed and a quiet fleet look identical to the join. The
    left edge has had a LEFT-CENSORED guard for weeks; this is its mirror.
    EDW.DEVICE_EVENT stopped at 2026-08-29 (NB16 preflight, 24-Sep: SOURCE FROZEN), so
    every run since has carried a censored final horizon. Default OFF so past runs stay
    reproducible; the RIGHT-CENSORED warning prints either way.
    """
    return os.environ.get("PS1_LABEL_OBSERVED_EDGE", "false").strip().lower() == "true"


def _ps1_env_flag(name):
    return os.environ.get(name, "false").strip().lower() == "true"


def _enable_repair_features() -> bool:
    """Repair durability. Default OFF.

    rep_last_hold_days / rep_prev_hold_days: failure-free days before the most recent (and
    the previous) completed episode start -- did the last repair hold? Only completed past
    intervals, so it does not encode the session gap. cdur_max_prior_30d /
    cdur_mean_min_prior_30d: longest and mean minutes-to-clear of outages that CLEARED in the
    prior 30 days (needs the cleared-duration flag). Failures follow the label's definition
    (fleet KPI flag, PS1_LABEL_MIN_OOS_MINUTES floor).
    """
    return _ps1_env_flag("PS1_ENABLE_REPAIR_FEATURES")


def _enable_maint_ledger_features() -> bool:
    """Commanded / maintenance OOS counts from silver.maintenance_ledger, prior windows."""
    return _ps1_env_flag("PS1_ENABLE_MAINT_LEDGER_FEATURES")


def _enable_last_ticket_features() -> bool:
    """The most recent ticket before D: priority, category (hashed), major, chargeable."""
    return _ps1_env_flag("PS1_ENABLE_LAST_TICKET_FEATURES")


def _enable_station_busy_features() -> bool:
    """Station busyness: total taps across all devices at the FACILITY_ID, prior windows."""
    return _ps1_env_flag("PS1_ENABLE_STATION_BUSY_FEATURES")


def _enable_silent_day_features() -> bool:
    """Days in the prior window on which the device logged NO event at all. Default OFF.

    A device that goes quiet is often losing its link before a DOPP outage; on the 25-Sep
    GATE screen the ~1% of in-service days WITHOUT the usual momentary events had a 63%
    next-week failure rate against 29%. Also carries the prior-window total event count.
    Only days strictly before D.
    """
    return os.environ.get("PS1_ENABLE_SILENT_DAY_FEATURES", "false").strip().lower() == "true"


def _enable_station_dopp_features() -> bool:
    """Prior-window DOPP warnings on the OTHER devices at the same station (FACILITY_ID).

    GATE failures are 98% DOPP, and DOPP trouble -- comms errors, fare lists behind -- is a
    network / back-office matter that can hit every gate at a station together. The peer
    value excludes the device itself; only prior-window sums reach the model. Default OFF.
    """
    return os.environ.get("PS1_ENABLE_STATION_DOPP_FEATURES", "false").strip().lower() == "true"


def _enable_incident_prior_features() -> bool:
    """Incidents opened in prior windows (silver.device_incident_features_daily). Default OFF.

    The gold incident_* features are populated only on days an incident opened (a COALESCE
    zero-fills every other day), so they carry almost nothing. Screened 25-Sep on GATE: a
    ticket opened in the prior 30 days goes with LOWER next-week risk (0.71x, 74% share).
    Prior sums over days strictly before D, plus days since the last ticket (cap 90).
    """
    return os.environ.get("PS1_ENABLE_INCIDENT_PRIOR_FEATURES", "false").strip().lower() == "true"


def _enable_long_window_features() -> bool:
    """90-day prior sums of the DOPP warning families. Default OFF.

    dopp_list_behind_prior_sum_30d is GATE's top feature; list staleness builds over weeks,
    so a 90-day window may carry more than the 30-day one. Strictly prior days.
    """
    return os.environ.get("PS1_ENABLE_LONG_WINDOW_FEATURES", "false").strip().lower() == "true"


def _enable_sn_repair_features() -> bool:
    """ServiceNow repair time: tickets RESOLVED per device-day and their minutes to resolve.
    Default OFF.

    Keyed by the resolution date, so a ticket only becomes visible the day after it was
    resolved; the prior windows (ending D-1) never see a repair still in progress. Slow or
    repeated repairs are a sign the device keeps coming back. GATE/TVM read
    silver.incident_history; VALIDATOR reads silver.incident_task_ci_link and only when
    PS1_ENABLE_VALIDATOR_TICKET_FEATURES is also on (incident_history has no validators).
    """
    return _ps1_env_flag("PS1_ENABLE_SN_REPAIR_FEATURES")


def _enable_validator_ticket_features() -> bool:
    """VALIDATOR ticket source: silver.incident_task_ci_link instead of incident_history.
    Default OFF.

    incident_history carries no VALIDATOR rows, so the incident prior family is constant
    zero on that fleet. incident_task_ci_link reaches ~823 validators through the bus
    number on the task CI, keyed by DEVICE_ID. With this on, VALIDATOR's incp_opened (and
    the SN repair family, when enabled) come from that table; GATE/TVM are unchanged.
    """
    return _ps1_env_flag("PS1_ENABLE_VALIDATOR_TICKET_FEATURES")


def _enable_chargability_features() -> bool:
    """CTA chargability tickets (silver servicenow_cta_chargability export). Default OFF.

    Resolution class (reset / replace / no fault found), request class (vandal/customer,
    planned) and repair minutes per device-day, keyed by the day the ticket CLOSED so the
    prior windows only see closed tickets. A reset that did not hold, or a string of
    no-fault-found visits, is repair history the OOS events do not carry.
    """
    return _ps1_env_flag("PS1_ENABLE_CHARGABILITY_FEATURES")


def _enable_calendar_features() -> bool:
    """Day of week and weekend flag of day D. Default OFF.

    Calendar facts are known in advance, so they are day-D values, not prior windows.
    Service load and field-crew staffing differ by weekday, which shifts both when faults
    surface and when they are logged.
    """
    return _ps1_env_flag("PS1_ENABLE_CALENDAR_FEATURES")


def _exclude_nondevice_outages() -> bool:
    """LABEL flag: drop failure days covered by a vandal/customer or planned chargability
    ticket on the same device. Default OFF; changes the label.

    An outage caused by vandalism, customer misuse or planned work is real downtime but not
    a device failure, and nothing in the device's own history can predict it. Applied after
    the relief exclusion and before sessionisation, so a removed day neither starts nor
    extends an episode. Needs the servicenow_cta_chargability export; if it is missing,
    nothing is excluded and the run says so.
    """
    return _ps1_env_flag("PS1_EXCLUDE_NONDEVICE_OUTAGES")


def _norm_device_id(col):
    """DEVICE_ID as upper-case trimmed text. Some sources carry it as a float ("1704.0");
    Spark returns a silent zero-row match on that rather than raising."""
    return F.upper(F.trim(F.regexp_replace(col.cast("string"), r"\.0+$", "")))


def _label_min_oos_minutes() -> float:
    """Count an OOS Set as a failure only if it lasted at least N minutes. 0 = off.

    Measured 25-Sep: TVM's KPI OOS device-days are dominated by five codes -- 222 roll-stock
    transport OOS, 221 CSC transport OOS, 113 No init, 534 No coins, 410 No bills -- each on
    ~60% of TVM-days, set 2-3 times a day, median time to clear 0-0.8 minutes. They are
    toggles and consumable states, not failures, and they are why TVM sat inside an
    "episode" on ~97% of days. Availability is counted in downtime minutes, so a
    zero-minute toggle costs the KPI nothing; a duration floor matches what the KPI
    penalises. A Set with no recorded clear (still open) is kept.
    """
    try:
        return max(0.0, float(os.environ.get("PS1_LABEL_MIN_OOS_MINUTES", "0").strip() or 0))
    except ValueError:
        raise ValueError("PS1_LABEL_MIN_OOS_MINUTES must be a number of minutes")


def _label_match_device_id() -> bool:
    """Match OOS events to current devices on DEVICE_ID, not DEVICE_KEY.

    DEVICE_KEY is an SCD2 surrogate and events carry the key that was current when they
    happened, so a key-matched label keeps only events logged since the device's last
    re-key. Measured 25-Sep, share of KPI OOS Sets on a non-current key: GATE 2.95%,
    TVM 5.18%, VALIDATOR 74.03% -- VALIDATOR's label saw about a quarter of its failures,
    while its DEVICE_ID-grouped features saw them all. Default OFF; changes the label.
    """
    return os.environ.get("PS1_LABEL_MATCH_DEVICE_ID", "false").strip().lower() == "true"


def _exclude_active_episode() -> bool:
    """Score only in-service devices: drop device-days inside an episode already open.

    Under the session rule an episode stays open until more than
    PS1_EVENT_SESSION_GAP_DAYS days pass without a failure day, so the device-days from a
    failure day F through F+gap belong to an episode in progress. A new episode can only
    start after that one closes, so their label mostly reflects the session rule, not the
    device's risk. Removing them asks: of the devices in service today, which start a new
    episode within the horizon? Calendar days. Default OFF; it changes the population.
    """
    return os.environ.get("PS1_EXCLUDE_ACTIVE_EPISODE", "false").strip().lower() == "true"


def _filter_after_features() -> bool:
    """Remove the active-episode and interior-mask rows AFTER features, not before.

    Both exclusions drop rows in CELL 6, so every prior window, forward fill, recency
    value and station peer value built in CELLS 7-8 sees only the surviving rows: a
    device's history loses the days it spent inside an open episode or beside an
    unobserved day. Scoring (with_label=False) runs neither exclusion and computes the
    same features over full history, so training and serving disagree. With this on,
    CELL 6 marks those rows in a boolean _ps1_drop column instead of dropping them,
    CELL 8 builds every feature over the full frame and removes the marked rows
    immediately before feature selection. The label and the scored rows are the same
    as with it off; only the feature values move. The observed-edge drop and the onset
    in-spell drop are unchanged. Default OFF.
    """
    return _ps1_env_flag("PS1_FILTER_AFTER_FEATURES")


def _label_mask_unobserved() -> bool:
    """Drop rows whose lookahead crosses a day the completeness guard marked UNOBSERVED.

    PS1_LABEL_OBSERVED_EDGE handles the end of the feed; this is the same rule for
    interior gaps such as the nine-day 2026-04 platform outage, where a silent lookahead
    is labelled "no failure" whether or not one happened. Needs PS1_COMPLETENESS_GUARD
    with PS1_COVERAGE_SIGNAL=fleet. Default OFF.
    """
    return os.environ.get("PS1_LABEL_MASK_UNOBSERVED", "false").strip().lower() == "true"


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


# -- device_event_enriched: DOPP warning families -----------------------------
# (event codes, states counted). Codes and states from the 25-Sep GATE event census;
# 2201 DAP OOS is the label's own event and is deliberately NOT here.
DOPP_FAMILIES = {
    "dopp_comms_err":   ([2202], ("Set",)),                   # DAPCommsError
    "dopp_offline":     ([2208], ("Set",)),                   # DAP Offline
    "dopp_list_behind": ([2230, 2232], ("Set",)),             # positive / negative list behind
    "dopp_list_far":    ([2231, 2233], ("Set",)),             # ... far behind
    "dopp_app_db":      ([2204, 2240, 2236], ("Set and Clear",)),  # app error, DB file/copy integrity
    "dopp_errors":      ([2214, 2224, 2229, 2235], ("Set",)),  # version, taps record, MAC, DB copy fail
}
DOPP_BASE_COLS = list(DOPP_FAMILIES)
DOPP_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in DOPP_BASE_COLS for w in (7, 30)]
if _enable_dopp_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in DOPP_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] DOPP warnings enabled: +{len(DOPP_FEATURE_COLS)} prior-window candidate features")


# -- silver.tvm_sale_daily: sale volume, errors, cash/card mix (TVM only) -------
TVM_SALE_BASE_COLS = ["tvm_sale_count", "tvm_sale_err_count", "tvm_sale_cash_count", "tvm_sale_card_count"]
TVM_SALE_RATIO_COLS = ([f"tvm_sale_err_rate_prior_{w}d" for w in (7, 30)]
                       + [f"tvm_sale_cash_share_prior_{w}d" for w in (7, 30)]
                       + ["tvm_sale_cash_share_trend_7v30d"])
TVM_SALE_FEATURE_COLS = ([f"{c}_prior_sum_{w}d" for c in TVM_SALE_BASE_COLS for w in (7, 30)]
                         + TVM_SALE_RATIO_COLS)
if _enable_tvm_sale_features():
    for _c in TVM_SALE_FEATURE_COLS:
        if _c not in FLEET_CONFIG["TVM"].all_candidate_features:
            FLEET_CONFIG["TVM"].all_candidate_features.append(_c)
    print(f"[features] TVM sale features enabled: +{len(TVM_SALE_FEATURE_COLS)} prior-window "
          f"candidate features (TVM only)")


# -- device_event_enriched: TVM precursor event families (TVM only) ------------
TVM_EVENT_FAMILIES = {
    "tvmev_csc_bin":     ([211, 212], ("Set", "Set and Clear")),        # CSC bin near full / full
    "tvmev_csc_jam":     ([205], ("Set", "Set and Clear")),             # CSC transport jam
    "tvmev_bhu_retract": ([411], ("Set", "Set and Clear")),             # bill unit retract at exit
    "tvmev_bill_error":  ([402, 403], ("Set", "Set and Clear")),        # bill error, bill unit comm error
    "tvmev_sct_mag":     ([224, 225, 229], ("Set", "Set and Clear")),   # SCT mag capacity / feed error
    "tvmev_scrst_stack": ([226, 227], ("Set", "Set and Clear")),        # SCRST stacker capacity
    "tvmev_keypad_ui":   ([701, 802], ("Set", "Set and Clear")),        # soft key stuck, keypad comm error
    "tvmev_alarm_mod":   ([1401, 1404], ("Set", "Set and Clear")),      # alarm module comm error / battery low
    "tvmev_reboot":      ([184], ("Set", "Set and Clear")),             # commanded reboot
    "tvmev_coin_vault":  ([521], ("Set", "Set and Clear")),             # coin vault open / close
}
TVM_EVENT_BASE_COLS = list(TVM_EVENT_FAMILIES)
TVM_EVENT_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in TVM_EVENT_BASE_COLS for w in (7, 30)]
if _enable_tvm_event_features():
    for _c in TVM_EVENT_FEATURE_COLS:
        if _c not in FLEET_CONFIG["TVM"].all_candidate_features:
            FLEET_CONFIG["TVM"].all_candidate_features.append(_c)
    print(f"[features] TVM event families enabled: +{len(TVM_EVENT_FEATURE_COLS)} prior-window "
          f"candidate features (TVM only)")

# -- device_event_enriched: outages keyed by the day they CLEARED ---------------
CDUR_BASE_COLS = ["cdur_short_cnt", "cdur_long_cnt", "cdur_minutes"]
CDUR_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in CDUR_BASE_COLS for w in (7, 30)]
if _enable_cleared_duration_features():
    for _cfg in FLEET_CONFIG.values():
        for _c in CDUR_FEATURE_COLS:
            if _c not in _cfg.all_candidate_features:
                _cfg.all_candidate_features.append(_c)
    print(f"[features] cleared-outage durations enabled: +{len(CDUR_FEATURE_COLS)} prior-window "
          f"candidate features (keyed by clear date)")

# -- station DOPP health, incident history, long DOPP windows (25-Sep) -----------
STATION_DOPP_SOURCE_COLS = ["dopp_comms_err", "dopp_list_behind", "dopp_list_far", "dopp_app_db"]
STATION_DOPP_BASE_COLS = [f"stn_{c}" for c in STATION_DOPP_SOURCE_COLS]
STATION_DOPP_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in STATION_DOPP_BASE_COLS for w in (7, 30)]
INCP_BASE_COLS = ["incp_opened"]
INCP_FEATURE_COLS = ([f"{c}_prior_sum_{w}d" for c in INCP_BASE_COLS for w in (7, 30)]
                     + ["incp_opened_days_since"])
LONG_WINDOW_FEATURE_COLS = [f"long_{c}_90d" for c in DOPP_BASE_COLS]
SILENT_BASE_COLS = ["silent_day", "evall_count"]   # names only; windows built in add_auxiliary
REPAIR_FEATURE_COLS = ["rep_last_hold_days", "rep_prev_hold_days",
                       "cdur_max_prior_30d", "cdur_mean_min_prior_30d"]
MLED_BASE_COLS = ["mled_cmd_cnt", "mled_maint_cnt"]
MLED_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in MLED_BASE_COLS for w in (7, 30)]
# major / chargeable are adjudicated after the ticket opens, so they are not as-of day D;
# priority and category are latest-version values too -- treat the family as exploratory.
INCLAST_FEATURE_COLS = ["inclast_priority", "inclast_cat_code"]
STNBUSY_BASE_COLS = ["stnbusy_taps"]
STNBUSY_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in STNBUSY_BASE_COLS for w in (7, 30)]
SILENT_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in SILENT_BASE_COLS for w in (7, 30)]
# ServiceNow repair time, keyed by RESOLUTION date. The mean is derived from the 30-day
# prior sums after the windows exist; NULL when no ticket resolved in the window.
SNREP_BASE_COLS = ["snrep_resolved_cnt", "snrep_resolve_min_sum", "snrep_resolve_min_max"]
SNREP_FEATURE_COLS = ([f"{c}_prior_sum_{w}d" for c in SNREP_BASE_COLS for w in (7, 30)]
                      + ["snrep_mean_resolve_min_30d"])
# VALIDATOR has no incident_history rows; it gets SN repair only through incident_task_ci_link.
SNREP_FLEETS = ("GATE", "TVM") + (("VALIDATOR",) if _enable_validator_ticket_features() else ())
# CTA chargability tickets, keyed by the day the ticket CLOSED.
CHG_BASE_COLS = ["chg_closed_cnt", "chg_reset_cnt", "chg_replace_cnt", "chg_nff_cnt", "chg_adjust_cnt",
                 "chg_vandal_cnt", "chg_planned_cnt", "chg_repair_min_sum", "chg_component_types"]
CHG_FEATURE_COLS = [f"{c}_prior_sum_{w}d" for c in CHG_BASE_COLS for w in (7, 30)]
# Day-D calendar facts, known in advance -- not windows, and not same-day leakage.
CAL_FEATURE_COLS = ["cal_dow", "cal_is_weekend"]
# (on, columns, label[, kind, fleets]) -- kind defaults to prior-window, fleets to all.
for _on, _cols, _label, *_opt in ((_enable_station_dopp_features(), STATION_DOPP_FEATURE_COLS, "station DOPP health"),
                             (_enable_incident_prior_features(), INCP_FEATURE_COLS, "incident history"),
                             (_enable_long_window_features(), LONG_WINDOW_FEATURE_COLS, "90-day DOPP windows"),
                             (_enable_silent_day_features(), SILENT_FEATURE_COLS, "silent days"),
                             (_enable_repair_features(), REPAIR_FEATURE_COLS, "repair durability"),
                             (_enable_maint_ledger_features(), MLED_FEATURE_COLS, "maintenance ledger"),
                             (_enable_last_ticket_features(), INCLAST_FEATURE_COLS, "last ticket"),
                             (_enable_station_busy_features(), STNBUSY_FEATURE_COLS, "station busyness"),
                             (_enable_sn_repair_features(), SNREP_FEATURE_COLS,
                              f"SN repair time ({'/'.join(SNREP_FLEETS)})", "prior-window", SNREP_FLEETS),
                             (_enable_chargability_features(), CHG_FEATURE_COLS, "chargability tickets"),
                             (_enable_calendar_features(), CAL_FEATURE_COLS, "calendar", "day-D calendar")):
    _kind = _opt[0] if _opt else "prior-window"
    _fleets = _opt[1] if len(_opt) > 1 else tuple(FLEET_CONFIG)
    if _on:
        for _fk in _fleets:
            _cfg = FLEET_CONFIG[_fk]
            for _c in _cols:
                if _c not in _cfg.all_candidate_features:
                    _cfg.all_candidate_features.append(_c)
        print(f"[features] {_label} enabled: +{len(_cols)} {_kind} candidate features")
if _enable_validator_ticket_features():
    if _enable_incident_prior_features() or _enable_sn_repair_features():
        _vt_fams = [n for n, on in (("incident history", _enable_incident_prior_features()),
                                    ("SN repair", _enable_sn_repair_features())) if on]
        print("[features] VALIDATOR tickets enabled: " + " and ".join(_vt_fams)
              + " read silver.incident_task_ci_link on DEVICE_ID")
    else:
        print("[features] VALIDATOR tickets enabled but idle: needs PS1_ENABLE_INCIDENT_PRIOR_FEATURES "
              "or PS1_ENABLE_SN_REPAIR_FEATURES")

# -- recency and trend over the warning families -------------------------------
# Ticket outcomes join the recency set with the chargability family: days since the last
# part swap, remote reset, hands-on clear or no-fault ticket, and their 7-v-30-day trend.
# A device just after a swap and one on its third reset in a week are different risks.
CHG_RECENCY_COLS = ["chg_closed_cnt", "chg_reset_cnt", "chg_replace_cnt", "chg_adjust_cnt", "chg_nff_cnt"]
RECENCY_BASE_COLS = (DOPP_BASE_COLS + TVM_EVENT_BASE_COLS + WARNING_BASE_COLS
                     + (MLED_BASE_COLS if _enable_maint_ledger_features() else [])
                     + (CHG_RECENCY_COLS if _enable_chargability_features() else []))
RECENCY_CAP_DAYS = 90.0
if _enable_recency_trend_features():
    _n_rt = 0
    for _cfg in FLEET_CONFIG.values():
        for _c in RECENCY_BASE_COLS:
            for _f in (f"rec_{_c}_days", f"trend_{_c}_7v30"):
                if _f not in _cfg.all_candidate_features:
                    _cfg.all_candidate_features.append(_f)
                    _n_rt += 1
    print(f"[features] recency + trend enabled over {len(RECENCY_BASE_COLS)} warning families "
          f"(only families present in a fleet's data become features)")


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


# -- features whose day-D value includes day D or later ---------------------
# Found 25-Sep by code review. Each slipped past GAP_ENCODING_FEATURES because it is a
# longer window, or arrives through a source block the gap list never sees:
#   usage_failure_count_30d, usage_cumulative_*   S20 windows end at CURRENT ROW
#   availability_pct_7d   a transform of outage_min_7d, whose window includes today
#   chain_length, met_comms_*   same-day counts
#   evq_dur_*   durations of D-1 Sets that may clear after D
# Mirrors ps1_native_gbdt_lab.py --drop-preset honest, so the two report the same figure.
SAME_DAY_FEATURES = ["usage_failure_count_30d", "usage_cumulative_failure_count",
                     "usage_cumulative_outage_min", "availability_pct_7d", "chain_length",
                     # facility mean of 7-day windows that END TODAY and include the device
                     "facility_peer_hw_oos_7d"]
SAME_DAY_PREFIXES = ("met_comms_", "evq_dur_")


def _exclude_same_day_features() -> bool:
    return os.environ.get("PS1_EXCLUDE_SAME_DAY_FEATURES", "false").strip().lower() == "true"


def _is_same_day(c):
    return c in SAME_DAY_FEATURES or c.startswith(SAME_DAY_PREFIXES)


if _exclude_same_day_features():
    _sd = set()
    for _cfg in FLEET_CONFIG.values():
        for _lst in (_cfg.all_candidate_features, _cfg.failure_features,
                     _cfg.chain_features, _cfg.mttr_features):
            _sd |= {c for c in _lst if _is_same_day(c)}
            _lst[:] = [c for c in _lst if not _is_same_day(c)]
    print(f"[features] PS1_EXCLUDE_SAME_DAY_FEATURES=true -- withheld {len(_sd)} features "
          f"whose day-D value includes day D or later")


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
    # Rows marked _ps1_drop (PS1_FILTER_AFTER_FEATURES) are held for prior windows only.
    # A separate partition keeps them out of the onset sequence, so the scored rows get
    # the same lag as when those rows were dropped; they are also left out of the counts
    # and are never removed as in-spell, since CELL 8 removes them.
    _held = "_ps1_drop" in frame.columns
    if _held:
        w = Window.partitionBy(key_col, "_ps1_drop").orderBy(date_col)
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
    stats = (tagged.where(~F.col("_ps1_drop")) if _held else tagged).agg(
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
    _keep = ~F.col("_in_spell")
    if _held:
        _keep = _keep | F.col("_ps1_drop")
    return (
        tagged.where(_keep)
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
    if _enable_station_features() or _enable_station_dopp_features():
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
        _nondev = _exclude_nondevice_outages()
        if _nondev:
            print("[label] non-device excl : ON -- vandal/customer and planned chargability outages dropped")
        print(f"[label] session gap days : {_gap}" + ("  (0 = count every event-day)" if not _gap else ""))
        _edge = _label_observed_edge()
        _active = _exclude_active_episode()
        _by_id = _label_match_device_id()
        _min_oos = _label_min_oos_minutes()
        print(f"[label] min OOS minutes  : {f'{_min_oos:g} -- Sets clearing sooner are toggles, not failures' if _min_oos else 'off'}")
        print(f"[label] device match     : {'DEVICE_ID -- events under old device keys kept' if _by_id else 'DEVICE_KEY -- current keys only'}")
        _mask = _label_mask_unobserved()
        _after_feats = _filter_after_features()
        print(f"[label] active episodes  : {'EXCLUDED -- only in-service device-days are scored' if _active else 'kept'}")
        print(f"[label] filter timing    : {'AFTER features -- dropped rows kept for prior windows' if _after_feats else 'BEFORE features'}")
        print(f"[label] interior mask    : {'ON -- rows whose lookahead crosses an unobserved day are dropped' if _mask else 'off'}")
        print(f"[label] observed edge    : {'ON -- rows with an unobservable lookahead are dropped' if _edge else 'off'}")
        _guard = _completeness_guard()
        if _guard:
            print(f"[label] completeness grd : ON  signal={_coverage_signal()} "
                  f"floor={_coverage_floor()} lookback={_coverage_lookback_days()}d")
            if not _gap:
                print("[label] !! GUARD ARMED BUT SESSIONISATION IS OFF "
                      "(PS1_EVENT_SESSION_GAP_DAYS=0). No gap rule runs, so the guard "
                      "has nothing to modify and will report zeros. That is NOT a clean "
                      "feed -- it is a guard that never fired.")
        if (_defn != PS1_EVENT_DEFINITION_DEFAULT or horizon_days != 3 or _relief or _gap
                or _guard or _edge or _active or _mask or _by_id or _min_oos > 0 or _nondev):
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
        if _by_id:
            _dim_dev = _silver_col(dim_raw, "DEVICE_ID", dim_map)
            current_devices = (
                dim_raw
                .where(F.col(is_current_col) == True)
                .select(F.col(_dim_dev).cast("string").alias("DEVICE_ID"))
                .distinct()
            )
            _cur_match = (F.col(f"dee.{dee_dev}").cast("string") == F.col("d_cur.DEVICE_ID"))
        else:
            current_devices = (
                dim_raw
                .where(F.col(is_current_col) == True)
                .select(F.col(dk_col).alias("DEVICE_KEY"))
                .distinct()
            )
            _cur_match = (F.col(f"dee.{dee_dk}") == F.col("d_cur.DEVICE_KEY"))
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
                _cur_match,
            )
            .where(F.col(f"dee.{dee_hw_oos}") == True)
            .where(F.col(f"dee.{dee_state}") == "Set")
            .where(F.col(f"dee.{dee_cat}") == device_category)
            .transform(_apply_event_definition)
            .transform(lambda fd: fd if not _min_oos else fd.where(
                F.col(f"dee.{_silver_col(dee_raw, 'duration_to_clear_min', dee_map)}").isNull()
                | (F.col(f"dee.{_silver_col(dee_raw, 'duration_to_clear_min', dee_map)}").cast("double")
                   >= F.lit(_min_oos))))
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

        # Outages a chargability ticket attributes to vandalism/customer misuse or planned
        # work: real downtime, but no device history predicts them. Same device, day inside
        # [to_date(start_dtm), to_date(end_dtm)]; an open ticket covers its start day only.
        # Built fully before failure_days is replaced, so a failure midway leaves it intact.
        # The active-episode filter still needs every OOS day, excluded or not: a device down
        # for vandalism is not in service, it just did not fail on its own.
        _all_fail_days = failure_days.select("DEVICE_ID", "failure_date")
        if _nondev:
            try:
                _cg_raw = spark.read.parquet(f"{s3_silver}/servicenow_cta_chargability/")
                _cg = _silver_col_map(_cg_raw)
                _g_dev, _g_s = _cg.get("device_id"), _cg.get("start_dtm")
                _g_e, _g_req = _cg.get("end_dtm"), _cg.get("req_class")
                if not (_g_dev and _g_s and _g_req):
                    raise ValueError(f"lacks device_id / start_dtm / req_class: {_cg_raw.columns[:25]}")
                _nd_s = F.to_date(F.col(_g_s))
                _nd_e = F.coalesce(F.to_date(F.col(_g_e)), _nd_s) if _g_e else _nd_s
                _nd = (
                    _cg_raw
                    .where(F.lower(F.trim(F.col(_g_req).cast("string")))
                           .isin("vandal_customer", "planned"))
                    .where(F.col(_g_dev).isNotNull() & F.col(_g_s).isNotNull())
                    .select(_norm_device_id(F.col(_g_dev)).alias("_nd_dev"),
                            _nd_s.alias("_nd_start"),
                            # capped at 30 days: END_DTM carries 1,000+ day outliers
                            F.least(F.greatest(_nd_e, _nd_s), F.date_add(_nd_s, 30)).alias("_nd_end"))
                    .distinct()
                )
                _fd_in = failure_days.persist(StorageLevel.MEMORY_AND_DISK)
                _nd_before = _fd_in.count()
                _fd_out = (
                    _fd_in.alias("fd")
                    .join(_nd.alias("nd"),
                          (_norm_device_id(F.col("fd.DEVICE_ID")) == F.col("nd._nd_dev"))
                          & (F.col("fd.failure_date") >= F.col("nd._nd_start"))
                          & (F.col("fd.failure_date") <= F.col("nd._nd_end")),
                          "left_anti")
                    .persist(StorageLevel.MEMORY_AND_DISK)
                )
                _nd_after = _fd_out.count()
                failure_days = _fd_out
                print(f"[label] non-device excl : {_nd_before - _nd_after:,} of {_nd_before:,} failure days "
                      f"(chargability req_class vandal_customer/planned)")
            except Exception as exc:
                print(f"[label] !! non-device exclusion requested but servicenow_cta_chargability "
                      f"is unavailable ({exc}); nothing excluded")

        # Every failure day (captured above, before the non-device exclusion) -- the active-
        # episode exclusion needs the days an episode stays open, not just where it began.
        _unobs_days = None
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
                    _unobs_days = _idx.where(F.col("_obs") == 0).select("cal_day")
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
        _labelled = (
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

        def _mark_drop(frame, hits, keys, broadcast=False):
            """Set _ps1_drop on rows matching `hits` instead of removing them.

            _ps1_new marks only the rows this step flags that no earlier step had, so its
            count equals what the step's left_anti join would have removed at that point.
            Column order is kept; `hits` is de-duplicated so the left join cannot fan out.
            """
            _cols = frame.columns
            _h = hits.select(*keys).distinct().withColumn("_ps1_hit", F.lit(True))
            if broadcast:
                _h = F.broadcast(_h)
            return (
                frame.join(_h, keys, "left")
                .withColumn("_ps1_new",
                            F.coalesce(F.col("_ps1_hit"), F.lit(False)) & ~F.col("_ps1_drop"))
                .withColumn("_ps1_drop", F.col("_ps1_drop") | F.col("_ps1_new"))
                .select(*_cols, "_ps1_new")
                .persist(StorageLevel.MEMORY_AND_DISK)
            )

        if _after_feats and (_active or _mask):
            _labelled = _labelled.withColumn("_ps1_drop", F.lit(False))

        if _active:
            _open = (
                _all_fail_days
                .crossJoin(spark.range(0, max(_gap, 0) + 1).select(F.col("id").cast("int").alias("_k")))
                .select("DEVICE_ID", F.date_add(F.col("failure_date"), F.col("_k")).alias("transit_day"))
                .distinct()
            )
            _labelled = _labelled.persist(StorageLevel.MEMORY_AND_DISK)
            if _after_feats:
                _labelled = _mark_drop(_labelled, _open, ["DEVICE_ID", "transit_day"])
                _d = (_labelled.where(F.col("_ps1_new"))
                      .agg(F.count(F.lit(1)).alias("n"), F.sum(F.col(TARGET_COL).cast("int")).alias("pos"))
                      .collect()[0])
                _labelled = _labelled.drop("_ps1_new")
            else:
                _d = (_labelled.join(_open, ["DEVICE_ID", "transit_day"], "left_semi")
                      .agg(F.count(F.lit(1)).alias("n"), F.sum(F.col(TARGET_COL).cast("int")).alias("pos"))
                      .collect()[0])
                _labelled = _labelled.join(_open, ["DEVICE_ID", "transit_day"], "left_anti")
            print(f"[label] active episodes  : dropped {_d['n']:,} device-days inside an open episode "
                  f"({(_d['pos'] or 0) / _d['n'] if _d['n'] else 0:.1%} of them labelled positive)")
        if _mask:
            if _unobs_days is None:
                print("[label] !! interior mask needs PS1_COMPLETENESS_GUARD=true and "
                      "PS1_COVERAGE_SIGNAL=fleet -- skipped")
            else:
                _masked = (_unobs_days.crossJoin(seq)
                           .select(F.date_sub(F.col("cal_day"), F.col("n")).alias("transit_day"))
                           .distinct())
                _labelled = _labelled.persist(StorageLevel.MEMORY_AND_DISK)
                if _after_feats:
                    _labelled = _mark_drop(_labelled, _masked, ["transit_day"], broadcast=True)
                    _n_m = _labelled.where(F.col("_ps1_new")).count()
                    _labelled = _labelled.drop("_ps1_new")
                else:
                    _n_m = _labelled.join(F.broadcast(_masked), "transit_day", "left_semi").count()
                    _labelled = _labelled.join(F.broadcast(_masked), "transit_day", "left_anti")
                print(f"[label] interior mask    : dropped {_n_m:,} rows whose {horizon_days}-day "
                      f"lookahead crosses an unobserved day")

        # ---- the RIGHT edge: where does the event feed actually stop? --------------
        # Measured over EVERY event, not just OOS Sets: a fleet can be quiet for days,
        # but the feed as a whole only goes silent when it has stopped. Future-dated
        # keys exist in this estate, so they are excluded from the max.
        _ev_max = (
            dee_raw.select(F.to_date(F.col(dee_dtm)).alias("_d"))
            .where(F.col("_d") <= F.current_date())
            .agg(F.max("_d")).collect()[0][0]
        )
        if _ev_max is not None and max_day is not None:
            _cut = _ev_max - _dt.timedelta(days=horizon_days)
            if max_day > _cut:
                _tail = _labelled.where(F.col("transit_day") > F.lit(_cut))
                if "_ps1_drop" in _labelled.columns:
                    # Count only scored rows; the drop below still removes marked rows too.
                    _tail = _tail.where(~F.col("_ps1_drop"))
                _n_tail = _tail.count()
                if _edge:
                    _labelled = _labelled.where(F.col("transit_day") <= F.lit(_cut))
                    print(f"[label] observed edge    : events end {_ev_max}; dropped {_n_tail:,} "
                          f"rows after {_cut} -- their {horizon_days}-day lookahead is not observable")
                else:
                    print(f"[label] !! RIGHT-CENSORED. Events end {_ev_max} but the spine runs to "
                          f"{max_day}: {_n_tail:,} rows after {_cut} are labelled from a lookahead "
                          f"that is partly unobserved, so their labels are biased toward 0. Set "
                          f"PS1_LABEL_OBSERVED_EDGE=true to drop them.")
        return _labelled


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
            # Rates describe the scored rows: rows marked _ps1_drop leave in CELL 8.
            _scored = (df_ps1.where(~F.col("_ps1_drop")) if "_ps1_drop" in df_ps1.columns
                       else df_ps1)
            _sla = _scored.agg(F.avg(SLA_TARGET_COL).alias("r")).collect()[0]["r"]
            _oos = _scored.agg(F.avg(TARGET_COL).alias("r")).collect()[0]["r"]
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

    # -- device_event_enriched -> device-day DOPP warning counts ---------------
    df_dopp = None
    if _enable_dopp_features():
        try:
            _dp_raw = spark.read.parquet(f"{s3_silver}/device_event_enriched/")
            _dp_lc = {c.casefold(): c for c in _dp_raw.columns}
            _d_dev, _d_day = _dp_lc.get("device_id"), _dp_lc.get("transit_day")
            _d_cat = _dp_lc.get("mars_device_category")
            _d_id, _d_state = _dp_lc.get("event_type_id"), _dp_lc.get("event_state_type_name")
            if not (_d_dev and _d_day and _d_id and _d_state):
                raise ValueError("device_event_enriched lacks DEVICE_ID / transit_day / "
                                 "EVENT_TYPE_ID / EVENT_STATE_TYPE_NAME")
            _codes = sorted({c for ids, _ in DOPP_FAMILIES.values() for c in ids})
            _dp = _dp_raw
            if _d_cat:
                _dp = _dp.where(F.col(_d_cat) == cfg.device_cat)
            _dp = (_dp.where(F.col(_d_id).cast("int").isin(*_codes))
                   .where(F.to_date(F.col(_d_day)) >= F.to_date(F.lit(start_day)))
                   .where(F.to_date(F.col(_d_day)) <= end_day_expr))
            _dp_aggs = [
                F.sum(F.when(F.col(_d_id).cast("int").isin(*ids) & F.col(_d_state).isin(*states), 1)
                       .otherwise(0)).alias(name)
                for name, (ids, states) in DOPP_FAMILIES.items()
            ]
            df_dopp = _dp.groupBy(
                F.col(_d_dev).cast("string").alias("DEVICE_ID"),
                F.to_date(F.col(_d_day)).alias("transit_day"),
            ).agg(*_dp_aggs)
            print(f"silver.device_event_enriched: DOPP rollup, {len(_dp_aggs)} warning families")
        except Exception as exc:
            df_dopp = None
            print(f"WARNING: DOPP rollup skipped ({exc})")

    # -- repair durability: completed episode starts and the hold before each -------
    df_rep = None
    if _enable_repair_features():
        try:
            _rp_raw = spark.read.parquet(f"{s3_silver}/device_event_enriched/")
            _rp = {c.casefold(): c for c in _rp_raw.columns}
            _r_dev, _r_dtm, _r_cat = _rp.get("device_id"), _rp.get("event_dtm"), _rp.get("mars_device_category")
            _r_hw, _r_st, _r_dur = _rp.get("is_hardware_oos_event"), _rp.get("event_state_type_name"), _rp.get("duration_to_clear_min")
            _r_kpi = _rp.get(KPI_FLAG_BY_FLEET.get(cfg.device_cat, "").casefold())
            if not (_r_dev and _r_dtm and _r_hw and _r_st):
                raise ValueError("device_event_enriched lacks DEVICE_ID / EVENT_DTM / is_hardware_oos_event / state")
            _rf = _rp_raw
            if _r_cat:
                _rf = _rf.where(F.col(_r_cat) == cfg.device_cat)
            _rf = _rf.where(F.col(_r_hw) == True).where(F.col(_r_st) == "Set")
            if _r_kpi:
                _rf = _rf.where(F.col(_r_kpi) == True)
            _floor = _label_min_oos_minutes()
            if _floor and _r_dur:
                _rf = _rf.where(F.col(_r_dur).isNull() | (F.col(_r_dur).cast("double") >= F.lit(_floor)))
            _fdays = (_rf.select(F.col(_r_dev).cast("string").alias("DEVICE_ID"),
                                 F.to_date(F.col(_r_dtm)).alias("_d"))
                      .where(F.col("_d") <= end_day_expr).distinct())
            _wd = Window.partitionBy("DEVICE_ID").orderBy("_d")
            _st = (_fdays.withColumn("_gap", F.datediff(F.col("_d"), F.lag("_d").over(_wd)))
                   .where(F.col("_gap").isNull() | (F.col("_gap") > 3))
                   .withColumn("rep_hold", (F.col("_gap") - 1).cast("double")))
            df_rep = (_st.withColumn("rep_hold_prev", F.lag("rep_hold").over(_wd))
                      .select("DEVICE_ID", F.col("_d").alias("transit_day"), "rep_hold", "rep_hold_prev"))
            print(f"repair durability: episode starts from KPI OOS >= {_floor:g} min")
        except Exception as exc:
            df_rep = None
            print(f"WARNING: repair-durability rollup skipped ({exc})")

    # -- silver.maintenance_ledger -> commanded / maintenance OOS per device-day -----
    df_mled = None
    if _enable_maint_ledger_features():
        try:
            _ml_raw = spark.read.parquet(f"{s3_silver}/maintenance_ledger/")
            _ml = {c.casefold(): c for c in _ml_raw.columns}
            _m_dev, _m_day, _m_cat = _ml.get("device_id"), _ml.get("ledger_date"), _ml.get("mars_device_category")
            _m_cmd, _m_mnt = _ml.get("is_commanded_oos"), _ml.get("is_maintenance_oos")
            if not (_m_dev and _m_day and (_m_cmd or _m_mnt)):
                raise ValueError(f"maintenance_ledger lacks DEVICE_ID / ledger_date / flags: {_ml_raw.columns[:25]}")
            _mf = _ml_raw
            if _m_cat:
                _mf = _mf.where(F.col(_m_cat) == cfg.device_cat)
            df_mled = (
                _mf.where(F.to_date(F.col(_m_day)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_m_day)) <= end_day_expr)
                .groupBy(F.col(_m_dev).cast("string").alias("DEVICE_ID"),
                         F.to_date(F.col(_m_day)).alias("transit_day"))
                .agg((F.sum(F.col(_m_cmd).cast("int")) if _m_cmd else F.lit(0)).cast("double").alias("mled_cmd_cnt"),
                     (F.sum(F.col(_m_mnt).cast("int")) if _m_mnt else F.lit(0)).cast("double").alias("mled_maint_cnt"))
            )
            print("silver.maintenance_ledger: commanded / maintenance OOS per device-day")
        except Exception as exc:
            df_mled = None
            print(f"WARNING: maintenance-ledger rollup skipped ({exc})")

    # -- silver.incident_history -> the ticket(s) opened per device-day -------------
    df_inclast = None
    if _enable_last_ticket_features():
        try:
            _ih_raw = spark.read.parquet(f"{s3_silver}/incident_history/")
            _ih = {c.casefold(): c for c in _ih_raw.columns}
            _h_dk, _h_day = _ih.get("device_key"), _ih.get("incident_date")
            _h_pri, _h_cat = _ih.get("priority"), _ih.get("category")
            _h_maj, _h_chg = _ih.get("is_major_incident"), _ih.get("is_chargeable")
            if not (_h_dk and _h_day):
                raise ValueError(f"incident_history lacks DEVICE_KEY / incident_date: {_ih_raw.columns[:25]}")
            df_inclast = (
                _ih_raw.where(F.col(_h_dk).isNotNull())
                .where(F.to_date(F.col(_h_day)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_h_day)) <= end_day_expr)
                .groupBy(F.regexp_replace(F.col(_h_dk).cast("string"), r"\.0+$", "").alias("_il_dk"),
                         F.to_date(F.col(_h_day)).alias("transit_day"))
                .agg((F.min(F.col(_h_pri).cast("double")) if _h_pri else F.lit(None).cast("double")).alias("_il_pri"),
                     (F.max(F.col(_h_maj).cast("double")) if _h_maj else F.lit(None).cast("double")).alias("_il_maj"),
                     (F.max(F.col(_h_chg).cast("double")) if _h_chg else F.lit(None).cast("double")).alias("_il_chg"),
                     (F.max(F.abs(F.hash(F.upper(F.trim(F.col(_h_cat))))) % 997).cast("double")
                      if _h_cat else F.lit(None).cast("double")).alias("_il_cat"))
            )
            print("silver.incident_history: last-ticket attributes per device-day")
        except Exception as exc:
            df_inclast = None
            print(f"WARNING: last-ticket rollup skipped ({exc})")

    # -- device_event_enriched -> every event per device-day (silent-day features) --
    df_evall = None
    if _enable_silent_day_features():
        try:
            _ea_raw = spark.read.parquet(f"{s3_silver}/device_event_enriched/")
            _ea = {c.casefold(): c for c in _ea_raw.columns}
            _a_dev, _a_day, _a_cat = _ea.get("device_id"), _ea.get("transit_day"), _ea.get("mars_device_category")
            if not (_a_dev and _a_day):
                raise ValueError("device_event_enriched lacks DEVICE_ID / transit_day")
            _ea_f = _ea_raw
            if _a_cat:
                _ea_f = _ea_f.where(F.col(_a_cat) == cfg.device_cat)
            df_evall = (
                _ea_f
                .where(F.to_date(F.col(_a_day)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_a_day)) <= end_day_expr)
                .groupBy(F.col(_a_dev).cast("string").alias("DEVICE_ID"),
                         F.to_date(F.col(_a_day)).alias("transit_day"))
                .agg(F.count(F.lit(1)).cast("double").alias("evall_count"))
                .withColumn("_ea_ep", F.col("transit_day").cast("timestamp").cast("long"))
            )
            # One row per device-day WITH events, unfiltered by the label. Silent days in
            # [D-N, D-1] = observable calendar days in that range minus event days in it.
            _w_first = Window.partitionBy("DEVICE_ID")
            _sil_cols = []
            for _n in (7, 30):
                _w_n = Window.partitionBy("DEVICE_ID").orderBy("_ea_ep").rangeBetween(-_n * 86_400, -1)
                _obs = F.least(F.lit(float(_n)),
                               ((F.col("_ea_ep") - F.min("_ea_ep").over(_w_first)) / 86_400).cast("double"))
                df_evall = (
                    df_evall
                    .withColumn(f"evall_count_prior_sum_{_n}d",
                                F.coalesce(F.sum("evall_count").over(_w_n), F.lit(0.0)))
                    .withColumn(f"silent_day_prior_sum_{_n}d",
                                F.greatest(F.lit(0.0), _obs - F.count(F.lit(1)).over(_w_n).cast("double")))
                )
                _sil_cols += [f"evall_count_prior_sum_{_n}d", f"silent_day_prior_sum_{_n}d"]
            df_evall = df_evall.select("DEVICE_ID", "transit_day", *_sil_cols)
            print("silver.device_event_enriched: silent days and event volume, prior windows on the "
                  "unfiltered event calendar")
        except Exception as exc:
            df_evall = None
            print(f"WARNING: silent-day rollup skipped ({exc})")

    # -- silver.incident_history -> tickets OPENED per device-day ----------------------
    # device_incident_features_daily keeps its per-day counts inside a CTE and publishes only
    # rolling columns, so the opening dates are read from incident_history directly. Only the
    # opening date is used: priority, major and chargeable are latest-version values.
    df_incp = None
    _vt_tickets = cfg.device_cat == "VALIDATOR" and _enable_validator_ticket_features()
    if _enable_incident_prior_features() and _vt_tickets:
        # incident_history has no validators; incident_task_ci_link reaches them through the
        # bus number on the task CI. One incident can link the same CI more than once, so
        # tickets are counted once per device. Keyed by DEVICE_ID -- the _incp_dev column
        # tells CELL 8 to join on DEVICE_ID instead of DEVICE_KEY.
        try:
            _vl_raw = spark.read.parquet(f"{s3_silver}/incident_task_ci_link/")
            _vl = {c.casefold(): c for c in _vl_raw.columns}
            _v_dev = _vl.get("device_id")
            _v_day = _vl.get("incident_date") or _vl.get("opened_at")
            _v_id = _vl.get("incident_sys_id") or _vl.get("incident_number")
            if not (_v_dev and _v_day):
                raise ValueError(f"incident_task_ci_link lacks DEVICE_ID / incident_date: {_vl_raw.columns[:25]}")
            # A row with no incident id counts as its own ticket.
            _v_tk = F.col(_v_id).cast("string") if _v_id else F.lit(None).cast("string")
            df_incp = (
                _vl_raw.where(F.col(_v_dev).isNotNull())
                .select(_norm_device_id(F.col(_v_dev)).alias("_incp_dev"),
                        F.to_date(F.col(_v_day)).alias("transit_day"),
                        F.coalesce(_v_tk, F.monotonically_increasing_id().cast("string")).alias("_tk"))
                .where(F.col("transit_day") >= F.to_date(F.lit(start_day)))
                .where(F.col("transit_day") <= end_day_expr)
                .groupBy("_incp_dev", "transit_day")
                .agg(F.countDistinct("_tk").cast("double").alias("incp_opened"))
            )
            print("silver.incident_task_ci_link: VALIDATOR tickets opened per device-day "
                  "(opening date only, DEVICE_ID key)")
        except Exception as exc:
            df_incp = None
            print(f"WARNING: VALIDATOR ticket rollup skipped ({exc})")
    elif _enable_incident_prior_features():
        try:
            _ip_raw = spark.read.parquet(f"{s3_silver}/incident_history/")
            _ip = {c.casefold(): c for c in _ip_raw.columns}
            _i_dk, _i_day = _ip.get("device_key"), _ip.get("incident_date")
            if not (_i_dk and _i_day):
                raise ValueError(f"incident_history lacks DEVICE_KEY / incident_date: {_ip_raw.columns[:25]}")
            df_incp = (
                _ip_raw.where(F.col(_i_dk).isNotNull())
                .where(F.to_date(F.col(_i_day)) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_i_day)) <= end_day_expr)
                .groupBy(F.regexp_replace(F.col(_i_dk).cast("string"), r"\.0+$", "").alias("_incp_dk"),
                         F.to_date(F.col(_i_day)).alias("transit_day"))
                .agg(F.count(F.lit(1)).cast("double").alias("incp_opened"))
            )
            print("silver.incident_history: tickets opened per device-day (opening date only)")
        except Exception as exc:
            df_incp = None
            print(f"WARNING: incident history rollup skipped ({exc})")

    # -- ServiceNow repair time, keyed by the day the ticket was RESOLVED --------------
    # Only fields fixed at resolution: the resolution timestamp and minutes to resolve.
    # GATE/TVM: incident_history on DEVICE_KEY. VALIDATOR: incident_task_ci_link on
    # DEVICE_ID, and only with PS1_ENABLE_VALIDATOR_TICKET_FEATURES. The key column name
    # (_snrep_dk / _snrep_dev) tells CELL 8 which spine column to join on.
    df_snrep = None
    if _enable_sn_repair_features() and (cfg.device_cat != "VALIDATOR" or _vt_tickets):
        try:
            _sr_src = "incident_task_ci_link" if _vt_tickets else "incident_history"
            _sr_raw = spark.read.parquet(f"{s3_silver}/{_sr_src}/")
            _sr = {c.casefold(): c for c in _sr_raw.columns}
            _s_key = _sr.get("device_id") if _vt_tickets else _sr.get("device_key")
            _s_res = _sr.get("resolved_dtm") or _sr.get("resolved_at")
            _s_open = _sr.get("opened_dtm") or _sr.get("opened_at")
            _s_min = _sr.get("time_to_resolve_minutes")
            _s_id = (_sr.get("incident_sys_id") or _sr.get("incident_number")
                     or _sr.get("sys_id") or _sr.get("number"))
            if not (_s_key and _s_res and (_s_min or _s_open)):
                raise ValueError(f"{_sr_src} lacks the key / resolution time / minutes to resolve: "
                                 f"{_sr_raw.columns[:25]}")
            # Fall back to resolved - opened when the minutes column is absent.
            _s_mins = (F.col(_s_min).cast("double") if _s_min
                       else (F.col(_s_res).cast("timestamp").cast("long")
                             - F.col(_s_open).cast("timestamp").cast("long")) / 60.0)
            _s_kname = "_snrep_dev" if _vt_tickets else "_snrep_dk"
            _s_kexpr = (_norm_device_id(F.col(_s_key)) if _vt_tickets
                        else F.regexp_replace(F.col(_s_key).cast("string"), r"\.0+$", ""))
            _s_tk = F.coalesce(F.col(_s_id).cast("string") if _s_id else F.lit(None).cast("string"),
                               F.monotonically_increasing_id().cast("string"))
            # Negative minutes are clock errors; a NULL cannot be averaged. Both are dropped
            # so the derived mean divides like by like.
            df_snrep = (
                _sr_raw.where(F.col(_s_key).isNotNull() & F.col(_s_res).isNotNull())
                .select(_s_kexpr.alias(_s_kname),
                        F.to_date(F.col(_s_res)).alias("transit_day"),
                        _s_mins.alias("_mins"),
                        _s_tk.alias("_tk"))
                .where(F.col("_mins").isNotNull() & (F.col("_mins") >= 0))
                .where(F.col("transit_day") >= F.to_date(F.lit(start_day)))
                .where(F.col("transit_day") <= end_day_expr)
                .dropDuplicates([_s_kname, "_tk"])
                .groupBy(_s_kname, "transit_day")
                .agg(F.count(F.lit(1)).cast("double").alias("snrep_resolved_cnt"),
                     F.sum("_mins").alias("snrep_resolve_min_sum"),
                     F.max("_mins").alias("snrep_resolve_min_max"))
            )
            print(f"silver.{_sr_src}: tickets resolved per device-day (resolution date, "
                  f"{'DEVICE_ID' if _vt_tickets else 'DEVICE_KEY'} key)")
        except Exception as exc:
            df_snrep = None
            print(f"WARNING: SN repair-time rollup skipped ({exc})")

    # -- servicenow_cta_chargability -> closed tickets per device-day -------------------
    # Keyed by to_date(end_dtm): a ticket is visible only after it closes. A ticket can
    # span several rows (one per component), so flags and minutes are taken once per
    # ticket (event_id) before the device-day sum; components are counted distinct.
    df_chg = None
    if _enable_chargability_features():
        try:
            _cg_raw = spark.read.parquet(f"{s3_silver}/servicenow_cta_chargability/")
            _cg = {c.casefold(): c for c in _cg_raw.columns}
            _g_dev, _g_end = _cg.get("device_id"), _cg.get("end_dtm")
            if not (_g_dev and _g_end):
                raise ValueError(f"servicenow_cta_chargability lacks device_id / end_dtm: "
                                 f"{_cg_raw.columns[:25]}")
            _g_res, _g_req = _cg.get("res_class"), _cg.get("req_class")
            _g_comp, _g_rep, _g_id = _cg.get("component"), _cg.get("repair_min"), _cg.get("event_id")

            def _cls(col, val):
                if not col:
                    return F.lit(0.0)
                return F.when(F.lower(F.trim(F.col(col).cast("string"))) == val, 1.0).otherwise(0.0)

            _g_tk = F.coalesce(F.col(_g_id).cast("string") if _g_id else F.lit(None).cast("string"),
                               F.monotonically_increasing_id().cast("string"))
            _tickets = (
                _cg_raw.where(F.col(_g_dev).isNotNull() & F.col(_g_end).isNotNull())
                .withColumn("_chg_dev", _norm_device_id(F.col(_g_dev)))
                .withColumn("transit_day", F.to_date(F.col(_g_end)))
                .withColumn("_tk", _g_tk)
                .where(F.col("transit_day") >= F.to_date(F.lit(start_day)))
                .where(F.col("transit_day") <= end_day_expr)
                .groupBy("_chg_dev", "transit_day", "_tk")
                .agg(F.max(_cls(_g_res, "reset")).alias("_reset"),
                     F.max(_cls(_g_res, "replace")).alias("_replace"),
                     F.max(_cls(_g_res, "nff")).alias("_nff"),
                     F.max(_cls(_g_res, "adjust")).alias("_adjust"),
                     F.max(_cls(_g_req, "vandal_customer")).alias("_vandal"),
                     F.max(_cls(_g_req, "planned")).alias("_planned"),
                     (F.max(F.col(_g_rep).cast("double")) if _g_rep
                      else F.lit(None).cast("double")).alias("_rep"),
                     *([F.collect_set(F.upper(F.trim(F.col(_g_comp).cast("string")))).alias("_comps")]
                       if _g_comp else []))
            )
            df_chg = (
                _tickets.groupBy("_chg_dev", "transit_day")
                .agg(F.count(F.lit(1)).cast("double").alias("chg_closed_cnt"),
                     F.sum("_reset").alias("chg_reset_cnt"),
                     F.sum("_replace").alias("chg_replace_cnt"),
                     F.sum("_nff").alias("chg_nff_cnt"),
                     # hands-on clears: feed errors, jams, worn/torn bills, foreign material
                     F.sum("_adjust").alias("chg_adjust_cnt"),
                     F.sum("_vandal").alias("chg_vandal_cnt"),
                     F.sum("_planned").alias("chg_planned_cnt"),
                     F.sum("_rep").alias("chg_repair_min_sum"),
                     (F.size(F.array_distinct(F.flatten(F.collect_list("_comps")))).cast("double")
                      if _g_comp else F.lit(0.0)).alias("chg_component_types"))
            )
            print("servicenow_cta_chargability: closed tickets per device-day (close date, "
                  "DEVICE_ID key)")
        except Exception as exc:
            df_chg = None
            print(f"WARNING: chargability rollup skipped ({exc})")

    # -- device_event_enriched -> outages keyed by the day they cleared -----------
    df_cdur = None
    if _enable_cleared_duration_features():
        try:
            _cd_raw = spark.read.parquet(f"{s3_silver}/device_event_enriched/")
            _cd_lc = {c.casefold(): c for c in _cd_raw.columns}
            _c_dev, _c_cat = _cd_lc.get("device_id"), _cd_lc.get("mars_device_category")
            _c_dtm, _c_dur = _cd_lc.get("event_dtm"), _cd_lc.get("duration_to_clear_min")
            _c_hw, _c_state = _cd_lc.get("is_hardware_oos_event"), _cd_lc.get("event_state_type_name")
            _c_kpi = _cd_lc.get(KPI_FLAG_BY_FLEET.get(cfg.device_cat, "").casefold())
            if not (_c_dev and _c_dtm and _c_dur and _c_hw and _c_state):
                raise ValueError("device_event_enriched lacks DEVICE_ID / EVENT_DTM / "
                                 "duration_to_clear_min / is_hardware_oos_event / state")
            _cd = _cd_raw
            if _c_cat:
                _cd = _cd.where(F.col(_c_cat) == cfg.device_cat)
            _cd = (_cd.where(F.col(_c_hw) == True)
                   .where(F.col(_c_state) == "Set")
                   .where(F.col(_c_dur).isNotNull() & (F.col(_c_dur).cast("double") >= 0)))
            if _c_kpi:
                _cd = _cd.where(F.col(_c_kpi) == True)
            _dur = F.col(_c_dur).cast("double")
            _clear_day = F.to_date(
                (F.col(_c_dtm).cast("timestamp").cast("long") + _dur * 60.0).cast("timestamp"))
            df_cdur = (
                _cd.withColumn("_clear_day", _clear_day)
                .where(F.col("_clear_day") >= F.to_date(F.lit(start_day)))
                .where(F.col("_clear_day") <= end_day_expr)
                .groupBy(F.col(_c_dev).cast("string").alias("DEVICE_ID"),
                         F.col("_clear_day").alias("transit_day"))
                .agg(F.sum(F.when(_dur < 60, 1).otherwise(0)).alias("cdur_short_cnt"),
                     F.sum(F.when(_dur >= 60, 1).otherwise(0)).alias("cdur_long_cnt"),
                     F.sum(_dur).alias("cdur_minutes"),
                     F.max(_dur).alias("cdur_max_min"))
            )
            print("silver.device_event_enriched: cleared-outage rollup keyed by clear date"
                  + ("" if _c_kpi else " (no KPI flag column -- all hardware OOS)"))
        except Exception as exc:
            df_cdur = None
            print(f"WARNING: cleared-outage rollup skipped ({exc})")

    # -- device_event_enriched -> device-day TVM precursor event families ---------
    df_tvmev = None
    if _enable_tvm_event_features() and cfg.device_cat == "TVM":
        try:
            _tv_raw = spark.read.parquet(f"{s3_silver}/device_event_enriched/")
            _tv_lc = {c.casefold(): c for c in _tv_raw.columns}
            _t_dev, _t_day = _tv_lc.get("device_id"), _tv_lc.get("transit_day")
            _t_cat = _tv_lc.get("mars_device_category")
            _t_id, _t_state = _tv_lc.get("event_type_id"), _tv_lc.get("event_state_type_name")
            if not (_t_dev and _t_day and _t_id and _t_state):
                raise ValueError("device_event_enriched lacks DEVICE_ID / transit_day / "
                                 "EVENT_TYPE_ID / EVENT_STATE_TYPE_NAME")
            _tcodes = sorted({c for ids, _ in TVM_EVENT_FAMILIES.values() for c in ids})
            _tv = _tv_raw
            if _t_cat:
                _tv = _tv.where(F.col(_t_cat) == cfg.device_cat)
            _tv = (_tv.where(F.col(_t_id).cast("int").isin(*_tcodes))
                   .where(F.to_date(F.col(_t_day)) >= F.to_date(F.lit(start_day)))
                   .where(F.to_date(F.col(_t_day)) <= end_day_expr))
            _tv_aggs = [
                F.sum(F.when(F.col(_t_id).cast("int").isin(*ids) & F.col(_t_state).isin(*states), 1)
                       .otherwise(0)).alias(name)
                for name, (ids, states) in TVM_EVENT_FAMILIES.items()
            ]
            df_tvmev = _tv.groupBy(
                F.col(_t_dev).cast("string").alias("DEVICE_ID"),
                F.to_date(F.col(_t_day)).alias("transit_day"),
            ).agg(*_tv_aggs)
            print(f"silver.device_event_enriched: TVM event rollup, {len(_tv_aggs)} precursor families")
        except Exception as exc:
            df_tvmev = None
            print(f"WARNING: TVM event rollup skipped ({exc})")

    # -- silver.tvm_sale_daily -> device-day sale volume, errors, cash/card --------
    df_sale = None
    if _enable_tvm_sale_features() and cfg.device_cat == "TVM":
        try:
            _sl_raw = spark.read.parquet(f"{s3_silver}/tvm_sale_daily/")
            _sl = {c.casefold(): c for c in _sl_raw.columns}
            _need = ["device_id", "transit_day", "daily_sales_count", "error_txn_count",
                     "cash_sales_count", "card_sales_count"]
            _miss = [n for n in _need if n not in _sl]
            if _miss:
                raise ValueError(f"tvm_sale_daily lacks {_miss}")
            df_sale = (
                _sl_raw
                .where(F.to_date(F.col(_sl["transit_day"])) >= F.to_date(F.lit(start_day)))
                .where(F.to_date(F.col(_sl["transit_day"])) <= end_day_expr)
                .groupBy(F.col(_sl["device_id"]).cast("string").alias("DEVICE_ID"),
                         F.to_date(F.col(_sl["transit_day"])).alias("transit_day"))
                .agg(F.sum(F.col(_sl["daily_sales_count"]).cast("double")).alias("tvm_sale_count"),
                     F.sum(F.col(_sl["error_txn_count"]).cast("double")).alias("tvm_sale_err_count"),
                     F.sum(F.col(_sl["cash_sales_count"]).cast("double")).alias("tvm_sale_cash_count"),
                     F.sum(F.col(_sl["card_sales_count"]).cast("double")).alias("tvm_sale_card_count"))
            )
            print("silver.tvm_sale_daily: rolled up to 4 device-day sale columns")
        except Exception as exc:
            df_sale = None
            print(f"WARNING: tvm_sale_daily rollup skipped ({exc})")

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
                _c = _up_lc.get(_src.casefold())
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
    global _PS1_DF_STATION, _PS1_DF_UPTIME, _PS1_DF_DOPP, _PS1_DF_SALE, _PS1_DF_TVMEV, _PS1_DF_CDUR, _PS1_DF_INCP, _PS1_DF_EVALL
    global _PS1_DF_REP, _PS1_DF_MLED, _PS1_DF_INCLAST
    global _PS1_DF_SNREP, _PS1_DF_CHG
    global _PS1_STN_MAX_DAY, _PS1_UPT_MAX_DAY
    _PS1_DF_WARN, _PS1_DF_AVAIL, _PS1_DF_EVQ = df_warn, df_avail, df_evq
    _PS1_DF_KPI = df_kpi
    _PS1_DF_STATION, _PS1_DF_UPTIME = df_station, df_uptime
    _PS1_DF_DOPP = df_dopp
    _PS1_DF_SALE = df_sale
    _PS1_DF_TVMEV = df_tvmev
    _PS1_DF_CDUR = df_cdur
    _PS1_DF_INCP = df_incp
    _PS1_DF_EVALL = df_evall
    _PS1_DF_REP, _PS1_DF_MLED, _PS1_DF_INCLAST = df_rep, df_mled, df_inclast
    _PS1_DF_SNREP, _PS1_DF_CHG = df_snrep, df_chg
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
    # Row-count backstop target: the spine rows that reach feature selection. Rows CELL 6
    # marked _ps1_drop (PS1_FILTER_AFTER_FEATURES) are removed before it in each branch.
    df_ps1_scored = (df_ps1.where(~F.col("_ps1_drop")) if "_ps1_drop" in df_ps1.columns
                     else df_ps1)
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
    df_dopp = globals().get("_PS1_DF_DOPP")
    df_sale = globals().get("_PS1_DF_SALE")
    df_tvmev = globals().get("_PS1_DF_TVMEV")
    df_cdur = globals().get("_PS1_DF_CDUR")
    df_incp = globals().get("_PS1_DF_INCP")
    df_evall = globals().get("_PS1_DF_EVALL")
    df_rep = globals().get("_PS1_DF_REP")
    df_mled = globals().get("_PS1_DF_MLED")
    df_inclast = globals().get("_PS1_DF_INCLAST")
    df_snrep = globals().get("_PS1_DF_SNREP")
    df_chg = globals().get("_PS1_DF_CHG")
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

        if df_dopp is not None:
            df_joined = df_joined.join(df_dopp, on=["DEVICE_ID", "transit_day"], how="left")
            for _dc in DOPP_BASE_COLS:
                if _dc in df_joined.columns:
                    df_joined = df_joined.withColumn(_dc, F.coalesce(F.col(_dc), F.lit(0.0)))
            print("Joined DOPP warning rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_sale is not None:
            df_joined = df_joined.join(df_sale, on=["DEVICE_ID", "transit_day"], how="left")
            for _sc in TVM_SALE_BASE_COLS:
                if _sc in df_joined.columns:
                    df_joined = df_joined.withColumn(_sc, F.coalesce(F.col(_sc), F.lit(0.0)))
            print("Joined tvm_sale_daily rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_tvmev is not None:
            df_joined = df_joined.join(df_tvmev, on=["DEVICE_ID", "transit_day"], how="left")
            for _tc in TVM_EVENT_BASE_COLS:
                if _tc in df_joined.columns:
                    df_joined = df_joined.withColumn(_tc, F.coalesce(F.col(_tc), F.lit(0.0)))
            print("Joined TVM event-family rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_cdur is not None:
            df_joined = df_joined.join(df_cdur, on=["DEVICE_ID", "transit_day"], how="left")
            for _cc in CDUR_BASE_COLS:
                if _cc in df_joined.columns:
                    df_joined = df_joined.withColumn(_cc, F.coalesce(F.col(_cc), F.lit(0.0)))
            print("Joined cleared-outage rollup on DEVICE_ID + clear date (prior windows only)")

        if df_incp is not None and "_incp_dev" in df_incp.columns:
            # VALIDATOR tickets from incident_task_ci_link: keyed by DEVICE_ID, not DEVICE_KEY.
            df_joined = (
                df_joined
                .withColumn("_incp_dev", _norm_device_id(F.col("DEVICE_ID")))
                .join(df_incp, on=["_incp_dev", "transit_day"], how="left")
                .drop("_incp_dev")
            )
            for _ic in INCP_BASE_COLS:
                if _ic in df_joined.columns:
                    df_joined = df_joined.withColumn(_ic, F.coalesce(F.col(_ic), F.lit(0.0)))
            print("Joined VALIDATOR tickets (incident_task_ci_link) on DEVICE_ID + transit_day "
                  "(prior windows only)")
        elif df_incp is not None and "DEVICE_KEY" in df_joined.columns:
            df_joined = (
                df_joined
                .withColumn("_incp_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                .join(df_incp, on=["_incp_dk", "transit_day"], how="left")
                .drop("_incp_dk")
            )
            for _ic in INCP_BASE_COLS:
                if _ic in df_joined.columns:
                    df_joined = df_joined.withColumn(_ic, F.coalesce(F.col(_ic), F.lit(0.0)))
            print("Joined incident history on DEVICE_KEY + transit_day (prior windows only)")

        # SN repair time: keyed by RESOLUTION date, so prior windows see only resolved tickets.
        if df_snrep is not None:
            if "_snrep_dev" in df_snrep.columns:
                df_joined = (
                    df_joined
                    .withColumn("_snrep_dev", _norm_device_id(F.col("DEVICE_ID")))
                    .join(df_snrep, on=["_snrep_dev", "transit_day"], how="left")
                    .drop("_snrep_dev")
                )
                print("Joined SN repair time on DEVICE_ID + resolution date (prior windows only)")
            elif "DEVICE_KEY" in df_joined.columns:
                df_joined = (
                    df_joined
                    .withColumn("_snrep_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                    .join(df_snrep, on=["_snrep_dk", "transit_day"], how="left")
                    .drop("_snrep_dk")
                )
                print("Joined SN repair time on DEVICE_KEY + resolution date (prior windows only)")
            else:
                print("WARNING: DEVICE_KEY missing on spine -- skipped SN repair-time join")
            for _rc in SNREP_BASE_COLS:
                if _rc in df_joined.columns:
                    df_joined = df_joined.withColumn(_rc, F.coalesce(F.col(_rc), F.lit(0.0)))

        # Chargability tickets: keyed by CLOSE date on DEVICE_ID, one row per device-day.
        if df_chg is not None:
            df_joined = (
                df_joined
                .withColumn("_chg_dev", _norm_device_id(F.col("DEVICE_ID")))
                .join(df_chg, on=["_chg_dev", "transit_day"], how="left")
                .drop("_chg_dev")
            )
            for _gc in CHG_BASE_COLS:
                if _gc in df_joined.columns:
                    df_joined = df_joined.withColumn(_gc, F.coalesce(F.col(_gc), F.lit(0.0)))
            print("Joined chargability tickets on DEVICE_ID + close date (prior windows only)")

        if (_enable_station_dopp_features() and df_dopp is not None
                and "FACILITY_ID" in df_joined.columns):
            # Mean over the OTHER devices at the station that day; NULL facility gets NULL.
            _w_fac_day = Window.partitionBy("FACILITY_ID", "transit_day")
            _n_fac = F.count(F.lit(1)).over(_w_fac_day)
            for _sc in STATION_DOPP_SOURCE_COLS:
                if _sc in df_joined.columns:
                    df_joined = df_joined.withColumn(
                        f"stn_{_sc}",
                        F.when(F.col("FACILITY_ID").isNotNull() & (_n_fac > 1),
                               (F.sum(F.col(_sc)).over(_w_fac_day) - F.col(_sc)) / (_n_fac - 1)))
            print("Station DOPP health: peer means over the other devices at each station")

        if df_evall is not None:
            # A spine day with no row in the rollup logged no event at all: that is the signal.
            df_joined = (
                df_joined.join(df_evall, on=["DEVICE_ID", "transit_day"], how="left")
            )
            print("Joined silent-day / event-volume prior windows (built on the unfiltered event calendar)")

        try:
            if df_rep is not None:
                df_joined = df_joined.join(df_rep, on=["DEVICE_ID", "transit_day"], how="left")
                print("Joined repair-durability episode starts (forward-filled from prior days)")
            if df_mled is not None:
                df_joined = df_joined.join(df_mled, on=["DEVICE_ID", "transit_day"], how="left")
                for _mc in MLED_BASE_COLS:
                    df_joined = df_joined.withColumn(_mc, F.coalesce(F.col(_mc), F.lit(0.0)))
                print("Joined maintenance ledger on DEVICE_ID + transit_day (prior windows only)")
            if df_inclast is not None and "DEVICE_KEY" in df_joined.columns:
                df_joined = (
                    df_joined
                    .withColumn("_il_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                    .join(df_inclast, on=["_il_dk", "transit_day"], how="left")
                    .drop("_il_dk")
                )
                print("Joined last-ticket attributes on DEVICE_KEY + transit_day (forward-filled from prior days)")
            if (_enable_station_busy_features() and "FACILITY_ID" in df_joined.columns
                    and "tap_count" in df_joined.columns):
                _w_busy = Window.partitionBy("FACILITY_ID", "transit_day")
                df_joined = df_joined.withColumn(
                    "stnbusy_taps",
                    F.when(F.col("FACILITY_ID").isNotNull(),
                           F.sum(F.col("tap_count").cast("double")).over(_w_busy)))
                print("Station busyness: total taps across the station's devices (prior windows only)")
        except Exception as exc:
            print(f"WARNING: repair / maintenance / ticket / busyness joins skipped ({exc})")

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

        if df_dopp is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in DOPP_BASE_COLS if c in df_joined.columns
            ]

        if df_sale is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in TVM_SALE_BASE_COLS if c in df_joined.columns
            ]

        if df_tvmev is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in TVM_EVENT_BASE_COLS if c in df_joined.columns
            ]

        if df_cdur is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in CDUR_BASE_COLS if c in df_joined.columns
            ]

        if df_incp is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in INCP_BASE_COLS if c in df_joined.columns
            ]

        if df_snrep is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in SNREP_BASE_COLS if c in df_joined.columns
            ]

        if df_chg is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in CHG_BASE_COLS if c in df_joined.columns
            ]

        _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
            c for c in STATION_DOPP_BASE_COLS if c in df_joined.columns
        ]


        _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
            c for c in MLED_BASE_COLS + STNBUSY_BASE_COLS if c in df_joined.columns
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
        if _enable_recency_trend_features():
            # Days since the family last fired: the most recent day STRICTLY before D with a
            # non-zero count (rangeBetween ends at -1 second). Never-fired and >90 days are 90.
            _w_hist = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                       .rangeBetween(Window.unboundedPreceding, -1))
            _n_rt = 0
            for _rc in RECENCY_BASE_COLS:
                if _rc not in df_joined.columns or f"{_rc}_prior_sum_7d" not in df_joined.columns:
                    continue
                _last = F.max(F.when(F.col(_rc) > 0, F.col("_day_epoch"))).over(_w_hist)
                df_joined = (
                    df_joined
                    .withColumn(f"rec_{_rc}_days",
                                F.coalesce(F.least(F.lit(RECENCY_CAP_DAYS),
                                                   (F.col("_day_epoch") - _last) / 86_400.0),
                                           F.lit(RECENCY_CAP_DAYS)))
                    .withColumn(f"trend_{_rc}_7v30",
                                F.log1p(F.coalesce(F.col(f"{_rc}_prior_sum_7d"), F.lit(0.0)) / 7.0)
                                - F.log1p(F.coalesce(F.col(f"{_rc}_prior_sum_30d"), F.lit(0.0)) / 30.0))
                )
                _n_rt += 1
            print(f"recency + trend materialized for {_n_rt} warning families (strictly prior days)")
        if _enable_long_window_features():
            _w90 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-90 * 86_400, -1)
            _n_lw = 0
            for _lc in DOPP_BASE_COLS:
                if _lc in df_joined.columns:
                    df_joined = df_joined.withColumn(f"long_{_lc}_90d", F.sum(F.col(_lc).cast("double")).over(_w90))
                    _n_lw += 1
            print(f"90-day DOPP windows materialized for {_n_lw} families (strictly prior days)")
        if "incp_opened" in df_joined.columns:
            _w_ihist = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                        .rangeBetween(Window.unboundedPreceding, -1))
            _i_last = F.max(F.when(F.col("incp_opened") > 0, F.col("_day_epoch"))).over(_w_ihist)
            df_joined = df_joined.withColumn(
                "incp_opened_days_since",
                F.coalesce(F.least(F.lit(90.0), (F.col("_day_epoch") - _i_last) / 86_400.0), F.lit(90.0)))
            print("incident recency materialized (days since the last ticket, strictly prior, cap 90)")
        if "snrep_resolved_cnt_prior_sum_30d" in df_joined.columns:
            # From the 30-day prior SUMS; NULL when nothing resolved in the window.
            _sr_n = F.col("snrep_resolved_cnt_prior_sum_30d")
            df_joined = df_joined.withColumn(
                "snrep_mean_resolve_min_30d",
                F.when(_sr_n > 0, F.col("snrep_resolve_min_sum_prior_sum_30d") / _sr_n))
            print("SN repair mean minutes materialized (30-day prior window)")
        if _enable_calendar_features():
            # Calendar facts of day D itself -- known in advance, not windows.
            _dow = F.dayofweek(F.col("transit_day"))
            df_joined = (df_joined
                         .withColumn("cal_dow", _dow.cast("double"))
                         .withColumn("cal_is_weekend", F.when(_dow.isin(1, 7), 1.0).otherwise(0.0)))
            print("calendar features materialized (day of week, weekend)")
        try:
            _w_ff = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                     .rangeBetween(Window.unboundedPreceding, -1))
            if "rep_hold" in df_joined.columns:
                df_joined = (df_joined
                             .withColumn("rep_last_hold_days", F.last("rep_hold", ignorenulls=True).over(_w_ff))
                             .withColumn("rep_prev_hold_days", F.last("rep_hold_prev", ignorenulls=True).over(_w_ff)))
            if _enable_repair_features() and "cdur_max_min" in df_joined.columns:
                _w30r = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-30 * 86_400, -1)
                _cnt = (F.coalesce(F.col("cdur_short_cnt_prior_sum_30d"), F.lit(0.0))
                        + F.coalesce(F.col("cdur_long_cnt_prior_sum_30d"), F.lit(0.0)))
                df_joined = (df_joined
                             .withColumn("cdur_max_prior_30d", F.max(F.col("cdur_max_min")).over(_w30r))
                             .withColumn("cdur_mean_min_prior_30d",
                                         F.when(_cnt > 0, F.col("cdur_minutes_prior_sum_30d") / _cnt)))
            if "_il_pri" in df_joined.columns:
                df_joined = (df_joined
                             .withColumn("inclast_priority", F.last("_il_pri", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_major", F.last("_il_maj", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_chargeable", F.last("_il_chg", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_cat_code", F.last("_il_cat", ignorenulls=True).over(_w_ff)))
            print("repair / last-ticket features materialized (strictly prior days)")
        except Exception as exc:
            print(f"WARNING: repair / last-ticket features skipped ({exc})")
        # Ratios from the prior-window SUMS, so a quiet day does not dilute them; NULL
        # when the window saw no sales (imputed downstream like any other gap).
        if df_sale is not None and "tvm_sale_count_prior_sum_7d" in df_joined.columns:
            for _w in (7, 30):
                _n = F.col(f"tvm_sale_count_prior_sum_{_w}d")
                df_joined = (
                    df_joined
                    .withColumn(f"tvm_sale_err_rate_prior_{_w}d",
                                F.when(_n > 0, F.col(f"tvm_sale_err_count_prior_sum_{_w}d") / _n))
                    .withColumn(f"tvm_sale_cash_share_prior_{_w}d",
                                F.when(_n > 0, F.col(f"tvm_sale_cash_count_prior_sum_{_w}d") / _n))
                )
            df_joined = df_joined.withColumn(
                "tvm_sale_cash_share_trend_7v30d",
                F.col("tvm_sale_cash_share_prior_7d") - F.col("tvm_sale_cash_share_prior_30d"))
        if "TRANSIT_ARRAY_ID" in df_joined.columns and "hardware_oos_events_7d" in df_joined.columns:
            _w_arr = Window.partitionBy("TRANSIT_ARRAY_ID", "transit_day")
            df_joined = df_joined.withColumn(
                "array_peer_hw_oos_7d", F.avg(F.col("hardware_oos_events_7d").cast("double")).over(_w_arr)
            )
        print("Spark prior-window GATE features materialized (lazy)")

        # PS1_FILTER_AFTER_FEATURES: rows CELL 6 marked for exclusion stayed in the frame
        # so every window, peer, forward-fill, recency and ratio above saw full history.
        # Remove them here, after the last of those and before feature selection.
        if "_ps1_drop" in df_joined.columns:
            df_joined = df_joined.where(~F.col("_ps1_drop")).drop("_ps1_drop")

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
                SPINE_ROW_COUNT = df_ps1_scored.select(*KEY_COLS).count()

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

        if df_dopp is not None:
            df_joined = df_joined.join(df_dopp, on=["DEVICE_ID", "transit_day"], how="left")
            for _dc in DOPP_BASE_COLS:
                if _dc in df_joined.columns:
                    df_joined = df_joined.withColumn(_dc, F.coalesce(F.col(_dc), F.lit(0.0)))
            print("Joined DOPP warning rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_sale is not None:
            df_joined = df_joined.join(df_sale, on=["DEVICE_ID", "transit_day"], how="left")
            for _sc in TVM_SALE_BASE_COLS:
                if _sc in df_joined.columns:
                    df_joined = df_joined.withColumn(_sc, F.coalesce(F.col(_sc), F.lit(0.0)))
            print("Joined tvm_sale_daily rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_tvmev is not None:
            df_joined = df_joined.join(df_tvmev, on=["DEVICE_ID", "transit_day"], how="left")
            for _tc in TVM_EVENT_BASE_COLS:
                if _tc in df_joined.columns:
                    df_joined = df_joined.withColumn(_tc, F.coalesce(F.col(_tc), F.lit(0.0)))
            print("Joined TVM event-family rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_cdur is not None:
            df_joined = df_joined.join(df_cdur, on=["DEVICE_ID", "transit_day"], how="left")
            for _cc in CDUR_BASE_COLS:
                if _cc in df_joined.columns:
                    df_joined = df_joined.withColumn(_cc, F.coalesce(F.col(_cc), F.lit(0.0)))
            print("Joined cleared-outage rollup on DEVICE_ID + clear date (prior windows only)")

        if df_incp is not None and "_incp_dev" in df_incp.columns:
            # VALIDATOR tickets from incident_task_ci_link: keyed by DEVICE_ID, not DEVICE_KEY.
            df_joined = (
                df_joined
                .withColumn("_incp_dev", _norm_device_id(F.col("DEVICE_ID")))
                .join(df_incp, on=["_incp_dev", "transit_day"], how="left")
                .drop("_incp_dev")
            )
            for _ic in INCP_BASE_COLS:
                if _ic in df_joined.columns:
                    df_joined = df_joined.withColumn(_ic, F.coalesce(F.col(_ic), F.lit(0.0)))
            print("Joined VALIDATOR tickets (incident_task_ci_link) on DEVICE_ID + transit_day "
                  "(prior windows only)")
        elif df_incp is not None and "DEVICE_KEY" in df_joined.columns:
            df_joined = (
                df_joined
                .withColumn("_incp_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                .join(df_incp, on=["_incp_dk", "transit_day"], how="left")
                .drop("_incp_dk")
            )
            for _ic in INCP_BASE_COLS:
                if _ic in df_joined.columns:
                    df_joined = df_joined.withColumn(_ic, F.coalesce(F.col(_ic), F.lit(0.0)))
            print("Joined incident history on DEVICE_KEY + transit_day (prior windows only)")

        # SN repair time: keyed by RESOLUTION date, so prior windows see only resolved tickets.
        if df_snrep is not None:
            if "_snrep_dev" in df_snrep.columns:
                df_joined = (
                    df_joined
                    .withColumn("_snrep_dev", _norm_device_id(F.col("DEVICE_ID")))
                    .join(df_snrep, on=["_snrep_dev", "transit_day"], how="left")
                    .drop("_snrep_dev")
                )
                print("Joined SN repair time on DEVICE_ID + resolution date (prior windows only)")
            elif "DEVICE_KEY" in df_joined.columns:
                df_joined = (
                    df_joined
                    .withColumn("_snrep_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                    .join(df_snrep, on=["_snrep_dk", "transit_day"], how="left")
                    .drop("_snrep_dk")
                )
                print("Joined SN repair time on DEVICE_KEY + resolution date (prior windows only)")
            else:
                print("WARNING: DEVICE_KEY missing on spine -- skipped SN repair-time join")
            for _rc in SNREP_BASE_COLS:
                if _rc in df_joined.columns:
                    df_joined = df_joined.withColumn(_rc, F.coalesce(F.col(_rc), F.lit(0.0)))

        # Chargability tickets: keyed by CLOSE date on DEVICE_ID, one row per device-day.
        if df_chg is not None:
            df_joined = (
                df_joined
                .withColumn("_chg_dev", _norm_device_id(F.col("DEVICE_ID")))
                .join(df_chg, on=["_chg_dev", "transit_day"], how="left")
                .drop("_chg_dev")
            )
            for _gc in CHG_BASE_COLS:
                if _gc in df_joined.columns:
                    df_joined = df_joined.withColumn(_gc, F.coalesce(F.col(_gc), F.lit(0.0)))
            print("Joined chargability tickets on DEVICE_ID + close date (prior windows only)")

        if (_enable_station_dopp_features() and df_dopp is not None
                and "FACILITY_ID" in df_joined.columns):
            # Mean over the OTHER devices at the station that day; NULL facility gets NULL.
            _w_fac_day = Window.partitionBy("FACILITY_ID", "transit_day")
            _n_fac = F.count(F.lit(1)).over(_w_fac_day)
            for _sc in STATION_DOPP_SOURCE_COLS:
                if _sc in df_joined.columns:
                    df_joined = df_joined.withColumn(
                        f"stn_{_sc}",
                        F.when(F.col("FACILITY_ID").isNotNull() & (_n_fac > 1),
                               (F.sum(F.col(_sc)).over(_w_fac_day) - F.col(_sc)) / (_n_fac - 1)))
            print("Station DOPP health: peer means over the other devices at each station")

        if df_evall is not None:
            # A spine day with no row in the rollup logged no event at all: that is the signal.
            df_joined = (
                df_joined.join(df_evall, on=["DEVICE_ID", "transit_day"], how="left")
            )
            print("Joined silent-day / event-volume prior windows (built on the unfiltered event calendar)")

        try:
            if df_rep is not None:
                df_joined = df_joined.join(df_rep, on=["DEVICE_ID", "transit_day"], how="left")
                print("Joined repair-durability episode starts (forward-filled from prior days)")
            if df_mled is not None:
                df_joined = df_joined.join(df_mled, on=["DEVICE_ID", "transit_day"], how="left")
                for _mc in MLED_BASE_COLS:
                    df_joined = df_joined.withColumn(_mc, F.coalesce(F.col(_mc), F.lit(0.0)))
                print("Joined maintenance ledger on DEVICE_ID + transit_day (prior windows only)")
            if df_inclast is not None and "DEVICE_KEY" in df_joined.columns:
                df_joined = (
                    df_joined
                    .withColumn("_il_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                    .join(df_inclast, on=["_il_dk", "transit_day"], how="left")
                    .drop("_il_dk")
                )
                print("Joined last-ticket attributes on DEVICE_KEY + transit_day (forward-filled from prior days)")
            if (_enable_station_busy_features() and "FACILITY_ID" in df_joined.columns
                    and "tap_count" in df_joined.columns):
                _w_busy = Window.partitionBy("FACILITY_ID", "transit_day")
                df_joined = df_joined.withColumn(
                    "stnbusy_taps",
                    F.when(F.col("FACILITY_ID").isNotNull(),
                           F.sum(F.col("tap_count").cast("double")).over(_w_busy)))
                print("Station busyness: total taps across the station's devices (prior windows only)")
        except Exception as exc:
            print(f"WARNING: repair / maintenance / ticket / busyness joins skipped ({exc})")

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

        if df_dopp is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in DOPP_BASE_COLS if c in df_joined.columns
            ]

        if df_sale is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in TVM_SALE_BASE_COLS if c in df_joined.columns
            ]

        if df_tvmev is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in TVM_EVENT_BASE_COLS if c in df_joined.columns
            ]

        if df_cdur is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in CDUR_BASE_COLS if c in df_joined.columns
            ]

        if df_incp is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in INCP_BASE_COLS if c in df_joined.columns
            ]

        if df_snrep is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in SNREP_BASE_COLS if c in df_joined.columns
            ]

        if df_chg is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in CHG_BASE_COLS if c in df_joined.columns
            ]

        _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
            c for c in STATION_DOPP_BASE_COLS if c in df_joined.columns
        ]


        _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
            c for c in MLED_BASE_COLS + STNBUSY_BASE_COLS if c in df_joined.columns
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
        if _enable_recency_trend_features():
            # Days since the family last fired: the most recent day STRICTLY before D with a
            # non-zero count (rangeBetween ends at -1 second). Never-fired and >90 days are 90.
            _w_hist = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                       .rangeBetween(Window.unboundedPreceding, -1))
            _n_rt = 0
            for _rc in RECENCY_BASE_COLS:
                if _rc not in df_joined.columns or f"{_rc}_prior_sum_7d" not in df_joined.columns:
                    continue
                _last = F.max(F.when(F.col(_rc) > 0, F.col("_day_epoch"))).over(_w_hist)
                df_joined = (
                    df_joined
                    .withColumn(f"rec_{_rc}_days",
                                F.coalesce(F.least(F.lit(RECENCY_CAP_DAYS),
                                                   (F.col("_day_epoch") - _last) / 86_400.0),
                                           F.lit(RECENCY_CAP_DAYS)))
                    .withColumn(f"trend_{_rc}_7v30",
                                F.log1p(F.coalesce(F.col(f"{_rc}_prior_sum_7d"), F.lit(0.0)) / 7.0)
                                - F.log1p(F.coalesce(F.col(f"{_rc}_prior_sum_30d"), F.lit(0.0)) / 30.0))
                )
                _n_rt += 1
            print(f"recency + trend materialized for {_n_rt} warning families (strictly prior days)")
        if _enable_long_window_features():
            _w90 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-90 * 86_400, -1)
            _n_lw = 0
            for _lc in DOPP_BASE_COLS:
                if _lc in df_joined.columns:
                    df_joined = df_joined.withColumn(f"long_{_lc}_90d", F.sum(F.col(_lc).cast("double")).over(_w90))
                    _n_lw += 1
            print(f"90-day DOPP windows materialized for {_n_lw} families (strictly prior days)")
        if "incp_opened" in df_joined.columns:
            _w_ihist = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                        .rangeBetween(Window.unboundedPreceding, -1))
            _i_last = F.max(F.when(F.col("incp_opened") > 0, F.col("_day_epoch"))).over(_w_ihist)
            df_joined = df_joined.withColumn(
                "incp_opened_days_since",
                F.coalesce(F.least(F.lit(90.0), (F.col("_day_epoch") - _i_last) / 86_400.0), F.lit(90.0)))
            print("incident recency materialized (days since the last ticket, strictly prior, cap 90)")
        if "snrep_resolved_cnt_prior_sum_30d" in df_joined.columns:
            # From the 30-day prior SUMS; NULL when nothing resolved in the window.
            _sr_n = F.col("snrep_resolved_cnt_prior_sum_30d")
            df_joined = df_joined.withColumn(
                "snrep_mean_resolve_min_30d",
                F.when(_sr_n > 0, F.col("snrep_resolve_min_sum_prior_sum_30d") / _sr_n))
            print("SN repair mean minutes materialized (30-day prior window)")
        if _enable_calendar_features():
            # Calendar facts of day D itself -- known in advance, not windows.
            _dow = F.dayofweek(F.col("transit_day"))
            df_joined = (df_joined
                         .withColumn("cal_dow", _dow.cast("double"))
                         .withColumn("cal_is_weekend", F.when(_dow.isin(1, 7), 1.0).otherwise(0.0)))
            print("calendar features materialized (day of week, weekend)")
        try:
            _w_ff = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                     .rangeBetween(Window.unboundedPreceding, -1))
            if "rep_hold" in df_joined.columns:
                df_joined = (df_joined
                             .withColumn("rep_last_hold_days", F.last("rep_hold", ignorenulls=True).over(_w_ff))
                             .withColumn("rep_prev_hold_days", F.last("rep_hold_prev", ignorenulls=True).over(_w_ff)))
            if _enable_repair_features() and "cdur_max_min" in df_joined.columns:
                _w30r = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-30 * 86_400, -1)
                _cnt = (F.coalesce(F.col("cdur_short_cnt_prior_sum_30d"), F.lit(0.0))
                        + F.coalesce(F.col("cdur_long_cnt_prior_sum_30d"), F.lit(0.0)))
                df_joined = (df_joined
                             .withColumn("cdur_max_prior_30d", F.max(F.col("cdur_max_min")).over(_w30r))
                             .withColumn("cdur_mean_min_prior_30d",
                                         F.when(_cnt > 0, F.col("cdur_minutes_prior_sum_30d") / _cnt)))
            if "_il_pri" in df_joined.columns:
                df_joined = (df_joined
                             .withColumn("inclast_priority", F.last("_il_pri", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_major", F.last("_il_maj", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_chargeable", F.last("_il_chg", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_cat_code", F.last("_il_cat", ignorenulls=True).over(_w_ff)))
            print("repair / last-ticket features materialized (strictly prior days)")
        except Exception as exc:
            print(f"WARNING: repair / last-ticket features skipped ({exc})")
        # Ratios from the prior-window SUMS, so a quiet day does not dilute them; NULL
        # when the window saw no sales (imputed downstream like any other gap).
        if df_sale is not None and "tvm_sale_count_prior_sum_7d" in df_joined.columns:
            for _w in (7, 30):
                _n = F.col(f"tvm_sale_count_prior_sum_{_w}d")
                df_joined = (
                    df_joined
                    .withColumn(f"tvm_sale_err_rate_prior_{_w}d",
                                F.when(_n > 0, F.col(f"tvm_sale_err_count_prior_sum_{_w}d") / _n))
                    .withColumn(f"tvm_sale_cash_share_prior_{_w}d",
                                F.when(_n > 0, F.col(f"tvm_sale_cash_count_prior_sum_{_w}d") / _n))
                )
            df_joined = df_joined.withColumn(
                "tvm_sale_cash_share_trend_7v30d",
                F.col("tvm_sale_cash_share_prior_7d") - F.col("tvm_sale_cash_share_prior_30d"))
        if "FACILITY_ID" in df_joined.columns and "hardware_oos_events_7d" in df_joined.columns:
            _w_fac = Window.partitionBy("FACILITY_ID", "transit_day")
            df_joined = df_joined.withColumn(
                "facility_peer_hw_oos_7d", F.avg(F.col("hardware_oos_events_7d").cast("double")).over(_w_fac)
            )
        print("Spark prior-window TVM features materialized (lazy)")

        # PS1_FILTER_AFTER_FEATURES: rows CELL 6 marked for exclusion stayed in the frame
        # so every window, peer, forward-fill, recency and ratio above saw full history.
        # Remove them here, after the last of those and before feature selection.
        if "_ps1_drop" in df_joined.columns:
            df_joined = df_joined.where(~F.col("_ps1_drop")).drop("_ps1_drop")

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
                SPINE_ROW_COUNT = df_ps1_scored.select(*KEY_COLS).count()

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

        if df_dopp is not None:
            df_joined = df_joined.join(df_dopp, on=["DEVICE_ID", "transit_day"], how="left")
            for _dc in DOPP_BASE_COLS:
                if _dc in df_joined.columns:
                    df_joined = df_joined.withColumn(_dc, F.coalesce(F.col(_dc), F.lit(0.0)))
            print("Joined DOPP warning rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_sale is not None:
            df_joined = df_joined.join(df_sale, on=["DEVICE_ID", "transit_day"], how="left")
            for _sc in TVM_SALE_BASE_COLS:
                if _sc in df_joined.columns:
                    df_joined = df_joined.withColumn(_sc, F.coalesce(F.col(_sc), F.lit(0.0)))
            print("Joined tvm_sale_daily rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_tvmev is not None:
            df_joined = df_joined.join(df_tvmev, on=["DEVICE_ID", "transit_day"], how="left")
            for _tc in TVM_EVENT_BASE_COLS:
                if _tc in df_joined.columns:
                    df_joined = df_joined.withColumn(_tc, F.coalesce(F.col(_tc), F.lit(0.0)))
            print("Joined TVM event-family rollup on DEVICE_ID + transit_day (prior windows only)")

        if df_cdur is not None:
            df_joined = df_joined.join(df_cdur, on=["DEVICE_ID", "transit_day"], how="left")
            for _cc in CDUR_BASE_COLS:
                if _cc in df_joined.columns:
                    df_joined = df_joined.withColumn(_cc, F.coalesce(F.col(_cc), F.lit(0.0)))
            print("Joined cleared-outage rollup on DEVICE_ID + clear date (prior windows only)")

        if df_incp is not None and "_incp_dev" in df_incp.columns:
            # VALIDATOR tickets from incident_task_ci_link: keyed by DEVICE_ID, not DEVICE_KEY.
            df_joined = (
                df_joined
                .withColumn("_incp_dev", _norm_device_id(F.col("DEVICE_ID")))
                .join(df_incp, on=["_incp_dev", "transit_day"], how="left")
                .drop("_incp_dev")
            )
            for _ic in INCP_BASE_COLS:
                if _ic in df_joined.columns:
                    df_joined = df_joined.withColumn(_ic, F.coalesce(F.col(_ic), F.lit(0.0)))
            print("Joined VALIDATOR tickets (incident_task_ci_link) on DEVICE_ID + transit_day "
                  "(prior windows only)")
        elif df_incp is not None and "DEVICE_KEY" in df_joined.columns:
            df_joined = (
                df_joined
                .withColumn("_incp_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                .join(df_incp, on=["_incp_dk", "transit_day"], how="left")
                .drop("_incp_dk")
            )
            for _ic in INCP_BASE_COLS:
                if _ic in df_joined.columns:
                    df_joined = df_joined.withColumn(_ic, F.coalesce(F.col(_ic), F.lit(0.0)))
            print("Joined incident history on DEVICE_KEY + transit_day (prior windows only)")

        # SN repair time: keyed by RESOLUTION date, so prior windows see only resolved tickets.
        if df_snrep is not None:
            if "_snrep_dev" in df_snrep.columns:
                df_joined = (
                    df_joined
                    .withColumn("_snrep_dev", _norm_device_id(F.col("DEVICE_ID")))
                    .join(df_snrep, on=["_snrep_dev", "transit_day"], how="left")
                    .drop("_snrep_dev")
                )
                print("Joined SN repair time on DEVICE_ID + resolution date (prior windows only)")
            elif "DEVICE_KEY" in df_joined.columns:
                df_joined = (
                    df_joined
                    .withColumn("_snrep_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                    .join(df_snrep, on=["_snrep_dk", "transit_day"], how="left")
                    .drop("_snrep_dk")
                )
                print("Joined SN repair time on DEVICE_KEY + resolution date (prior windows only)")
            else:
                print("WARNING: DEVICE_KEY missing on spine -- skipped SN repair-time join")
            for _rc in SNREP_BASE_COLS:
                if _rc in df_joined.columns:
                    df_joined = df_joined.withColumn(_rc, F.coalesce(F.col(_rc), F.lit(0.0)))

        # Chargability tickets: keyed by CLOSE date on DEVICE_ID, one row per device-day.
        if df_chg is not None:
            df_joined = (
                df_joined
                .withColumn("_chg_dev", _norm_device_id(F.col("DEVICE_ID")))
                .join(df_chg, on=["_chg_dev", "transit_day"], how="left")
                .drop("_chg_dev")
            )
            for _gc in CHG_BASE_COLS:
                if _gc in df_joined.columns:
                    df_joined = df_joined.withColumn(_gc, F.coalesce(F.col(_gc), F.lit(0.0)))
            print("Joined chargability tickets on DEVICE_ID + close date (prior windows only)")

        if (_enable_station_dopp_features() and df_dopp is not None
                and "FACILITY_ID" in df_joined.columns):
            # Mean over the OTHER devices at the station that day; NULL facility gets NULL.
            _w_fac_day = Window.partitionBy("FACILITY_ID", "transit_day")
            _n_fac = F.count(F.lit(1)).over(_w_fac_day)
            for _sc in STATION_DOPP_SOURCE_COLS:
                if _sc in df_joined.columns:
                    df_joined = df_joined.withColumn(
                        f"stn_{_sc}",
                        F.when(F.col("FACILITY_ID").isNotNull() & (_n_fac > 1),
                               (F.sum(F.col(_sc)).over(_w_fac_day) - F.col(_sc)) / (_n_fac - 1)))
            print("Station DOPP health: peer means over the other devices at each station")

        if df_evall is not None:
            # A spine day with no row in the rollup logged no event at all: that is the signal.
            df_joined = (
                df_joined.join(df_evall, on=["DEVICE_ID", "transit_day"], how="left")
            )
            print("Joined silent-day / event-volume prior windows (built on the unfiltered event calendar)")

        try:
            if df_rep is not None:
                df_joined = df_joined.join(df_rep, on=["DEVICE_ID", "transit_day"], how="left")
                print("Joined repair-durability episode starts (forward-filled from prior days)")
            if df_mled is not None:
                df_joined = df_joined.join(df_mled, on=["DEVICE_ID", "transit_day"], how="left")
                for _mc in MLED_BASE_COLS:
                    df_joined = df_joined.withColumn(_mc, F.coalesce(F.col(_mc), F.lit(0.0)))
                print("Joined maintenance ledger on DEVICE_ID + transit_day (prior windows only)")
            if df_inclast is not None and "DEVICE_KEY" in df_joined.columns:
                df_joined = (
                    df_joined
                    .withColumn("_il_dk", F.regexp_replace(F.col("DEVICE_KEY").cast("string"), r"\.0+$", ""))
                    .join(df_inclast, on=["_il_dk", "transit_day"], how="left")
                    .drop("_il_dk")
                )
                print("Joined last-ticket attributes on DEVICE_KEY + transit_day (forward-filled from prior days)")
            if (_enable_station_busy_features() and "FACILITY_ID" in df_joined.columns
                    and "tap_count" in df_joined.columns):
                _w_busy = Window.partitionBy("FACILITY_ID", "transit_day")
                df_joined = df_joined.withColumn(
                    "stnbusy_taps",
                    F.when(F.col("FACILITY_ID").isNotNull(),
                           F.sum(F.col("tap_count").cast("double")).over(_w_busy)))
                print("Station busyness: total taps across the station's devices (prior windows only)")
        except Exception as exc:
            print(f"WARNING: repair / maintenance / ticket / busyness joins skipped ({exc})")

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

        if df_dopp is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in DOPP_BASE_COLS if c in df_joined.columns
            ]

        if df_sale is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in TVM_SALE_BASE_COLS if c in df_joined.columns
            ]

        if df_tvmev is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in TVM_EVENT_BASE_COLS if c in df_joined.columns
            ]

        if df_cdur is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in CDUR_BASE_COLS if c in df_joined.columns
            ]

        if df_incp is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in INCP_BASE_COLS if c in df_joined.columns
            ]

        if df_snrep is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in SNREP_BASE_COLS if c in df_joined.columns
            ]

        if df_chg is not None:
            _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
                c for c in CHG_BASE_COLS if c in df_joined.columns
            ]

        _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
            c for c in STATION_DOPP_BASE_COLS if c in df_joined.columns
        ]


        _SPARK_PRIOR_COLS = _SPARK_PRIOR_COLS + [
            c for c in MLED_BASE_COLS + STNBUSY_BASE_COLS if c in df_joined.columns
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
        if _enable_recency_trend_features():
            # Days since the family last fired: the most recent day STRICTLY before D with a
            # non-zero count (rangeBetween ends at -1 second). Never-fired and >90 days are 90.
            _w_hist = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                       .rangeBetween(Window.unboundedPreceding, -1))
            _n_rt = 0
            for _rc in RECENCY_BASE_COLS:
                if _rc not in df_joined.columns or f"{_rc}_prior_sum_7d" not in df_joined.columns:
                    continue
                _last = F.max(F.when(F.col(_rc) > 0, F.col("_day_epoch"))).over(_w_hist)
                df_joined = (
                    df_joined
                    .withColumn(f"rec_{_rc}_days",
                                F.coalesce(F.least(F.lit(RECENCY_CAP_DAYS),
                                                   (F.col("_day_epoch") - _last) / 86_400.0),
                                           F.lit(RECENCY_CAP_DAYS)))
                    .withColumn(f"trend_{_rc}_7v30",
                                F.log1p(F.coalesce(F.col(f"{_rc}_prior_sum_7d"), F.lit(0.0)) / 7.0)
                                - F.log1p(F.coalesce(F.col(f"{_rc}_prior_sum_30d"), F.lit(0.0)) / 30.0))
                )
                _n_rt += 1
            print(f"recency + trend materialized for {_n_rt} warning families (strictly prior days)")
        if _enable_long_window_features():
            _w90 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-90 * 86_400, -1)
            _n_lw = 0
            for _lc in DOPP_BASE_COLS:
                if _lc in df_joined.columns:
                    df_joined = df_joined.withColumn(f"long_{_lc}_90d", F.sum(F.col(_lc).cast("double")).over(_w90))
                    _n_lw += 1
            print(f"90-day DOPP windows materialized for {_n_lw} families (strictly prior days)")
        if "incp_opened" in df_joined.columns:
            _w_ihist = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                        .rangeBetween(Window.unboundedPreceding, -1))
            _i_last = F.max(F.when(F.col("incp_opened") > 0, F.col("_day_epoch"))).over(_w_ihist)
            df_joined = df_joined.withColumn(
                "incp_opened_days_since",
                F.coalesce(F.least(F.lit(90.0), (F.col("_day_epoch") - _i_last) / 86_400.0), F.lit(90.0)))
            print("incident recency materialized (days since the last ticket, strictly prior, cap 90)")
        if "snrep_resolved_cnt_prior_sum_30d" in df_joined.columns:
            # From the 30-day prior SUMS; NULL when nothing resolved in the window.
            _sr_n = F.col("snrep_resolved_cnt_prior_sum_30d")
            df_joined = df_joined.withColumn(
                "snrep_mean_resolve_min_30d",
                F.when(_sr_n > 0, F.col("snrep_resolve_min_sum_prior_sum_30d") / _sr_n))
            print("SN repair mean minutes materialized (30-day prior window)")
        if _enable_calendar_features():
            # Calendar facts of day D itself -- known in advance, not windows.
            _dow = F.dayofweek(F.col("transit_day"))
            df_joined = (df_joined
                         .withColumn("cal_dow", _dow.cast("double"))
                         .withColumn("cal_is_weekend", F.when(_dow.isin(1, 7), 1.0).otherwise(0.0)))
            print("calendar features materialized (day of week, weekend)")
        try:
            _w_ff = (Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch"))
                     .rangeBetween(Window.unboundedPreceding, -1))
            if "rep_hold" in df_joined.columns:
                df_joined = (df_joined
                             .withColumn("rep_last_hold_days", F.last("rep_hold", ignorenulls=True).over(_w_ff))
                             .withColumn("rep_prev_hold_days", F.last("rep_hold_prev", ignorenulls=True).over(_w_ff)))
            if _enable_repair_features() and "cdur_max_min" in df_joined.columns:
                _w30r = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-30 * 86_400, -1)
                _cnt = (F.coalesce(F.col("cdur_short_cnt_prior_sum_30d"), F.lit(0.0))
                        + F.coalesce(F.col("cdur_long_cnt_prior_sum_30d"), F.lit(0.0)))
                df_joined = (df_joined
                             .withColumn("cdur_max_prior_30d", F.max(F.col("cdur_max_min")).over(_w30r))
                             .withColumn("cdur_mean_min_prior_30d",
                                         F.when(_cnt > 0, F.col("cdur_minutes_prior_sum_30d") / _cnt)))
            if "_il_pri" in df_joined.columns:
                df_joined = (df_joined
                             .withColumn("inclast_priority", F.last("_il_pri", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_major", F.last("_il_maj", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_chargeable", F.last("_il_chg", ignorenulls=True).over(_w_ff))
                             .withColumn("inclast_cat_code", F.last("_il_cat", ignorenulls=True).over(_w_ff)))
            print("repair / last-ticket features materialized (strictly prior days)")
        except Exception as exc:
            print(f"WARNING: repair / last-ticket features skipped ({exc})")
        # Ratios from the prior-window SUMS, so a quiet day does not dilute them; NULL
        # when the window saw no sales (imputed downstream like any other gap).
        if df_sale is not None and "tvm_sale_count_prior_sum_7d" in df_joined.columns:
            for _w in (7, 30):
                _n = F.col(f"tvm_sale_count_prior_sum_{_w}d")
                df_joined = (
                    df_joined
                    .withColumn(f"tvm_sale_err_rate_prior_{_w}d",
                                F.when(_n > 0, F.col(f"tvm_sale_err_count_prior_sum_{_w}d") / _n))
                    .withColumn(f"tvm_sale_cash_share_prior_{_w}d",
                                F.when(_n > 0, F.col(f"tvm_sale_cash_count_prior_sum_{_w}d") / _n))
                )
            df_joined = df_joined.withColumn(
                "tvm_sale_cash_share_trend_7v30d",
                F.col("tvm_sale_cash_share_prior_7d") - F.col("tvm_sale_cash_share_prior_30d"))
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

        # PS1_FILTER_AFTER_FEATURES: rows CELL 6 marked for exclusion stayed in the frame
        # so every window, peer, forward-fill, recency, ratio and station-cascade value
        # above saw full history. Remove them here, before feature selection.
        if "_ps1_drop" in df_joined.columns:
            df_joined = df_joined.where(~F.col("_ps1_drop")).drop("_ps1_drop")

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
                SPINE_ROW_COUNT = df_ps1_scored.select(*KEY_COLS).count()

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

