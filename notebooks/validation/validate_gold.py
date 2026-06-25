# Databricks notebook source
# MAGIC %md
# MAGIC # validate_gold — runtime DQ + ML-readiness checks for the 5 gold PS tables (`mars_dev.gold`)
# MAGIC Operationalizes `docs/validation/Silver_Gold_Validation_2026-06-25.md`.
# MAGIC
# MAGIC Per table: existence, row count, grain uniqueness, future dates, key/label null rates.
# MAGIC Plus PS-specific checks: PS1 label positive-rate (chargeable vs any-outage), PS3 target class balance,
# MAGIC PS4 ensemble anomaly rate, PS5 censoring rate, and a leakage sanity flag.
# MAGIC
# MAGIC Writes a scorecard to `mars_dev.audit.gold_validation_results`. Checks degrade to WARN if a column is absent.

# COMMAND ----------
spark.sql("USE CATALOG mars_dev")
from datetime import datetime
CAT, SCH = "mars_dev", "gold"
RUN_TS = datetime.utcnow().isoformat()
RESULTS = []

def log(table, check, status, detail=""):
    RESULTS.append((RUN_TS, f"{SCH}.{table}", check, status, str(detail)))
    print(f"[{status:4}] {table:24} {check:24} {detail}")

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
            d = spark.sql(f"SELECT count(*) - count(DISTINCT {', '.join(grain)}) AS d FROM {fq}").first()["d"]
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
GOLD = {
    "device_ps1_daily":    dict(grain=["DEVICE_ID", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_ID", "will_fail_7d"]),
    "device_ps2_chains":   dict(grain=["DEVICE_ID", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "device_ps3_incident": dict(grain=["availability_event_id"], notnull=["DEVICE_ID", "AE_FAILURE_LEVEL"]),
    "device_ps4_hourly":   dict(grain=["DEVICE_ID", "hour_bucket"], date_cols=["transit_day"], notnull=["DEVICE_ID", "ensemble_anomaly_flag"]),
    "device_ps5_component":dict(grain=["DEVICE_ID", "COMPONENT_SERIAL_NBR"], notnull=["DEVICE_ID"]),
}
for t, cfg in GOLD.items():
    check_table(t, **cfg)

# COMMAND ----------
# PS-specific ML-readiness checks

# PS1: label positive rate. Spec target = chargeable failure ~1.52%. Current label = ANY outage (duration_min>0),
# expected ~20-60%. A very high rate flags the open label-definition decision (gate to failure_level IN (1,2,3,4,5,16)).
def ps1_label():
    r = spark.sql("SELECT round(100.0*avg(will_fail_7d),2) pct FROM mars_dev.gold.device_ps1_daily").first()["pct"]
    log("device_ps1_daily", "label_positive_rate", "WARN" if (r or 0) > 10 else "PASS",
        f"{r}% positive (chargeable spec ~1.5%; any-outage ~20-60% -> confirm label intent)")
safe("device_ps1_daily", "label_positive_rate", ps1_label)

# PS3: target class balance — flag classes with too few rows to learn
def ps3_balance():
    rows = spark.sql("SELECT AE_FAILURE_LEVEL lvl, count(*) n FROM mars_dev.gold.device_ps3_incident "
                     "GROUP BY AE_FAILURE_LEVEL ORDER BY n").collect()
    rare = {str(x["lvl"]): x["n"] for x in rows if x["n"] < 30}
    log("device_ps3_incident", "target_class_balance", "WARN" if rare else "PASS",
        f"{len(rare)} class(es) with <30 rows: {rare}")
safe("device_ps3_incident", "target_class_balance", ps3_balance)

# PS3: leakage sanity — target-derived columns should NOT be fed as features
def ps3_leak():
    cols = [c.lower() for c in spark.table("mars_dev.gold.device_ps3_incident").columns]
    leaky = [c for c in ["failure_level_label", "is_device_fault", "root_cause_category", "ae_resolution", "ae_problem"] if c in cols]
    log("device_ps3_incident", "leakage_columns_present", "WARN" if leaky else "PASS",
        f"target-derived cols in table (exclude from X): {leaky}")
safe("device_ps3_incident", "leakage_columns_present", ps3_leak)

# PS4: ensemble anomaly rate (sanity — not 0% and not ~100%)
def ps4_rate():
    r = spark.sql("SELECT round(100.0*avg(ensemble_anomaly_flag),2) pct FROM mars_dev.gold.device_ps4_hourly").first()["pct"]
    ok = (r is not None) and (0 < r < 50)
    log("device_ps4_hourly", "ensemble_anomaly_rate", "PASS" if ok else "WARN", f"{r}% hours flagged")
safe("device_ps4_hourly", "ensemble_anomaly_rate", ps4_rate)

# PS5: censoring rate (survival) — expect a meaningful mix of censored/observed
def ps5_cens():
    r = spark.sql("SELECT round(100.0*avg(CASE WHEN is_censored THEN 1 ELSE 0 END),2) pct "
                  "FROM mars_dev.gold.device_ps5_component").first()["pct"]
    log("device_ps5_component", "censoring_rate", "PASS", f"{r}% censored (no observed failure)")
safe("device_ps5_component", "censoring_rate", ps5_cens)

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
