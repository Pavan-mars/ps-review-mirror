# Databricks notebook source
# =============================================================================
# NB100 — ServiceNow CTA → servicenow_incident Merge
# 21-Jul-2026  PK / Mars-Techs
#
# PURPOSE
# -------
# bronze.cta_servicenow_incident (CSV origin, 44 cols, ~278K rows) has ~20,850
# incidents that are NOT present in bronze.servicenow_incident (XML/spark-xml
# origin, 321 cols, ~301K rows).  These are pre-XML-ingestion-window historical
# incidents.  Merging them makes servicenow_incident the single richer source
# for PS3 root-cause and PS1 incident features.
#
# WHAT THIS NOTEBOOK DOES
# -----------------------
# Step 1  Anti-join on TRIM(UPPER(number)) — identify truly-unique-to-cta rows
# Step 2  Year-profile the unique rows (confirm historical coverage, not noise)
# Step 3  Build a 44→321 column schema bridge:
#           • 31 direct-name matches (23 clean + 8 type-coercion)
#           • 13 reference fields → {field}_display_value  (sys_id = NULL)
#           • ~277 XML-only cols in dest → NULL
# Step 4  Type coercions:
#           STRING-label → BIGINT for incident_state/priority/severity/state
#             (extract leading digit: "1 - Critical" → 1, "1" → 1)
#           String-int → BIGINT for child_incidents / reopen_count
#           Duration strings → best-effort BIGINT or STRING for
#             calendar_duration / u_estimated_time (guarded try-cast)
# Step 5  DRY_RUN=true (default) prints plan; false executes Delta APPEND
# Step 6  Post-merge row-count + duplicate-number validation
#
# WIDGETS
# -------
#   dry_run    true | false   (default true  — safety gate)
#   catalog    mars_dev
#
# UC / FILE-IO NOTE
# -----------------
# No /dbfs or dbfs:/FileStore writes — all Delta ops go through Spark catalog.
# =============================================================================

from typing import Any

dbutils: Any = globals().get("dbutils")
spark: Any   = globals().get("spark")

# COMMAND ----------
# ── Widgets ────────────────────────────────────────────────────────────────────
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"])
dbutils.widgets.text("catalog",    "mars_dev")

DRY_RUN = dbutils.widgets.get("dry_run").lower() == "true"
CATALOG  = dbutils.widgets.get("catalog").strip()

SRC_TABLE  = f"{CATALOG}.bronze.cta_servicenow_incident"   # 44-col CSV origin
DEST_TABLE = f"{CATALOG}.bronze.servicenow_incident"        # 321-col XML origin

print(f"DRY_RUN     : {DRY_RUN}")
print(f"SRC_TABLE   : {SRC_TABLE}")
print(f"DEST_TABLE  : {DEST_TABLE}")
print(f"Mode        : {'DRY RUN — no writes performed' if DRY_RUN else '🔴 LIVE — will APPEND to DEST_TABLE'}")

# COMMAND ----------
# ── Step 1 : Load tables + anti-join on normalised number ─────────────────────
spark.sql(f"USE CATALOG {CATALOG}")

cta = spark.table(SRC_TABLE)
sni = spark.table(DEST_TABLE)

n_cta = cta.count()
n_sni = sni.count()

print(f"cta_servicenow_incident : {n_cta:>8,} rows  |  {len(cta.columns)} cols")
print(f"servicenow_incident     : {n_sni:>8,} rows  |  {len(sni.columns)} cols")

# COMMAND ----------
from pyspark.sql import functions as F
from pyspark.sql.types import (LongType, IntegerType, ShortType, ByteType,
                               StringType, DoubleType, FloatType,
                               TimestampType, DateType, BooleanType)

# Normalise the join key in both tables
cta_norm = cta.withColumn("_num_norm", F.trim(F.upper(F.col("number"))))
sni_keys  = (sni.withColumn("_num_norm", F.trim(F.upper(F.col("number"))))
                 .select("_num_norm"))

# Anti-join: keep cta rows whose normalised number is NOT in sni
unique_cta = (cta_norm
              .join(sni_keys, on="_num_norm", how="left_anti")
              .drop("_num_norm"))

n_unique = unique_cta.count()
print(f"\nUnique-to-cta rows (not found in servicenow_incident): {n_unique:,}")
print(f"  ({n_unique / n_cta * 100:.1f}% of cta rows)")
print(f"  (Expected ~20,850 — delta from the 92.5% overlap estimate)")

# COMMAND ----------
# ── Step 2 : Year-profile the unique rows ─────────────────────────────────────
# Expected: these are pre-XML-window historical records (older years)
# If scattered across years that XML already covers → investigate before merging

print("=== Unique-to-cta rows by opened_at year ===")
print("(Should cluster in older years — pre-XML pipeline coverage window)\n")

if "opened_at" in unique_cta.columns:
    (unique_cta
     .withColumn("year", F.year(F.to_timestamp(F.col("opened_at"))))
     .groupBy("year")
     .count()
     .orderBy("year")
     .show(30, truncate=False))
else:
    print("[WARN] opened_at not found in cta columns — skipping year profile")

print("=== Category breakdown of unique rows ===")
if "category" in unique_cta.columns:
    (unique_cta
     .groupBy("category")
     .count()
     .orderBy(F.desc("count"))
     .show(15, truncate=False))

# COMMAND ----------
# ── Step 3 : Build 44 → 321 schema bridge ─────────────────────────────────────
#
# Mapping strategy (evaluated left to right per dest column):
#   A. Direct name match   — cta col with same name AND not a ref field
#   B. {ref}_display_value — the 13 cta ref-field cols map here; sys_id = NULL
#   C. {ref}_sys_id        — always NULL (CSV has no sys_ids)
#   D. All others          — NULL cast to dest type (277 XML-only cols)

# The 13 reference fields in cta that map to {field}_display_value in sni
REF_FIELDS = {
    "assigned_to", "assignment_group", "caller_id", "caused_by", "closed_by",
    "cmdb_ci", "location", "problem_id", "resolved_by", "rfc", "task_for",
    "u_chargeable_level", "u_event_code",
}

# 8 type-mismatch columns — exist by name in both tables but types differ
# STRING label → BIGINT (leading-digit extraction handles "1 - Critical" → 1
#                         AND bare "1" → 1 correctly for all ServiceNow codes)
LABEL_TO_LONG = {"incident_state", "priority", "severity", "state"}

# String integer → BIGINT (plain integer strings, just need CAST)
STR_TO_LONG   = {"child_incidents", "reopen_count"}

# Duration strings — type depends on what sni actually stores; resolved at runtime
DURATION_COLS  = {"calendar_duration", "u_estimated_time"}

# Collect dest schema once (name → DataType)
dest_fields  = {f.name: f.dataType for f in sni.schema.fields}
cta_col_set  = set(cta.columns)

# Helper: safe cast expression given a source Spark column and a target DataType
def _safe_cast(col_expr, target_type):
    """Cast col_expr to target_type; returns NULL on parse failure for numeric."""
    if isinstance(target_type, (LongType, IntegerType, ShortType, ByteType)):
        return F.col(col_expr).cast("bigint")
    elif isinstance(target_type, (DoubleType, FloatType)):
        return F.col(col_expr).cast("double")
    elif isinstance(target_type, BooleanType):
        return F.col(col_expr).cast("boolean")
    elif isinstance(target_type, (TimestampType,)):
        return F.to_timestamp(F.col(col_expr))
    elif isinstance(target_type, DateType):
        return F.to_date(F.col(col_expr))
    else:
        return F.col(col_expr).cast("string")


# Build SELECT expression list
select_exprs  = []
bridge_report = {"direct_clean": [], "direct_coerced": [], "ref_display": [],
                 "ref_sys_id": [], "null_xml_only": []}

for col_name, dest_type in dest_fields.items():

    # ── A. Direct match (not a ref field) ────────────────────────────────────
    if col_name in cta_col_set and col_name not in REF_FIELDS:

        if col_name in LABEL_TO_LONG:
            # Extract the leading digit from labels like "1 - Critical", "2", "New"
            # "New" → "" → CAST("") → NULL (no leading digit = unknown code)
            expr = (F.when(
                        F.regexp_extract(F.col(col_name), r"^(\d+)", 1) != "",
                        F.regexp_extract(F.col(col_name), r"^(\d+)", 1).cast("bigint")
                    ).otherwise(F.lit(None).cast("bigint"))
                    .alias(col_name))
            select_exprs.append(expr)
            bridge_report["direct_coerced"].append(f"{col_name} [label→bigint]")

        elif col_name in STR_TO_LONG:
            select_exprs.append(F.col(col_name).cast("bigint").alias(col_name))
            bridge_report["direct_coerced"].append(f"{col_name} [str→bigint]")

        elif col_name in DURATION_COLS:
            # Duration: if dest is numeric try leading-digit extract, else string
            if isinstance(dest_type, (LongType, IntegerType, ShortType, ByteType)):
                expr = (F.when(
                            F.col(col_name).isNotNull() & (F.col(col_name) != ""),
                            F.regexp_extract(F.col(col_name), r"^(\d+)", 1).cast("bigint")
                        ).otherwise(F.lit(None).cast("bigint"))
                        .alias(col_name))
            else:
                expr = F.col(col_name).cast("string").alias(col_name)
            select_exprs.append(expr)
            bridge_report["direct_coerced"].append(f"{col_name} [duration coerce]")

        else:
            # Clean direct copy with safe type cast
            select_exprs.append(_safe_cast(col_name, dest_type).alias(col_name))
            bridge_report["direct_clean"].append(col_name)

    # ── B. {ref}_display_value → cta.ref ─────────────────────────────────────
    elif col_name.endswith("_display_value"):
        base = col_name[: -len("_display_value")]
        if base in REF_FIELDS and base in cta_col_set:
            select_exprs.append(F.col(base).cast("string").alias(col_name))
            bridge_report["ref_display"].append(f"{base} → {col_name}")
        else:
            select_exprs.append(F.lit(None).cast(dest_type).alias(col_name))
            bridge_report["null_xml_only"].append(col_name)

    # ── C. {ref}_sys_id → always NULL ────────────────────────────────────────
    elif col_name.endswith("_sys_id"):
        select_exprs.append(F.lit(None).cast(dest_type).alias(col_name))
        base = col_name[: -len("_sys_id")]
        if base in REF_FIELDS:
            bridge_report["ref_sys_id"].append(col_name)
        else:
            bridge_report["null_xml_only"].append(col_name)

    # ── D. XML-only column → NULL ─────────────────────────────────────────────
    else:
        select_exprs.append(F.lit(None).cast(dest_type).alias(col_name))
        bridge_report["null_xml_only"].append(col_name)

# ── Bridge summary ─────────────────────────────────────────────────────────────
print("=== Schema Bridge Summary ===")
print(f"  SELECT expressions built : {len(select_exprs)}")
print(f"  A. Direct clean copies   : {len(bridge_report['direct_clean'])}")
print(f"  A. Direct + type coerce  : {len(bridge_report['direct_coerced'])}")
print(f"  B. ref → _display_value  : {len(bridge_report['ref_display'])}")
print(f"  C. ref → _sys_id (NULL)  : {len(bridge_report['ref_sys_id'])}")
print(f"  D. XML-only → NULL       : {len(bridge_report['null_xml_only'])}")
print()
print("  Coerced columns:")
for c in bridge_report["direct_coerced"]:
    print(f"    {c}")
print()
print("  Reference field mappings (cta col → display_value col):")
for m in bridge_report["ref_display"]:
    print(f"    {m}")

# COMMAND ----------
# ── Step 4 : Apply bridge ─────────────────────────────────────────────────────
bridged = unique_cta.select(select_exprs)

n_bridged = bridged.count()
assert n_bridged == n_unique, f"Row count changed during bridge: {n_unique} → {n_bridged}"
print(f"Bridged DataFrame: {n_bridged:,} rows × {len(bridged.columns)} cols ✅")
assert len(bridged.columns) == len(sni.columns), (
    f"Column count mismatch: bridged={len(bridged.columns)}, sni={len(sni.columns)}"
)
print(f"Column count matches servicenow_incident ({len(sni.columns)}) ✅")

# COMMAND ----------
# ── Step 4b : Spot-check key PS1 / PS3 columns ───────────────────────────────
KEY_COLS = [c for c in [
    "number", "opened_at", "closed_at", "resolved_at",
    "category", "subcategory", "priority", "severity",
    "incident_state", "state",
    "cmdb_ci_display_value",       # device linkage (PS1 + PS3)
    "u_event_code_display_value",  # PS3 root-cause classification
    "u_chargeable_level_display_value",  # PS1 chargeable signal
    "u_chargeable",                # PS1 label support
    "u_cause_category",            # PS3
    "assigned_to_display_value",
    "assignment_group_display_value",
] if c in bridged.columns]

print("=== Sample bridged rows (key PS1/PS3 columns) ===")
bridged.select(KEY_COLS).show(5, truncate=60)

# COMMAND ----------
# ── Step 4c : Null-rate check on key columns ──────────────────────────────────
print(f"=== Null rates on key columns (bridged rows, n={n_bridged:,}) ===\n")
print(f"  {'Column':<50} {'Non-null':>9}  {'Null%':>6}")
print(f"  {'-'*50} {'-'*9}  {'-'*6}")

for c in KEY_COLS:
    n_non_null = bridged.filter(F.col(c).isNotNull()).count()
    pct_null   = (n_bridged - n_non_null) / n_bridged * 100
    flag = "  ← ⚠️" if pct_null > 90 and c in ("number", "opened_at") else ""
    print(f"  {c:<50} {n_non_null:>9,}  {pct_null:>5.1f}%{flag}")

# COMMAND ----------
# ── Step 5 : DRY_RUN gate → APPEND ───────────────────────────────────────────
if DRY_RUN:
    print("=" * 70)
    print("DRY_RUN = True — NO WRITE PERFORMED")
    print()
    print(f"  Source unique rows  : {n_bridged:,}")
    print(f"  Dest current count  : {n_sni:,}")
    print(f"  Expected post-merge : {n_sni + n_bridged:,}")
    print()
    print("To execute the merge, rerun with dry_run = false.")
    print("=" * 70)
else:
    print(f"🔴 LIVE MODE — appending {n_bridged:,} rows to {DEST_TABLE} ...")
    (bridged
     .write
     .format("delta")
     .mode("append")
     .option("mergeSchema", "false")   # schema must match exactly
     .saveAsTable(DEST_TABLE))
    print("Append complete.")

# COMMAND ----------
# ── Step 6 : Post-merge validation (only when not dry-run) ────────────────────
if not DRY_RUN:
    post_count = spark.table(DEST_TABLE).count()
    expected   = n_sni + n_bridged

    print("=== Post-merge row-count validation ===")
    print(f"  Pre-merge count    : {n_sni:,}")
    print(f"  Rows appended      : {n_bridged:,}")
    print(f"  Expected total     : {expected:,}")
    print(f"  Actual total       : {post_count:,}")

    if post_count == expected:
        print("  ✅ COUNT EXACT MATCH")
    elif abs(post_count - expected) <= 5:
        print(f"  ✅ COUNT WITHIN TOLERANCE (delta = {post_count - expected})")
    else:
        print(f"  ❌ COUNT MISMATCH — delta = {post_count - expected:+,} — investigate!")

    # ── Duplicate-number check ────────────────────────────────────────────────
    print("\n=== Duplicate number check (post-merge) ===")
    dup_df = (spark.table(DEST_TABLE)
              .withColumn("_num_norm", F.trim(F.upper(F.col("number"))))
              .groupBy("_num_norm")
              .agg(F.count("*").alias("cnt"))
              .filter("cnt > 1"))

    n_dup_tickets = dup_df.count()
    if n_dup_tickets == 0:
        print("  ✅ No duplicate incident numbers")
    else:
        print(f"  ⚠️  {n_dup_tickets:,} incident numbers appear more than once")
        print("     Top duplicates:")
        dup_df.orderBy(F.desc("cnt")).show(10, truncate=False)

    # ── Coverage improvement check ────────────────────────────────────────────
    print("\n=== Year-distribution of servicenow_incident after merge ===")
    (spark.table(DEST_TABLE)
     .withColumn("year", F.year(F.to_timestamp(F.col("opened_at"))))
     .groupBy("year")
     .count()
     .orderBy("year")
     .show(30, truncate=False))

    # ── PS3 signal quality: key ref-field coverage ───────────────────────────
    print("=== PS3/PS1 key-field coverage in the appended rows ===")
    print(f"  (sampling {DEST_TABLE} for rows from this append only)\n")

    # Identify the appended rows by number
    appended_nums = bridged.select(F.trim(F.upper(F.col("number"))).alias("_num_norm"))
    appended_sni  = (spark.table(DEST_TABLE)
                     .withColumn("_num_norm", F.trim(F.upper(F.col("number"))))
                     .join(appended_nums, on="_num_norm", how="inner")
                     .drop("_num_norm"))

    ps_check_cols = [c for c in [
        "cmdb_ci_display_value", "u_event_code_display_value",
        "u_chargeable_level_display_value", "u_chargeable",
        "u_cause_category", "category", "subcategory",
    ] if c in appended_sni.columns]

    n_app = appended_sni.count()
    print(f"  Appended-row sample size: {n_app:,}")
    print(f"  {'Column':<50} {'Fill%':>6}")
    print(f"  {'-'*50} {'-'*6}")
    for c in ps_check_cols:
        fill = appended_sni.filter(F.col(c).isNotNull() & (F.col(c).cast("string") != "")).count()
        print(f"  {c:<50} {fill / n_app * 100:>5.1f}%")

else:
    print("(Post-merge validation skipped in DRY_RUN mode)")
    print()
    print("=== Quick sanity: overlap confirmation re-check (no write) ===")
    # Re-confirm the unique set is clean before any live run
    overlap_count = (cta_norm
                     .join(sni.withColumn("_num_norm",
                                          F.trim(F.upper(F.col("number")))).select("_num_norm"),
                           on="_num_norm", how="inner")
                     .count())
    print(f"  cta rows WITH a match in sni  : {overlap_count:,}")
    print(f"  cta rows WITHOUT a match (merge candidates) : {n_unique:,}")
    total_check = overlap_count + n_unique
    assert total_check == n_cta, f"Partition check failed: {overlap_count} + {n_unique} = {total_check} ≠ {n_cta}"
    print(f"  Partition integrity check      : {overlap_count} + {n_unique} = {total_check} == {n_cta} ✅")

# COMMAND ----------
# =============================================================================
# USAGE GUIDE
# =============================================================================
# 1. First run: DRY_RUN = true (default)
#    → Review Step 1 (unique count, confirm ~20,850)
#    → Review Step 2 (year profile, confirm historical clustering)
#    → Review Step 3 (bridge summary, confirm ref-field mappings)
#    → Review Step 4b/4c (spot-check + null rates on key PS cols)
#    → If everything looks good, proceed to step 2.
#
# 2. Live run: flip dry_run widget to false, Run All
#    → Appends bridged rows to bronze.servicenow_incident
#    → Validates count + duplicate-number check
#    → Reports PS3/PS1 key-field fill rates on appended rows
#
# 3. After merge — recommended follow-on actions:
#    a. Rebuild silver.incident_history (S15) and silver.incident_root_cause
#       (S17) from the enriched servicenow_incident; the dedup at S17 already
#       handles duplicate availability_event_id — check the FAIL count drops.
#    b. Re-export silver tables to S3:
#         run export_silver_to_s3.py  (widgets: mode=overwrite)
#    c. Rebuild gold.device_ps1_daily to pull in the richer incident history,
#       then re-run SageMaker PS1 notebooks for the AUC-boost measurement.
#    d. The CTA table can be archived (DO NOT DROP):
#         spark.sql("ALTER TABLE mars_dev.bronze.cta_servicenow_incident
#                    RENAME TO mars_dev.bronze.cta_servicenow_incident_archived_20Jul2026")
#       Only do this AFTER validating the merge in a live run.
# =============================================================================
print("NB100 complete.")
