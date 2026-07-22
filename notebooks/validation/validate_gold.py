# Databricks notebook source
# MAGIC %md
# MAGIC # validate_gold — runtime DQ + ML-readiness checks for the 5 gold PS tables (`mars_dev.gold`)
# MAGIC Operationalizes gap fixes + expects clean S17 grain after silver audit rebuild (2026-07-22).
# MAGIC
# MAGIC Per table: existence, row count, grain uniqueness, future dates, key/label null rates.
# MAGIC Plus PS-specific checks: PS1 label positive-rate, PS3 target class balance + new column presence
# MAGIC + failure_level=1 absence (merged into 2), PS4 ensemble anomaly rate + adaptive Signal 3 sanity,
# MAGIC PS5 censoring rate + COMPONENT_TYPE_NAME coverage improvement.
# MAGIC
# MAGIC Writes a scorecard to `mars_dev.audit.gold_validation_results`. Checks degrade to WARN if a column is absent.

# COMMAND ----------
from typing import Any
from datetime import datetime

# Databricks runtime globals — injected into the notebook namespace before execution.
spark: Any   = globals().get("spark")
display: Any = globals().get("display")

spark.sql("USE CATALOG mars_dev")
CAT, SCH = "mars_dev", "gold"
RUN_TS = datetime.utcnow().isoformat()
RESULTS = []

def log(table, check, status, detail=""):
    RESULTS.append((RUN_TS, f"{SCH}.{table}", check, status, str(detail)))
    print(f"[{status:4}] {table:24} {check:32} {detail}")

def exists(t):
    return spark.sql(f"SELECT count(*) n FROM {CAT}.information_schema.tables "
                     f"WHERE table_schema='{SCH}' AND table_name='{t}'").first()["n"] > 0

def safe(table, check, fn):
    try:
        fn()
    except Exception as e:
        log(table, check, "WARN", f"skipped: {str(e).splitlines()[0][:90]}")

def check_table(t, grain=None, date_cols=None, min_rows=1, notnull=None):
    if not exists(t):
        log(t, "exists", "FAIL", "table not found"); return
    fq = f"{CAT}.{SCH}.{t}"
    n = spark.table(fq).count()
    log(t, "row_count", "PASS" if n >= min_rows else "FAIL", f"{n:,} rows")
    if grain:
        def g():
            # GROUP BY correctly handles NULLs; count(*) - count(DISTINCT ...) excludes any row
            # with a NULL grain column from the distinct count, producing false positives.
            d = spark.sql(
                f"SELECT COALESCE(SUM(cnt - 1), 0) d FROM "
                f"(SELECT count(*) cnt FROM {fq} GROUP BY {', '.join(grain)} HAVING count(*) > 1)"
            ).first()["d"]
            log(t, "grain_unique", "PASS" if d == 0 else "FAIL", f"{d:,} dup rows on ({', '.join(grain)})")
        safe(t, "grain_unique", g)
    for dc in (date_cols or []):
        def fut(dc=dc):
            c = spark.sql(f"SELECT count(*) c FROM {fq} WHERE try_cast({dc} AS DATE) > current_date()").first()["c"]
            log(t, f"no_future:{dc}", "PASS" if c == 0 else "WARN", f"{c:,} rows with {dc} > today")
        safe(t, f"no_future:{dc}", fut)
    for col in (notnull or []):
        def nn(col=col):
            r = spark.sql(f"SELECT round(100.0*sum(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END)/greatest(count(*),1),2) pct FROM {fq}").first()["pct"]
            log(t, f"notnull:{col}", "PASS" if (r or 0) == 0 else "WARN", f"{r}% null")
        safe(t, f"notnull:{col}", nn)

# COMMAND ----------
# Per-PS config
# PS3 notnull: was "AE_FAILURE_LEVEL" — fixed to "failure_level" (column renamed in output, Gap 4 fix 2026-07-15)
GOLD = {
    "device_ps1_daily":    dict(grain=["DEVICE_ID", "transit_day"], date_cols=["transit_day"],
                                notnull=["DEVICE_ID", "will_fail_3d"]),
    "device_ps2_chains":   dict(grain=["DEVICE_ID", "transit_day"], date_cols=["transit_day"],
                                notnull=["DEVICE_ID"]),
    "device_ps3_incident": dict(grain=["availability_event_id"],
                                notnull=["DEVICE_ID", "failure_level"]),         # Gap 4 fix: AE_FAILURE_LEVEL -> failure_level
    # PS4 grain is (DEVICE_ID, DEVICE_KEY, hour_bucket, transit_day).
    # hourly_events groups by (DEVICE_ID, DEVICE_KEY, hour_bucket, transit_day): midnight clock-hours
    # can span two transit service days (S16 transit_day is service-day-adjusted, not calendar-date),
    # producing 2 rows per (DEVICE_ID, DEVICE_KEY, hour_bucket) with different transit_days.
    # Each row has its own feature context (rrb/dr/inc24 join on transit_day), so keeping them
    # separate is correct — transit_day is a genuine grain dimension here.
    "device_ps4_hourly":   dict(grain=["DEVICE_ID", "DEVICE_KEY", "hour_bucket", "transit_day"],
                                date_cols=["transit_day"],
                                notnull=["DEVICE_ID", "ensemble_anomaly_flag"]),
    "device_ps5_component":dict(grain=["DEVICE_ID", "COMPONENT_SERIAL_NBR"],
                                notnull=["DEVICE_ID"]),
}
for t, cfg in GOLD.items():
    check_table(t, **cfg)

# COMMAND ----------
# PS-specific ML-readiness checks

# PS1: label positive rate per category.
# TVM/GATE: is_chargeable SLA failure within 3d (expect 0.6-15%).
# VALIDATOR: hardware OOS Set event within 3d from device_failures (R6-1 2026-07-17, expect 2-10%).
# Overall rate check retained for quick summary; per-category check catches VALIDATOR=0 regression.
def ps1_label():
    r = spark.sql("SELECT round(100.0*avg(will_fail_3d),2) pct FROM mars_dev.gold.device_ps1_daily").first()["pct"]
    log("device_ps1_daily", "label_positive_rate", "WARN" if (r or 0) > 15 else "PASS",
        f"{r}% positive overall (TVM/GATE chargeable 3d; VALIDATOR OOS Set 3d from device_failures)")
safe("device_ps1_daily", "label_positive_rate", ps1_label)

def ps1_label_by_cat():
    rows = spark.sql(
        "SELECT mars_device_category cat, round(100.0*avg(will_fail_3d),3) pct, count(*) n "
        "FROM mars_dev.gold.device_ps1_daily GROUP BY 1 ORDER BY 1"
    ).collect()
    for row in rows:
        cat, pct, n = row["cat"], row["pct"] or 0, row["n"]
        if cat == "VALIDATOR":
            # OOS-event label (R6-1): 0% means device_failures is empty or gold SQL not re-run
            status = "PASS" if 0 < pct < 15 else "WARN"
            hint = "OOS Set label from device_failures; 0%=S26 empty or gold not re-run; >15%=too many OOS events"
        else:
            status = "WARN" if pct > 15 else "PASS"
            hint = "chargeable SLA; expect TVM 10-15%, GATE 0.5-3%"
        log("device_ps1_daily", f"label_positive_rate:{cat}", status,
            f"{pct}% positive ({n:,} rows) — {hint}")
safe("device_ps1_daily", "label_positive_rate_by_cat", ps1_label_by_cat)

# PS1: R6-1 fix (2026-07-17) — rolling failure feature columns from device_failures (S26)
# 5 new columns: roll_fail_7d, roll_fail_30d, roll_fail_90d, days_since_fail, fail_free_streak
def ps1_failure_feature_cols():
    cols_expected = ["roll_fail_7d", "roll_fail_30d", "roll_fail_90d", "days_since_fail", "fail_free_streak"]
    actual = [c.lower() for c in spark.table("mars_dev.gold.device_ps1_daily").columns]
    missing = [c for c in cols_expected if c not in actual]
    log("device_ps1_daily", "failure_feature_cols_present",
        "PASS" if not missing else "FAIL",
        f"missing={missing} (should be empty — all 5 rolling failure cols expected)")
safe("device_ps1_daily", "failure_feature_cols_present", ps1_failure_feature_cols)

def ps1_failure_feature_sanity():
    r = spark.sql(
        "SELECT "
        "sum(CASE WHEN roll_fail_7d  > roll_fail_30d  THEN 1 ELSE 0 END) w7_gt_w30, "
        "sum(CASE WHEN roll_fail_30d > roll_fail_90d  THEN 1 ELSE 0 END) w30_gt_w90, "
        "sum(CASE WHEN days_since_fail < 0             THEN 1 ELSE 0 END) neg_days, "
        "sum(CASE WHEN days_since_fail = 999           THEN 1 ELSE 0 END) never_failed "
        "FROM mars_dev.gold.device_ps1_daily"
    ).first()
    violations = (r["w7_gt_w30"] or 0) + (r["w30_gt_w90"] or 0) + (r["neg_days"] or 0)
    log("device_ps1_daily", "failure_feature_window_sanity",
        "PASS" if violations == 0 else "FAIL",
        f"w7>w30={r['w7_gt_w30']} w30>w90={r['w30_gt_w90']} neg_days={r['neg_days']} "
        f"never_failed={r['never_failed']:,} (999=device never failed in window, expected for GATE/some TVMs)")
safe("device_ps1_daily", "failure_feature_window_sanity", ps1_failure_feature_sanity)

# PS3: target class balance — flag classes with too few rows to learn
def ps3_balance():
    rows = spark.sql("SELECT failure_level lvl, count(*) n FROM mars_dev.gold.device_ps3_incident "
                     "GROUP BY failure_level ORDER BY n").collect()
    rare = {str(x["lvl"]): x["n"] for x in rows if x["n"] < 30}
    log("device_ps3_incident", "target_class_balance", "WARN" if rare else "PASS",
        f"{len(rare)} class(es) with <30 rows: {rare}")
safe("device_ps3_incident", "target_class_balance", ps3_balance)

# PS3: Gap 4 fix — failure_level=1 must not exist (6 rows merged into level 2)
def ps3_no_level1():
    c = spark.sql("SELECT count(*) c FROM mars_dev.gold.device_ps3_incident WHERE failure_level = 1").first()["c"]
    log("device_ps3_incident", "no_failure_level_1", "PASS" if c == 0 else "FAIL",
        f"{c} rows with failure_level=1 (should be 0 — all merged into level 2)")
safe("device_ps3_incident", "no_failure_level_1", ps3_no_level1)

# PS3: Gap 2 fix — derived_component_type fill rate (expect >0% — regex covers printer/CSC/BHU/CHU etc.)
def ps3_derived_component():
    r = spark.sql("SELECT round(100.0*sum(CASE WHEN derived_component_type IS NOT NULL THEN 1 ELSE 0 END)"
                  "/greatest(count(*),1),2) pct FROM mars_dev.gold.device_ps3_incident").first()["pct"]
    # Was 0% before (column didn't exist); expect >20% after regex derivation
    ok = (r is not None) and r > 0
    log("device_ps3_incident", "derived_component_fill_rate", "PASS" if ok else "WARN",
        f"{r}% rows have derived_component_type (was 0% before fix; expect >20%)")
safe("device_ps3_incident", "derived_component_fill_rate", ps3_derived_component)

# PS3: Gap 3 fix — kpi_rule_id fill rate (from edw_kpi_rules join)
def ps3_kpi_rule():
    r = spark.sql("SELECT round(100.0*sum(CASE WHEN kpi_rule_id IS NOT NULL THEN 1 ELSE 0 END)"
                  "/greatest(count(*),1),2) pct FROM mars_dev.gold.device_ps3_incident").first()["pct"]
    log("device_ps3_incident", "kpi_rule_id_fill_rate", "PASS" if (r or 0) > 0 else "WARN",
        f"{r}% rows have kpi_rule_id from edw_kpi_rules join")
safe("device_ps3_incident", "kpi_rule_id_fill_rate", ps3_kpi_rule)

# PS3: Gap 5 — NLP keyword flags present and sum > 0 (at least some rows matched)
def ps3_nlp_flags():
    cols = ["desc_printer_flag", "desc_card_reader_flag", "desc_bill_handler_flag",
            "desc_coin_flag", "desc_comms_flag", "desc_timeout_flag", "desc_replacement_flag"]
    expr = " + ".join(f"sum({c})" for c in cols)
    total = spark.sql(f"SELECT {expr} AS total FROM mars_dev.gold.device_ps3_incident").first()["total"]
    log("device_ps3_incident", "nlp_flags_nonzero", "PASS" if (total or 0) > 0 else "WARN",
        f"total keyword flag hits across 7 cols = {total:,} (expect >0)")
safe("device_ps3_incident", "nlp_flags_nonzero", ps3_nlp_flags)

# PS3: leakage sanity — target-derived columns should NOT be fed as features
def ps3_leak():
    cols = [c.lower() for c in spark.table("mars_dev.gold.device_ps3_incident").columns]
    leaky = [c for c in ["failure_level_label", "is_device_fault", "root_cause_category",
                          "ae_resolution", "ae_problem"] if c in cols]
    log("device_ps3_incident", "leakage_columns_present", "WARN" if leaky else "PASS",
        f"target-derived cols in table (exclude from X): {leaky}")
safe("device_ps3_incident", "leakage_columns_present", ps3_leak)

# PS3: after SIL-C1 silver fix, gold dedup should still pass (belt-and-suspenders)
def ps3_silver_c1_clean():
    d = spark.sql(
        "SELECT COALESCE(SUM(cnt - 1), 0) d FROM "
        "(SELECT count(*) cnt FROM mars_dev.silver.incident_root_cause "
        "GROUP BY availability_event_id HAVING count(*) > 1)"
    ).first()["d"]
    log("device_ps3_incident", "silver_s17_ae_grain",
        "PASS" if d == 0 else "WARN",
        f"silver incident_root_cause has {d:,} AE dupes — rebuild S17 before trusting PS3 features")
safe("device_ps3_incident", "silver_s17_ae_grain", ps3_silver_c1_clean)

# PS2: S27 station network columns present and non-trivial (2026-07-17)
def ps2_station_cols():
    cols_expected = ["is_coordinated_station_failure", "is_major_station_event",
                     "station_devices_failed", "station_total_downtime_min"]
    actual = [c.lower() for c in spark.table("mars_dev.gold.device_ps2_chains").columns]
    missing = [c for c in cols_expected if c not in actual]
    log("device_ps2_chains", "station_cols_present",
        "PASS" if not missing else "FAIL",
        f"missing={missing} (should be empty — S27 join added 2026-07-17)")
safe("device_ps2_chains", "station_cols_present", ps2_station_cols)

def ps2_station_coordinated_rate():
    r = spark.sql(
        "SELECT sum(CASE WHEN is_coordinated_station_failure THEN 1 ELSE 0 END) coord, "
        "count(*) total FROM mars_dev.gold.device_ps2_chains"
    ).first()
    pct = round(100.0 * (r["coord"] or 0) / max(r["total"] or 1, 1), 2)
    log("device_ps2_chains", "station_coordinated_rate", "PASS" if r["total"] > 0 else "WARN",
        f"{pct}% of chains are part of a coordinated station event (≥3 devices same day/station)")
safe("device_ps2_chains", "station_coordinated_rate", ps2_station_coordinated_rate)

# PS2: S29 survival interval columns present (2026-07-17)
def ps2_survival_cols():
    cols_expected = ["days_healthy_before_chain", "last_failure_before_chain", "no_prior_failure_in_window"]
    actual = [c.lower() for c in spark.table("mars_dev.gold.device_ps2_chains").columns]
    missing = [c for c in cols_expected if c not in actual]
    log("device_ps2_chains", "survival_cols_present",
        "PASS" if not missing else "FAIL",
        f"missing={missing} (should be empty — S29 join added 2026-07-17)")
safe("device_ps2_chains", "survival_cols_present", ps2_survival_cols)

def ps2_days_healthy_sanity():
    r = spark.sql(
        "SELECT sum(CASE WHEN days_healthy_before_chain < 0 THEN 1 ELSE 0 END) neg, "
        "round(avg(days_healthy_before_chain), 1) avg_days "
        "FROM mars_dev.gold.device_ps2_chains"
    ).first()
    log("device_ps2_chains", "days_healthy_before_chain_sanity",
        "PASS" if (r["neg"] == 0) else "FAIL",
        f"neg_values={r['neg']} (should be 0)  avg_days_healthy={r['avg_days']}")
safe("device_ps2_chains", "days_healthy_before_chain_sanity", ps2_days_healthy_sanity)

# PS4: ensemble anomaly rate (sanity — not 0% and not ~100%)
def ps4_rate():
    r = spark.sql("SELECT round(100.0*avg(ensemble_anomaly_flag),2) pct FROM mars_dev.gold.device_ps4_hourly").first()["pct"]
    ok = (r is not None) and (0 < r < 50)
    log("device_ps4_hourly", "ensemble_anomaly_rate", "PASS" if ok else "WARN",
        f"{r}% hours flagged (adaptive 2σ Signal 3 — expect lower than static 5% cutoff)")
safe("device_ps4_hourly", "ensemble_anomaly_rate", ps4_rate)

# PS4: Gap 3 fix — Signal 3 (reject_rate_anomaly) must have some NULL baseline rows
# (first 28 days per device have no baseline — STDDEV is NULL -> reject_rate_anomaly = 0 for those rows)
def ps4_signal3_baseline():
    r = spark.sql("SELECT round(100.0*avg(reject_rate_anomaly),2) pct_anomaly "
                  "FROM mars_dev.gold.device_ps4_hourly").first()["pct_anomaly"]
    # Adaptive rate should be lower than static 5% cutoff (which fired on every device above 5%)
    log("device_ps4_hourly", "signal3_adaptive_rate", "PASS" if (r is not None) else "WARN",
        f"{r}% hours with reject_rate_anomaly=1 (adaptive 2σ per-device baseline)")
safe("device_ps4_hourly", "signal3_adaptive_rate", ps4_signal3_baseline)

# PS4: revenue_zero_flag ensemble override — at least some rows should trigger it
def ps4_revenue_override():
    c = spark.sql("SELECT sum(use_revenue_zero_flag) c FROM mars_dev.gold.device_ps4_hourly").first()["c"]
    log("device_ps4_hourly", "revenue_zero_flag_present", "PASS" if (c or 0) > 0 else "WARN",
        f"{c:,} hourly rows with use_revenue_zero_flag=1 (Gap 4 ensemble override)")
safe("device_ps4_hourly", "revenue_zero_flag_present", ps4_revenue_override)

# PS5: censoring rate (survival) — expect a meaningful mix of censored/observed
def ps5_cens():
    r = spark.sql("SELECT round(100.0*avg(CASE WHEN is_censored THEN 1 ELSE 0 END),2) pct "
                  "FROM mars_dev.gold.device_ps5_component").first()["pct"]
    log("device_ps5_component", "censoring_rate", "PASS", f"{r}% censored (no observed failure)")
safe("device_ps5_component", "censoring_rate", ps5_cens)

# PS5: S28 MTTR columns present and non-zero for VALIDATOR (2026-07-17)
def ps5_mttr_cols():
    cols_expected = ["avg_downtime_per_fail_day_min", "avg_rolling_mttr_30d_min",
                     "avg_rolling_mttr_90d_min", "max_downtime_ever_min",
                     "total_failure_days_s28", "last_failure_date_s28"]
    actual = [c.lower() for c in spark.table("mars_dev.gold.device_ps5_component").columns]
    missing = [c for c in cols_expected if c not in actual]
    log("device_ps5_component", "mttr_cols_present",
        "PASS" if not missing else "FAIL",
        f"missing={missing} (should be empty — S28 join added 2026-07-17)")
safe("device_ps5_component", "mttr_cols_present", ps5_mttr_cols)

def ps5_validator_mttr_coverage():
    r = spark.sql(
        "SELECT mars_device_category cat, "
        "sum(CASE WHEN avg_downtime_per_fail_day_min > 0 THEN 1 ELSE 0 END) with_mttr, "
        "count(*) total "
        "FROM mars_dev.gold.device_ps5_component "
        "GROUP BY mars_device_category"
    ).collect()
    for row in r:
        pct = round(100.0 * (row["with_mttr"] or 0) / max(row["total"] or 1, 1), 1)
        cat = row["cat"]
        expected = "expect >0% for all 3 categories after S28 join (VALIDATOR was 0% via S15)"
        log("device_ps5_component", f"mttr_coverage:{cat}",
            "PASS" if pct > 0 else "WARN",
            f"{pct}% rows with avg_downtime_per_fail_day_min > 0 — {expected}")
safe("device_ps5_component", "validator_mttr_coverage", ps5_validator_mttr_coverage)

# PS1: S10 v2 comms/timing features in PS1 daily (R7-1/R7-2 2026-07-17)
# Gold SQL updated 2026-07-17 — aliases: metric_p95/p99/slow_tap/rolling/z_score, comms_*
def ps1_s10_v2_features():
    cols_expected = [
        "metric_p95_txn_ms", "metric_p99_txn_ms",
        "metric_slow_tap_count", "metric_slow_tap_pct",
        "metric_rolling_7d_avg_ms", "metric_z_score_vs_28d",
        "comms_csc_read_err_count", "comms_host_comm_lost_count",
        "comms_device_comms_lost_count", "comms_total_count", "comms_event_flag",
    ]
    actual = [c.lower() for c in spark.table("mars_dev.gold.device_ps1_daily").columns]
    missing = [c for c in cols_expected if c not in actual]
    log("device_ps1_daily", "s10_v2_feature_cols",
        "PASS" if not missing else "FAIL",
        f"missing={missing}")
safe("device_ps1_daily", "s10_v2_feature_cols", ps1_s10_v2_features)

# PS1: cross-wired output table (device + component/serial grain, written by NB2 CELL 38)
# Written as parquet to S3; check if also registered as a Delta table in gold catalog
def ps1_cross_wired_output():
    in_catalog = spark.sql(
        "SELECT count(*) n FROM mars_dev.information_schema.tables "
        "WHERE table_schema='gold' AND table_name='device_ps1_cross_wired_daily'"
    ).first()["n"] > 0
    if in_catalog:
        r = spark.sql("SELECT count(*) n, count(DISTINCT DEVICE_KEY) devs "
                      "FROM mars_dev.gold.device_ps1_cross_wired_daily").first()
        log("device_ps1_cross_wired_daily", "exists_and_populated",
            "PASS" if r["n"] > 0 else "FAIL",
            f"{r['n']:,} rows, {r['devs']:,} devices (device+component grain for ServiceNow API)")
    else:
        log("device_ps1_cross_wired_daily", "exists_and_populated",
            "WARN", "not in gold catalog (written as S3 parquet by NB2 CELL 38 — register as table if needed)")
safe("device_ps1_cross_wired_daily", "exists_and_populated", ps1_cross_wired_output)

# PS5: Gap fix — COMPONENT_TYPE_NAME null rate should improve after COALESCE with derived_component_type_hw
# Censored components (is_censored=TRUE) previously always had NULL COMPONENT_TYPE_NAME
def ps5_component_type_coverage():
    r = spark.sql("SELECT "
                  "round(100.0*sum(CASE WHEN COMPONENT_TYPE_NAME IS NULL THEN 1 ELSE 0 END)/greatest(count(*),1),2) overall_null_pct, "
                  "round(100.0*sum(CASE WHEN is_censored AND COMPONENT_TYPE_NAME IS NULL THEN 1 ELSE 0 END)"
                  "/greatest(sum(CASE WHEN is_censored THEN 1 ELSE 0 END),1),2) censored_null_pct "
                  "FROM mars_dev.gold.device_ps5_component").first()
    log("device_ps5_component", "component_type_null_rate", "PASS",
        f"overall {r['overall_null_pct']}% null, censored {r['censored_null_pct']}% null "
        f"(expect censored null < 100% after derived_component_type_hw COALESCE)")
safe("device_ps5_component", "component_type_null_rate", ps5_component_type_coverage)

# COMMAND ----------
# Write scorecard + summary
res = spark.createDataFrame(RESULTS, "run_ts string, table string, check string, status string, detail string")
spark.sql("CREATE SCHEMA IF NOT EXISTS mars_dev.audit")
res.write.mode("append").saveAsTable("mars_dev.audit.gold_validation_results")

print("\n=== SUMMARY ===")
display(res.groupBy("status").count().orderBy("status"))
print("FAILs (if any):")
display(res.where("status = 'FAIL'"))
print("WARNs (if any):")
display(res.where("status = 'WARN'"))
